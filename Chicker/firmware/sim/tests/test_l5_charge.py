"""Level 5: the charge state machine on the simulated LT3750 / bank / battery.

One test per agreed behaviour in docs/CHARGE-STATE-MACHINE-REVIEW.md ("element N" in the test names / comments).
Commands go in as real CAN frames; the charge machine is only ever driven through the mode machine's flags.
"""

from sim.harness import Sim
from sim import world as W
import protocol as P


def _ready_and_charging(**kw):
    """Boot, wait until the board accepts commands, send MODE_CHG."""
    s = Sim(**kw).ready()
    s.charge()
    return s


def _charge_rises(s):
    """Times (ms) at which the CHARGE pin went high."""
    return [t / 1e6 for t, n, lv in s.w.pin_events if n == W.PIN_CHARGE and lv]


def _state_entries(s, machine, state):
    return [t for t, m, _frm, to, _why in s.transitions if m == machine and to == state]


# ------------------------------------------------------------------------------------------- normal charging
def test_first_charge_waits_2s_for_the_battery_then_charges_to_full():
    # elements 2, 3, 7, 8, 10: Wait for Bat -> Startup -> New Charge from Bat (2 s) -> GUARD -> Charge Toggle Wait
    # -> done rose -> Charge Started -> 4500 ms, done == 0 -> Assuming Charged
    s = _ready_and_charging()
    t_cmd = s.w.now / 1e6
    s.run_until(lambda: s.charge_state == "ChargeStarted", 3000, "charge started")
    first_rise = _charge_rises(s)[0]
    assert first_rise - t_cmd >= s.cfg.BATT_SETTLE_MS - 1             # the 2 s battery wait ran first (1 ms tick)
    s.run_until(lambda: s.charge_state == "AssumingCharged", 5000, "assuming charged")
    assert 206.0 < s.hv < 213.0 and 206.0 < s.app.hv < 213.0
    assert s.w.now / 1e6 - first_rise >= s.cfg.CHARGE_TIMEOUT_MS      # the verification is timer-based
    assert s.w.level(W.PIN_CHARGE) == 1 and s.w.level(W.PIN_NDIS) == 1   # CHARGE held high, dump off
    s.assert_safe()


def test_top_off_every_5s_and_a_short_done_pulse_still_counts_as_started():
    # element 11: Assuming Charged -> 5000 ms -> Charge Toggle (CHARGE low 50 ms) -> Charge Toggle Wait.
    # element 8: on a nearly full bank DONE pulses for only a few ms; the edge latch must still see it.
    s = _ready_and_charging()
    s.run_until(lambda: s.charge_state == "AssumingCharged", 9000, "charged")
    s.run(16000)
    rises = _charge_rises(s)
    gaps = [b - a for a, b in zip(rises, rises[1:])]
    assert len(gaps) >= 3 and all(abs(g - (s.cfg.TOPOFF_PERIOD_MS + s.cfg.CHARGE_TOGGLE_LOW_MS)) < 5 for g in gaps), gaps
    assert s.app.charge.cycles >= 4 and s.charge_state != "Disabled"
    lows = [row[1] for row in s.w.trace if row[0] > rises[1]]
    assert min(lows) > 205.0                                          # bank stays topped up
    s.assert_safe()


def test_new_cycles_are_never_closer_than_500ms():
    # element 7: GUARD "time since last chg >= 500ms", even when the mode machine flips stop / start quickly
    s = _ready_and_charging()
    s.run_until(lambda: s.charge_state == "ChargeStarted", 3000, "charging")
    for _ in range(6):
        s.mode_idle(); s.run(60)
        s.charge(); s.run(60)
    s.run(1500)
    rises = _charge_rises(s)
    assert len(rises) >= 2
    assert min(b - a for a, b in zip(rises, rises[1:])) >= s.cfg.CHARGE_SPACING_MS
    s.assert_safe()


# ------------------------------------------------------------------------------------------- stop / hold / battery
def test_stop_goes_inhibited_and_dumps_with_dead_time():
    # element 4: chg_stop_mode_ctrl == 1 -> Inhibited = CHARGE low now (mid-cycle too), then the dump
    s = _ready_and_charging()
    s.run_until(lambda: s.charge_state == "ChargeStarted", 3000, "charging")
    s.run(400)
    v_stop = s.hv
    s.mode_idle()
    s.run(3000)
    assert s.charge_state == "Inhibited"
    assert s.w.level(W.PIN_CHARGE) == 0 and s.w.level(W.PIN_NDIS) == 0
    assert s.hv < v_stop * 0.6                                        # dumping
    s.assert_safe()                                                   # I1 / I2: never charged into the dump


def test_hold_keeps_the_charge_and_releases_into_a_new_cycle():
    # element 5a: chg_hold_mode_ctrl == 1 -> Not Charging (CHARGE low, NO dump); hold released -> New Charge
    s = _ready_and_charging()
    s.run_until(lambda: s.charge_state == "AssumingCharged", 9000, "charged")
    s.app.mode.chg_hold_mode_ctrl = 1                                 # what the mode machine's "Charge Hold" does
    s.run(2000)
    assert s.charge_state == "NotCharging"
    assert s.w.level(W.PIN_CHARGE) == 0 and s.w.level(W.PIN_NDIS) == 1
    assert s.hv > 205.0                                               # charge kept (only the divider bleeds)
    s.app.mode.chg_hold_mode_ctrl = 0
    s.run_until(lambda: s.charge_state == "ChargeStarted", 1500, "new cycle")
    assert [w for _, _, _, _, w in s.transitions if w == "hold_released"]
    s.assert_safe()


def test_no_battery_then_plug_in():
    # elements 1, 2: nothing happens without a battery; when it appears the 2 s wait runs first
    s = Sim(physics=dict(batt_present=False))
    s.run(3000)
    assert s.mode == "WaitForBat" and s.charge_state == "WaitForBat" and not _charge_rises(s)
    s.set_battery(present=True)
    s.ready()
    t0 = s.w.now / 1e6
    s.charge()
    s.run_until(lambda: s.charge_state == "ChargeStarted", 4000, "charging")
    assert _charge_rises(s)[0] - t0 >= s.cfg.BATT_SETTLE_MS - 1
    s.assert_safe()


def test_battery_lost_mid_charge_stops_charging():
    # element 1: ANY STATE chg_ok == 0 -> Wait for Bat (battery rule beats the stop / hold rules)
    s = _ready_and_charging()
    s.run_until(lambda: s.charge_state == "ChargeStarted", 3000, "charging")
    s.set_battery(volts=12.0)                                         # pack sags below the window
    s.run(3 * s.cfg.BATT_SAMPLE_MS * s.cfg.BATT_DEBOUNCE_SAMPLES)
    assert s.charge_state == "WaitForBat" and s.w.level(W.PIN_CHARGE) == 0
    assert s.mode == "WaitForBat"
    s.assert_safe()


def test_low_battery_never_charges():
    s = Sim(physics=dict(batt_v=13.5))
    s.run(6000)
    assert s.charge_state == "WaitForBat" and not _charge_rises(s)
    s.assert_safe()


# ------------------------------------------------------------------------------------------- faults -> Disabled
def _fault(s):
    return s.app.charge.fault


def test_uvlo_no_done_edge_disables_and_locks():
    # element 8: no DONE rising edge 50 ms after CHARGE rose -> "Charging Disabled. UVLO / thermal" -> Disabled
    s = _ready_and_charging(physics=dict(lt_fault="uvlo"))
    s.run(4000)
    assert s.charge_state == "Disabled" and _fault(s) == P.F_CHG_UVLO_THERMAL
    assert s.app.charge.chg_disable_chip_level == 1
    assert s.mode == "Locked"                                         # mode element 5
    assert s.w.level(W.PIN_CHARGE) == 0 and s.w.level(W.PIN_NDIS) == 0   # Disabled dumps (element 12)
    ev = [e for _, e in s.events() if e["code"] == P.EV_FAULT]
    assert ev and ev[0]["a"] == P.F_CHG_UVLO_THERMAL
    assert s.statuses()[-1][1]["fault"] == P.F_CHG_UVLO_THERMAL        # Locked tells the Pi in every STATUS
    s.assert_safe()


def test_done_stuck_low_and_stuck_high_are_caught_as_no_start():
    # element 8: a DONE line that never changes gives no rising edge either
    for fault in ("done_stuck_low", "done_stuck_high"):
        s = _ready_and_charging(physics=dict(lt_fault=fault))
        s.run(4000)
        assert s.charge_state == "Disabled" and _fault(s) == P.F_CHG_UVLO_THERMAL, fault
        s.assert_safe()


def test_done_never_falling_is_too_slow():
    # element 10: done still 1 at "time since last chg >= 4500ms" -> "Too slow to charge" -> Disabled
    s = _ready_and_charging(physics=dict(lt_fault="done_follows_charge"))
    s.run_until(lambda: s.charge_state == "Disabled", 9000, "disabled")
    assert _fault(s) == P.F_CHG_TOO_SLOW
    rise = _charge_rises(s)[0]
    assert s.w.now / 1e6 - rise >= s.cfg.CHARGE_TIMEOUT_MS
    s.assert_safe()


def test_overvoltage_disables_after_3_readings():
    # X-1: ANY STATE HV >= 220 V for 3 readings in a row -> Disabled ("Overvoltage"), reading sent to the Pi
    s = _ready_and_charging(physics=dict(lt_fault="no_regulation"))
    s.run_until(lambda: s.charge_state == "Disabled", 9000, "disabled")
    assert _fault(s) == P.F_OVERVOLT
    ev = [e for _, e in s.events() if e["code"] == P.EV_FAULT][0]
    assert ev["b"] >= 10 * s.cfg.OVERVOLT_V                           # the HV that tripped it, in dV
    s.run(3000)
    assert s.hv < 200 and s.mode == "Locked"
    s.assert_safe()                                                   # bank never went past the safety margin


def test_one_noisy_reading_does_not_trip_overvoltage():
    s = _ready_and_charging()
    s.run_until(lambda: s.charge_state == "AssumingCharged", 9000, "charged")
    ch = s.app.charge
    ch._count_overvolt_readings(230.0)                                # one spike...
    ch._count_overvolt_readings(210.0)                                # ...then normal again
    s.run(200)
    assert s.charge_state != "Disabled"


def test_disabled_is_one_way():
    # element 12: nothing leaves Disabled, whatever the robot sends
    s = _ready_and_charging(physics=dict(lt_fault="uvlo"))
    s.run(4000)
    for send in (s.mode_idle, s.charge, lambda: s.kick(1000), lambda: s.damp(30000, 12.0)):
        send()
        s.run(300)
        assert s.charge_state == "Disabled" and s.mode == "Locked"
    rej = [e for _, e in s.events() if e["code"] == P.EV_CMD_REJECTED]
    assert rej and all(e["b"] == P.REJ_FAULTED for e in rej)
    s.assert_safe()


def test_safe_charge_bench_test():
    # element 9: SAFE_CHARGE = 1 -> charge for 236 ms (~50 V) -> Charged to ~50V -> wait 5 s -> Disabled
    s = _ready_and_charging(SAFE_CHARGE=1)
    s.run_until(lambda: s.charge_state == "ChargedTo50V", 4000, "charged to ~50 V")
    assert 30.0 < s.hv < 80.0, s.hv
    s.run_until(lambda: s.charge_state == "Disabled", 6000, "disabled")
    assert _fault(s) == P.F_SAFE_CHARGE_TEST
    s.assert_safe()
