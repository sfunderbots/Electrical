"""Level 8: randomized soak - random commands, ball events, battery and LT3750 faults, all invariants on."""

import random
from sim.harness import Sim
from sim.run_tests import slow
import protocol as P


def _soak(seed, seconds):
    rng = random.Random(seed)
    s = Sim(seed=seed, physics=dict(R_bleed=rng.choice((1.003e6, 60e3))))
    if rng.random() < 0.5:
        s.heartbeat_ms = 50
    end = s.w.now + seconds * 1_000_000_000
    actions = [
        (8, lambda: s.charge()),
        (2, lambda: s.mode_idle()),
        (6, lambda: s.kick(rng.choice((300, 800, 1000, 2500, 5000, 6000, 0)), now=rng.random() < 0.4,
                           arm_ms=rng.choice((0, 300, 2000)))),
        (5, lambda: s.damp(rng.choice((20000, 30000, 37000, 500)), rng.choice((2.0, 6.0, 12.0, 30.0, 99.0)),
                           hold_ms=rng.choice((0, 100, 800, 3000)), init_kick_us=rng.choice((0, 500, 800)))),
        (2, lambda: s.legacy(rng.randrange(0, 5), rng.choice((0, 800, 30000)), rng.randrange(0, 120))),
        (1, lambda: s.send(rng.choice((0x2A0, 0x2A1, 0x2A2, 0x2A4, 0x2AA)),
                           bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9))))),
        (4, lambda: s.place_ball(rng.uniform(-0.002, 0.02), rng.uniform(-3.0, 0.0))),
        (3, lambda: s.remove_ball()),
        (1, lambda: s.set_battery(present=rng.random() < 0.8)),
        (1, lambda: s.set_battery(volts=rng.choice((13.5, 14.6, 15.2, 16.4)))),
        (1, lambda: s.lt_fault(rng.choice(("ok", "ok", "ok", "uvlo", "early_done", "slow", "done_stuck_low")))),
        (1, lambda: s.usb(rng.choice(("ceil 40", "ceil off", "status", "tlm 0", "tlm 100")))),
        (1, lambda: setattr(s, "heartbeat_ms", rng.choice((0, 50)))),
    ]
    weights = [w for w, _ in actions]
    while s.w.now < end:
        rng.choices(actions, weights)[0][1]()
        s.run(rng.choice((5, 20, 100, 300, 1000, 2500)))
        assert not s.violations, (seed, s.violations[:3], s.transitions[-6:])
        assert s.crashed is None, (seed, s.crashed)
    return s


def test_soak_short_seeds():
    for seed in range(4):
        _soak(seed, 20)


@slow
def test_soak_long():
    for seed in range(100, 106):
        _soak(seed, 120)
