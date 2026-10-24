"""Level 9: replay the old RoboCup 2026 firmware on the same simulated board.

Not a pass/fail gate for v2 - these document what the simulator finds in the old code, and prove the
simulator catches real bugs (a simulator that only ever says "pass" proves nothing).
"""

from sim.legacy import run_legacy
from sim import world as W

ID = 0x2AA


def _frame(mode, value=0, duty=0):
    return bytes((mode, value & 0xFF, value >> 8, duty))


def _rules(w):
    return sorted(set(v.rule for v in w.violations))


def test_legacy_boot_and_charge_runs_on_the_sim():
    w = run_legacy([], 6.0)
    assert any(n == W.PIN_CHARGE and lv for _, n, lv in w.pin_events)     # old code auto-charges at boot
    assert w.phys.max_v > 200
    assert not [v for v in w.violations if v.rule.startswith("I1")]


def test_legacy_idle_mid_charge_reproduces_charge_into_dump():
    # IDLE (old mode 0) arrives while a charge cycle is still running (old code charges from ~0.1 s).
    # idle() turns the dump on but never clears `charge`: the LT3750 keeps charging into the 1 k dump,
    # settles near 201 V (below its 215 V stop) and never finishes -> ~40 W into the resistors.
    w = run_legacy([(1000, ID, _frame(0))], 30.0)
    rules = _rules(w)
    print("legacy IDLE mid-charge ->", rules, "dump energy %.0f J, max temp %.0f C (thermal model is a guess)"
          % (w.phys.e_dump, w.phys.max_temp))
    assert "I1_charge_while_dumping" in rules and "I10_dump_overtemp" in rules
    assert w.phys.e_dump > 800


def test_legacy_damp_then_kick_glitches():
    # damp (30 kHz / 12 %) then a kick request mid-hold, as at RoboCup
    sched = [(6000, ID, _frame(2, 30000, 12)), (6400, ID, _frame(1, 1000, 0))]
    w = run_legacy(sched, 9.0, physics=dict(beam_zone=0.025))
    print("legacy damp->kick ->", _rules(w))
    assert w.violations                                   # documents whatever the old code does here
