# Fast HV capture on the RP2040: the ADC runs free and DMA copies every sample into a buffer, so the CPU stays free
# (e.g. to keep the kicker's PWM FIFO topped up) while thousands of samples per second are recorded.
#
# Needs MicroPython 1.22 or newer (rp2.DMA). On older firmware capture() falls back to a read_u16() loop, which is
# much slower and less even; the result says which was used.
#
# RP2040 datasheet references: ADC registers 4.9.6, DMA 2.5, DREQ_ADC = 36.
#
#   from hv_dma_capture import HvCapture
#   cap = HvCapture(adc_pin=29, rate_hz=50000, n_samples=40000)   # allocate early: 80 KB of RAM
#   cap.start()                 # returns immediately, DMA fills the buffer in the background
#   while cap.running(): ...    # do other work meanwhile
#   raw = cap.samples()         # memoryview of 12-bit counts
#   cap.release()               # give the ADC back to machine.ADC

import machine
import utime
from machine import Pin, ADC

try:
    import rp2
    _HAVE_DMA = hasattr(rp2, "DMA")
except ImportError:
    _HAVE_DMA = False

ADC_BASE = 0x4004C000
ADC_CS = ADC_BASE + 0x00       # EN bit 0, START_MANY bit 3, AINSEL bits 14:12
ADC_FCS = ADC_BASE + 0x08      # EN bit 0, SHIFT bit 1, ERR bit 2, DREQ_EN bit 3, EMPTY bit 8, UNDER 10, OVER 11, THRESH 27:24
ADC_FIFO = ADC_BASE + 0x0C     # VAL bits 11:0, ERR bit 15
ADC_DIV = ADC_BASE + 0x10      # INT bits 23:8, FRAC bits 7:0 -> one sample every (1 + INT + FRAC/256) cycles of 48 MHz
ADC_CLOCK_HZ = 48_000_000
DREQ_ADC = 36


class HvCapture:
    def __init__(self, adc_pin=29, rate_hz=50000, n_samples=40000):
        if not (1000 <= rate_hz <= 500000):
            raise ValueError("rate_hz must be 1 kHz .. 500 kHz")
        self._adc = ADC(Pin(adc_pin))           # sets the pin up as an analog input
        self._ainsel = adc_pin - 26              # GPIO26..29 = ADC inputs 0..3
        self.rate_hz = rate_hz
        self.n = n_samples
        self._buf = bytearray(2 * n_samples)     # allocate once, early (big block of RAM)
        self._dma = None
        self.used_dma = False
        self.errors = 0                          # samples the ADC flagged as conversion errors

    # ------------------------------------------------------------------ control
    @staticmethod
    def has_dma():
        return _HAVE_DMA

    def start(self, service=None):
        """Start capturing n_samples at rate_hz. Returns at once when DMA is available. Without DMA it captures by
        polling and only returns when done, calling service() every 64 samples (e.g. kicker.service) meanwhile."""
        if not _HAVE_DMA:
            self._capture_by_polling(service)
            return
        m = machine.mem32
        div = ADC_CLOCK_HZ / self.rate_hz - 1
        m[ADC_CS] = 1 | (self._ainsel << 12)                    # ADC on, our input, not running yet
        m[ADC_DIV] = (int(div) << 8) | int((div - int(div)) * 256)
        m[ADC_FCS] = 1 | (1 << 3) | (1 << 24)                    # FIFO on, DREQ on, threshold 1, 12-bit samples
        while not (m[ADC_FCS] & (1 << 8)):                       # empty the FIFO
            _ = m[ADC_FIFO]
        m[ADC_FCS] = m[ADC_FCS] | (1 << 10) | (1 << 11)          # clear the under / overflow flags
        self._dma = rp2.DMA()
        ctrl = self._dma.pack_ctrl(size=1, inc_read=False, inc_write=True, treq_sel=DREQ_ADC)
        self._dma.config(read=ADC_FIFO, write=self._buf, count=self.n, ctrl=ctrl, trigger=True)
        m[ADC_CS] = 1 | (1 << 3) | (self._ainsel << 12)          # START_MANY: free-running from now
        self.used_dma = True

    def running(self):
        return self._dma is not None and self._dma.active()

    def wait(self, timeout_ms=5000):
        t0 = utime.ticks_ms()
        while self.running():
            if utime.ticks_diff(utime.ticks_ms(), t0) > timeout_ms:
                raise RuntimeError("capture did not finish")
        if self.used_dma:
            self._stop_adc()

    def release(self):
        """Stop the ADC and hand it back to machine.ADC (normal read_u16() works again)."""
        if self._dma is not None:
            self._dma.close()
            self._dma = None
        if self.used_dma:                    # the polling fallback never touches the ADC registers
            self._stop_adc()
            machine.mem32[ADC_DIV] = 0
            self.used_dma = False

    def _stop_adc(self):
        m = machine.mem32
        m[ADC_CS] = 1 | (self._ainsel << 12)                     # stop free-running
        m[ADC_FCS] = 0
        while not (m[ADC_FCS] & (1 << 8)):
            _ = m[ADC_FIFO]

    # ------------------------------------------------------------------ results
    def count(self, k):
        """Sample k as a 12-bit ADC count (bit 15, the ADC's conversion-error flag, is masked off)."""
        b = self._buf
        return (b[2 * k] | (b[2 * k + 1] << 8)) & 0x0FFF

    def lines(self, per_line=32):
        """The capture as text lines of comma-separated counts, made one line at a time (a list of every sample
        would not fit in RAM next to the buffer). Also counts the ADC's conversion-error flags into .errors."""
        b = self._buf
        self.errors = 0
        for start in range(0, self.n, per_line):
            parts = []
            for k in range(start, min(start + per_line, self.n)):
                w = b[2 * k] | (b[2 * k + 1] << 8)
                if w & 0x8000:
                    self.errors += 1
                parts.append(str(w & 0x0FFF))
            yield ",".join(parts)

    # ------------------------------------------------------------------ fallback (no rp2.DMA)
    def _capture_by_polling(self, service):
        """Old firmware only: read_u16() as fast as Python allows, then report the real rate achieved."""
        read = self._adc.read_u16
        buf = self._buf
        t0 = utime.ticks_us()
        for k in range(self.n):
            w = read() >> 4
            buf[2 * k] = w & 0xFF
            buf[2 * k + 1] = w >> 8
            if service is not None and not (k & 63):
                service()
        dt = utime.ticks_diff(utime.ticks_us(), t0)
        self.rate_hz = int(self.n * 1_000_000 / max(dt, 1))
        self.used_dma = False
