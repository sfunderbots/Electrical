"""Build the v2 state-machine flowcharts in Henry's draw.io style, and check them against the code.

    python -m sim.drawio          # (re)writes flowcharts/*-v2.drawio

Every arrow that stands for code transitions carries a `transitions` property ("From>To:why;...",
visible in draw.io under Edit Data). sim/tests/test_l0_flowcharts.py checks that the union of those
properties equals the TRANSITIONS tables in charge_sm.py / mode_sm.py, so a chart can be re-arranged
by hand freely but can never silently disagree with the firmware.
"""

import html
import os
import re
from xml.sax.saxutils import escape, quoteattr

FLOW_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "flowcharts")

# Henry's styles (copied from flowcharts/chicker-*-state-machine.drawio)
S_PAGE = "shape=process;whiteSpace=wrap;html=1;backgroundOutline=1;"
S_STATE = "ellipse;whiteSpace=wrap;html=1;"
S_DECISION = "ellipse;whiteSpace=wrap;html=1;aspect=fixed;"
S_ANY = "rounded=0;whiteSpace=wrap;html=1;"
S_BOX = "rounded=0;whiteSpace=wrap;html=1;"
S_FAULT = "rhombus;whiteSpace=wrap;html=1;fillColor=#FF701A;"
S_GUARD = ("swimlane;fontStyle=0;childLayout=stackLayout;horizontal=1;startSize=30;horizontalStack=0;"
           "resizeParent=1;resizeParentMax=0;resizeLast=0;collapsible=1;marginBottom=0;whiteSpace=wrap;html=1;")
S_GUARD_ROW = ("text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;spacingLeft=4;"
               "spacingRight=4;overflow=hidden;points=[[0,0.5],[1,0.5]];portConstraint=eastwest;"
               "rotatable=0;whiteSpace=wrap;html=1;")
S_TEXT = "text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;align=center;verticalAlign=middle;rounded=0;"
S_NOTE = "text;html=1;whiteSpace=wrap;strokeColor=none;fillColor=none;align=left;verticalAlign=top;rounded=0;"
S_RED_BOX = "rounded=0;whiteSpace=wrap;html=1;strokeColor=#FF3333;fillColor=none;"
S_HEX = "shape=hexagon;perimeter=hexagonPerimeter2;whiteSpace=wrap;html=1;fixedSize=1;fillColor=#FFF2CC;"
S_EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;"
S_DOTTED = "endArrow=none;dashed=1;html=1;dashPattern=1 3;strokeWidth=2;rounded=0;"
S_EDGE_LABEL = "edgeLabel;html=1;align=center;verticalAlign=middle;resizable=0;points=[];"


class Chart:
    def __init__(self, title, page_w, page_h):
        self.cells = []
        self.nodes = {}
        self.n = 0
        self.page_w, self.page_h = page_w, page_h
        self.vertex("page", "", S_PAGE, 40, 20, page_w, page_h)
        self.vertex("title", '<b><font style="font-size: 24px;">%s</font></b>' % escape(title),
                    S_TEXT, 40 + page_w / 2 - 330, 30, 660, 34)

    def _id(self):
        self.n += 1
        return "v2-%d" % self.n

    def vertex(self, key, label, style, x, y, w, h, parent="1"):
        cid = self._id()
        self.nodes[key] = (cid, x, y, w, h)
        self.cells.append('<mxCell id="%s" value=%s style=%s vertex="1" parent="%s">'
                          '<mxGeometry x="%g" y="%g" width="%g" height="%g" as="geometry" /></mxCell>'
                          % (cid, quoteattr(label), quoteattr(style), parent, x, y, w, h))
        return cid

    def state(self, key, label, x, y, w=100, h=64):
        return self.vertex(key, label, S_STATE, x, y, w, h)

    def any_state(self, key, x, y):
        return self.vertex(key, "ANY STATE", S_ANY, x, y, 90, 40)

    def guard(self, key, title, rows, x, y, w):
        gid = self.vertex(key, title, S_GUARD, x, y, w, 30 + 26 * len(rows))
        for i, r in enumerate(rows):
            self.vertex("%s_r%d" % (key, i), escape(r), S_GUARD_ROW, 0, 30 + 26 * i, w, 26, parent=gid)
        return gid

    def edge(self, src, dst, label="", transitions=(), points=(), exit=None, entry=None, dotted=False):
        sid, did = self.nodes[src][0], self.nodes[dst][0]
        style = S_DOTTED if dotted else S_EDGE
        if exit:
            style += "exitX=%g;exitY=%g;exitDx=0;exitDy=0;" % exit
        if entry:
            style += "entryX=%g;entryY=%g;entryDx=0;entryDy=0;" % entry
        eid = self._id()
        pts = "".join('<mxPoint x="%g" y="%g" />' % p for p in points)
        geo = ('<mxGeometry relative="1" as="geometry">%s</mxGeometry>'
               % ('<Array as="points">%s</Array>' % pts if pts else ""))
        cell = ('<mxCell style=%s edge="1" parent="1" source="%s" target="%s">%s</mxCell>'
                % (quoteattr(style), sid, did, geo))
        if transitions:
            self.cells.append('<object label="" transitions=%s id="%s">%s</object>'
                              % (quoteattr(";".join(transitions)), eid, cell))
        else:
            self.cells.append(cell.replace("<mxCell ", '<mxCell id="%s" ' % eid, 1))
        if label:
            lid = self._id()
            self.cells.append('<mxCell id="%s" value=%s style=%s vertex="1" connectable="0" parent="%s">'
                              '<mxGeometry x="-0.1" relative="1" as="geometry"><mxPoint as="offset" /></mxGeometry>'
                              '</mxCell>' % (lid, quoteattr(label), quoteattr(S_EDGE_LABEL), eid))
        return eid

    def xml(self):
        body = "\n        ".join(self.cells)
        return ('<mxfile host="app.diagrams.net">\n  <diagram name="Page-1" id="chicker-v2">\n'
                '    <mxGraphModel dx="1200" dy="900" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" '
                'arrows="1" fold="1" page="1" pageScale="1" pageWidth="%d" pageHeight="%d" math="0" shadow="0">\n'
                '      <root>\n        <mxCell id="0" />\n        <mxCell id="1" parent="0" />\n        %s\n'
                '      </root>\n    </mxGraphModel>\n  </diagram>\n</mxfile>\n'
                % (self.page_w + 80, self.page_h + 60, body))


def _t(rows):
    return tuple("%s>%s:%s" % r for r in rows)


# ============================================================================ charge machine
def charge_chart():
    c = Chart("Chicker Charge State Machine (v2)", 1340, 980)
    c.vertex("notes", (
        "<b>Owns:</b> the decision to run the LT3750. <b>Output:</b> &quot;want CHARGE high&quot; - the power stage "
        "only raises it once ~DISCHARGE has been released for 50 ms.<br>"
        "<b>Inputs (read-only, passed in by app.py):</b> allow = mode machine allows charging AND kicker idle AND "
        "&gt;= 100 ms since the last kick &nbsp;|&nbsp; battery OK (debounced, 14.7 V on / 14.3 V off) &nbsp;|&nbsp; "
        "DONE (high = switching) &nbsp;|&nbsp; HV (ADC) &nbsp;|&nbsp; bench ceiling.<br>"
        "Charging is driven by the <b>HV measurement</b>: top off below 205 V, stop at a ceiling, check HV really "
        "rises and that DONE falls at a plausible voltage. Timers are only backstops. "
        "<b>CHARGE is LOW in every state except Starting / Charging.</b>"), S_NOTE, 60, 70, 1300, 70)

    # left column: ANY STATE rules
    c.any_state("any_bat", 60, 165)
    c.state("NoBattery", "No Battery", 230, 153)
    c.state("BattSettle", "Battery Settle<div>(wait 2 s)</div>", 470, 153)
    c.any_state("any_inh", 60, 265)
    c.state("Inhibited", "Inhibited<div>(CHARGE low)</div>", 470, 253)

    # charge cycle
    c.state("Gap", "Charge Toggle<div>Reset (Gap)</div>", 230, 520, 110, 70)
    c.guard("g_start", "GUARD", ["CHARGE low >= 50 ms", "~DISCHARGE released >= 50 ms",
                                 ">= 500 ms since last cycle start"], 400, 495, 230)
    c.state("Starting", "Starting<div>(CHARGE high)</div>", 690, 520, 110, 70)
    c.vertex("d_start", "", S_DECISION, 860, 525, 60, 60)
    c.state("Charging", "Charging<div>(DONE high)</div>", 1000, 520, 110, 70)
    c.vertex("d_done", "", S_DECISION, 1180, 525, 60, 60)
    c.vertex("d_full", "", S_DECISION, 860, 660, 60, 60)
    c.state("Full", "Full<div>(CHARGE low,</div><div>LT3750 idle)</div>", 1150, 650, 110, 80)
    c.guard("g_fault", "GUARD - charging faults", ["DONE fell below 190 V (early done)",
                                                   "HV >= 220 V (not regulating)",
                                                   "HV +< 5 V in 1 s below 150 V (no progress)",
                                                   "cycle > 6 s (timeout)"], 960, 790, 290)
    c.vertex("d_fault", "count<div>faults</div>", S_FAULT, 690, 780, 90, 90)
    c.state("FaultWait", "Fault Wait<div>(retry in 30 s)</div>", 470, 790, 110, 70)
    c.any_state("any_ov", 60, 925)
    c.state("Locked", "Locked<div>(bank dumped,</div><div>report to Pi)</div>", 230, 905, 110, 80)
    c.vertex("ceil_note", "<font color=\"#CC0000\">bench ceiling: HV &gt;= ceiling -&gt; Full</font>",
             S_TEXT, 820, 455, 200, 30)

    # battery / inhibit / overvoltage rules
    c.edge("any_bat", "NoBattery", "battery not OK<div>/ pack dropped out mid-cycle</div>",
           ["ANY>NoBattery:batt_lost", "ANY>NoBattery:batt_dropped"])
    c.edge("NoBattery", "BattSettle", "battery OK", ["NoBattery>BattSettle:batt_ok"])
    c.edge("BattSettle", "Gap", "2 s &amp;&amp; allowed", ["BattSettle>Gap:settled"],
           exit=(1, 0.5), entry=(0.75, 0), points=[(660, 185), (660, 460), (312, 460)])
    c.edge("BattSettle", "Inhibited", "2 s &amp;&amp; not allowed", ["BattSettle>Inhibited:settled_inhibited"],
           exit=(0.5, 1), entry=(0.5, 0))
    c.edge("any_inh", "Inhibited", "mode disallows charging<div>(CHARGE low now, mid-cycle too)</div>",
           ["ANY>Inhibited:inhibit"])
    c.edge("Inhibited", "Gap", "allowed", ["Inhibited>Gap:allowed"],
           exit=(1, 0.5), entry=(0.25, 0), points=[(630, 285), (630, 440), (257, 440)])
    c.edge("any_ov", "Locked", "HV &gt;= 225 V", ["ANY>Locked:overvolt"])
    c.edge("Locked", "Inhibited", "MODE IDLE clears faults", ["Locked>Inhibited:cleared"],
           exit=(0, 0.25), entry=(0.35, 1), points=[(190, 925), (190, 350), (505, 350)])

    # one charge cycle
    c.edge("Gap", "g_start", "", exit=(1, 0.5), entry=(0, 0.5))
    c.edge("g_start", "Starting", "", ["Gap>Starting:start"], exit=(1, 0.5), entry=(0, 0.5))
    c.edge("Starting", "d_start", "after 2 ms")
    c.edge("d_start", "Charging", "DONE == 1", ["Starting>Charging:done_high"])
    c.edge("d_start", "d_full", "no DONE<div>in 200 ms</div>", exit=(0.5, 1), entry=(0.5, 0))
    c.edge("d_full", "Full", "HV &gt;= 206 V<div>(already full)</div>", ["Starting>Full:already_full"],
           exit=(1, 0.5), entry=(0, 0.5))
    c.edge("d_full", "d_fault", "HV &lt; 206 V<div>(NO_START)</div>",
           ["Starting>FaultWait:no_start", "Starting>Locked:no_start"],
           exit=(0.5, 1), entry=(0.5, 0), points=[(890, 745), (735, 745)])
    c.edge("Starting", "Full", "", ["Starting>Full:ceiling"], exit=(0.5, 0), entry=(1, 0.5),
           points=[(745, 490), (1310, 490), (1310, 690)])
    c.edge("Charging", "d_done", "DONE fell<div>(2 ms)</div>")
    c.edge("d_done", "Full", "HV &gt;= 190 V", ["Charging>Full:done"], exit=(0.5, 1), entry=(0.5, 0))
    c.edge("d_done", "g_fault", "HV &lt; 190 V", ["Charging>FaultWait:early_done", "Charging>Locked:early_done"],
           exit=(1, 0.5), entry=(1, 0.3), points=[(1285, 555), (1285, 830)])
    c.edge("Charging", "Full", "", ["Charging>Full:ceiling"], exit=(0.75, 1), entry=(0.25, 0),
           points=[(1082, 625), (1177, 625)])
    c.edge("Charging", "g_fault", "", exit=(0.5, 1), entry=(0.3, 0), points=[(1055, 770), (1047, 770)])
    c.edge("g_fault", "d_fault", "", [
        "Charging>FaultWait:no_progress", "Charging>FaultWait:no_regulation", "Charging>FaultWait:timeout",
        "Charging>Locked:no_progress", "Charging>Locked:no_regulation", "Charging>Locked:timeout"],
        exit=(0, 0.5), entry=(1, 0.5))
    c.edge("d_fault", "FaultWait", "&lt; 3 in a row", exit=(0, 0.5), entry=(1, 0.5))
    c.edge("d_fault", "Locked", "3 in a row", exit=(0.5, 1), entry=(1, 0.5), points=[(735, 945)])
    c.edge("Full", "Gap", "HV &lt; 205 V (or ceiling - 3 V): top off", ["Full>Gap:sagged"],
           exit=(0.5, 1), entry=(0.75, 1), points=[(1205, 760), (312, 760)])
    c.edge("FaultWait", "Gap", "30 s &amp;&amp; allowed", ["FaultWait>Gap:retry"],
           exit=(0, 0.5), entry=(0.25, 1), points=[(257, 825)])
    c.edge("FaultWait", "Inhibited", "30 s &amp;&amp; not allowed<div>/ MODE IDLE clears</div>",
           ["FaultWait>Inhibited:retry_inhibited", "FaultWait>Inhibited:cleared"],
           exit=(0.5, 1), entry=(0.65, 1), points=[(525, 885), (205, 885), (205, 365), (535, 365)])
    return c


# ============================================================================ mode machine
_DAMP = ("DampWaitHV", "DampPrep", "DampExtend", "DampHold")


def mode_chart():
    c = Chart("Chicker Mode Control State Machine (v2)", 1400, 1180)
    c.vertex("notes", (
        "<b>Owns:</b> what the board is doing for the robot. <b>Never</b> writes charge-machine or power-stage "
        "variables - it outputs <i>charge allowed</i>, <i>dump bank</i>, kicker requests and events; app.py passes "
        "them on.<br><b>Commands</b> (CAN protocol v2, or USB console - same decoder): MODE (IDLE / CHARGE), "
        "KICK (width, trigger beam / now, arm time), DAMP (freq, duty 0.01 %, hold, initial kick), HEARTBEAT 20 Hz. "
        "Legacy 0x2AA frames still accepted.<br><b>Kicks fire at any time</b>: charging is stopped first (5 ms quiet), "
        "never overlapping a charge cycle. Board boots Idle and only charges when commanded."),
        S_NOTE, 60, 70, 1360, 75)

    c.any_state("any_idle", 60, 170)
    c.any_state("any_hb", 60, 240)
    c.any_state("any_fault", 60, 310)
    c.state("Idle", "Idle<div>(no charging,</div><div>bank dumped,</div><div>kicker off)</div>", 560, 155, 120, 90)
    c.state("Fault", "Fault (latched)<div>bank dumped,</div><div>report to Pi</div>", 560, 290, 120, 80)
    c.vertex("abort", "abort kicker<div>stop PWM</div>", S_STATE, 330, 165, 100, 60)

    c.vertex("wait", "Wait for<div>Command</div>", S_STATE, 60, 430, 100, 64)
    c.vertex("parse", "Parse<div>(protocol.py)</div>", S_STATE, 250, 430, 100, 64)
    c.vertex("reject", "Rejected<div>EVENT to Pi</div>", S_STATE, 250, 540, 100, 64)
    c.vertex("hub", "Command<div>Received</div>", S_STATE, 450, 430, 100, 64)
    c.state("Ready", "Ready<div>(charging allowed)</div>", 700, 430, 120, 70)
    c.vertex("rej_note", "<font color=\"#CC0000\">KICK / DAMP in Idle -&gt; rejected<br>(send MODE CHARGE first)."
             "<br>Anything but MODE IDLE in Fault -&gt; rejected.</font>", S_TEXT, 60, 625, 290, 60)

    # kick row
    c.state("KickArmed", "Kick Armed<div>(charging allowed)</div>", 700, 650, 120, 70)
    c.state("KickPrep", "Kick Prep<div>(charging stopped)</div>", 930, 650, 120, 70)
    c.guard("g_fire", "GUARD", ["50 us <= PW <= 5000 us", "CHARGE low >= 5 ms", "kicker idle"], 1110, 640, 180)
    c.state("KickCooldown", "Kick Cooldown<div>100 ms</div>", 1150, 520, 120, 70)
    c.vertex("bb_box", "", S_RED_BOX, 600, 770, 560, 150)
    c.vertex("bb_title", "Break-beam Mini State Machine (hard IRQ)", S_TEXT, 600, 770, 260, 30)
    c.vertex("bb_irq", "Beam broken<div>(falling edge)</div>", S_HEX, 620, 820, 100, 60)
    c.vertex("bb_check", "fast_ok?<div>(armed, CHARGE low</div><div>>= 5 ms, kicker idle)</div>", S_STATE, 770, 810, 130, 80)
    c.vertex("bb_fire", "Fire pre-validated<div>kick on the PIO</div>", S_STATE, 950, 810, 120, 80)
    c.vertex("bb_no", "no: main loop<div>takes the beam</div>", S_TEXT, 760, 890, 150, 30)

    # damp row
    c.state("DampWaitHV", "Damp Wait HV<div>(charging allowed)</div>", 700, 960, 120, 70)
    c.state("DampPrep", "Damp Prep<div>(charging stopped)</div>", 880, 960, 120, 70)
    c.state("DampExtend", "Initial Kick<div>(800 us)</div>", 1060, 960, 120, 70)
    c.state("DampHold", "Damp Hold<div>PIO PWM</div>", 1240, 960, 120, 70)
    c.vertex("adj", "every 25 ms: HV adjustment -&gt; set duty<div>D = D0 * V_start / V (hold force),</div>"
             "<div>capped by gain 2x, max duty, power limit</div>", S_NOTE, 1080, 1060, 300, 60)
    c.vertex("wait_y", "<font color=\"#CC0000\">wait 4 ms (settle)</font>", S_TEXT, 1170, 935, 90, 20)

    # ANY STATE rules
    c.edge("any_idle", "abort", "MODE IDLE", ["ANY>Idle:cmd_idle"])
    c.edge("abort", "Idle", "dump bank,<div>clear faults</div>")
    c.edge("any_hb", "Idle", "no heartbeat 300 ms<div>(COMMS_LOST)</div>", ["ANY>Idle:comms_lost"],
           exit=(1, 0.5), entry=(0, 0.75), points=[(480, 260), (480, 223)])
    c.edge("any_fault", "Fault", "charge Locked /<div>kicker fault</div>",
           ["ANY>Fault:charge_locked", "ANY>Fault:kicker_fault"])
    c.edge("Idle", "hub", "", exit=(0, 1), entry=(0.5, 0), dotted=True)

    c.edge("wait", "parse", "incoming frame")
    c.edge("parse", "reject", "bad / out of range", exit=(0.5, 1), entry=(0.5, 0))
    c.edge("reject", "wait", "", exit=(0, 0.5), entry=(0.5, 1), points=[(110, 572)])
    c.edge("parse", "hub", "good frame")

    c.edge("hub", "Ready", "MODE CHARGE", [
        "Idle>Ready:cmd_charge", "KickArmed>Ready:cmd_charge", "KickPrep>Ready:cmd_charge"]
        + ["%s>Ready:cmd_charge" % s for s in _DAMP])
    c.edge("hub", "KickArmed", "KICK (trigger = beam)", [
        "Ready>KickArmed:cmd_kick_beam", "KickPrep>KickArmed:cmd_kick_beam"]
        + ["%s>KickArmed:cmd_kick_beam" % s for s in _DAMP], exit=(0.5, 1), entry=(0, 0.5),
        points=[(500, 685)])
    c.edge("hub", "KickPrep", "KICK (now / ball present)", [
        "Ready>KickPrep:cmd_kick_now", "KickArmed>KickPrep:cmd_kick_now"]
        + ["%s>KickPrep:cmd_kick_now" % s for s in _DAMP], exit=(0.25, 1), entry=(0.5, 1),
        points=[(475, 745), (990, 745)])
    c.edge("hub", "DampWaitHV", "DAMP", [
        "Ready>DampWaitHV:cmd_damp", "KickArmed>DampWaitHV:cmd_damp", "KickPrep>DampWaitHV:cmd_damp"],
        exit=(0, 1), entry=(0, 0.5), points=[(450, 995)])

    c.edge("KickArmed", "KickPrep", "beam broken<div>(main loop)</div>", ["KickArmed>KickPrep:beam"])
    c.edge("KickPrep", "g_fire", "stop charging,<div>quiet 5 ms</div>", exit=(1, 0.5), entry=(0, 0.5))
    c.edge("g_fire", "KickCooldown", "FIRE (any HV)", ["KickPrep>KickCooldown:fire"],
           exit=(0.5, 0), entry=(0.5, 1))
    c.edge("KickArmed", "Ready", "arm expired<div>(2 s default)</div>",
           ["KickArmed>Ready:expired", "KickPrep>Ready:expired"], exit=(0.25, 0), entry=(0.25, 1))
    c.edge("KickCooldown", "Ready", "100 ms &amp;&amp; pulse done", ["KickCooldown>Ready:cooled"],
           exit=(0, 0.5), entry=(1, 0.5))
    c.edge("KickCooldown", "KickArmed", "pending KICK<div>received in cooldown</div>", [
        "KickCooldown>KickArmed:pending_kick_beam", "KickCooldown>KickPrep:pending_kick_now"],
        exit=(0.5, 0), entry=(0.5, 0), points=[(1210, 500), (1320, 500), (1320, 630), (760, 630)])
    c.edge("KickCooldown", "DampWaitHV", "pending DAMP", ["KickCooldown>DampWaitHV:pending_damp"],
           exit=(1, 0.5), entry=(0.5, 0), points=[(1380, 555), (1380, 940), (760, 940)])

    c.edge("bb_irq", "bb_check", "")
    c.edge("bb_check", "bb_fire", "yes")
    c.edge("bb_fire", "KickCooldown", "kick flagged to<div>main loop</div>", ["KickArmed>KickCooldown:fast_fire"],
           exit=(1, 0.5), entry=(0.75, 1), points=[(1240, 850)])
    c.edge("KickArmed", "bb_check", "", dotted=True, exit=(0.5, 1), entry=(0.5, 0))

    c.edge("DampWaitHV", "DampPrep", "HV &gt;= 206 V", ["DampWaitHV>DampPrep:hv_ready"])
    c.edge("DampPrep", "DampExtend", "quiet 5 ms", ["DampPrep>DampExtend:fire_initial"])
    c.edge("DampExtend", "DampHold", "", ["DampExtend>DampHold:settled"])
    c.edge("DampWaitHV", "Ready", "HV not ready<div>in 5 s</div>", ["DampWaitHV>Ready:hv_timeout"],
           exit=(0, 0.25), entry=(0.15, 1), points=[(580, 977), (580, 520), (718, 520)])
    c.edge("DampHold", "Ready", "hold time elapsed<div>/ HV &lt; 60 V</div>",
           ["DampHold>Ready:hold_done", "DampHold>Ready:hv_floor"],
           exit=(0.5, 0), entry=(1, 0.25), points=[(1300, 470), (870, 470)])
    return c


def write_all():
    out = {}
    for name, chart in (("chicker-charge-state-machine-v2.drawio", charge_chart()),
                        ("chicker-mode-control-state-machine-v2.drawio", mode_chart())):
        path = os.path.join(FLOW_DIR, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(chart.xml())
        out[name] = path
    return out


def chart_transitions(path):
    """Set of 'From>To:why' strings declared on the arrows of a .drawio file."""
    x = open(path, encoding="utf-8").read()
    found = set()
    for m in re.finditer(r'transitions="([^"]*)"', x):
        found.update(t for t in html.unescape(m.group(1)).split(";") if t)
    return found


def code_transitions(mod):
    out = set()
    for frm, to, why, _ in mod.TRANSITIONS:
        out.add("%s>%s:%s" % ("ANY" if frm == -1 else mod.NAMES[frm], mod.NAMES[to], why))
    return out


if __name__ == "__main__":
    for k, v in write_all().items():
        print("wrote", v)
