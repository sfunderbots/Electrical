"""Checks the impact detector on synthetic bank-voltage traces, before any hardware is involved.

    python test_impact_detector.py            (plain Python 3, no extra packages)

What is modelled (and what isn't):
  * the bank (2 mF) discharged by a steady hold current, plus an impact: a short extra (or missing) current
  * the real HV sense chain from the schematic: divider 990k / 13k (x77.15), RC filter ~128 us, 12-bit ADC at 3.3 V,
    with RP2040-like ADC noise (~2.8 counts rms, about 9 effective bits)
  * DMA capture at 50 kS/s, averaged in blocks of 20 -> 2.5 kS/s into the detector
  NOT modelled: the solenoid. The size and shape of a real impact are unknown until measured on the bench, so the
  impact here is a simple half-sine current pulse of a chosen size. These tests check the DETECTOR (no false
  triggers, catches pulses of a given size, separates bounces); the bench tells us how big real impacts are.
"""

import math
import random
from impact_detector import ImpactDetector, decimate

C_BANK = 0.002
DIVIDER = (13.0 + 990.0) / 13.0
ADC_FS = 50000           # ADC samples per second (DMA capture, as bench_impact_capture.py)
DECIMATE = 20            # -> 2500 samples per second into the detector
RC_TAU = 128e-6          # HV sense filter (10 nF across the 13k leg)
ADC_NOISE_COUNTS = 2.8   # rms, RP2040 ADC (~9 effective bits)


def synth(seconds, v0=200.0, i_hold=0.1, impacts=(), drift_a_per_s=0.0, step=None, seed=0):
    """Bank voltage as the ADC sees it, in volts, at ADC_FS. impacts = [(t_start_s, delta_amps, duration_s)],
    step = (t_s, delta_amps): the hold current changes level for good (like a duty change)."""
    rng = random.Random(seed)
    dt = 1.0 / ADC_FS
    v = v0
    vf = v0
    a = 1.0 - math.exp(-dt / RC_TAU)
    out = []
    for k in range(int(seconds * ADC_FS)):
        t = k * dt
        i = i_hold + drift_a_per_s * t
        if step is not None and t >= step[0]:
            i += step[1]
        for t0, di, dur in impacts:
            if t0 <= t < t0 + dur:
                i += di * math.sin(math.pi * (t - t0) / dur)
        v -= i * dt / C_BANK
        vf += (v - vf) * a
        counts = vf / DIVIDER / 3.3 * 4095 + rng.gauss(0.0, ADC_NOISE_COUNTS)
        counts = min(max(int(round(counts)), 0), 4095)
        out.append(counts * 3.3 / 4095 * DIVIDER)
    return out


def detect(trace):
    det = ImpactDetector(sample_rate_hz=ADC_FS / DECIMATE)
    events = []
    for v in decimate(trace, DECIMATE):
        ev = det.feed(v)
        if ev:
            events.append((ev[0] * DECIMATE / ADC_FS, ev[1]))      # (time s, step V)
    return events


def test_no_ball_no_false_triggers():
    for seed in range(15):
        ev = detect(synth(1.0, seed=seed))
        assert not ev, (seed, ev)


def test_slow_drift_is_not_an_impact():
    # the hold current creeping up (e.g. the HV adjustment raising the duty) must not look like a ball
    for seed in range(5):
        ev = detect(synth(1.0, drift_a_per_s=0.1, seed=seed))
        assert not ev, (seed, ev)


def test_a_lasting_step_is_one_event_at_most_then_relearned():
    # the hold current jumping to a new level (a duty step) may look like a hit once, never as a stream of events
    for seed in range(5):
        ev = detect(synth(1.0, step=(0.4, 0.2), seed=300 + seed))
        assert len(ev) <= 1, (seed, ev)


def test_impact_dip_is_caught_quickly():
    for seed in range(15):
        t0 = 0.3 + 0.03 * seed
        ev = detect(synth(0.8, impacts=[(t0, -0.2, 0.003)], seed=seed))
        assert len(ev) == 1, (seed, ev)
        assert 0 <= ev[0][0] - t0 < 0.010, (seed, ev[0][0] - t0)     # within 10 ms of the hit starting
        assert ev[0][1] > 0                                          # less current drawn -> voltage steps UP


def test_impact_bump_is_caught_too():
    # the sign of a real impact is unknown until measured: the detector works both ways
    for seed in range(10):
        ev = detect(synth(0.8, impacts=[(0.4, +0.2, 0.003)], seed=100 + seed))
        assert len(ev) == 1 and ev[0][1] < 0, (seed, ev)            # more current drawn -> voltage steps DOWN


def test_bounce_gives_two_events():
    ev = detect(synth(1.0, impacts=[(0.3, -0.15, 0.003), (0.38, -0.15, 0.003)], seed=7))
    assert len(ev) == 2, ev


def report_sensitivity():
    """Not a test: how small an impact the detector catches reliably with this noise level."""
    print("impact size -> caught in 20 runs (3 ms pulse, 200 V bank, 0.1 A hold):")
    for di in (0.03, 0.05, 0.08, 0.1, 0.15, 0.2):
        hits = sum(1 for seed in range(20) if len(detect(synth(0.6, impacts=[(0.3, -di, 0.003)], seed=200 + seed))) == 1)
        print("  %.2f A: %2d / 20" % (di, hits))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("PASS", name)
    report_sensitivity()
