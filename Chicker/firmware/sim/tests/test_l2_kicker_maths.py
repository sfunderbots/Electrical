"""Level 2: kick / PWM encoding and the damping duty law + power limit (pure code)."""

import random
from sim.harness import make_cfg

cfg = make_cfg()
import kicker as K    # noqa: E402

CYC = 125              # 125 MHz


def test_pulse_words_exact_and_limited():
    for w in (cfg.KICK_MIN_US, 500, 800, 1000, 2500, cfg.KICK_MAX_US):
        n, hi, lo = K.pulse_words(cfg, CYC, w)
        assert n == 0 and hi + K.HIGH_OVERHEAD == w * CYC and lo >= 0
    for bad in (0, cfg.KICK_MIN_US - 1, cfg.KICK_MAX_US + 1, 65535):
        try:
            K.pulse_words(cfg, CYC, bad)
        except ValueError:
            continue
        raise AssertionError("width %d accepted" % bad)


def test_pwm_grid_accuracy_and_caps():
    for f in (1000, 5000, 10000, 20000, 25000, 30000, 37000, 40000):
        period = round(CYC * 1e6 / f)
        for duty in (0.5, 1, 2.5, 5, 8, 12, 12.34, 20, 35, 50, 80, 95):
            hi, lo = K.pwm_cycles(cfg, CYC, f, duty)
            assert hi + lo == period
            assert hi <= cfg.DAMP_MAX_ON_US * CYC, (f, duty)
            ideal = period * duty / 100
            if ideal <= cfg.DAMP_MAX_ON_US * CYC and ideal <= period - 3:
                assert abs(hi - ideal) <= 0.5 + 1e-9, (f, duty, hi, ideal)   # within one 8 ns cycle
            n, h2, l2 = K.segment_words(10, hi, lo)
            assert n == 9 and h2 >= 0 and l2 >= 0


def test_pwm_resolution_beats_whole_percent():
    # at 30 kHz the PIO can move duty in ~0.024 % steps; 1 % steps were ~17 % force steps at 12 %
    a = K.pwm_cycles(cfg, CYC, 30000, 12.00)[0]
    b = K.pwm_cycles(cfg, CYC, 30000, 12.03)[0]
    assert b - a == 1


def test_pwm_frequency_limits():
    for bad in (cfg.DAMP_FREQ_MIN - 1, cfg.DAMP_FREQ_MAX + 1, 0):
        try:
            K.pwm_cycles(cfg, CYC, bad, 10)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_hold_force_law_keeps_dv_constant_and_never_exceeds_start():
    # mode 1 (hold force) is kept as an option; the firmware runs mode 2 (RoboCup formula, mode note DMP-3)
    cfg = make_cfg(DAMP_COMP_MODE=1)
    rng = random.Random(2)
    for _ in range(5000):
        d0 = rng.uniform(cfg.DAMP_DUTY_MIN, 30)
        v0 = rng.uniform(150, 215)
        v = rng.uniform(1, v0)
        d, lim = K.damp_duty(cfg, d0, v0, v, 500)
        assert d * v <= d0 * v0 * 1.000001 or d == cfg.DAMP_DUTY_MIN, (d0, v0, v, d)
        if not lim:
            assert abs(d * v - d0 * v0) < 1e-6 * d0 * v0
        assert d <= cfg.DAMP_DUTY_MAX


def test_power_limit_always_wins():
    c = make_cfg(DAMP_MAX_HOLD_POWER_W=50.0)
    rng = random.Random(3)
    for _ in range(5000):
        d0 = rng.uniform(0.5, 60)
        v0 = rng.uniform(100, 215)
        v = rng.uniform(5, v0)
        d, _ = K.damp_duty(c, d0, v0, v, rng.uniform(0, 3000))
        assert K.hold_power_w(c, d, v) <= c.DAMP_MAX_HOLD_POWER_W * 1.0001, (d0, v, d)


def test_gain_cap():
    cfg = make_cfg(DAMP_COMP_MODE=1)
    d, lim = K.damp_duty(cfg, 10.0, 200.0, 50.0, 500)
    assert lim and abs(d - 10.0 * cfg.DAMP_COMP_MAX_GAIN) < 1e-9


def test_legacy_law_reproduces_robocup_formula():
    c = make_cfg(DAMP_COMP_MODE=2, DAMP_MAX_HOLD_POWER_W=1e9)
    for duty in (5, 12, 20):
        for hv in (60, 120, 150, 180, 206, 210):
            want = min(c.DAMP_DUTY_MAX, max(duty, int(duty * ((210 / max(hv, 60)) ** 0.1))))
            assert K.damp_duty(c, duty, 206, hv, 500)[0] == want
    # the finding: truncation made it a no-op for typical duties
    assert K.damp_duty(c, 12, 206, 150, 500)[0] == 12


def test_catch_boost_only_during_catch_window():
    c = make_cfg(DAMP_CATCH_MS=120, DAMP_COMP_MODE=0, DAMP_MAX_HOLD_POWER_W=1e9)
    assert K.damp_duty(c, 10, 200, 200, 50)[0] == 10 + c.DAMP_CATCH_BOOST
    assert K.damp_duty(c, 10, 200, 200, 150)[0] == 10


def test_predicted_hold_power_matches_robocup_operating_point():
    # documents the model's prediction for the RoboCup setting (see PLAN D6b)
    p = K.hold_power_w(cfg, 12.0, 210.0)
    assert 300 < p < 450, p
