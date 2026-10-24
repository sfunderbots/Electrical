"""Level 0: the v2 flowcharts in flowcharts/ match the firmware's transition tables exactly.

Re-arrange the charts in draw.io as much as you like; just keep each arrow's `transitions` property
(Edit Data) - if the code gains, loses or renames a transition, this test says which arrow to fix.
"""

import os
from sim.harness import make_cfg
from sim import drawio

make_cfg()
import charge_sm   # noqa: E402
import mode_sm     # noqa: E402


def _check(fname, mod):
    path = os.path.join(drawio.FLOW_DIR, fname)
    chart = drawio.chart_transitions(path)
    code = drawio.code_transitions(mod)
    missing = sorted(code - chart)
    extra = sorted(chart - code)
    assert not missing and not extra, "chart %s out of date.\n  missing arrows: %s\n  stale arrows: %s" % (
        fname, missing, extra)


def test_charge_chart_matches_code():
    _check("chicker-charge-state-machine-v2.drawio", charge_sm)


def test_mode_chart_matches_code():
    _check("chicker-mode-control-state-machine-v2.drawio", mode_sm)
