import machine
import rp2
import rp2_util
import dma_util
import time
import array
import uctypes


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

    @staticmethod
    @rp2.asm_pio(
        out_init     = rp2.PIO.OUT_LOW,
        out_shiftdir = rp2.PIO.SHIFT_RIGHT,
        set_init     = rp2.PIO.OUT_LOW,
        autopull     = False,
        pull_thresh  = PUT_WORD_SIZE
    )
    def sm_put_pulses():
        set(pindirs, 1)         # pin = output

        label("on_phase")
        pull()                  # pull on_ticks — stalls here when done, pin is LOW
        mov(x, osr)             # x = countdown
        set(pins, 1)            # pin HIGH

        label("on_count")
        jmp(x_dec, "on_count")  # count down on_ticks

        set(pins, 0)            # pin LOW before pulling off_ticks
                                # if FIFO empties here, pin stays LOW safely
        pull()                  # pull off_ticks
        mov(x, osr)             # x = countdown

        label("off_count")
        jmp(x_dec, "off_count") # count down off_ticks

        jmp("on_phase")         # loop back for next pair

    # ── IRQ handler ─────────────────────────────────────────────────────────

    def _irq_finished(self, sm):
        if sm == self.sm_put:
            self.put_done = True
        else:
            self.get_done = True

    # ── GET PIO program (unchanged from original) ─────────────────────────────

    @staticmethod
    @rp2.asm_pio(
        in_shiftdir = rp2.PIO.SHIFT_LEFT,
        autopull    = False,
        autopush    = False,
        push_thresh = GET_WORD_SIZE
    )
    def sm_get_pulses():
        set(pindirs, 0)             # set to input
        pull()                      # get start timeout value

# start section: wait for a transition up to start_timeout ticks
        mov(isr, null)
        in_(pins, 1)                # get the initial pin state
        mov(y, isr)                 # cannot use mov(y, pins)
        jmp("start_timeout")

        label("trigger")
        mov(osr, x)                 # save the decremented timeout
        mov(isr, null)              # clear ISR
        in_(pins, 1)                # and get a clean 1/0
        mov(x, isr)                 # get the actual pin state
        jmp(x_not_y, "start")       # Transition found

        label("start_timeout")      # test for start timeout
        mov(x, osr)                 # get the timeout value
        jmp(x_dec, "trigger")       # nope, still wait

# trigger seen or timeout
# get the pulse counter, bit_timeout and report the inital state
        label("start")              # got a trigger, go
        push(block)                 # report the last pin value
        pull()                      # pull bit count
        mov(y, osr)                 # store it into the counter
        pull()                      # get the bit timeout value
                                    # keep it in osr
        jmp("check_done")

# pulse loop section, go and time pulses
        label("get_pulse")
        mov(x, osr)                 # preload with the max value
        jmp(pin, "count_high")      # have a high level

        label("count_low")          # timing a low pulse
        jmp(pin, "issue")           #
        jmp(x_dec, "count_low")     # count cycles
        # get's here if the pulse is longer than max_time
        jmp("issue")                # could as well jmp("end")

        label("count_high")
        jmp(pin, "still_high")
        jmp("issue")

        label("still_high")
        jmp(x_dec, "count_high")    # count cycles
        # get's here if the pulse is longer than max_time

        label("issue")              # report the result
        mov(isr, x)
        push(block)

        label("check_done")         # pulse counter
        jmp(y_dec, "get_pulse")     # and go for another loop

        label("end")
        irq(noblock, rel(0))        # get finished!

    def _dma_done(self, expected_count):
        """Check if DMA has finished transferring expected_count words."""
        remaining = rp2_util.dma_transfer_count(0)
        return remaining == 0
    
    def put_pulses(self, duration_us, blocking=True):
        if self.sm_put is None:
            raise ValueError("put_pin not configured")
        ticks_per_us = self.sm_freq // 1_000_000
        on_ticks  = max(1, duration_us * ticks_per_us - 7)
        off_ticks = 1
        buf = array.array("L", [on_ticks, off_ticks])
        self._fire(buf, blocking, restart=True)

    def put_pwm(self, duty, freq_hz, duration_us):
        if self.sm_put is None:
            raise ValueError("put_pin not configured")
        if not (1 <= duty <= 99) or freq_hz is None:
            raise ValueError("duty must be 1-99 and freq_hz must be set")
        ticks_per_us = self.sm_freq // 1_000_000
        period_us    = 1_000_000 // freq_hz
        on_us        = max(1, (period_us * duty) // 100)
        off_us       = period_us - on_us
        on_ticks     = max(1, on_us  * ticks_per_us - 4)
        off_ticks    = max(1, off_us * ticks_per_us - 4)
        n_cycles     = duration_us // period_us
        buf = array.array("L", [on_ticks, off_ticks])
        for _ in range(n_cycles):
            self._fire(buf, blocking=True, restart=False)

    def put_pwm_v2(self, duty, freq_hz, duration_us):
        if self.sm_put is None:
            raise ValueError("put_pin not configured")
        if not (1 <= duty <= 99) or freq_hz is None:
            raise ValueError("duty must be 1-99 and freq_hz must be set")
        ticks_per_us = self.sm_freq // 1_000_000
        period_us    = 1_000_000 // freq_hz
        on_us        = max(1, (period_us * duty) // 100)
        off_us       = period_us - on_us
        on_ticks     = max(1, on_us  * ticks_per_us - 4)
        off_ticks    = max(1, off_us * ticks_per_us - 4)
        n_cycles     = duration_us // period_us
        buf = array.array("L", [on_ticks, off_ticks])
        for _ in range(n_cycles):
            self._fire(buf, blocking=True, restart=False)

    def _fire(self, buf, blocking=True, restart=False):
        self.put_done = False
        self._buf = buf
        if restart:
            self.sm_put.restart()
        self.sm_put.active(1)
        rp2_util.sm_dma_put(0, self.sm_put_nr, buf, len(buf))
        if blocking:
            while rp2_util.dma_transfer_count(0) > 0:
                time.sleep_us(1)
            while rp2_util.sm_tx_fifo_level(self.sm_put_nr) > 0:
                time.sleep_us(1)
            last_period_us = (buf[-2] + buf[-1]) // (self.sm_freq // 1_000_000)
            time.sleep_us(last_period_us + 100)
            self.sm_put.active(0)
            self.put_done = True
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
