"""Level 5: charge state machine on the simulated LT3750 / bank / battery."""

from sim.harness import Sim
from sim import world as W
import protocol as P

FAST = dict(CHARGE_RETRY_MS=1500)          # shorter fault retry so the suite stays quick


def _charging_sim(**kw):
    s = Sim(**kw)
    s.charge()
    return s


def _charge_edges(s):
    return [t for t, n, lv in s.w.pin_events if n == W.PIN_CHARGE and lv]


def test_normal_charge_time_and_stop_voltage():
    s = _charging_sim()
    s.run_until(lambda: s.charge_state == "Charging", 3000, "charging")
    t = s.run_until(lambda: s.charge_state == "Full", 4000, "full")
    assert 1.2 * 1000 < t < 2.2 * 1000, t
    assert 213.0 < s.hv < 216.5 and 213.0 < s.app.hv < 216.5
    assert s.w.level(W.PIN_CHARGE) == 0                 # charger off while full
    s.run(3000)
    assert s.charge_state == "Full" and len(_charge_edges(s)) == 1
    s.assert_safe()


def test_top_off_from_hv_measurement():
    s = _charging_sim(physics=dict(R_bleed=20e3))       # leaky bank: ~5 V/s at 210 V
    s.run_until(lambda: s.charge_state == "Full", 5000, "full")
    t_full = s.w.now / 1e6
    s.run(7000)
    edges = _charge_edges(s)
    assert len(edges) >= 3, edges
    gaps = [(b - a) / 1e6 for a, b in zip(edges, edges[1:])]
    assert min(gaps) >= s.cfg.MIN_CYCLE_INTERVAL_MS
    lows = [row[1] for row in s.w.trace if row[0] > t_full + 100]
    assert min(lows) > s.cfg.RECHARGE_BELOW_V - 3          # topped off near the threshold, not at 5 s ticks
    s.assert_safe()


def test_no_battery_then_plug_in():
    s = Sim(physics=dict(batt_present=False))
    s.charge()
    s.run(3000)
    assert s.charge_state == "NoBattery" and not _charge_edges(s)
    s.set_battery(present=True)
    t = s.run_until(lambda: s.charge_state == "Charging", 4000, "charging")
    assert t >= s.cfg.BATT_SETTLE_MS                      # 2 s settle after the pack appears
    s.assert_safe()


def test_battery_unplugged_mid_charge_stops_immediately():
    s = _charging_sim()
    s.run_until(lambda: s.charge_state == "Charging", 3000, "charging")
    s.run(300)
    s.set_battery(present=False)
    s.run(400)
    assert s.charge_state == "NoBattery" and s.w.level(W.PIN_CHARGE) == 0
    assert s.app.charge.consecutive_faults == 0           # an unplug is not a charger fault
    s.assert_safe()


def test_low_battery_never_charges():
    s = Sim(physics=dict(batt_v=14.2))
    s.charge()
    s.run(5000)
    assert not _charge_edges(s) and s.charge_state == "NoBattery"


def test_marginal_battery_does_not_chatter():
    s = _charging_sim(physics=dict(batt_v=14.85, batt_r=0.15))   # sags ~0.5 V while charging
    s.run(8000)
    assert len(_charge_edges(s)) <= 2, len(_charge_edges(s))
    s.assert_safe()


def _fault_run(mode, expect_fault, **kw):
    s = Sim(**dict(FAST, **kw))
    s.lt_fault(mode)
    s.charge()
    s.run_until(lambda: s.app.charge.last_fault != 0, 9000, "first fault")
    assert s.app.charge.last_fault == expect_fault, (mode, s.app.charge.last_fault)
    return s


def test_uvlo_no_start_retries_then_locks_and_idle_clears():
    s = _fault_run("uvlo", P.F_CHG_NO_START)
    s.run_until(lambda: s.charge_state == "Locked", 8000, "locked")
    s.run(50)
    assert s.mode == "Fault" and P.EV_FAULT in s.event_codes()
    assert s.w.level(W.PIN_NDIS) == 0 or s.app.power.discharging is False   # dump requested
    s.lt_fault("ok")
    s.charge()
    s.run(50)
    assert s.mode == "Fault"                                # CHARGE refused while faulted
    s.mode_idle()
    s.run(50)
    assert s.mode == "Idle" and s.charge_state in ("Inhibited", "NoBattery")
    s.charge()
    s.run_until(lambda: s.charge_state == "Full", 5000, "full after clear")
    s.assert_safe()


def test_early_done_fault():
    s = _fault_run("early_done", P.F_CHG_EARLY_DONE)
    assert s.hv < 150
    s.assert_safe()


def test_lt3750_not_regulating_hits_backstop_never_overvolt():
    s = _fault_run("no_regulation", P.F_CHG_NO_REGULATION)
    s.run(4000)
    assert s.w.phys.max_v < s.cfg.OVERVOLT_V
    s.assert_safe()


def test_done_stuck_high_caught_by_timeout():
    # the bank charges normally but DONE never falls: the cycle timeout catches it
    s = _fault_run("done_stuck_high", P.F_CHG_TIMEOUT)
    assert s.w.phys.max_v < s.cfg.CHARGE_BACKSTOP_V
    s.assert_safe()


def test_done_stuck_low_detected_as_no_start():
    s = _fault_run("done_stuck_low", P.F_CHG_NO_START)
    s.assert_safe()


def test_slow_charger_is_safe():
    s = Sim(**FAST)
    s.lt_fault("slow")
    s.charge()
    s.run(12000)
    assert s.charge_state in ("Full", "FaultWait", "Gap", "Starting", "Charging", "Locked")
    s.assert_safe()


def test_bench_ceiling_holds_voltage():
    s = Sim()
    s.usb("ceil 30")
    s.charge()
    s.run_until(lambda: s.charge_state == "Full", 6000, "full at ceiling")
    s.run(200)
    assert 29.0 < s.hv < 32.0, s.hv
    s.run(3000)
    assert s.w.phys.max_v < 33.0
    s.usb("ceil off")
    s.run_until(lambda: s.hv > 200, 6000, "full voltage after ceiling off")
    s.assert_safe()


def test_idle_mid_charge_stops_then_dumps_with_dead_time():
    s = _charging_sim()
    s.run_until(lambda: s.charge_state == "Charging", 3000, "charging")
    s.run(600)
    s.mode_idle()
    t_cmd = s.w.now
    s.run(5)
    assert s.w.level(W.PIN_CHARGE) == 0
    s.run(5000)
    falls = [t for t, n, lv in s.w.pin_events if n == W.PIN_NDIS and not lv and t > t_cmd]
    assert falls and falls[0] - t_cmd >= 50 * W.NS_PER_MS
    assert s.hv < 15                                         # ~2 s time constant through the 1 k dump
    assert s.w.phys.max_temp < 60
    s.assert_safe()


def test_fault_retry_after_wait():
    s = _fault_run("uvlo", P.F_CHG_NO_START)
    s.lt_fault("ok")
    s.run_until(lambda: s.charge_state == "Full", 6000, "recovered after retry")
    assert s.app.charge.consecutive_faults == 0
    s.assert_safe()


def test_dead_flyback_no_progress_fault():
    s = Sim(**FAST, physics=dict(lt_k=0.01))            # flyback switching but delivering ~nothing
    s.charge()
    s.run_until(lambda: s.app.charge.last_fault != 0, 6000, "no-progress fault")
    assert s.app.charge.last_fault == P.F_CHG_NO_PROGRESS and s.charge_state == "FaultWait"
    s.assert_safe()
