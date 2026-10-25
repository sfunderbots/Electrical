"""Test harness: boots the real firmware (board.py + app.py) on the simulated board.

    from sim.harness import Sim
    s = Sim()                      # boots; board is Idle
    s.charge(); s.run(3000)        # MODE CHARGE over (fake) CAN, run 3 s of virtual time
    s.kick(1000, now=True); s.run(200)
    assert not s.violations

Commands go in as real CAN frames through the fake MCP2515 (or as USB console lines), exactly the
bytes the robot / bench would send. Everything the board sends back (STATUS / EVENT frames, USB
telemetry) is captured.
"""

import os
import types
from . import world as W
from . import shims

FW_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# transition coverage collected across every Sim created in this process (see run_tests.py)
ALL_COVERAGE = {}


def make_cfg(**overrides):
    shims.install(FW_DIR)
    import config
    ns = types.SimpleNamespace(**{k: getattr(config, k) for k in dir(config) if not k.startswith("_")})
    for k, v in overrides.items():
        if not hasattr(ns, k):
            raise KeyError("unknown config constant %s" % k)
        setattr(ns, k, v)
    return ns


class Sim:
    def __init__(self, physics=None, seed=0, start_ticks_ms=0, boot=True, **cfg_overrides):
        shims.install(FW_DIR)
        self.cfg = make_cfg(**cfg_overrides)
        c = self.cfg
        self.w = W.World(seed=seed, start_ticks_ms=start_ticks_ms, physics=physics,
                         deadtime_ms=c.CHG_DISCHARGE_DEADTIME_MS, kick_max_us=c.KICK_MAX_US,
                         charge_timeout_ms=c.TOPOFF_PERIOD_MS, overvolt_v=c.OVERVOLT_V + 10)  # I9: the bank must never pass the 220 V trip by more than 10 V (caps: 250 V)  # CHARGE is held high for a whole cycle
        W.activate(self.w)
        self.coverage = {}
        self.transitions = []
        self.crashed = None
        self.app = None
        self.board = None
        self._hb_seq = 0
        self.heartbeat_ms = 200        # robot sends heartbeats at this period (5 Hz like the Pi); 0 = stop
        self._t_hb = 0
        if boot:
            self.boot()

    # ------------------------------------------------------------------ lifecycle
    def boot(self):
        W.activate(self.w)
        import board as B
        import app as A
        self.board = B.Board(self.cfg)
        self.app = A.App(self.board, self.cfg)
        self.app.charge.on_transition = self._on_transition
        self.app.mode.on_transition = self._on_transition
        self._t_mode = self.w.now
        self.w.mode_probe = self._mode_probe
        return self

    def _mode_probe(self):
        # A bench dry fire pulses the kicker in Idle on purpose (bank empty): report it as such.
        app = self.app
        if app.dry_fire_until is not None and                 (app.dry_fire_until - self.w.ticks_ms()) % W.TICKS_PERIOD < W.TICKS_PERIOD // 2:
            return ("DryFire", self._t_mode)
        # The invariant monitor only needs to know "may the kicker fire now?": map the chart states onto
        # the two names it checks (I6: no kick while Idle / Locked).
        if app.mode.state_name() == "Locked":
            return ("Fault", self._t_mode)
        if app.mode.idle_like():
            return ("Idle", self._t_mode)
        return (app.mode.state_name(), self._t_mode)

    def _on_transition(self, sm, frm, to, why):
        name = type(sm).__name__
        if name == "ModeSM":
            self._t_mode = self.w.now
        key = (name, frm, to, why)
        self.coverage[key] = self.coverage.get(key, 0) + 1
        ALL_COVERAGE[key] = ALL_COVERAGE.get(key, 0) + 1
        self.transitions.append((self.w.now / 1e6, name, sm.names[frm], sm.names[to], why))

    def step(self):
        """One main-loop pass. Exceptions take the main.py path: outputs safe, loop stops."""
        if self.crashed is not None:
            self.w.advance(1_000_000)
            return
        W.activate(self.w)
        if self.heartbeat_ms and (self.w.now - self._t_hb) >= self.heartbeat_ms * W.NS_PER_MS:
            self._t_hb = self.w.now
            self.heartbeat()
        try:
            self.app.step()
        except Exception as e:     # noqa - mirror main.run()
            self.crashed = e
            self.board.safe_shutdown(self.cfg.CHG_DISCHARGE_DEADTIME_MS)
        self.w.cost("loop_overhead")

    def run(self, ms):
        end = self.w.now + int(ms * W.NS_PER_MS)
        while self.w.now < end:
            self.step()
        return self

    def run_until(self, pred, timeout_ms, what="condition"):
        t0 = self.w.now
        end = t0 + int(timeout_ms * W.NS_PER_MS)
        while self.w.now < end:
            self.step()
            if pred():
                return (self.w.now - t0) / 1e6
        raise TimeoutError("%s not reached within %d ms (mode=%s chg=%s hv=%.1f)" % (
            what, timeout_ms, self.mode, self.charge_state, self.hv))

    def ready(self, timeout_ms=5000):
        """Run until the board accepts commands (battery seen, first heartbeat, Wait for Message)."""
        self.run_until(lambda: self.mode == "WaitForMessage", timeout_ms, "board ready")
        return self

    # ------------------------------------------------------------------ robot side (CAN)
    def send(self, can_id, data):
        W.activate(self.w)
        return self.w.can.deliver(can_id, bytes(data))

    def mode_idle(self):
        import protocol as P
        return self.send(self.cfg.CAN_ID_MODE, P.encode_mode(P.MODE_IDLE))

    def charge(self):
        import protocol as P
        return self.send(self.cfg.CAN_ID_MODE, P.encode_mode(P.MODE_CHARGE))

    def kick(self, width_us, now=False, arm_ms=0):
        """MODE_AUTOKICK with this pulse width (now=True sends the old "kick now" trigger, which is rejected)."""
        import protocol as P
        return self.send(self.cfg.CAN_ID_KICK,
                         P.encode_kick(width_us, P.TRIGGER_NOW if now else P.TRIGGER_BEAM, arm_ms))

    def damp(self, freq_hz, duty_pct, hold_ms=0, init_kick_us=0):
        import protocol as P
        return self.send(self.cfg.CAN_ID_DAMP, P.encode_damp(freq_hz, duty_pct, hold_ms, init_kick_us))

    def heartbeat(self):
        import protocol as P
        self._hb_seq = (self._hb_seq + 1) & 0xFF
        return self.send(self.cfg.CAN_ID_HEARTBEAT, P.encode_heartbeat(self._hb_seq))

    def legacy(self, mode, value, duty):
        import protocol as P
        return self.send(self.cfg.CAN_ID_LEGACY, P.encode_legacy(mode, value, duty))

    def usb(self, line):
        self.w.usb_in.extend(line + "\n")

    # ------------------------------------------------------------------ world side
    def place_ball(self, pos=0.0, vel=0.0):
        self.w.phys.place_ball(pos, vel)

    def remove_ball(self):
        self.w.phys.ball = None

    def set_battery(self, volts=None, present=None):
        p = self.w.phys.p
        if volts is not None:
            p["batt_v"] = volts
        if present is not None:
            p["batt_present"] = present

    def lt_fault(self, mode):
        self.w.phys.p["lt_fault"] = mode

    # ------------------------------------------------------------------ inspection
    @property
    def mode(self):
        return self.app.mode.state_name()

    @property
    def charge_state(self):
        return self.app.charge.state_name()

    @property
    def hv(self):
        return self.w.phys.v

    @property
    def violations(self):
        return self.w.violations

    def tx_frames(self, can_id=None):
        return [(t / 1e6, cid, d) for t, cid, d in self.w.can.tx if can_id is None or cid == can_id]

    def events(self):
        import protocol as P
        return [(t, P.decode_event(d)) for t, _, d in self.tx_frames(self.cfg.CAN_ID_EVENT)]

    def event_codes(self):
        return [e["code"] for _, e in self.events()]

    def statuses(self):
        import protocol as P
        return [(t, P.decode_status(d)) for t, _, d in self.tx_frames(self.cfg.CAN_ID_STATUS)]

    def usb_lines(self, prefix=""):
        return [l for _, l in self.w.usb_out if l.startswith(prefix)]

    def kick_pulses(self):
        """[(t_start_ms, width_us)] of every KICK-net high period."""
        out, t_hi = [], None
        for t, n, lv in self.w.pin_events:
            if n != W.PIN_KICK:
                continue
            if lv:
                t_hi = t
            elif t_hi is not None:
                out.append((t_hi / 1e6, (t - t_hi) / 1e3))
                t_hi = None
        return out

    def assert_safe(self):
        if self.violations:
            raise AssertionError("safety violations: %r" % (self.violations[:5],))
        if self.crashed is not None:
            raise AssertionError("firmware crashed: %r" % (self.crashed,))
