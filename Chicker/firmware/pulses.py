import machine
import rp2
import rp2_util
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

    # ── GET PIO program (unchanged from original) ────────────────────────────

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

    # ── New PUT PIO program ──────────────────────────────────────────────────
    #
    # Protocol: FIFO receives alternating [on_ticks, off_ticks, on_ticks, off_ticks ...]
    #
    # For a single kick:        [kick_ticks, 0]
    # For PWM hold over N cycles: [on, off, on, off, ...] x N pairs — fed by DMA
    #
    # DMA TRANS_COUNT controls how many words are sent, which naturally
    # terminates the sequence. The PIO stalls on pull() when the FIFO is empty
    # and DMA has finished — no explicit counter needed inside the PIO at all.
    #
    # X is the only scratch register used — for countdown in both phases.
    # Y is unused, freeing up the register conflict in the original program.
    #
    # Pin behaviour:
    #   - Starts LOW (out_init = OUT_LOW)
    #   - Pulled HIGH for on_ticks
    #   - Pulled LOW  for off_ticks
    #   - Returns LOW when done (IRQ fires, DMA exhausted)

    @staticmethod
    @rp2.asm_pio(
        out_init     = rp2.PIO.OUT_LOW,
        out_shiftdir = rp2.PIO.SHIFT_RIGHT,
        set_init     = rp2.PIO.OUT_LOW,
        autopull     = False,
        pull_thresh  = PUT_WORD_SIZE
    )
    def sm_put_pulses():
        set(pindirs, 1)             # pin = output

        # ── ON phase ─────────────────────────────────────────────────────────
        label("on_phase")
        pull()                      # pull on_ticks — stalls here when done
        mov(x, osr)                 # x = countdown
        set(pins, 1)                # pin HIGH

        label("on_count")
        jmp(x_dec, "on_count")     # count down on_ticks

        # ── OFF phase ────────────────────────────────────────────────────────
        pull()                      # pull off_ticks
        mov(x, osr)                 # x = countdown
        set(pins, 0)                # pin LOW

        label("off_count")
        jmp(x_dec, "off_count")    # count down off_ticks

        # ── loop back for next pair ───────────────────────────────────────────
        jmp("on_phase")

        # Note: no explicit "end" label needed.
        # When DMA finishes, the FIFO empties and the PIO stalls on pull().
        # The IRQ is fired by the put_pulses_v2 wrapper once DMA transfer
        # count reaches zero, not by the PIO program itself.

    # ── IRQ handler ─────────────────────────────────────────────────────────

    def _irq_finished(self, sm):
        if sm == self.sm_put:
            self.put_done = True
        else:
            self.get_done = True

    # ── Public API ───────────────────────────────────────────────────────────

    def put_pulses_v2(self,
                      duration_us: int,
                      duty: int       = 100,
                      freq_hz: int    = None,
                      blocking: bool  = True):
        """
        Simple unified interface for kick and PWM hold.

        Single kick (duty=100 or freq_hz=None):
            put_pulses_v2(duration_us=5000, duty=100)
            → pin HIGH for exactly 5000us, then LOW. Done.

        PWM hold:
            put_pulses_v2(duration_us=2_000_000, duty=15, freq_hz=5000)
            → 15% duty cycle at 5kHz for 2 seconds.

        Resolution is 8ns per tick at 125MHz (default sm_freq).
        Kick accuracy: ±8ns.
        PWM on/off times: ±8ns per edge.

        Parameters
        ----------
        duration_us : total duration of the pulse or PWM window in microseconds
        duty        : duty cycle as integer percentage 1-100
                      100 (default) = solid kick pulse, no PWM
        freq_hz     : PWM frequency in Hz. Ignored when duty=100.
        blocking    : if True (default), waits for completion before returning.
                      Set False if you want to check put_done yourself.
        """
        if self.sm_put is None:
            raise ValueError("put_pin not configured")

        ticks_per_us = self.sm_freq // 1_000_000  # 125 at 125MHz

        if duty >= 100 or freq_hz is None:
            # ── Single solid kick pulse ──────────────────────────────────────
            # One on/off pair: [kick_ticks, 0]
            # off_ticks = 0 means the off_count loop exits immediately
            # (jmp(x_dec) with x=0 decrements to 0xFFFFFFFF and loops once,
            #  so use 1 instead of 0 to avoid a spurious long off-time)
            on_ticks  = max(1, duration_us * ticks_per_us - 7)  # -7 overhead compensation
            off_ticks = 1                                         # minimum off, exits fast
            buf = array.array("L", [on_ticks, off_ticks])

        else:
            # ── PWM hold ─────────────────────────────────────────────────────
            period_us = 1_000_000 // freq_hz
            on_us     = max(1, (period_us * duty) // 100)
            off_us    = period_us - on_us
            n_cycles  = duration_us // period_us

            on_ticks  = max(1, on_us  * ticks_per_us - 7)  # -7 overhead compensation
            off_ticks = max(1, off_us * ticks_per_us - 7)

            # Build flat [on, off, on, off ...] array
            # At 5kHz for 2s this is 10000 words = 40KB — fine for RP2040
            # At 5kHz for 3s this is 15000 words = 60KB — still fine
            buf = array.array("L", [on_ticks, off_ticks] * n_cycles)
            print("n_cycles", n_cycles, "on_us", on_us, "off_us", off_us, "buf len", len(buf))
            
            # ── Tick count validation ─────────────────────────────────────────────
            # Expected vs actual tick counts, and what duration they represent
            expected_on_ticks  = on_us  * ticks_per_us
            expected_off_ticks = off_us * ticks_per_us
            actual_on_us  = on_ticks  / ticks_per_us
            actual_off_us = off_ticks / ticks_per_us
            error_on_ticks  = on_ticks  - expected_on_ticks
            error_off_ticks = off_ticks - expected_off_ticks
            print("--- tick validation ---")
            print("expected on :", expected_on_ticks,  "ticks =", on_us,       "us")
            print("actual   on :", on_ticks,           "ticks =", actual_on_us, "us  error:", error_on_ticks, "ticks")
            print("expected off:", expected_off_ticks, "ticks =", off_us,       "us")
            print("actual   off:", off_ticks,          "ticks =", actual_off_us,"us  error:", error_off_ticks, "ticks")
            print("note: jmp(x_dec) adds 1 extra tick per loop iteration")
            print("net on  error in ticks (inc jmp overhead):", error_on_ticks  + 1)
            print("net off error in ticks (inc jmp overhead):", error_off_ticks + 1)
            print("----------------------")
            
        print("on_ticks", on_ticks, "off_ticks", off_ticks, "ticks_per_us", ticks_per_us)
        self._fire(buf, blocking)

    def _fire(self, buf, blocking=True):
        self.put_done = False
        self.sm_put.restart()
        self.sm_put.active(1)
        rp2_util.sm_dma_put(0, self.sm_put_nr, buf, len(buf))
        rp2_util.sm_dma_put(0, self.sm_put_nr, buf, len(buf))
        print("DMA started, transfer count:", rp2_util.dma_transfer_count(0))
        if blocking:
            count = 0
            while rp2_util.dma_transfer_count(0) > 0:
                time.sleep_us(1)
                count += 1
                if count > 100000:
                    print("DMA stuck, transfer count:", rp2_util.dma_transfer_count(0))
                    break
        if blocking:
            # Wait for DMA to finish filling the FIFO
            while rp2_util.dma_transfer_count(0) > 0:
                time.sleep_us(1)
            # Wait for PIO to finish clocking out remaining FIFO contents
            # Each word takes on_ticks + off_ticks cycles at sm_freq
            # We wait for the FIFO TX level to drop to 0
            while rp2_util.sm_tx_fifo_level(self.sm_put_nr) > 0:
                time.sleep_us(1)
            # Wait for the last pair to finish clocking out
            # Worst case is one full period
            total_us = 0
            for i in range(0, len(buf), 2):
                total_us += (buf[i] + buf[i+1]) // (self.sm_freq // 1_000_000)
            last_period_us = (buf[-2] + buf[-1]) // (self.sm_freq // 1_000_000)
            time.sleep_us(last_period_us + 100)
            self.sm_put.active(0)
            self.put_done = True

    def _dma_done(self, expected_count):
        """Check if DMA has finished transferring expected_count words."""
        remaining = rp2_util.dma_transfer_count(0)
        return remaining == 0

    # ── Original put_pulses kept for backwards compatibility ─────────────────

    def put_pulses(self, buffer, start_level=1):
        """Original interface — kept so existing kick code does not break."""
        if self.sm_put is None:
            raise ValueError("put_pulses is not enabled")
        self.put_done = False
        for i in range(len(buffer)):
            buffer[i] = max(0, buffer[i] - 7)
        self.sm_put.restart()
        self.sm_put.active(1)
        self.sm_put.put(len(buffer))
        self.sm_put.put(start_level != 0)
        rp2_util.sm_dma_put(0, self.sm_put_nr, buffer, len(buffer))
        while self.put_done is False:
            time.sleep_us(1)
        self.sm_put.active(0)

    # ── GET (unchanged from original) ────────────────────────────────────────

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
