import machine
import rp2
import rp2_util
import dma_util
import time
import array

PUT_WORD_SIZE = const(32)
GET_WORD_SIZE = const(32)

SM_FREQ_DEFAULT = const(125_000_000)  # 125MHz = 8ns per tick (maximum resolution)


class Pulses:
    def __init__(self, get_pin=None, put_pin=None, sm_freq=SM_FREQ_DEFAULT):
        self.sm_freq  = sm_freq
        self.get_done = False
        self.put_done = False
        self.sm_get_nr = 0
        self.sm_put_nr = 4

        if get_pin is not None:
            if (sm_freq * 2) > machine.freq():
                raise ValueError("sm_freq too high for get")
            self.sm_get = rp2.StateMachine(
                self.sm_get_nr,
                self.sm_get_pulses,
                freq     = sm_freq * 2,
                jmp_pin  = get_pin,
                in_base  = get_pin,
                set_base = get_pin
            )
            self.sm_get.irq(self._irq_finished)
        else:
            self.sm_get = None

        if put_pin is not None:
            if sm_freq > machine.freq():
                raise ValueError("sm_freq too high for put")
            self.sm_put = rp2.StateMachine(
                self.sm_put_nr,
                self.sm_put_pulses,
                freq     = sm_freq,
                out_base = put_pin,
                set_base = put_pin
            )
            self.sm_put.irq(self._irq_finished)
        else:
            self.sm_put = None

    # ── PUT PIO program ───────────────────────────────────────────────────────
    #
    # Protocol: FIFO receives alternating [on_ticks, off_ticks, on_ticks, off_ticks ...]
    # For a single kick:          [kick_ticks, off_ticks]
    # For PWM hold over N cycles: [on, off, on, off, ...] x N — fed by DMA ring
    #
    # PIO stalls at pull() when FIFO empties — pin stays LOW safely.

    @staticmethod
    @rp2.asm_pio(
        out_init     = rp2.PIO.OUT_LOW,
        out_shiftdir = rp2.PIO.SHIFT_RIGHT,
        set_init     = rp2.PIO.OUT_LOW,
        autopull     = False,
        pull_thresh  = PUT_WORD_SIZE
    )
    def sm_put_pulses():
        set(pindirs, 1)

        label("on_phase")
        pull()                      # stall here with pin LOW when FIFO empty
        mov(x, osr)
        set(pins, 1)                # pin HIGH

        label("on_count")
        jmp(x_dec, "on_count")

        pull()                      # pull off_ticks
        mov(x, osr)
        set(pins, 0)                # pin LOW

        label("off_count")
        jmp(x_dec, "off_count")

        jmp("on_phase")

    # ── GET PIO program (unchanged from original) ─────────────────────────────

    @staticmethod
    @rp2.asm_pio(
        in_shiftdir = rp2.PIO.SHIFT_LEFT,
        autopull    = False,
        autopush    = False,
        push_thresh = GET_WORD_SIZE
    )
    def sm_get_pulses():
        set(pindirs, 0)
        pull()
        mov(isr, null)
        in_(pins, 1)
        mov(y, isr)
        jmp("start_timeout")
        label("trigger")
        mov(osr, x)
        mov(isr, null)
        in_(pins, 1)
        mov(x, isr)
        jmp(x_not_y, "start")
        label("start_timeout")
        mov(x, osr)
        jmp(x_dec, "trigger")
        label("start")
        push(block)
        pull()
        mov(y, osr)
        pull()
        jmp("check_done")
        label("get_pulse")
        mov(x, osr)
        jmp(pin, "count_high")
        label("count_low")
        jmp(pin, "issue")
        jmp(x_dec, "count_low")
        jmp("issue")
        label("count_high")
        jmp(pin, "still_high")
        jmp("issue")
        label("still_high")
        jmp(x_dec, "count_high")
        label("issue")
        mov(isr, x)
        push(block)
        label("check_done")
        jmp(y_dec, "get_pulse")
        label("end")
        irq(noblock, rel(0))

    # ── IRQ handler ──────────────────────────────────────────────────────────

    def _irq_finished(self, sm):
        if sm == self.sm_put:
            self.put_done = True
        else:
            self.get_done = True

    # ── Public API ────────────────────────────────────────────────────────────

    def put_pulses(self, duration_us):
        """
        Fire a single kick pulse. Blocking.
        duration_us: pulse duration in microseconds.
        """
        if self.sm_put is None:
            raise ValueError("put_pulses is not enabled")
        ticks_per_us = self.sm_freq // 1_000_000
        on_ticks     = max(1, duration_us * ticks_per_us - 7)
        off_ticks    = max(1, ticks_per_us - 7)  # 1us off time
        self._buf    = array.array("L", [on_ticks, off_ticks])
        self.put_done = False
        self.sm_put.restart()
        self.sm_put.active(1)
        rp2_util.sm_dma_put(0, self.sm_put_nr, self._buf, len(self._buf))
        # wait for DMA to complete
        while rp2_util.dma_transfer_count(0) > 0:
            time.sleep_us(10)
        # wait for PIO to finish clocking out FIFO
        while rp2_util.sm_tx_fifo_level(self.sm_put_nr) > 0:
            time.sleep_us(10)
        # wait for last pulse to complete
        last_us = (on_ticks + off_ticks) // ticks_per_us
        time.sleep_us(last_us + 100)
        self.sm_put.active(0)
        self.put_done = True

    def put_pwm(self, on_us, off_us, duration_us):
        """
        PWM hold via DMA ring buffer. Non-blocking.
        on_us:       on time per cycle in microseconds
        off_us:      off time per cycle in microseconds
        duration_us: total duration in microseconds
        Only 2 words in RAM regardless of duration or frequency.
        No restart() — no glitch.
        """
        if self.sm_put is None:
            raise ValueError("put_pin not configured")
        ticks_per_us  = self.sm_freq // 1_000_000
        on_ticks      = max(1, on_us  * ticks_per_us - 7)
        off_ticks     = max(1, off_us * ticks_per_us - 7)
        n_cycles      = duration_us // (on_us + off_us)
        self._pwm_buf = array.array("L", [on_ticks, off_ticks])
        # ring_size=3 wraps every 2^3=8 bytes = 2 x 32-bit words
        dma_util.sm_dma_put_ring(0, self.sm_put_nr, self._pwm_buf, n_cycles * 2, 3)

    # ── GET (unchanged from original) ─────────────────────────────────────────

    def get_pulses(self, buffer, start_timeout=100_000, bit_timeout=100_000):
        if self.sm_get is None:
            raise ValueError("get_pulses is not enabled")
        self.get_done = False
        self.sm_get.restart()
        self.sm_get.put(start_timeout)
        self.sm_get.put(len(buffer))
        self.sm_get.put(bit_timeout)
        self.sm_get.active(1)
        start_state = self.sm_get.get()
        self.sm_get.get(buffer)
        self.sm_get.active(0)
        buffer[0] = bit_timeout - buffer[0] + 7
        for i in range(1, len(buffer)):
            buffer[i] = bit_timeout - buffer[i] + 3
        return start_state
