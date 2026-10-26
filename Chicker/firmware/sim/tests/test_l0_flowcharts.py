"""Level 0: every state in the code has a box with the same name on Henry's updated charts (flowcharts/*-v2.drawio).

The charts are drawn by hand, so this only checks names, not arrows: if a state is renamed or added in the code but
not on the chart (or the other way round), this test says which one. The arrows themselves are checked against the
review documents by the other test levels.
"""

import html
import os
import re
from sim.harness import make_cfg

make_cfg()
import charge_sm   # noqa: E402
import mode_sm     # noqa: E402

FLOW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "flowcharts")

# Code state -> the box text on the chart, where they differ (the chart reuses a box name on several paths).
MODE_BOX = {
    "StartBKBM": "Start BKBM interrupt",
    "WaitForKick": "wait for kick",
    "Locked": "Locked",
    "AutokickEnableCharging": "Enable Charging",
    "DampEnableCharging": "Enable Charging",
    "DampChargeHold": "Charge Hold",
    "DampWait": "Wait",
    "DampStopPWM": "Stop PWM",
}
CHARGE_BOX = {
    "Startup": "Startup (reset vars)",
    "ChargedTo50V": "Charged to ~50V",
    "Disabled": "Disabled",
}


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _box_texts(fname):
    xml = open(os.path.join(FLOW_DIR, fname), encoding="utf-8").read()
    texts = []
    for m in re.finditer(r'<mxCell ([^>]*?)/?>', xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        if attrs.get("vertex") == "1" and attrs.get("value"):
            raw = html.unescape(html.unescape(attrs["value"]))
            texts.append(_norm(re.sub(r"<[^>]+>", " ", raw)))
    return texts


def _check(fname, names, box_for):
    texts = _box_texts(fname)
    missing = []
    for name in names:
        want = _norm(box_for.get(name, name))
        if not any(t == want or t.startswith(want) for t in texts):
            missing.append("%s (chart box '%s')" % (name, box_for.get(name, name)))
    assert not missing, "%s has no box for: %s" % (fname, ", ".join(missing))


def test_every_charge_state_is_on_the_charge_chart():
    _check("chicker-charge-state-machine-v2.drawio", charge_sm.NAMES, CHARGE_BOX)


def test_every_mode_state_is_on_the_mode_chart():
    _check("chicker-mode-control-state-machine-v2.drawio", mode_sm.NAMES, MODE_BOX)
