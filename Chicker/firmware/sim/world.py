"""Virtual board: clock, GPIO nets, interrupts, ADCs, PIO/PWM peripherals and the invariant monitor.

Every call the firmware makes into machine/utime/rp2/canbus/usb_io lands here, costs a little virtual
CPU time, and gives pending interrupts a chance to run (hard IRQs can fire between any two calls,
unless the firmware has disabled them). Time is integer nanoseconds.
"""

import random
from . import pio as pio_mod
from .physics import Physics

TICKS_PERIOD = 1 << 30            # MicroPython ticks wrap at 2^30
NS_PER_MS = 1_000_000
NS_PER_US = 1_000

# virtual CPU cost of firmware calls (rough MicroPython-on-RP2040 figures)
COST = dict(pin_write=2_000, pin_read=1_500, adc=6_000, ticks=500, mem32=800, sm_put=1_500,
            sm_other=1_000, can_check=40_000, can_recv=150_000, can_send=250_000, usb_write=20_000,
            loop_overhead=600_000, irq_entry=15_000, sleep=0)

# board nets that matter for safety
PIN_CHIP, PIN_KICK, PIN_DONE, PIN_CHARGE, PIN_LED, PIN_NDIS = 2, 3, 4, 5, 6, 8
PIN_SHELL, PIN_BEAM = 9, 23
ADC_PINS = {26: "batt", 27: "v5", 29: "hv"}

_current = None


def current():
    if _current is None:
        raise RuntimeError("no simulated world is active")
    return _current


def activate(world):
    global _current
    _current = world


class GpioState:
    __slots__ = ("mode", "func", "out", "pull", "irq_handler", "irq_trigger", "irq_hard", "owner", "last_in")

    def __init__(self):
        self.mode = "in"
        self.func = "sio"
        self.out = 0
        self.pull = "down"            # RP2040 reset default
        self.irq_handler = None
        self.irq_trigger = 0
        self.irq_hard = False
        self.owner = None             # PIO SM / PWM object when func is pio / pwm
        self.last_in = None


class Violation:
    def __init__(self, t_ns, rule, detail):
        self.t_ns = t_ns
        self.rule = rule
        self.detail = detail

    def __repr__(self):
        return "<%s @ %.3f ms: %s>" % (self.rule, self.t_ns / 1e6, self.detail)


class World:
    def __init__(self, seed=0, start_ticks_ms=0, physics=None, deadtime_ms=50, kick_max_us=5000,
                 charge_timeout_ms=6000, overvolt_v=225.0, damp_max_on_us=200):
        self.rng = random.Random(seed)
        self.now = 0
        self.ticks_offset_ms = start_ticks_ms
        self.phys = Physics(self.rng, **(physics or {}))
        self.gpio = {}
        self.sms = {}
        self.pwms = {}
        self.irq_enabled = True
        self.in_irq = False
        self.pending_irqs = []
        self.mem32 = _Mem32(self)
        self.sys_hz = 125_000_000
        self.reset_cause = 1          # PWRON
        self.wdt_timeout_ms = None
        self.wdt_last_feed = 0
        self.wdt_resets = 0
        self.can = None               # set by fake canbus
        self.usb_in = []
        self.usb_out = []
        self.trace = []
        self.trace_period_ns = 1 * NS_PER_MS
        self._t_trace = 0
        self.kick_edges = []          # (t, level) of the KICK net
        self.pin_events = []          # (t, pin, level) for CHARGE / NDIS / KICK / CHIP
        self.func_changes = []        # (t, pin, func)
        self.kicker_owned_from = None
        self.mode_probe = None        # callable -> mode state name (set by the harness)
        # monitor
        self.violations = []
        self.deadtime_ns = deadtime_ms * NS_PER_MS
        self.kick_max_ns = kick_max_us * NS_PER_US
        self.charge_timeout_ns = charge_timeout_ms * NS_PER_MS
        self.overvolt_v = overvolt_v
        self.allow_kick_in_idle = False
        self.idle_abort_grace_ns = 200 * NS_PER_US
        self._net = {PIN_CHARGE: 0, PIN_NDIS: 0, PIN_KICK: 0, PIN_CHIP: 0}
        self._net_t = {PIN_CHARGE: 0, PIN_NDIS: 0, PIN_KICK: 0, PIN_CHIP: 0}
        self._ov_reported = False
        self._temp_reported = False
        self.cpu_scale = 1.0

    # ================================================================ time
    def ticks_ms(self):
        return (self.now // NS_PER_MS + self.ticks_offset_ms) % TICKS_PERIOD

    def ticks_us(self):
        return (self.now // NS_PER_US + self.ticks_offset_ms * 1000) % TICKS_PERIOD

    def cost(self, kind):
        self.advance(int(COST[kind] * self.cpu_scale))
        self.poll_irqs()

    def advance(self, dt_ns):
        target = self.now + dt_ns
        phys = self.phys
        while self.now < target:
            step = 20 * NS_PER_US if (phys.busy() or self._net[PIN_KICK] or self._pio_running()) else 500 * NS_PER_US
            t_end = min(target, self.now + step)
            # peripherals first: they record KICK edges with exact times inside [now, t_end]
            start_edges = len(self.kick_edges)
            for sm in self.sms.values():
                sm.advance(t_end)
            for pw in self.pwms.values():
                pw.advance(t_end)
            t = self.now
            level = self._kick_level_at_start(start_edges)
            for te, lv in self.kick_edges[start_edges:]:
                if te > t:
                    phys.integrate((te - t) * 1e-9, level)
                    t = te
                level = lv
            if t_end > t:
                phys.integrate((t_end - t) * 1e-9, level)
            self.now = t_end
            self._after_physics()

    def _kick_level_at_start(self, start_edges):
        if start_edges == 0:
            return 0
        return self.kick_edges[start_edges - 1][1]

    def _pio_running(self):
        for sm in self.sms.values():
            if sm.enabled and not sm.stalled:
                return True
        return False

    def sync_pio(self):
        for sm in self.sms.values():
            sm.advance(self.now)

    def _after_physics(self):
        p = self.phys
        # input pins driven by the board
        self._input_changed(PIN_DONE, p.done_level())
        self._input_changed(PIN_BEAM, 0 if p.beam_broken() else 1)
        if p.max_v > self.overvolt_v and not self._ov_reported:
            self._ov_reported = True
            self.violate("I9_overvolt", "bank reached %.1f V" % p.max_v)
        if p.max_temp > p.p["dump_temp_limit"] and not self._temp_reported:
            self._temp_reported = True
            self.violate("I10_dump_overtemp", "dump resistor reached %.0f C" % p.max_temp)
        if self._net[PIN_CHARGE] and self.now - self._net_t[PIN_CHARGE] > self.charge_timeout_ns + 500 * NS_PER_MS:
            self.violate("I11_charge_stuck", "CHARGE high for > timeout")
            self._net_t[PIN_CHARGE] = self.now
        if self._net[PIN_KICK] and self.now - self._net_t[PIN_KICK] > self.kick_max_ns:
            self.violate("I4_kick_too_long", "KICK high for %.0f us" % ((self.now - self._net_t[PIN_KICK]) / 1e3))
            self._net_t[PIN_KICK] = self.now
        if self.wdt_timeout_ms is not None and \
                self.now - self.wdt_last_feed > self.wdt_timeout_ms * NS_PER_MS:
            self.wdt_resets += 1
            self.wdt_last_feed = self.now
        if self.now - self._t_trace >= self.trace_period_ns:
            self._t_trace = self.now
            self.trace.append((self.now / 1e6, p.v, p.i, self._net[PIN_CHARGE], self._net[PIN_NDIS],
                               self._net[PIN_KICK], p.x, p.done_level(), p.max_temp))

    # ================================================================ GPIO
    def pin(self, n):
        g = self.gpio.get(n)
        if g is None:
            g = self.gpio[n] = GpioState()
        return g

    def gpio_set_function(self, n, func, owner=None):
        g = self.pin(n)
        if g.func != func:
            self.func_changes.append((self.now, n, func))
            before = self.level(n)
        g.func = func
        g.owner = owner
        if self.func_changes and self.func_changes[-1][0] == self.now and self.func_changes[-1][1] == n:
            after = self.level(n)
            # Switching the KICK pin between peripherals is only hazardous if it can produce a high
            # level (frozen PWM/PIO output, old compare value). A switch with the pin LOW on both
            # sides - e.g. the emergency hand-back to GPIO after an abort - is harmless.
            if n == PIN_KICK and self.kicker_owned_from is not None and (before or after):
                self.violate("I7_kick_pin_function", "KICK pin switched to %s while high" % func)
            if n == PIN_KICK and func == "pio" and self.kicker_owned_from is None:
                self.kicker_owned_from = self.now
        self._update_net(n)

    def gpio_write(self, n, v):
        g = self.pin(n)
        g.out = 1 if v else 0
        self._update_net(n)

    def gpio_config(self, n, mode=None, pull=None, value=None):
        g = self.pin(n)
        if value is not None:
            g.out = 1 if value else 0          # MicroPython sets the value before the mode
        if mode is not None:
            g.mode = mode
            self.gpio_set_function(n, "sio")
        if pull is not None or mode is not None:
            g.pull = pull
        self._update_net(n)

    def level(self, n):
        g = self.pin(n)
        if g.func == "pio":
            return g.owner.level if g.owner is not None else 0
        if g.func == "pwm":
            return g.owner.level
        if g.mode == "out":
            return g.out
        return self._input_level(n)

    def _input_level(self, n):
        p = self.phys
        if n == PIN_DONE:
            return p.done_level()
        if n == PIN_BEAM:
            return 0 if p.beam_broken() else 1
        if n == PIN_SHELL:
            return 1
        g = self.pin(n)
        return 1 if g.pull == "up" else 0

    def pio_pin_change(self, sm, n, level, t):
        if n == PIN_KICK and self.pin(n).func == "pio":
            self._net_edge(PIN_KICK, level, t)
            self.kick_edges.append((t, level))

    def pwm_pin_change(self, n, level, t):
        if n == PIN_KICK and self.pin(n).func == "pwm":
            self._net_edge(PIN_KICK, level, t)
            self.kick_edges.append((t, level))

    def _update_net(self, n):
        if n in self._net:
            lv = self.level(n)
            if n == PIN_KICK and lv != self._net[n]:
                self.kick_edges.append((self.now, lv))
            self._net_edge(n, lv, self.now)

    def _net_edge(self, n, level, t):
        if self._net.get(n) == level:
            return
        prev_t = self._net_t[n]
        self._net[n] = level
        self._net_t[n] = t
        self.pin_events.append((t, n, level))
        if n == PIN_CHARGE:
            self.phys.set_charge_pin(level)
        elif n == PIN_NDIS:
            self.phys.set_ndis_pin(level)
        self._check(n, level, t, prev_t)

    # ================================================================ monitor
    def violate(self, rule, detail):
        self.violations.append(Violation(self.now, rule, detail))

    def _check(self, n, level, t, prev_t):
        net, nt = self._net, self._net_t
        if net[PIN_CHARGE] and not net[PIN_NDIS]:
            self.violate("I1_charge_while_dumping", "CHARGE high while ~DISCHARGE low")
        if n == PIN_CHARGE and level and t - nt[PIN_NDIS] < self.deadtime_ns:
            self.violate("I2_deadtime", "CHARGE rose %.1f ms after the dump turned off" % ((t - nt[PIN_NDIS]) / 1e6))
        if n == PIN_NDIS and not level and t - nt[PIN_CHARGE] < self.deadtime_ns and nt[PIN_CHARGE] > 0:
            self.violate("I2_deadtime", "dump on %.1f ms after CHARGE fell" % ((t - nt[PIN_CHARGE]) / 1e6))
        if net[PIN_KICK] and net[PIN_CHARGE]:
            self.violate("I3_kick_while_charging", "KICK high while CHARGE high")
        if n == PIN_KICK and not level and t - prev_t > self.kick_max_ns:
            self.violate("I4_kick_too_long", "KICK pulse %.0f us" % ((t - prev_t) / 1e3))
        if n == PIN_KICK and level and self.mode_probe is not None and not self.allow_kick_in_idle:
            name, t_entered = self.mode_probe()
            # grace: the abort lands a few tens of us after the decision to go Idle (measured separately)
            if name in ("Idle", "Fault") and t - t_entered > self.idle_abort_grace_ns:
                self.violate("I6_kick_in_idle", "KICK rose %.0f us after entering %s" % ((t - t_entered) / 1e3, name))
        if n == PIN_CHIP and level:
            self.violate("I12_chip_fired", "CHIP gate driven high")

    # ================================================================ interrupts
    def _input_changed(self, n, level):
        g = self.gpio.get(n)
        if g is None:
            return
        old = g.last_in
        g.last_in = level
        if old is None or old == level or g.irq_handler is None:
            return
        rising = level == 1
        if (rising and g.irq_trigger & 2) or ((not rising) and g.irq_trigger & 1):
            self.pending_irqs.append(n)

    def poll_irqs(self):
        if not self.irq_enabled or self.in_irq or not self.pending_irqs:
            return
        pending, self.pending_irqs = self.pending_irqs, []
        for n in pending:
            g = self.gpio[n]
            self.in_irq = True
            try:
                self.advance(COST["irq_entry"])
                g.irq_handler(_PinRef(n))
            finally:
                self.in_irq = False


class _PinRef:
    """What the IRQ handler receives as `pin` (only .value() is used)."""

    def __init__(self, n):
        self.n = n

    def value(self, *a):
        return current().level(self.n)


class _Mem32:
    def __init__(self, world):
        self.w = world

    def __getitem__(self, addr):
        w = self.w
        w.cost("mem32")
        off = addr - pio_mod.PIO0_BASE
        if 0 <= off < 0x200:
            for sm_id, sm in w.sms.items():
                base = sm_id * pio_mod.SM_STRIDE
                reg = off - base
                if reg in (pio_mod.REG_ADDR, pio_mod.REG_EXECCTRL, pio_mod.REG_PINCTRL):
                    w.sync_pio()
                    return sm.reg_read(reg)
        return 0
