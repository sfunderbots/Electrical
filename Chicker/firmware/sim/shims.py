"""Fake MicroPython modules (machine, utime, rp2, micropython) plus fake canbus and usb_io.

install() puts them in sys.modules so the real firmware files import them unchanged. Only module
names that do not exist in CPython are replaced (plus the firmware-local canbus / usb_io), so the
rest of Python (and Jupyter) is unaffected.
"""

import sys
import types
from . import world as W
from . import pio as pio_mod


def _w():
    return W.current()


# ============================================================================ utime
def _make_utime():
    m = types.ModuleType("utime")

    def ticks_ms():
        w = _w()
        w.cost("ticks")
        return w.ticks_ms()

    def ticks_us():
        w = _w()
        w.cost("ticks")
        return w.ticks_us()

    def ticks_diff(a, b):
        d = (a - b) % W.TICKS_PERIOD
        if d >= W.TICKS_PERIOD // 2:
            d -= W.TICKS_PERIOD
        return d

    def ticks_add(a, d):
        return (a + d) % W.TICKS_PERIOD

    def sleep_ms(ms):
        w = _w()
        w.advance(int(ms * W.NS_PER_MS))
        w.poll_irqs()

    def sleep_us(us):
        w = _w()
        w.advance(int(us * W.NS_PER_US))
        w.poll_irqs()

    m.ticks_ms, m.ticks_us, m.ticks_diff, m.ticks_add = ticks_ms, ticks_us, ticks_diff, ticks_add
    m.sleep_ms, m.sleep_us = sleep_ms, sleep_us
    m.sleep = lambda s: sleep_ms(s * 1000)
    return m


# ============================================================================ machine
class Pin:
    IN = "in"
    OUT = "out"
    PULL_UP = "up"
    PULL_DOWN = "down"
    IRQ_FALLING = 1
    IRQ_RISING = 2

    def __init__(self, n, mode=None, pull=None, value=None):
        self.n = n
        w = _w()
        if mode is not None or value is not None:
            w.gpio_config(n, mode=mode, pull=pull, value=value)
            w.cost("pin_write")

    def init(self, mode=None, pull=None, value=None):
        _w().gpio_config(self.n, mode=mode, pull=pull, value=value)
        _w().cost("pin_write")

    def value(self, v=None):
        w = _w()
        if v is None:
            w.cost("pin_read")
            return w.level(self.n)
        w.gpio_write(self.n, v)
        w.cost("pin_write")

    def on(self):
        self.value(1)

    def off(self):
        self.value(0)

    def irq(self, handler=None, trigger=3, hard=False):
        g = _w().pin(self.n)
        g.irq_handler = handler
        g.irq_trigger = trigger
        g.irq_hard = hard
        g.last_in = _w().level(self.n)


class ADC:
    def __init__(self, pin):
        self.n = pin.n if isinstance(pin, Pin) else pin

    def read_u16(self):
        w = _w()
        w.cost("adc")
        p = w.phys
        kind = W.ADC_PINS.get(self.n)
        if kind == "hv":
            v = p.adc_hv_volts()
        elif kind == "batt":
            v = p.adc_batt_volts()
        elif kind == "v5":
            v = p.adc_5v_volts()
        else:
            v = 0.0
        q = int(round(v / 3.3 * 4095 + w.rng.gauss(0, p.p["adc_noise_lsb"])))
        q = min(max(q, 0), 4095)
        return (q << 4) | (q >> 8)


class PWM:
    """RP2040 PWM slice model, used to replay the old firmware.

    Duty writes are double-buffered and latch at the counter wrap. deinit() stops the counter and the
    output FREEZES at its current level; re-creating PWM(pin) resumes from the frozen phase with the
    old compare value until the next wrap - the hazards the v2 kicker avoids by never using PWM.
    """

    def __init__(self, pin, freq=None, duty_u16=None):
        w = _w()
        self.n = pin.n
        slot = w.pwms.get(self.n)
        if slot is None:
            self.level = 0
            self.enabled = False
            self.top_ns = 10_000
            self.cc_frac = 0.0
            self.pending_frac = None
            self.t_wrap = w.now
            self.phase = 0
            self._rise_done = False
            self._fall_done = False
        else:
            self.__dict__.update(slot.__dict__)
        w.pwms[self.n] = self
        w.gpio_set_function(self.n, "pwm", self)     # pin shows the slice's (possibly frozen) level now
        if not self.enabled:
            self.t_wrap = w.now - self.phase
            self.enabled = True
        if freq is not None:
            self.freq(freq)
        if duty_u16 is not None:
            self.duty_u16(duty_u16)
        w.cost("pin_write")

    def freq(self, f=None):
        if f is None:
            return int(1e9 / self.top_ns)
        self.sync()
        self.top_ns = int(1e9 / f)
        self.t_wrap = _w().now
        self._rise_done = self._fall_done = False

    def duty_u16(self, d=None):
        if d is None:
            return int(self.cc_frac * 65535)
        self.sync()
        self.pending_frac = d / 65535.0
        _w().cost("pin_write")

    def duty_ns(self, ns):
        self.duty_u16(int(min(ns / self.top_ns, 1.0) * 65535))

    def deinit(self):
        self.sync()
        w = _w()
        self.phase = (w.now - self.t_wrap) % self.top_ns
        self.enabled = False                  # output frozen at self.level
        w.cost("pin_write")

    def sync(self):
        self.advance(_w().now)

    def advance(self, t_end):
        while self.enabled:
            ps, top = self.t_wrap, self.top_ns
            he = ps + int(round(self.cc_frac * top))
            nw = ps + top
            if not self._rise_done:
                if ps > t_end:
                    return
                if he > ps:
                    self._set(1, ps)
                self._rise_done = True
            if not self._fall_done:
                if he < nw:
                    if he > t_end:
                        return
                    self._set(0, he)
                self._fall_done = True
            if nw > t_end:
                return
            self.t_wrap = nw
            self._rise_done = self._fall_done = False
            if self.pending_frac is not None:
                self.cc_frac = self.pending_frac
                self.pending_frac = None

    def _set(self, level, t):
        if level != self.level:
            self.level = level
            _w().pwm_pin_change(self.n, level, t)


class WDT:
    def __init__(self, id=0, timeout=5000):
        w = _w()
        w.wdt_timeout_ms = timeout
        w.wdt_last_feed = w.now

    def feed(self):
        w = _w()
        w.wdt_last_feed = w.now


def _make_machine():
    m = types.ModuleType("machine")
    m.Pin, m.ADC, m.PWM, m.WDT = Pin, ADC, PWM, WDT
    m.freq = lambda *a: _w().sys_hz
    m.reset_cause = lambda: _w().reset_cause

    def disable_irq():
        w = _w()
        st = w.irq_enabled
        w.irq_enabled = False
        return st

    def enable_irq(st=True):
        w = _w()
        w.irq_enabled = bool(st)
        w.poll_irqs()

    m.disable_irq, m.enable_irq = disable_irq, enable_irq

    class _M32:
        def __getitem__(self, a):
            return _w().mem32[a]
    m.mem32 = _M32()
    m.PWRON_RESET, m.WDT_RESET = 1, 3
    return m


# ============================================================================ rp2
class StateMachine:
    def __init__(self, sm_id, prog=None, freq=None, sideset_base=None, **kw):
        w = _w()
        emu = w.sms.get(sm_id)
        if emu is None:
            emu = w.sms[sm_id] = pio_mod.StateMachineEmu(w, sm_id)
        self._emu = emu
        if prog is not None:
            base = sideset_base.n if isinstance(sideset_base, Pin) else sideset_base
            emu.init(prog, freq or w.sys_hz, base)
        w.cost("sm_other")

    def active(self, v=None):
        _w().cost("sm_other")
        return self._emu.active(v)

    def put(self, word):
        _w().cost("sm_put")
        self._emu.put(word)

    def tx_fifo(self):
        _w().cost("sm_other")
        return self._emu.tx_fifo()

    def exec(self, instr):
        _w().cost("sm_other")
        self._emu.exec(instr)


def _make_rp2():
    m = types.ModuleType("rp2")
    m.asm_pio = pio_mod.asm_pio
    m.StateMachine = StateMachine

    class PIO:
        OUT_LOW = pio_mod.OUT_LOW
        OUT_HIGH = pio_mod.OUT_HIGH
        JOIN_TX = pio_mod.JOIN_TX
        JOIN_RX = pio_mod.JOIN_RX
        SHIFT_LEFT = 0
        SHIFT_RIGHT = 1
    m.PIO = PIO
    return m


def _make_micropython():
    m = types.ModuleType("micropython")
    m.const = lambda x: x
    m.native = lambda f: f
    m.viper = lambda f: f
    return m


# ============================================================================ canbus
class CanMsg:
    def __init__(self, can_id=0, data=None, flags=None):
        self.can_id = can_id
        self.data = bytes(data or b"")


class _Mcp:
    def __init__(self, port):
        self.port = port

    def setLoopbackMode(self):
        self.port.loopback = True
        return 0

    def setNormalMode(self):
        self.port.loopback = False
        return 0


class FakeCan:
    """Stands in for canbus.Can (MCP2515). Frames come from World.can_rx_schedule."""

    def __init__(self):
        w = _w()
        w.can = self
        self.masks = [0, 0]
        self.filters = [0] * 6
        self.loopback = False
        self.rx = []                  # [(t_ns, id, data)]
        self.tx = []                  # [(t_ns, id, data)]
        self.can = _Mcp(self)
        self.fail_begin = False

    def begin(self, *a, **k):
        return 1 if self.fail_begin else 0

    def init_mask(self, idx, ext, mask):
        self.masks[idx] = mask
        return 0

    def init_filter(self, idx, ext, fid):
        self.filters[idx] = fid
        return 0

    def accepts(self, can_id):
        m = self.masks[0]
        return any((can_id & m) == (f & m) for f in self.filters)

    def checkReceive(self):
        w = _w()
        w.cost("can_check")
        return any(t <= w.now for t, _, _ in self.rx)

    def recv(self):
        w = _w()
        w.cost("can_recv")
        for k, (t, cid, data) in enumerate(self.rx):
            if t <= w.now:
                del self.rx[k]
                return 0, CanMsg(cid, data)
        return 5, None

    def send(self, msg):
        w = _w()
        w.cost("can_send")
        if self.loopback:
            if self.accepts(msg.can_id):
                self.rx.append((w.now, msg.can_id, bytes(msg.data)))
        else:
            self.tx.append((w.now, msg.can_id, bytes(msg.data)))
        return 0

    def deliver(self, can_id, data, t_ns=None):
        """Robot side: put a frame on the bus (dropped if the hardware filter rejects it)."""
        w = _w()
        if self.accepts(can_id):
            self.rx.append((w.now if t_ns is None else t_ns, can_id, bytes(data)))
            return True
        return False


def _make_canbus():
    m = types.ModuleType("canbus")
    m.Can = FakeCan
    m.CanMsg = CanMsg

    class CanError:
        ERROR_OK = 0
        ERROR_FAIL = 1
    m.CanError = CanError
    m.CanMsgFlag = type("CanMsgFlag", (), {"RTR": 0, "EFF": 0})
    return m


def _make_usb_io():
    m = types.ModuleType("usb_io")

    def read_char():
        w = _w()
        if w.usb_in:
            return w.usb_in.pop(0)
        return None

    def write(line):
        w = _w()
        w.cost("usb_write")
        w.usb_out.append((w.now, line))
    m.read_char, m.write = read_char, write
    return m


_FIRMWARE_MODULES = ("config", "protocol", "sm_base", "charge_sm", "mode_sm", "kickmath", "kicker",
                     "power_stage", "sensors", "board", "console", "app", "main")


def install(firmware_dir):
    """Install the fakes and put the firmware directory on sys.path. Idempotent."""
    for name, maker in (("utime", _make_utime), ("machine", _make_machine), ("rp2", _make_rp2),
                        ("micropython", _make_micropython), ("canbus", _make_canbus),
                        ("usb_io", _make_usb_io)):
        if not isinstance(sys.modules.get(name), types.ModuleType) or \
                not getattr(sys.modules[name], "__sim__", False):
            mod = maker()
            mod.__sim__ = True
            sys.modules[name] = mod
    if not hasattr(sys, "print_exception"):          # MicroPython-only; main.py uses it
        import traceback
        sys.print_exception = lambda e, f=None: traceback.print_exception(e)
    if firmware_dir not in sys.path:
        sys.path.insert(0, firmware_dir)
