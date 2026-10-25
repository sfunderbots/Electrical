"""Level 6b: every event in every state.

For each state of each machine, the board is driven into that state with real CAN frames and world events, then
every kind of event is thrown at it. Nothing is checked about WHICH state it ends up in (the element tests do
that); this checks that nothing ever goes wrong: no exception, no crash, no safety violation (simulator invariants
I1-I12), and the board still answers afterwards (STATUS frames keep coming, it still takes MODE_IDLE).

Charge states Startup, Charge Toggle Reset and Charge Toggle Reset from Bat are pass-through: the machine is in them
for no time at all (it follows the next arrow in the same loop pass), so there is nothing to interrupt there.
"""

from sim.harness import Sim
from sim.run_tests import slow
import protocol as P


# ------------------------------------------------------------------------------------------- helpers
def _pwm_on(s):
    return s.app.b.kicker.pwm_running


def _set_bank(s, volts):
    s.w.phys.v = volts
    s.w.phys.hv_filt = volts


def _charging(s, until_v=200.0):
    s.ready()
    s.charge()
    s.run_until(lambda: s.hv >= until_v, 8000, "bank at %g V" % until_v)
    return s


def _step_until(s, pred, timeout_ms, what):
    """Like run_until, but stops on the very loop pass the condition becomes true (for short-lived states)."""
    s.run_until(pred, timeout_ms, what)
    return s


# ------------------------------------------------------------------------------------------- reach each MODE state
def _mode_wait_for_bat():
    s = Sim(physics=dict(batt_present=False))
    s.run(1000)
    return s


def _mode_start_bkbm():
    s = Sim()
    s.heartbeat_ms = 0
    s.run(2000)
    return s


def _mode_wait_for_message():
    return Sim().ready()


def _mode_locked():
    s = Sim(physics=dict(lt_fault="uvlo")).ready()
    s.charge()
    return _step_until(s, lambda: s.mode == "Locked", 4000, "Locked")


def _mode_autokick_enable_charging():
    s = _charging(Sim())
    s.kick(1000)
    s.place_ball(0.0)
    _step_until(s, lambda: s.app.mode.last_why == "kicked", 1000, "first kick")
    s.kick(1000)                                  # second kick within 500 ms: waits for the spacing
    return _step_until(s, lambda: s.mode == "AutokickEnableCharging", 300, "AutokickEnableCharging")


def _mode_arm_kick():
    s = _charging(Sim())
    s.kick(1000)
    return _step_until(s, lambda: s.mode == "ArmKick", 300, "ArmKick")


def _mode_charge_hold():
    s = _charging(Sim())
    s.kick(1000)
    s.place_ball(0.0)
    return _step_until(s, lambda: s.mode == "ChargeHold", 500, "ChargeHold")


def _mode_damp_enable_charging():
    s = Sim().ready()
    s.damp(30000, 12.0, 2000)                     # bank empty: waits for HV >= 180 while charging
    return _step_until(s, lambda: s.mode == "DampEnableCharging", 300, "DampEnableCharging")


def _mode_damp_charge_hold():
    s = _charging(Sim(), 185.0)
    s.damp(30000, 12.0, 2000)
    return _step_until(s, lambda: s.mode == "DampChargeHold", 500, "DampChargeHold")


def _mode_initial_kick():
    s = _charging(Sim(), 185.0)
    s.damp(30000, 12.0, 2000)
    return _step_until(s, lambda: s.mode == "InitialKick", 500, "InitialKick")


def _mode_damp_wait():
    s = _charging(Sim(), 185.0)
    s.damp(30000, 12.0, 2000)
    return _step_until(s, lambda: _pwm_on(s), 500, "DampWait")


def _mode_damp_stop_pwm():
    s = _mode_damp_wait()
    s.kick(1500)
    s.run(20)
    _set_bank(s, 80.0)
    s.place_ball(0.0)
    return _step_until(s, lambda: s.mode == "DampStopPWM", 200, "DampStopPWM")


MODE_STATES = {
    "WaitForBat": _mode_wait_for_bat,
    "StartBKBM": _mode_start_bkbm,
    "WaitForMessage": _mode_wait_for_message,
    "Locked": _mode_locked,
    "AutokickEnableCharging": _mode_autokick_enable_charging,
    "ArmKick": _mode_arm_kick,
    "ChargeHold": _mode_charge_hold,
    "DampEnableCharging": _mode_damp_enable_charging,
    "DampChargeHold": _mode_damp_charge_hold,
    "InitialKick": _mode_initial_kick,
    "DampWait": _mode_damp_wait,
    "DampStopPWM": _mode_damp_stop_pwm,
}


# ------------------------------------------------------------------------------------------- reach each CHARGE state
def _charge_from_mode(state, setup=None, **kw):
    def reach():
        s = Sim(**kw).ready()
        if setup:
            setup(s)
        else:
            s.charge()
        return _step_until(s, lambda: s.charge_state == state, 12000, state)
    return reach


def _hold(s):
    s.charge()
    s.run_until(lambda: s.charge_state == "ChargeStarted", 4000, "charging")
    s.app.mode.chg_hold_mode_ctrl = 1             # what Charge Hold does


def _inhibit(s):
    s.charge()
    s.run_until(lambda: s.charge_state == "ChargeStarted", 4000, "charging")
    s.mode_idle()


def _toggle_after_hold(s):
    _hold(s)
    s.run(100)
    s.app.mode.chg_hold_mode_ctrl = 0


CHARGE_STATES = {
    "WaitForBat": lambda: Sim().ready(),
    "NewChargeFromBat": _charge_from_mode("NewChargeFromBat"),
    "Inhibited": _charge_from_mode("Inhibited", _inhibit),
    "NotCharging": _charge_from_mode("NotCharging", _hold),
    "NewCharge": _charge_from_mode("NewCharge", _toggle_after_hold),
    "ChargeToggleWait": _charge_from_mode("ChargeToggleWait"),
    "ChargeStarted": _charge_from_mode("ChargeStarted"),
    "AssumingCharged": _charge_from_mode("AssumingCharged"),
    "ChargeToggle": _charge_from_mode("ChargeToggle"),
    "ChargedTo50V": _charge_from_mode("ChargedTo50V", SAFE_CHARGE=1),
    "Disabled": _charge_from_mode("Disabled", physics=dict(lt_fault="uvlo")),
}


# ------------------------------------------------------------------------------------------- the events
def _bad_frames(s):
    s.send(s.cfg.CAN_ID_KICK, P.encode_kick(50))
    s.send(s.cfg.CAN_ID_MODE, b"\x09")
    s.send(s.cfg.CAN_ID_DAMP, b"\x01")
    s.send(s.cfg.CAN_ID_KICK, P.encode_kick(1000, P.TRIGGER_NOW))


def _command_burst(s):
    for send in (s.charge, lambda: s.kick(800), lambda: s.damp(30000, 10.0, 500), s.mode_idle, s.charge):
        send()


def _heartbeat_lost(s):
    s.heartbeat_ms = 0
    s.run(s.cfg.HEARTBEAT_TIMEOUT_MS + 200)
    s.heartbeat_ms = 200


def _battery_blip(s):
    s.set_battery(present=False)
    s.run(2000)
    s.set_battery(present=True)


def _charger_dies(s):
    s.lt_fault("uvlo")


def _overvoltage(s):
    _set_bank(s, 226.0)


EVENTS = {
    "MODE_IDLE": lambda s: s.mode_idle(),
    "MODE_CHG": lambda s: s.charge(),
    "AUTOKICK": lambda s: s.kick(1200),
    "DAMP": lambda s: s.damp(30000, 12.0, 800),
    "legacy frames": lambda s: (s.legacy(1, 900, 0), s.legacy(2, 30000, 12), s.legacy(0, 0, 0)),
    "bad frames": _bad_frames,
    "command burst": _command_burst,
    "ball in": lambda s: s.place_ball(0.0),
    "ball out": lambda s: s.remove_ball(),
    "heartbeat lost": _heartbeat_lost,
    "battery blip": _battery_blip,
    "charger dies": _charger_dies,
    "overvoltage": _overvoltage,
    "usb console": lambda s: [s.usb(c) for c in ("status", "info", "ceil 50", "dry kick 500", "hb", "tlm 0")],
}


# ------------------------------------------------------------------------------------------- the checks
def _still_answers(s):
    """After the event: STATUS frames keep coming and MODE_IDLE is still handled (or the board is Locked)."""
    n = len(s.statuses())
    s.run(300)
    assert len(s.statuses()) > n, "STATUS frames stopped"
    if s.mode not in ("Locked", "WaitForBat", "StartBKBM"):
        s.mode_idle()
        s.run(300)
        assert s.app.mode.mode == P.MODE_IDLE, "MODE_IDLE not handled"


def _run_matrix(states, label):
    failures = []
    for state_name, reach in states.items():
        for event_name, event in EVENTS.items():
            try:
                s = reach()
                event(s)
                s.run(1500)
                s.assert_safe()
                _still_answers(s)
                s.assert_safe()
            except Exception as e:      # noqa - collect everything, report all at once
                failures.append("%s state %-24s + %-15s -> %s: %s" % (label, state_name, event_name,
                                                                      type(e).__name__, e))
    assert not failures, "\n" + "\n".join(failures)


@slow
def test_every_event_in_every_mode_state():
    _run_matrix(MODE_STATES, "mode")


@slow
def test_every_event_in_every_charge_state():
    _run_matrix(CHARGE_STATES, "charge")


def test_every_state_can_be_reached():
    """Fast check that every recipe above really reaches its state (so the slow matrix tests what it says)."""
    for table in (MODE_STATES, CHARGE_STATES):
        for name, reach in table.items():
            reach().assert_safe()
