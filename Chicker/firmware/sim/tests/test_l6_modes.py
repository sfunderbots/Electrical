"""Level 6: mode state machine on the full simulated board (commands sent as real CAN frames)."""

from sim.harness import Sim
from sim import world as W
import protocol as P


def _ready(**kw):
    s = Sim(**kw)
    s.charge()
    s.run_until(lambda: s.charge_state == "Full", 6000, "full")
    return s


def _pulses_after(s, t_ns):
    return [(t, w) for t, w in s.kick_pulses() if t * 1e6 >= t_ns]


# ------------------------------------------------------------------ kicks
def test_kick_on_beam_fast_irq_path():
    s = _ready()
    s.kick(1000)
    s.run(20)
    assert s.mode == "KickArmed"
    t0 = s.w.now
    s.place_ball(0.0)
    s.run(200)
    p = _pulses_after(s, t0)
    assert [w for _, w in p] == [1000.0]
    latency_us = p[0][0] * 1000 - t0 / 1000
    assert latency_us < 100, latency_us              # fired from the IRQ, not the next loop pass
    assert P.EV_KICK_FIRED in s.event_codes()
    assert s.w.phys.ball_max_v > 0.3
    s.assert_safe()


def test_kick_on_beam_main_loop_path():
    s = _ready(BEAM_FAST_FIRE=False)
    s.kick(800)
    s.run(20)
    t0 = s.w.now
    s.place_ball(0.0)
    s.run(200)
    p = _pulses_after(s, t0)
    assert [w for _, w in p] == [800.0]
    assert p[0][0] * 1000 - t0 / 1000 < 5000          # within a few loop passes + 5 ms settle
    s.assert_safe()


def test_kick_now_and_ball_already_present():
    s = _ready()
    s.kick(600, now=True)
    s.run(100)
    assert [w for _, w in s.kick_pulses()] == [600.0]
    s.run_until(lambda: s.charge_state == "Full", 3000, "recharge")
    s.place_ball(0.0)
    s.run(20)
    s.kick(700)                                       # trigger=beam but the ball is already there
    s.run(100)
    assert [w for _, w in s.kick_pulses()][-1] == 700.0
    s.assert_safe()


def test_ball_arrives_during_top_off_charging_stops_then_kick_fires():
    # leaky bank tops off every ~2 s while armed; drop the ball in mid top-off cycle
    s = _ready(physics=dict(R_bleed=20e3))
    s.kick(1000, arm_ms=8000)
    s.run_until(lambda: s.charge_state == "Charging", 6000, "top-off while armed")
    s.run(20)
    t0 = s.w.now
    s.place_ball(0.0)
    s.run(60)
    p = _pulses_after(s, t0)
    assert [w for _, w in p] == [1000.0]
    falls = [t for t, n, lv in s.w.pin_events if n == W.PIN_CHARGE and not lv and t >= t0]
    assert falls and p[0][0] * 1e6 - falls[0] >= 4 * W.NS_PER_MS     # charger stopped first, then settle
    assert p[0][0] * 1000 - t0 / 1000 < 10_000                         # still fires within ~10 ms
    s.assert_safe()                                                    # I3 / I3b: never overlapping


def test_arm_expires():
    s = _ready()
    s.kick(1000, arm_ms=300)
    s.run(400)
    assert s.mode == "Ready" and P.EV_ARM_EXPIRED in s.event_codes() and not s.kick_pulses()


def test_kick_while_still_charging_stops_charger_and_fires_at_current_voltage():
    s = Sim()
    s.charge()
    s.run_until(lambda: s.charge_state == "Charging", 3000, "charging")
    s.run(300)                                        # bank well below kick-ready, still charging
    t0 = s.w.now
    s.kick(1000, now=True)
    s.run(30)
    p = _pulses_after(s, t0)
    assert [w for _, w in p] == [1000.0]              # a kick goes through at any time...
    assert s.w.phys.max_v < s.cfg.KICK_READY_V        # ...without waiting for 180 V
    falls = [t for t, n, lv in s.w.pin_events if n == W.PIN_CHARGE and not lv and t >= t0]
    assert falls and p[0][0] * 1e6 - falls[0] >= 4 * W.NS_PER_MS     # charging disabled first
    s.run_until(lambda: s.charge_state == "Full", 4000, "recharge after")
    s.assert_safe()


def test_kick_and_damp_rejected_in_idle():
    s = Sim()
    s.kick(1000, now=True)
    s.damp(30000, 12)
    s.run(100)
    rej = [e for _, e in s.events() if e["code"] == P.EV_CMD_REJECTED]
    assert len(rej) == 2 and all(e["b"] == P.REJ_NOT_CHARGING for e in rej)
    assert not s.kick_pulses()


def test_out_of_range_commands_rejected():
    s = _ready()
    s.kick(20000, now=True)
    s.damp(100, 12)                                   # 100 Hz: below DAMP_FREQ_MIN
    s.damp(30000, 99)                                 # duty above DAMP_DUTY_MAX
    s.run(100)
    rej = [e for _, e in s.events() if e["code"] == P.EV_CMD_REJECTED]
    assert len(rej) == 3 and all(e["b"] == P.REJ_OUT_OF_RANGE for e in rej)
    assert not s.kick_pulses() and s.mode == "Ready"


# ------------------------------------------------------------------ damping
def test_damp_sequence_initial_kick_then_pwm_hold():
    s = _ready()
    t0 = s.w.now
    s.damp(30000, 12.0, hold_ms=300, init_kick_us=800)
    s.run_until(lambda: s.mode == "Ready" and s.app.mode.last_why in ("hold_done", "hv_floor"), 3000, "damp done")
    p = _pulses_after(s, t0)
    assert p[0][1] == 800.0
    assert (p[1][0] - p[0][0]) * 1000 >= s.cfg.DAMP_SETTLE_US - 1
    assert all(w <= s.cfg.DAMP_MAX_ON_US for _, w in p[1:])
    assert P.EV_DAMP_DONE in s.event_codes()
    s.assert_safe()


def test_kick_right_after_damp():
    s = _ready()
    s.damp(30000, 6.0, hold_ms=2000)
    s.run_until(lambda: s.mode == "DampHold", 2000, "hold")
    s.run(80)
    t_cmd = s.w.now
    s.kick(1200, now=True)
    s.run(40)
    hold_pulses = [x for x in _pulses_after(s, t_cmd) if x[1] < 300]
    assert all(t * 1e6 < t_cmd + 10 * W.NS_PER_MS for t, _ in hold_pulses)   # PWM stopped promptly
    s.run_until(lambda: any(w == 1200.0 for _, w in s.kick_pulses()), 4000, "kick after damp")
    s.assert_safe()


def test_damp_received_during_kick_cooldown_runs_after():
    s = _ready()
    s.kick(500, now=True)
    s.run(30)
    assert s.mode == "KickCooldown"
    s.damp(30000, 8.0, hold_ms=200)
    s.run_until(lambda: s.mode == "DampHold", 4000, "pending damp")
    s.assert_safe()


def test_repeated_legacy_damp_frames_do_not_restart_the_damp():
    s = _ready()
    t0 = s.w.now
    for _ in range(20):
        s.legacy(2, 30000, 8)
        s.run(15)
    starts = [w for _, w in _pulses_after(s, t0) if w == s.cfg.DAMP_INITIAL_KICK_US]
    assert len(starts) == 1, starts
    s.assert_safe()


def test_idle_mid_hold_stops_pwm_and_dumps():
    s = _ready()
    s.damp(30000, 8.0, hold_ms=3000)
    s.run_until(lambda: s.mode == "DampHold", 3000, "hold")
    s.run(50)
    t_cmd = s.w.now
    s.mode_idle()
    s.run(5)
    assert s.w.level(W.PIN_KICK) == 0
    last = [t for t, n, lv in s.w.pin_events if n == W.PIN_KICK and not lv][-1]
    assert last - t_cmd < 2 * W.NS_PER_MS, (last - t_cmd) / 1e3        # frame on the bus -> pin low
    s.run(100)
    assert not [1 for t, n, lv in s.w.pin_events if n == W.PIN_KICK and lv and t > last]
    assert s.app.power.discharging
    s.assert_safe()


def test_charge_command_cancels_arm_and_damp():
    s = _ready()
    s.kick(1000)
    s.run(20)
    s.charge()
    s.run(20)
    assert s.mode == "Ready"
    s.damp(30000, 8.0, hold_ms=3000)
    s.run_until(lambda: s.mode == "DampHold", 3000, "hold")
    s.charge()
    s.run(10)
    assert s.mode == "Ready" and not s.board.kicker.pwm_running
    s.assert_safe()


def test_idle_aborts_kick_in_flight():
    s = _ready()
    s.kick(5000, now=True)
    s.run_until(lambda: s.w.level(W.PIN_KICK) == 1, 200, "kick high")
    s.mode_idle()
    s.run(10)
    assert s.kick_pulses()[-1][1] < 5000
    s.assert_safe()


# ------------------------------------------------------------------ every command in every state
_COMMANDS = {
    "idle": lambda s: s.mode_idle(),
    "charge": lambda s: s.charge(),
    "kick_beam": lambda s: s.kick(900),
    "kick_now": lambda s: s.kick(900, now=True),
    "damp": lambda s: s.damp(30000, 8.0, hold_ms=150),
    "heartbeat": lambda s: s.heartbeat(),
    "legacy_kick": lambda s: s.legacy(1, 900, 0),
    "legacy_damp": lambda s: s.legacy(2, 30000, 8),
    "garbage": lambda s: s.send(s.cfg.CAN_ID_MODE, b"\x09"),
}


def _reach(state):
    s = Sim(CHARGE_RETRY_MS=500)
    if state == "Idle":
        return s
    if state == "Fault":
        s.lt_fault("uvlo")
        s.charge()
        s.run_until(lambda: s.mode == "Fault", 6000, "fault")
        return s
    s.charge()
    if state == "Ready":
        s.run(20)
        return s
    if state == "KickPrep":                           # armed while still charging
        s.run_until(lambda: s.charge_state == "Charging", 3000, "charging")
        s.kick(900, now=True)
        s.run_until(lambda: s.mode == "KickPrep", 500, state)
        return s
    if state == "DampWaitHV":                         # damp requested while the bank is still low
        s.run_until(lambda: s.charge_state == "Charging", 3000, "charging")
        s.damp(30000, 8.0, hold_ms=400)
        s.run_until(lambda: s.mode == state, 500, state)
        return s
    s.run_until(lambda: s.charge_state == "Full", 6000, "full")
    if state == "KickArmed":
        s.kick(900)
    elif state == "KickCooldown":
        s.kick(900, now=True)
    else:
        s.damp(30000, 8.0, hold_ms=400)
    s.run_until(lambda: s.mode == state, 3000, state)
    return s


def test_every_command_in_every_state():
    states = ("Idle", "Fault", "Ready", "KickArmed", "KickPrep", "KickCooldown",
              "DampWaitHV", "DampPrep", "DampExtend", "DampHold")
    for st in states:
        for name, send in _COMMANDS.items():
            s = _reach(st)
            assert s.mode == st or st in ("DampWaitHV", "DampPrep", "DampExtend"), (st, s.mode)
            send(s)
            s.run(400)
            if st == "Fault" and name != "idle":
                assert s.mode == "Fault", (st, name, s.mode)
            assert s.crashed is None, (st, name, s.crashed)
            assert not s.violations, (st, name, s.violations[:2])
