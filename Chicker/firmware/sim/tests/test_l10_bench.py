"""Level 10: the hardware bring-up procedure (bench/bring_up.py) rehearsed on the simulator.

The sweep that will run on the real board is itself tested here: it must pass on a healthy board, and
it must stop - and leave the board Idle - when the hardware is broken.

SKIPPED for now: bench/bring_up.py was written for the old firmware (charge ceiling, "kick now"). The ceiling is
deferred in the charge review (decide with the functional board tests and safe_chg, element 9), so the bring-up
procedure is rewritten after that decision. See docs/REVIEW-PLAN.md.
"""

_WHY = "bench/bring_up.py needs rewriting for the reviewed firmware (charge ceiling deferred, no 'kick now')"

import io
import contextlib
from sim.run_tests import slow, skip
from bench.link import SimLink
from bench.bring_up import BringUp, SimOperator


def _run(**sim_kwargs):
    link = SimLink(**sim_kwargs)
    bu = BringUp(link, SimOperator(link), max_voltage=150, skip_full=False)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        ok = bu.run()
    return ok, bu, link, out.getvalue()


@slow
@skip(_WHY)
def test_bring_up_passes_on_a_healthy_board():
    ok, bu, link, out = _run()
    assert ok, out[-2000:]
    assert link.sim.mode == "Idle" and link.sim.hv < 10
    link.sim.assert_safe()


@skip(_WHY)
def test_bring_up_stops_when_dump_resistors_are_open():
    ok, bu, link, out = _run(physics=dict(R_dump=1e9))
    assert not ok and "discharge path" in out
    assert link.sim.mode == "Idle" and link.sim.app.power.discharging
    assert "DO NOT TOUCH" in out                      # bank could not be confirmed empty
    link.sim.assert_safe()


@skip(_WHY)
def test_bring_up_stops_when_solenoid_is_open_circuit():
    ok, bu, link, out = _run(physics=dict(R_coil=1e6))
    assert not ok and "kick sag vs model" in out
    assert link.sim.mode == "Idle"
    link.sim.assert_safe()
