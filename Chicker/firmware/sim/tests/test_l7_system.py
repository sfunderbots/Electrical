"""Level 7: whole-system behaviour - status / events to the Pi, USB console, clock wrap-around, crashes, watchdog,
hardware self-tests."""

from sim.harness import Sim
from sim import world as W
import protocol as P


def _charged(s):
    s.ready()
    s.charge()
    s.run_until(lambda: s.charge_state == "AssumingCharged", 9000, "charged")


# ------------------------------------------------------------------ status / events to the Pi
def test_status_frames_20hz_with_hv_and_flags():
    s = Sim()
    _charged(s)
    t0 = s.w.now / 1e6
    s.run(1000)
    st = [x for t, x in s.statuses() if t > t0]
    assert 19 <= len(st) <= 30, len(st)
    last = st[-1]
    assert abs(last["hv"] - s.hv) < 1.5
    f = last["flags"]
    assert f & P.FL_KICK_READY and f & P.FL_DAMP_READY and f & P.FL_HEARTBEAT_OK and not f & P.FL_DISCHARGING
    s.place_ball(0.01)
    s.run(60)
    assert s.statuses()[-1][1]["flags"] & P.FL_BEAM
    seqs = [x["seq"] for _, x in s.statuses()]
    assert all((b - a) & 0xFF == 1 for a, b in zip(seqs, seqs[1:]))


def test_heartbeat_late_flag_is_an_early_warning_only():
    # note HB-1: after ~1 s without a frame the STATUS flag clears; nothing else changes until 5 s
    s = Sim()
    _charged(s)
    s.kick(1000)
    s.run(300)
    s.heartbeat_ms = 0
    s.run(1500)
    assert not s.statuses()[-1][1]["flags"] & P.FL_HEARTBEAT_OK
    assert s.mode == "WaitForKick" and s.app.mode.armed


def test_heartbeat_gaps_are_counted_not_acted_on():
    s = Sim().ready()
    s.heartbeat_ms = 0
    for seq in (10, 11, 15, 15, 16):
        s.send(s.cfg.CAN_ID_HEARTBEAT, P.encode_heartbeat(seq))
        s.run(100)
    assert s.app.hb_gaps >= 3 and s.app.hb_repeats >= 1 and s.mode == "WaitForMessage"


def test_kick_event_reports_width_and_hv():
    s = Sim()
    _charged(s)
    s.kick(1000)
    s.place_ball(0.0)
    s.run(300)
    ev = [e for _, e in s.events() if e["code"] == P.EV_KICK_FIRED][-1]
    assert ev["a"] == 1000 and ev["b"] / 10 > 200


# ------------------------------------------------------------------ USB console
def test_usb_console_drives_the_same_decoder():
    s = Sim()
    s.heartbeat_ms = 0
    s.run(1500)
    s.usb("hb")                                          # the bench sends heartbeats over USB too
    s.run(50)
    s.usb("charge")
    s.run(50)
    s.usb("hb")
    s.run_until(lambda: s.charge_state == "ChargeStarted", 4000, "charging via usb")
    s.usb("can 2A0 00")                                  # raw frame: MODE IDLE
    s.run(50)
    assert s.app.mode.mode == P.MODE_IDLE and s.app.mode.chg_stop_mode_ctrl == 1
    s.usb("bogus words")
    s.usb("kick notanumber")
    s.usb("ceil 100")
    s.run(50)
    assert any("unknown command" in l for l in s.usb_lines("C"))
    assert any("error" in l for l in s.usb_lines("C"))
    assert any("deferred" in l for l in s.usb_lines("L"))
    s.assert_safe()


def test_dry_fire_gating():
    s = Sim()
    s.w.allow_kick_in_idle = True
    s.ready()
    s.usb("dry kick 500")
    s.run(20)
    assert [w for _, w in s.kick_pulses()] == [500.0]
    s.w.allow_kick_in_idle = False
    s.charge()
    s.run_until(lambda: s.hv > 50, 5000, "charged a bit")
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


def test_tick_wraparound_during_charge_damp_and_kick():
    s = Sim(start_ticks_ms=_wrap_offset())
    _charged(s)
    s.damp(30000, 6.0, 1500)
    s.run(2500)
    s.kick(800)
    s.place_ball(0.0)
    s.run_until(lambda: any(w == 800.0 for _, w in s.kick_pulses()), 6000, "kick after wrap")
    assert s.w.ticks_ms() < 100_000                      # we really did wrap
    s.assert_safe()


def test_exception_in_main_loop_lands_safe_via_main_py():
    s = Sim(boot=False)                                  # world first: main.py drives pins at import
    import main
    import charge_sm
    real = charge_sm.ChargeSM.step

    def boom(self, now_ms, *a):
        if self.state == charge_sm.CHARGE_STARTED and self.in_state_ms(now_ms) > 300:
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
            if calls["n"] % 400 == 0:                    # the Pi: heartbeats, and MODE_CHG once the board is up
                s.w.can.deliver(s.cfg.CAN_ID_HEARTBEAT, P.encode_heartbeat(calls["n"] & 0xFF))
                s.w.can.deliver(s.cfg.CAN_ID_MODE, P.encode_mode(P.MODE_CHG))
            if s.w.now > 20_000 * W.NS_PER_MS:
                raise Stop()
            return orig_ticks()
        utime.ticks_ms = ticks_ms
        print("(expected) this test crashes the firmware on purpose mid-charge; the traceback below is main.py "
              "catching it and leaving the board safe", flush=True)
        main.run(s.cfg)
    finally:
        charge_sm.ChargeSM.step = real
        utime.ticks_ms = orig_ticks
    assert s.w.level(W.PIN_CHARGE) == 0 and s.w.level(W.PIN_NDIS) == 0 and s.w.level(W.PIN_KICK) == 0
    assert any(n == W.PIN_CHARGE and lv for _, n, lv in s.w.pin_events)     # it was charging when it broke
    assert not s.violations                              # including the dead time on the emergency path


def test_watchdog_starts_late_is_fed_and_catches_a_hang():
    s = Sim()
    s.run(2000)
    assert s.app.wdt is None                             # boot window: Thonny can still stop main.py
    s.run(2000)
    assert s.app.wdt is not None
    s.run(5000)
    assert s.w.wdt_resets == 0
    s.w.advance(1500 * W.NS_PER_MS)                      # loop hung
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
    s.heartbeat_ms = 0
    s.run(1500)
    s.usb("hb"); s.run(50)
    s.usb("charge")
    for _ in range(4):
        s.usb("hb"); s.run(1000)
    assert s.charge_state in ("ChargeStarted", "AssumingCharged")
    s.assert_safe()


def test_hardware_filter_and_loopback_selftest():
    s = Sim()
    assert s.board.can.ok and s.board.can.selftest_msg == "ok"
    assert not s.w.can.deliver(0x123, b"\x01")           # motor-controller traffic never reaches the CPU
    assert s.w.can.deliver(s.cfg.CAN_ID_MODE, b"\x01")


def test_kicker_selftest_failure_is_reported_and_no_pulse_ever_fires():
    # Not on the charts: a kicker that fails its boot self-test refuses every pulse; the Pi is told.
    import kicker
    orig = kicker.Kicker._selftest
    kicker.Kicker._selftest = lambda self: (False, "injected")
    try:
        s = Sim()
    finally:
        kicker.Kicker._selftest = orig
    ev = [e for _, e in s.events() if e["code"] == P.EV_FAULT]
    assert ev and ev[0]["a"] == P.F_KICKER_SELFTEST
    _charged(s)
    s.kick(1000)
    s.place_ball(0.0)
    s.run(500)
    assert not s.kick_pulses()
    s.assert_safe()
