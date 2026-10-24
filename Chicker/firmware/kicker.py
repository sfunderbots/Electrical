# Kick / damping gate driver: one PIO state machine owns the KICK pin from boot to shutdown.
#
# Why: switching the pin between PWM, PIO and GPIO is what caused the high-power glitches.
# Stopping a PWM slice or a PIO state machine freezes the pin at its current level (possibly
# HIGH), and PWM duty writes only latch at the next counter wrap. Here the pin is handed to the
# PIO once and never reassigned. Every instruction drives it via side-set, so it is LOW everywhere
# except the on-phase, including while stalled waiting for the next command.
#
# Commands are "segments" (see kickmath.py): a kick is one segment with one period, a damping hold
# is a stream of DAMP_SEGMENT_MS segments, so duty changes land on period boundaries. If the main
# loop stalls, the FIFO runs dry and the pin stays LOW (fail-safe). abort() forces the pin LOW
# within one PIO cycle with an exec'd jump, then re-initialises the state machine.

import machine
import rp2
from machine import Pin
import kickmath


@rp2.asm_pio(sideset_init=rp2.PIO.OUT_LOW, fifo_join=rp2.PIO.JOIN_TX)
def kick_segments():
    wrap_target()
    pull(block).side(0)            # n_periods - 1      (idle here, pin LOW)
    mov(y, osr).side(0)
    pull(block).side(0)            # high_cycles - 2
    mov(isr, osr).side(0)
    pull(block).side(0)            # low_cycles - 3, stays in OSR
    label("period")
    mov(x, isr).side(1)            # pin HIGH
    label("high")
    jmp(x_dec, "high").side(1)
    mov(x, osr).side(0)            # pin LOW
    label("low")
    jmp(x_dec, "low").side(0)
    jmp(y_dec, "period").side(0)
    wrap()


# RP2040 PIO0 register offsets (datasheet 3.7)
_PIO0_BASE = 0x50200000
_SM0_EXECCTRL = 0x0CC
_SM0_ADDR = 0x0D4
_SM0_PINCTRL = 0x0DC
_SM_STRIDE = 0x18


class Kicker:
    def __init__(self, cfg, pin_no, sm_id):
        self.cfg = cfg
        self._pin_no = pin_no
        self._sm_id = sm_id
        self._pin = Pin(pin_no, Pin.OUT, value=0)     # driven LOW as GPIO before the PIO takes over
        self.sys_hz = machine.freq()
        self.cyc_per_us = kickmath.cycles_per_us(self.sys_hz)
        self._regs = _PIO0_BASE + sm_id * _SM_STRIDE
        self._sm = None
        self._idle_pc = -1
        self._init_sm()
        self.ok, self.selftest_msg = self._selftest()
        # PWM streaming state
        self.pwm_running = False
        self._pwm_high = 0
        self._pwm_low = 0
        self._pwm_freq = 0
        self._pwm_left = 0              # periods still to queue
        self._seg_periods = 1
        self._pwm_queued = False
        self.underruns = 0
        self.aborts = 0
        self.pulses = 0

    # ------------------------------------------------------------------ setup / checks
    def _init_sm(self):
        self._sm = rp2.StateMachine(self._sm_id, kick_segments, freq=self.sys_hz,
                                    sideset_base=Pin(self._pin_no))
        self._sm.active(1)
        # Stalls immediately on the first pull: the PC is now the program's absolute start address.
        self._idle_pc = machine.mem32[self._regs + _SM0_ADDR] & 0x1F

    def _selftest(self):
        execctrl = machine.mem32[self._regs + _SM0_EXECCTRL]
        pinctrl = machine.mem32[self._regs + _SM0_PINCTRL]
        if execctrl & (1 << 30):
            return False, "side-set is optional (expected mandatory)"
        if (pinctrl >> 29) & 0x7 != 1:
            return False, "side-set count != 1"
        if (pinctrl >> 10) & 0x1F != self._pin_no:
            return False, "side-set base pin mismatch"
        if self._sm.tx_fifo() != 0:
            return False, "TX FIFO not empty at boot"
        if self._pin.value() != 0:
            return False, "kick pin not LOW at boot"
        return True, "ok"

    def _pc(self):
        return machine.mem32[self._regs + _SM0_ADDR] & 0x1F

    # ------------------------------------------------------------------ status
    def idle(self):
        """No command queued or running (stalled on the first pull)."""
        return self._sm.tx_fifo() == 0 and self._pc() == self._idle_pc

    def pin_level(self):
        return self._pin.value()

    # ------------------------------------------------------------------ single pulses
    def pulse_words(self, width_us):
        return kickmath.pulse_words(self.cfg, self.cyc_per_us, width_us)

    def push(self, words):
        """Queue precomputed words. Safe in a hard IRQ: no allocation, never blocks when idle."""
        sm = self._sm
        sm.put(words[0])
        sm.put(words[1])
        sm.put(words[2])
        self.pulses += 1

    def fire(self, width_us):
        if not self.ok or not self.idle() or self.pwm_running:
            return False
        self.push(self.pulse_words(width_us))
        return True

    # ------------------------------------------------------------------ damping PWM
    def start_pwm(self, freq_hz, duty_pct, duration_us):
        if not self.ok or not self.idle():
            return False
        self._pwm_freq = freq_hz
        self._pwm_high, self._pwm_low = kickmath.pwm_cycles(self.cfg, self.cyc_per_us, freq_hz, duty_pct)
        self._pwm_left = kickmath.periods_in(self.cyc_per_us, self._pwm_high, self._pwm_low, duration_us)
        self._seg_periods = kickmath.periods_in(self.cyc_per_us, self._pwm_high, self._pwm_low,
                                                self.cfg.DAMP_SEGMENT_MS * 1000)
        self.pwm_running = True
        self._pwm_queued = False
        self.service()
        return True

    def set_duty(self, duty_pct):
        if self.pwm_running:
            self._pwm_high, self._pwm_low = kickmath.pwm_cycles(self.cfg, self.cyc_per_us,
                                                                self._pwm_freq, duty_pct)

    def service(self):
        """Keep the FIFO topped up while a hold runs. Call every loop."""
        if not self.pwm_running:
            return
        sm = self._sm
        if self._pwm_queued and self._pwm_left > 0 and self.idle():
            self.underruns += 1           # FIFO ran dry mid-hold: pin was LOW for a while (safe, but a glitch)
        # At most ONE segment waiting behind the running one: if the main loop hangs, the pin goes
        # LOW within 2 segments (50 ms) on its own.
        while self._pwm_left > 0 and sm.tx_fifo() < 3:
            n = min(self._seg_periods, self._pwm_left)
            w = kickmath.segment_words(n, self._pwm_high, self._pwm_low)
            sm.put(w[0])
            sm.put(w[1])
            sm.put(w[2])
            self._pwm_left -= n
            self._pwm_queued = True
        if self._pwm_left == 0 and self.idle():
            self.pwm_running = False

    def stop_pwm(self):
        if self.pwm_running:
            return self.abort()
        return True

    # ------------------------------------------------------------------ abort
    def abort(self):
        """Pin LOW within one PIO cycle, drop anything queued. Returns True if the pin reads LOW."""
        sm = self._sm
        sm.exec(self._idle_pc)        # 'jmp idle side 0': encoding = absolute address, side bit 0
        sm.active(0)
        self._init_sm()               # clears the FIFO, PC back to the start; pin stays PIO / LOW
        self.pwm_running = False
        self._pwm_left = 0
        self.aborts += 1
        return self._pin.value() == 0

    def shutdown(self):
        """Emergency path (main.py exception handler): abort, then hand the pin back to GPIO LOW."""
        try:
            self._sm.exec(self._idle_pc)
            self._sm.active(0)
        finally:
            Pin(self._pin_no, Pin.OUT, value=0)
