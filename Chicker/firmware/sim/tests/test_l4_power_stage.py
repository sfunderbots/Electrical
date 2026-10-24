"""Level 4: CHARGE / ~DISCHARGE interlock (the smoking-resistor class of bug)."""

import random
from sim.harness import Sim
from sim import world as W
from power_stage import PowerStageError


def test_random_requests_never_break_the_interlock():
    rng = random.Random(6)
    s = Sim()
    pw = s.app.power
    import utime
    for _ in range(3000):
        want_c = rng.random() < 0.6
        want_d = rng.random() < 0.3
        for _ in range(rng.randrange(1, 40)):
            pw.apply(want_c, want_d, utime.ticks_ms())
            s.w.advance(rng.choice((100_000, 1_000_000, 5_000_000)))
    assert not s.violations, s.violations[:3]
    assert any(n == W.PIN_CHARGE and lv for _, n, lv in s.w.pin_events)   # it really did charge
    assert any(n == W.PIN_NDIS and not lv for _, n, lv in s.w.pin_events[2:])


def test_dead_time_both_ways():
    s = Sim()
    pw = s.app.power
    import utime
    pw.apply(True, False, utime.ticks_ms())
    assert not pw.charge_on and not pw.discharging       # dump released, charge must wait
    s.w.advance(49 * W.NS_PER_MS)
    pw.apply(True, False, utime.ticks_ms())
    assert not pw.charge_on
    s.w.advance(2 * W.NS_PER_MS)
    pw.apply(True, False, utime.ticks_ms())
    assert pw.charge_on
    pw.apply(False, True, utime.ticks_ms())
    assert not pw.charge_on and not pw.discharging        # charge off now, dump waits
    s.w.advance(51 * W.NS_PER_MS)
    pw.apply(False, True, utime.ticks_ms())
    assert pw.discharging
    s.assert_safe()


def test_readback_mismatch_goes_safe_and_raises():
    s = Sim()
    pw = s.app.power
    import utime
    s.w.advance(60 * W.NS_PER_MS)
    pw.apply(True, False, utime.ticks_ms())
    s.w.advance(60 * W.NS_PER_MS)
    real = pw._chg

    class Stuck:                      # CHARGE pin that never goes high (broken trace / pin)
        def value(self, v=None):
            return 0 if v is None else real.value(v)
    pw._chg = Stuck()
    try:
        pw.apply(True, False, utime.ticks_ms())
    except PowerStageError:
        pass
    else:
        raise AssertionError("mismatch not detected")
    assert not pw.charge_on and pw.discharging and s.w.level(W.PIN_CHARGE) == 0


def test_boot_state_is_reset_state():
    s = Sim()
    assert s.w.level(W.PIN_CHARGE) == 0 and s.w.level(W.PIN_NDIS) == 0 and s.w.level(W.PIN_KICK) == 0
    assert s.mode == "Idle"
    s.run(5000)                        # nothing charges without a command
    assert not any(n == W.PIN_CHARGE and lv for _, n, lv in s.w.pin_events)
    assert s.hv < 1.0
