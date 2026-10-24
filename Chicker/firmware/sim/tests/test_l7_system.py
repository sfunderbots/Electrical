"""Level 7: whole-system behaviour - heartbeat, status/events, USB console, wraparound, crashes, watchdog."""

from sim.harness import Sim
from sim import world as W
import protocol as P


def _full(s):
    s.charge()
    s.run_until(lambda: s.charge_state == "Full", 6000, "full")


# ------------------------------------------------------------------ heartbeat / comms
def test_heartbeat_loss_goes_idle_and_reports():
    s = Sim()
    s.heartbeat_ms = 50
    _full(s)
    s.kick(1000)
    s.run(100)
    assert s.app.mode.heartbeat_ok(s.app.mode._t_rx)
    s.heartbeat_ms = 0                                 # Pi dies
    t = s.run_until(lambda: s.mode == "Idle", 1000, "comms-lost idle")
    assert t <= s.cfg.HEARTBEAT_TIMEOUT_MS + 60
    assert P.EV_COMMS_LOST in s.event_codes()
    s.run(3000)
    assert s.hv < 60                                   # bank dumping
    s.assert_safe()


def test_no_heartbeat_ever_means_not_enforced_in_default_mode():
    s = Sim()
    _full(s)
    s.run(2000)
    assert s.mode == "Ready"


def test_strict_heartbeat_mode():
    s = Sim(HEARTBEAT_MODE=2)
    s.charge()
    s.run(600)
    assert s.mode == "Idle" and P.EV_COMMS_LOST in s.event_codes()


# ------------------------------------------------------------------ status / events to the Pi
def test_status_frames_20hz_with_hv_and_flags():
    s = Sim()
    _full(s)
    t0 = s.w.now / 1e6
    s.run(1000)
    st = [x for t, x in s.statuses() if t > t0]
    assert 19 <= len(st) <= 30, len(st)
    last = st[-1]
    assert abs(last["hv"] - s.hv) < 1.5
    assert last["flags"] & P.FL_KICK_READY and not last["flags"] & P.FL_DISCHARGING
    s.place_ball(0.01)
    s.run(60)
    assert s.statuses()[-1][1]["flags"] & P.FL_BEAM
    seqs = [x["seq"] for _, x in s.statuses()]
    assert all((b - a) & 0xFF == 1 for a, b in zip(seqs, seqs[1:]))


def test_kick_event_reports_energy():
    s = Sim()
    _full(s)
    s.kick(1000, now=True)
    s.run(300)
    ev = [e for _, e in s.events() if e["code"] == P.EV_KICK_FIRED][-1]
    assert ev["a"] == 1000 and ev["b"] / 10 > 205 and 5 < (ev["b"] - ev["c"]) / 10 < 40


# ------------------------------------------------------------------ USB console
def test_usb_console_drives_the_same_decoder():
    s = Sim()
    s.usb("charge")
    s.run_until(lambda: s.charge_state == "Full", 6000, "full via usb")
    s.usb("kick 900 now")
    s.run(100)
    assert [w for _, w in s.kick_pulses()] == [900.0]
    s.usb("can 2A0 00")                                # raw frame: MODE IDLE
    s.run(50)
    assert s.mode == "Idle"
    s.usb("bogus words")
    s.usb("kick notanumber")
    s.run(50)
    assert any("unknown command" in l for l in s.usb_lines("C"))
    assert any("error" in l for l in s.usb_lines("C"))
    s.assert_safe()


def test_dry_fire_gating():
    s = Sim()
    s.w.allow_kick_in_idle = True
    s.run(200)
    s.usb("dry kick 500")
    s.run(20)
    assert [w for _, w in s.kick_pulses()] == [500.0]
    s.w.allow_kick_in_idle = False
    _full(s)
    s.usb("dry kick 500")
    s.run(20)
    assert len(s.kick_pulses()) == 1 and any("refused" in l for l in s.usb_lines("L"))


# ------------------------------------------------------------------ robustness
def _wrap_offset():
    # start so that ticks_ms wraps ~4.5 s after boot and ticks_us wraps ~5 s after boot
    for m in range((1 << 30) - 6000, (1 << 30) - 3000):
        if (1 << 30) - 6_000_000 < (m * 1000) % (1 << 30) < (1 << 30) - 4_000_000:
            return m
    raise RuntimeError


def test_tick_wraparound_during_charge_and_damp():
    s = Sim(start_ticks_ms=_wrap_offset())
    _full(s)
    s.damp(30000, 6.0, hold_ms=1500)
    s.run(2500)
    s.kick(800, now=True)
    s.run_until(lambda: any(w == 800.0 for _, w in s.kick_pulses()), 4000, "kick after wrap")
    assert s.w.ticks_ms() < 100_000                    # we really did wrap
    s.assert_safe()


def test_exception_in_main_loop_lands_safe_via_main_py():
    s = Sim(boot=False)                                # world first: main.py drives pins at import
    import main
    import charge_sm
    real = charge_sm.ChargeSM.step

    def boom(self, now_ms, *a):
        if self.state == charge_sm.CHARGING and self.elapsed_ms(now_ms) > 300:
            raise RuntimeError("injected fault mid-charge")
        return real(self, now_ms, *a)
    charge_sm.ChargeSM.step = boom
    try:
        import utime

        class Stop(BaseException):
            pass
        orig_ticks = utime.ticks_ms
        calls = {"n": 0}

        def ticks_ms():
            calls["n"] += 1
            if calls["n"] == 2000:
                s.w.can.deliver(s.cfg.CAN_ID_MODE, P.encode_mode(P.MODE_CHARGE))
            if s.w.now > 20_000 * W.NS_PER_MS:
                raise Stop()
            return orig_ticks()
        utime.ticks_ms = ticks_ms
        main.run(s.cfg)
    finally:
        charge_sm.ChargeSM.step = real
        utime.ticks_ms = orig_ticks
    assert s.w.level(W.PIN_CHARGE) == 0 and s.w.level(W.PIN_NDIS) == 0 and s.w.level(W.PIN_KICK) == 0
    assert any(n == W.PIN_CHARGE and lv for _, n, lv in s.w.pin_events)     # it was charging when it broke
    assert not s.violations                            # including the dead time on the emergency path


def test_watchdog_starts_late_is_fed_and_catches_a_hang():
    s = Sim()
    s.run(2000)
    assert s.app.wdt is None                           # boot window: Thonny can still stop main.py
    s.run(2000)
    assert s.app.wdt is not None
    s.run(5000)
    assert s.w.wdt_resets == 0
    s.w.advance(1500 * W.NS_PER_MS)                     # loop hung
    assert s.w.wdt_resets == 1


def test_can_init_failure_still_usable_over_usb():
    from sim import shims
    orig = shims.FakeCan.begin
    shims.FakeCan.begin = lambda self, *a, **k: 1
    try:
        s = Sim()
    finally:
        shims.FakeCan.begin = orig
    assert not s.board.can.ok
    assert any("CAN" in l for l in s.usb_lines("L"))
    s.usb("charge")
    s.run_until(lambda: s.charge_state == "Full", 6000, "full over usb")
    s.assert_safe()


def test_hardware_filter_and_loopback_selftest():
    s = Sim()
    assert s.board.can.ok and s.board.can.selftest_msg == "ok"
    assert not s.w.can.deliver(0x123, b"\x01")         # motor-controller traffic never reaches the CPU
    assert s.w.can.deliver(s.cfg.CAN_ID_MODE, b"\x01")


def test_kicker_selftest_failure_refuses_to_arm():
    import kicker
    orig = kicker.Kicker._selftest
    kicker.Kicker._selftest = lambda self: (False, "injected")
    try:
        s = Sim()
    finally:
        kicker.Kicker._selftest = orig
    assert s.mode == "Fault"
    s.charge()
    s.run(3000)
    assert s.mode == "Fault" and s.hv < 1


# ------------------------------------------------------------------ rare paths (transition coverage)
def test_overvoltage_locks_and_dumps():
    # LT3750 not regulating AND the software backstop disabled: only the 225 V overvoltage rule is left
    s = Sim(CHARGE_BACKSTOP_V=999.0)
    s.lt_fault("no_regulation")
    _full_or_fault = lambda: s.charge_state == "Locked"
    s.charge()
    s.run_until(_full_or_fault, 8000, "overvolt lock")
    s.run(200)
    assert s.app.charge.last_fault == P.F_OVERVOLT and s.mode == "Fault" and s.app.power.discharging
    assert s.w.phys.max_v < s.cfg.OVERVOLT_V + 3            # caught within one loop pass of crossing
    assert all(v.rule == "I9_overvolt" for v in s.violations)   # the monitor flags >225 V on purpose


def test_ceiling_already_reached_at_cycle_start():
    s = Sim()
    _full(s)
    s.mode_idle()
    s.run_until(lambda: s.hv < 170, 3000, "dumped to 170 V")
    s.usb("ceil 100")
    s.charge()
    s.run_until(lambda: s.charge_state == "Full", 2000, "full via ceiling")
    assert s.app.charge.last_why == "ceiling"


def test_each_charge_fault_can_lock_directly():
    for mode, why, phys in (("early_done", "early_done", {}),
                            ("ok", "no_progress", {"lt_k": 0.01}),
                            ("done_stuck_high", "timeout", {})):
        s = Sim(MAX_CONSECUTIVE_CHARGE_FAULTS=1, physics=phys)
        s.lt_fault(mode)
        s.charge()
        s.run_until(lambda: s.charge_state == "Locked", 9000, "lock on " + why)
        assert s.app.charge.last_why == why, (mode, s.app.charge.last_why)
        s.run(50)
        assert s.mode == "Fault"
        s.assert_safe()


def test_fault_retry_while_charging_not_allowed():
    s = Sim(CHARGE_RETRY_MS=1500, physics=dict(R_bleed=20e3))
    _full(s)
    s.lt_fault("uvlo")
    s.run_until(lambda: s.charge_state == "FaultWait", 6000, "fault on top-off")
    s.usb("ceil 200")
    s.damp(30000, 1.0, hold_ms=3000)
    s.run_until(lambda: s.mode == "DampHold", 2000, "hold")
    s.run_until(lambda: s.app.charge.last_why == "retry_inhibited", 3000, "retry while inhibited")
    s.assert_safe()


def test_idle_clears_fault_wait():
    s = Sim(CHARGE_RETRY_MS=60000)
    s.lt_fault("uvlo")
    s.charge()
    s.run_until(lambda: s.charge_state == "FaultWait", 6000, "fault wait")
    s.mode_idle()
    s.run(20)
    assert s.charge_state == "Inhibited" and s.app.charge.last_why == "cleared"


def test_kicker_abort_readback_failure_faults():
    s = Sim()
    _full(s)
    s.board.kicker.abort = lambda: False                # pin does not read LOW after an abort
    s.mode_idle()
    s.run(20)
    assert s.mode == "Fault" and s.app.mode.fault == P.F_KICKER_READBACK


def test_damp_gives_up_when_hv_never_ready():
    s = Sim(DAMP_READY_TIMEOUT_MS=1000)
    s.lt_fault("uvlo")
    s.charge()
    s.run(400)
    s.damp(30000, 8.0)
    s.run_until(lambda: s.app.mode.last_why == "hv_timeout", 3000, "damp timeout")
    assert P.EV_DAMP_ABORTED in s.event_codes() and not s.kick_pulses()


def test_kick_prep_expires_when_min_fire_voltage_not_met():
    s = Sim(KICK_MIN_FIRE_V=100.0)
    s.lt_fault("uvlo")
    s.charge()
    s.run(400)
    s.kick(1000, now=True, arm_ms=300)
    s.run_until(lambda: s.app.mode.last_why == "expired", 1000, "prep expiry")
    assert s.mode == "Ready" and not s.kick_pulses()
