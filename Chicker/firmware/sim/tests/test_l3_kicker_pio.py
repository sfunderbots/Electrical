"""Level 3: the PIO kick/PWM driver running the real PIO program on the emulator.

These drive the kicker directly (no HV, like a bench dry fire), so the "kick while Idle" rule is
relaxed for this file; every other invariant stays on.
"""

import random
from sim.harness import Sim
from sim import world as W
import kicker as K    # noqa: E402

CYC_NS = 8


def _dry_sim(**cfg):
    s = Sim(**cfg)
    s.w.allow_kick_in_idle = True
    return s


def _rising_edges(s, pin=W.PIN_KICK):
    return [t for t, n, lv in s.w.pin_events if n == pin and lv]


def _highs(s):
    out, t_hi = [], None
    for t, n, lv in s.w.pin_events:
        if n != W.PIN_KICK:
            continue
        if lv:
            t_hi = t
        elif t_hi is not None:
            out.append((t_hi, t - t_hi))
            t_hi = None
    return out


def test_selftest_and_program_layout():
    s = _dry_sim()
    k = s.board.kicker
    assert k.ok, k.selftest_msg
    emu = s.w.sms[0]
    assert k._idle_pc == emu.offset == 32 - len(emu.prog.instrs)
    assert not emu.prog.sideset_opt            # every instruction drives the pin
    assert k.idle() and k.pin_level() == 0
    assert s.w.kicker_owned_from is not None


def test_pulse_widths_are_exact():
    s = _dry_sim()
    k = s.board.kicker
    rng = random.Random(4)
    widths = [100, 101, 500, 800, 1000, 4999, 5000] + [rng.randrange(100, 5001) for _ in range(20)]
    for w in widths:
        assert k.fire(w)
        s.run(7)
        assert k.idle()
    got = [d for _, d in _highs(s)]
    assert got == [w * 1000 for w in widths], got[:5]
    s.assert_safe()


def test_fire_refused_while_busy_and_out_of_range():
    s = _dry_sim()
    k = s.board.kicker
    assert k.fire(3000)
    assert not k.fire(1000)                    # busy: refused, not queued
    s.run(10)
    try:
        k.fire(6000)
    except ValueError:
        pass
    else:
        raise AssertionError("6000 us accepted")
    assert len(_highs(s)) == 1


def test_pwm_waveform_matches_plan_and_is_continuous():
    s = _dry_sim()
    k = s.board.kicker
    f, duty, dur_ms = 30000, 12.0, 100
    hi, lo = K.pwm_cycles(s.cfg, 125, f, duty)
    assert k.start_pwm(f, duty, dur_ms * 1000)
    s.run(dur_ms + 30)
    highs = _highs(s)
    assert len(highs) == K.periods_in(125, hi, lo, dur_ms * 1000)
    assert all(d == hi * CYC_NS for _, d in highs)
    rises = [t for t, _ in highs]
    gaps = set(b - a for a, b in zip(rises, rises[1:]))
    period = (hi + lo) * CYC_NS
    assert gaps <= {period, period + 5 * CYC_NS}, gaps       # only the 5-instruction segment reload
    assert k.underruns == 0 and not k.pwm_running
    s.assert_safe()


def test_duty_updates_land_on_period_boundaries():
    s = _dry_sim()
    k = s.board.kicker
    k.start_pwm(30000, 10.0, 200_000)
    widths = set()
    for d in (10.0, 14.5, 7.25, 20.0, 12.0):
        k.set_duty(d)
        widths.add(K.pwm_cycles(s.cfg, 125, 30000, d)[0] * CYC_NS)
        s.run(37)
    s.run(60)
    got = set(d for _, d in _highs(s))
    assert got <= widths, got - widths                    # never a truncated / stretched pulse


def test_abort_at_random_instants_forces_low_immediately():
    rng = random.Random(5)
    s = _dry_sim()
    k = s.board.kicker
    for i in range(150):
        if i % 2:
            k.fire(rng.randrange(200, 5000))
        else:
            k.start_pwm(rng.choice((5000, 20000, 30000)), rng.uniform(2, 40), 50_000)
        s.w.advance(rng.randrange(1_000, 3_000_000))   # land anywhere inside the pulse / hold
        t_abort = s.w.now
        assert k.abort()
        assert s.w.level(W.PIN_KICK) == 0
        last_edge = [e for e in s.w.pin_events if e[1] == W.PIN_KICK][-1]
        assert last_edge[2] == 0 and last_edge[0] <= t_abort + 3 * CYC_NS + 50_000
        assert k.idle() and s.w.sms[0].tx_fifo() == 0
        s.w.advance(2_000_000)
        assert not [e for e in s.w.pin_events if e[1] == W.PIN_KICK and e[0] > t_abort + 50_000 and e[2]]
    s.assert_safe()                                        # includes: KICK pin never left the PIO


def test_stalled_main_loop_fails_low():
    s = _dry_sim()
    k = s.board.kicker
    t0 = s.w.now
    k.start_pwm(30000, 20.0, 3_000_000)
    s.w.advance(500 * W.NS_PER_MS)                        # firmware hung: nobody services the FIFO
    assert s.w.level(W.PIN_KICK) == 0
    highs = _highs(s)
    last = highs[-1][0] + highs[-1][1] - t0
    assert last < 2 * s.cfg.DAMP_SEGMENT_MS * W.NS_PER_MS + 5 * W.NS_PER_MS   # running + one queued segment
    assert max(d for _, d in highs) <= s.cfg.DAMP_MAX_ON_US * 1000
    s.assert_safe()


def test_led_dry_run_pin_keeps_real_kick_low():
    s = _dry_sim(KICK_OUTPUT_PIN=6)
    k = s.board.kicker
    assert k.ok
    k.fire(2000)
    s.run(5)
    assert _rising_edges(s, W.PIN_KICK) == []
    assert s.w.level(W.PIN_KICK) == 0
    assert s.w.sms[0].pin == 6
