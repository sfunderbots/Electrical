"""Level 6: the mode control state machine, driven by real CAN frames on the simulated board.

One test per agreed behaviour in docs/MODE-STATE-MACHINE-REVIEW.md ("element N" / note names in the comments).
"""

import random
from sim.harness import Sim
from sim import world as W
import protocol as P


def _charged(**kw):
    """Board ready, MODE_CHG sent, bank full."""
    s = Sim(**kw).ready()
    s.charge()
    s.run_until(lambda: s.charge_state == "AssumingCharged", 9000, "charged")
    return s


def _kicks(s, min_us=50):
    """Single kick pulses (damping PWM pulses are a few us and are left out)."""
    return [(t, w) for t, w in s.kick_pulses() if w >= min_us]


def _pwm_on(s):
    return s.app.b.kicker.pwm_running


def _set_bank(s, volts):
    """Test hook: set the simulated bank voltage. The simulator's solenoid model is not representative (the real
    inductance changes with plunger position and air gap) and drains the bank far too fast during a hold, so tests
    of the in-damp kick set the bank voltage they need directly. Energy and drain come from the bench."""
    s.w.phys.v = volts
    s.w.phys.hv_filt = volts


# ------------------------------------------------------------------------------------------- start-up, heartbeat
def test_boot_waits_for_battery_then_the_first_heartbeat():
    # elements 1-3: Wait for Bat -> Start BKBM interrupt -> (heartbeat == OK) -> Set Idle Mode -> Wait for Message
    s = Sim()
    s.heartbeat_ms = 0                                   # the Pi isn't talking yet
    s.run(3000)
    assert s.mode == "StartBKBM"
    s.charge()                                           # commands before the board is ready are dropped
    s.run(100)
    rej = [e for _, e in s.events() if e["code"] == P.EV_CMD_REJECTED]
    assert rej and rej[-1]["b"] == P.REJ_NOT_READY
    s.heartbeat()
    s.run(50)
    assert s.mode == "WaitForMessage" and s.app.mode.chg_stop_mode_ctrl == 1   # idle: charging stopped, dumping
    s.run(2000)
    # the earlier MODE_CHG was not saved for later: with chg_stop_mode_ctrl == 1 the charge machine stays in
    # Wait for Bat (charge element 2), dumping, and CHARGE never rose
    assert s.charge_state == "WaitForBat" and s.w.level(W.PIN_NDIS) == 0
    assert not any(n == W.PIN_CHARGE and lv for _, n, lv in s.w.pin_events)
    s.assert_safe()


def test_heartbeat_loss_dumps_dearms_and_recovers():
    # element 4, HB-2: no frame for 5 s -> Disable Charging, discharge -> de-arm kick, stop PWM -> Start BKBM.
    # The heartbeat timeout is shortened to 1.5 s here so the kick is still armed when the link is lost (with the
    # real 5 s, the 2 s arm timeout, element 21, would de-arm it first).
    s = _charged(HEARTBEAT_TIMEOUT_MS=1500)
    s.kick(1000)
    s.run(200)
    assert s.app.mode.armed
    s.heartbeat_ms = 0
    s.run(s.cfg.HEARTBEAT_TIMEOUT_MS - 300)
    assert s.app.mode.armed                              # carries on as normal before the timeout
    s.run(600)
    assert s.mode == "StartBKBM" and not s.app.mode.armed
    assert s.charge_state == "Inhibited" and P.EV_COMMS_LOST in s.event_codes()
    s.place_ball(0.0)
    s.run(500)
    assert not _kicks(s)                                 # de-armed: the ball doesn't trigger a kick
    s.heartbeat_ms = 200
    s.run(400)
    assert s.mode == "WaitForMessage" and s.app.mode.mode == P.MODE_IDLE
    s.assert_safe()


def test_battery_loss_aborts_everything():
    # element 1: ANY STATE chg_ok == 0 -> Wait for Bat, kick / damp aborted
    s = _charged()
    s.damp(30000, 12.0, 3000)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    s.set_battery(volts=11.0)
    s.run(3 * s.cfg.BATT_SAMPLE_MS * s.cfg.BATT_DEBOUNCE_SAMPLES)
    assert s.mode == "WaitForBat" and not _pwm_on(s) and s.w.level(W.PIN_KICK) == 0
    s.assert_safe()


# ------------------------------------------------------------------------------------------- AUTOKICK
def test_autokick_fires_when_the_ball_arrives_then_charges_again():
    # elements 9-13: Enable Charging -> Arm Kick -> (HV >= 100 && kick assigned) -> Charge Hold -> done == 0
    # -> GUARD -> Kick with PW Assigned -> de-arm -> Enable Charging -> Wait for Message
    s = _charged()
    s.kick(1200)
    s.run(300)
    assert s.mode == "WaitForKick" and s.app.mode.armed and not _kicks(s)
    s.place_ball(0.0)
    s.run(200)
    k = _kicks(s)
    assert len(k) == 1 and abs(k[0][1] - 1200) < 1
    assert s.mode == "WaitForMessage" and not s.app.mode.armed
    assert P.EV_KICK_FIRED in s.event_codes()
    s.run(3000)
    assert len(_kicks(s)) == 1                           # one kick per AUTOKICK command
    assert s.charge_state in ("ChargeStarted", "AssumingCharged")   # charging again
    s.assert_safe()                                      # I3: never kicked with CHARGE high


def test_ball_already_in_the_beam_is_kicked_as_soon_as_armed():
    # element 13: the "armed == 0" loop on LED on keeps checking, so no new interrupt is needed
    s = _charged()
    s.place_ball(0.0)
    s.run(300)
    assert not _kicks(s)
    s.kick(1000)
    s.run(100)
    assert len(_kicks(s)) == 1
    s.assert_safe()


def test_arm_expires_after_2s_without_a_ball():
    # element 21: no ball within KICK_ARM_TIMEOUT_MS of the AUTOKICK command -> de-arm, back to Wait for Message
    s = _charged()
    s.kick(1000)
    s.run(s.cfg.KICK_ARM_TIMEOUT_MS - 200)
    assert s.mode == "WaitForKick" and s.app.mode.armed
    s.run(400)
    assert s.mode == "WaitForMessage" and not s.app.mode.armed
    assert P.EV_ARM_EXPIRED in s.event_codes()
    assert s.app.mode.chg_stop_mode_ctrl == 0            # still charging
    s.place_ball(0.0)
    s.run(500)
    assert not _kicks(s)                                 # a late ball is not kicked
    s.kick(1000)                                         # a new AUTOKICK arms again (ball already there: kicks)
    s.run(100)
    assert len(_kicks(s)) == 1
    s.assert_safe()


def test_repeated_autokick_renews_the_arm_without_re_arming():
    # element 23: AUTOKICK while armed -> renew (timer + PW), no de-arm, no Enable Charging, charging untouched
    s = _charged()
    s.kick(1000)
    s.run(100)
    n0 = len(s.transitions)
    for _ in range(15):                                  # every 200 ms for 3 s: longer than the 2 s timeout
        s.kick(1200)
        s.run(200)
        assert s.mode == "WaitForKick" and s.app.mode.armed
    whys = [w for _, m, _, _, w in s.transitions[n0:] if m == "ModeSM"]
    assert whys and set(whys) == {"cmd_autokick_renew"}, set(whys)
    assert P.EV_ARM_EXPIRED not in s.event_codes()
    s.place_ball(0.0)
    s.run(100)
    k = _kicks(s)
    assert len(k) == 1 and abs(k[0][1] - 1200) < 1            # the newest PW is used
    s.heartbeat_ms = 200
    s.run(s.cfg.KICK_ARM_TIMEOUT_MS + 300)                    # and it still expires once the Pi stops asking
    s.assert_safe()


def test_autokick_renew_during_charge_hold_does_not_touch_charging():
    # element 23: a renew that lands in Charge Hold (ball seen, kick about to fire) never releases the charge hold
    s = _charged()
    s.kick(1000)
    s.run(300)
    s.place_ball(0.0)
    s.run_until(lambda: s.mode == "ChargeHold", 500, "Charge Hold")
    n0 = len(s.transitions)
    s.kick(1000)
    s.run(200)
    assert len(_kicks(s)) == 1
    after = s.transitions[n0:]
    whys = [w for _, m, _, _, w in after]
    assert "cmd_autokick_renew" in whys
    before_kick = after[:whys.index("kicked")]
    assert not [w for _, m, _, _, w in before_kick if m == "ChargeSM"], before_kick   # charging never touched
    s.assert_safe()


def test_kick_waits_for_100V():
    # element 10: Arm Kick needs HV >= 100 V; a ball in the beam waits until the bank gets there
    s = Sim().ready()
    s.place_ball(0.0)
    s.kick(1000)                                         # bank empty: AUTOKICK enables charging itself
    s.run_until(lambda: _kicks(s), 6000, "kick")
    hv_at_kick = [row[1] for row in s.w.trace if row[0] <= _kicks(s)[0][0]][-1]
    assert hv_at_kick >= s.cfg.KICK_MIN_HV - 2
    s.assert_safe()


def test_kicks_can_be_spammed_but_pulses_stay_500ms_apart():
    # KCK-2: the spacing gates the ARM, so pulses never come closer than KICK_MIN_INTERVAL_MS
    s = _charged()
    s.place_ball(0.0)
    for _ in range(8):
        s.kick(1000)
        s.place_ball(0.0)
        s.run(100)
    s.run(1500)
    t = [t for t, _ in _kicks(s)]
    assert len(t) >= 2
    assert min(b - a for a, b in zip(t, t[1:])) >= s.cfg.KICK_MIN_INTERVAL_MS - 1
    s.assert_safe()


def test_bad_frames_and_heartbeats_never_dearm():
    # element 7: only a GOOD command de-arms; a bad frame changes nothing; heartbeats never reach the machine
    s = _charged()
    s.kick(1000)
    s.run(200)
    s.send(s.cfg.CAN_ID_KICK, P.encode_kick(50))          # PW below 100 us: bad command
    s.send(s.cfg.CAN_ID_MODE, b"\x07")                    # unknown mode
    s.run(1500)                                           # 7 heartbeats meanwhile (and under the 2 s arm timeout)
    assert s.mode == "WaitForKick" and s.app.mode.armed
    reasons = [e["b"] for _, e in s.events() if e["code"] == P.EV_CMD_REJECTED]
    assert P.REJ_OUT_OF_RANGE in reasons and P.REJ_BAD_VALUE in reasons
    s.assert_safe()


def test_chg_and_idle_cancel_an_armed_kick():
    # elements 6, 8: a good command de-arms; IDLE also dumps, CHG keeps charging
    s = _charged()
    s.kick(1000); s.run(200)
    s.charge(); s.run(200)
    assert not s.app.mode.armed and s.mode == "WaitForMessage" and s.app.mode.chg_stop_mode_ctrl == 0
    s.kick(1000); s.run(200)
    s.mode_idle(); s.run(1000)
    assert not s.app.mode.armed and s.charge_state == "Inhibited"
    s.place_ball(0.0); s.run(300)
    assert not _kicks(s)
    s.assert_safe()


# ------------------------------------------------------------------------------------------- DAMP
def test_damp_sequence_initial_kick_4ms_then_pwm_until_the_timeout():
    # elements 14-16: (spacing && HV >= 180) -> Charge Hold -> done == 0 -> Initial Kick -> 4 ms -> PWM -> timeout
    s = _charged()
    t0 = s.w.now / 1e6
    s.damp(30000, 12.0, 1000)
    s.run(1500)
    kicks = [(t, w) for t, w in _kicks(s, 500) if t > t0]
    assert len(kicks) == 1 and abs(kicks[0][1] - s.cfg.DAMP_INITIAL_KICK_US) < 1     # 800 us Initial Kick
    pwm = [t for t, w in s.kick_pulses() if w < 50 and t > kicks[0][0]]
    assert pwm and pwm[0] - kicks[0][0] >= s.cfg.DAMP_SETTLE_US / 1000.0 - 0.1      # "wait for 4ms"
    assert 950 < pwm[-1] - pwm[0] < 1100                                            # ~1 s hold
    assert s.mode == "WaitForMessage" and not _pwm_on(s)
    assert P.EV_DAMP_DONE in s.event_codes()
    s.assert_safe()


def test_damp_renew_extends_without_a_new_initial_kick():
    # element 17: DAMP while the PWM is running -> Renew Damp start variable (timeout restarts from now)
    s = _charged()
    s.damp(30000, 12.0, 1000)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    t_start = s.w.now / 1e6
    s.run(600)
    s.damp(30000, 12.0, 1000)
    s.run_until(lambda: not _pwm_on(s), 2500, "damp end")
    held = s.w.now / 1e6 - t_start
    assert 1500 < held < 1750, held
    assert len(_kicks(s, 500)) == 1                       # only the first Initial Kick
    s.assert_safe()


def test_renewing_forever_stops_at_the_max_hold():
    # element 16 "max hold": however often it is renewed, the damp ends DAMP_MAX_TOTAL_MS after the Initial Kick
    s = _charged()
    s.damp(30000, 12.0, 1000)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    t_start = s.w.now / 1e6
    while _pwm_on(s) and s.w.now / 1e6 - t_start < 12000:
        s.damp(30000, 12.0, 1000)
        s.run(300)
    assert not _pwm_on(s)
    assert s.w.now / 1e6 - t_start <= s.cfg.DAMP_MAX_TOTAL_MS + 400
    s.assert_safe()


def test_kick_during_a_damp_holds_until_the_ball_then_kicks():
    # elements 18, 19 / MSG-3: AUTOKICK while damping arms inside the damp; the PWM keeps holding and stops
    # only at the kick (same PIO step). (The simulator's beam ignores the plunger; on the board the held-out plunger
    # may block it, note DMP-5.)
    s = _charged()
    s.damp(30000, 12.0, 3000)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    s.run(50)
    s.kick(1500)
    s.run(150)
    assert _pwm_on(s) and s.mode == "DampWait" and s.app.mode.armed
    assert s.app.mode.mode == P.MODE_DAMP                 # "set mode back to MODE_DAMP"
    _set_bank(s, 190.0)                                   # bank still above 100 V when the ball arrives
    s.place_ball(0.0)
    s.run(50)
    k = [w for _, w in _kicks(s, 500)]
    assert k[-1] > 1400 and not _pwm_on(s) and s.mode == "WaitForMessage"
    s.assert_safe()                                       # I7: the KICK pin never changed function


def test_kick_during_a_damp_is_dropped_at_the_timeout():
    s = _charged()
    s.damp(30000, 12.0, 800)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    s.kick(1500)
    s.run(1500)
    assert not _pwm_on(s) and not s.app.mode.armed and s.mode == "WaitForMessage"
    s.place_ball(0.0); s.run(300)
    assert len(_kicks(s, 500)) == 1                       # only the Initial Kick ever fired
    s.assert_safe()


def test_kick_armed_during_a_damp_does_not_time_out():
    # Henry: no 2 s arm timeout during a damp (the kick is expected; the damp's own timeout ends it)
    s = _charged()
    s.damp(30000, 12.0, 3000)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    s.kick(1500)
    s.run(s.cfg.KICK_ARM_TIMEOUT_MS + 300)
    assert s.app.mode.armed and _pwm_on(s) and s.mode == "DampWait"
    assert P.EV_ARM_EXPIRED not in s.event_codes()
    s.assert_safe()


def test_renew_keeps_a_queued_kick_armed():
    # element 17: a DAMP renew is the one good command that does NOT de-arm
    s = _charged()
    s.damp(30000, 12.0, 1000)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    s.kick(1500); s.run(60)
    s.damp(30000, 12.0, 1000); s.run(60)
    assert s.app.mode.armed
    _set_bank(s, 190.0)
    s.place_ball(0.0); s.run(50)
    assert _kicks(s, 500)[-1][1] > 1400
    s.assert_safe()


def test_low_bank_during_a_damp_stops_pwm_recharges_then_kicks():
    # element 20: HV < 100 V && kick assigned -> Stop PWM -> 50 ms -> Enable Charging -> HV >= 100 -> Charge Hold -> kick
    s = _charged()
    s.damp(30000, 12.0, 3000)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    s.kick(1500); s.run(100)
    _set_bank(s, 80.0)                                    # the hold drained the bank
    s.place_ball(0.0)
    s.run(30)
    assert s.mode == "DampStopPWM" and not _pwm_on(s)
    s.run_until(lambda: _kicks(s, 1400), 3000, "kick after recharge")
    whys = [w for _, m, _, _, w in s.transitions if m == "ModeSM"]
    i = whys.index("damp_kick_bank_low")
    assert whys[i:i + 4] == ["damp_kick_bank_low", "recharge_for_kick", "kick_assigned", "kicked"]
    s.assert_safe()


def test_idle_mid_damp_stops_the_pwm_and_dumps():
    # element 6 / DMP-3: IDLE leaves the damp loop through "de-arm kick / stop PWM" -> Set Idle Mode
    s = _charged()
    s.damp(30000, 12.0, 3000)
    s.run_until(lambda: _pwm_on(s), 500, "damping")
    s.run(300)
    s.mode_idle()
    s.run(20)
    assert not _pwm_on(s) and s.w.level(W.PIN_KICK) == 0 and s.mode == "WaitForMessage"
    s.run(1000)
    assert s.charge_state == "Inhibited"
    s.assert_safe()


# ------------------------------------------------------------------------------------------- everything at once
def test_random_commands_and_balls_never_break_an_invariant():
    rng = random.Random(7)
    s = _charged()
    sends = (s.mode_idle, s.charge, lambda: s.kick(rng.randrange(100, 5001)),
             lambda: s.damp(rng.choice((10000, 30000)), rng.uniform(5, 20), rng.randrange(200, 3000)),
             lambda: s.send(s.cfg.CAN_ID_KICK, bytes(rng.randrange(256) for _ in range(rng.randrange(6)))))
    for _ in range(150):
        rng.choice(sends)()
        if rng.random() < 0.3:
            s.place_ball(0.0)
        elif rng.random() < 0.3:
            s.remove_ball()
        s.run(rng.randrange(5, 400))
    s.assert_safe()
