# Pure maths for the kick / damping driver: PIO command words, PWM timing, damping duty law and
# hold-power limit. No hardware access, so every limit here is unit-tested in the simulator.
#
# PIO program (kicker.py) runs "segments" pulled from the FIFO as three words:
#   [n_periods - 1, high_cycles - 2, low_cycles - 3]
# High time is exactly high_cycles, low time exactly low_cycles (the constants are the
# instructions spent setting up each phase). One cycle = 1 / sys clock = 8 ns at 125 MHz.

HIGH_OVERHEAD = 2
LOW_OVERHEAD = 3


def cycles_per_us(sys_hz):
    if sys_hz % 1000000:
        raise ValueError("sys clock must be a whole number of MHz")
    return sys_hz // 1000000


def pulse_words(cfg, cyc_per_us, width_us):
    """Single kick pulse of exactly width_us. Raises ValueError outside the hard limits."""
    if not (cfg.KICK_MIN_US <= width_us <= cfg.KICK_MAX_US):
        raise ValueError("kick width out of range")
    high = int(width_us) * cyc_per_us
    low = max(cfg.KICK_TAIL_LOW_US * cyc_per_us, LOW_OVERHEAD)
    return (0, high - HIGH_OVERHEAD, low - LOW_OVERHEAD)


def pwm_cycles(cfg, cyc_per_us, freq_hz, duty_pct):
    """(high, low) cycles for one PWM period. Duty and on-time are clamped to the hard limits."""
    if not (cfg.DAMP_FREQ_MIN <= freq_hz <= cfg.DAMP_FREQ_MAX):
        raise ValueError("damp frequency out of range")
    period = (cyc_per_us * 1000000 + freq_hz // 2) // freq_hz
    duty = min(max(duty_pct, 0.0), cfg.DAMP_DUTY_MAX)    # only ever clamps down (power cap may go below DAMP_DUTY_MIN)
    high = int(period * duty / 100.0 + 0.5)
    high = min(high, cfg.DAMP_MAX_ON_US * cyc_per_us, period - LOW_OVERHEAD)
    high = max(high, HIGH_OVERHEAD)
    return high, period - high


def periods_in(cyc_per_us, high, low, duration_us):
    period = high + low
    return max(1, (duration_us * cyc_per_us + period // 2) // period)


def segment_words(n_periods, high, low):
    return (n_periods - 1, high - HIGH_OVERHEAD, low - LOW_OVERHEAD)


def hold_power_w(cfg, duty_pct, hv):
    """Predicted bank power during a PWM hold: coil current I = D*V/R, bank current D*I."""
    dv = duty_pct / 100.0 * hv
    return dv * dv / cfg.DAMP_R_EFF_OHM


def max_duty_for_power(cfg, hv):
    if hv <= 0:
        return cfg.DAMP_DUTY_MAX
    return 100.0 * (cfg.DAMP_MAX_HOLD_POWER_W * cfg.DAMP_R_EFF_OHM) ** 0.5 / hv


def damp_duty(cfg, base_pct, v_start, v_now, t_hold_ms):
    """Duty for the next PWM segment. Returns (duty_pct, limited).

    Mode 1 (hold force): D = D0 * V_start / V. Coil current ~ D*V/R and force ~ I^2, so D*V
    constant keeps the force at the tuned value as the bank drains, and never above it.
    Mode 2 reproduces the RoboCup 2026 formula (including its whole-% truncation).
    Mode 0 holds D0. limited = a cap (gain, duty or power) cut the result.
    """
    mode = cfg.DAMP_COMP_MODE
    limited = False
    if mode == 1:
        gain = v_start / max(v_now, 1.0)
        if gain > cfg.DAMP_COMP_MAX_GAIN:
            gain = cfg.DAMP_COMP_MAX_GAIN
            limited = True
        d = base_pct * gain
    elif mode == 2:
        d = int(base_pct * (cfg.DAMP_LEGACY_NOMINAL_V / max(v_now, cfg.DAMP_MIN_V)) ** cfg.DAMP_LEGACY_EXP)
        d = max(base_pct, d)
    else:
        d = base_pct
    if cfg.DAMP_CATCH_MS and t_hold_ms < cfg.DAMP_CATCH_MS:
        d += cfg.DAMP_CATCH_BOOST
    if d > cfg.DAMP_DUTY_MAX:
        d = cfg.DAMP_DUTY_MAX
        limited = True
    if d < cfg.DAMP_DUTY_MIN:
        d = cfg.DAMP_DUTY_MIN
    p_cap = max_duty_for_power(cfg, v_now)   # applied last: the power limit always wins
    if d > p_cap:
        d = p_cap
        limited = True
    return d, limited
