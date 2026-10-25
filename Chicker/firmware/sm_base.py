# Shared engine for the chicker state machines (charge_sm.py, mode_sm.py).
#
# A state machine built on this is a TRANSITION TABLE: every arrow on the flowchart is one row
#
#     (from_states, condition, to_state, why, chart_label)
#
#   from_states  a tuple of states, or AnyState(...) for an "ANY STATE" box on the chart
#   condition    the NAME of a method on the machine that returns True when the arrow should be taken
#                (it only reads inputs and timers, it never changes anything)
#   to_state     where the arrow goes
#   why          short key naming the arrow (used in telemetry, test coverage and the flowchart check)
#   chart_label  the text on the arrow, in the chart's own notation (e.g. "chg_ok == 1")
#
# Every pass, the engine:
#   1. runs the current state's "during" bookkeeping, if it has any (e.g. the damp loop's HV adjustment);
#   2. walks the table top to bottom and takes the FIRST row whose from_states contains the current
#      state and whose condition is true (the subclass's _take() runs that arrow's actions);
#   3. runs the entry action of the new state (_on_enter).
# Row order is the priority order: the ANY STATE rows are at the top of each table.
#
# _go() refuses any move that is not a row in the table (TransitionError -> main.py safe state).

import utime

ANY = -1   # "from any state" in TRANSITIONS (what the flowchart check and coverage report see)


class AnyState:
    """from_states for an ANY STATE box: every state except the listed ones."""

    def __init__(self, *except_states):
        self.except_states = except_states

    def __contains__(self, state):
        return state not in self.except_states


def expand(arrows):
    """TRANSITIONS rows (from, to, why, label) from a table; AnyState rows become from = ANY."""
    rows = []
    for frm, _cond, to, why, label in arrows:
        if isinstance(frm, AnyState):
            rows.append((ANY, to, why, label))
        else:
            for f in frm:
                rows.append((f, to, why, label))
    return tuple(rows)


class TransitionError(Exception):
    pass


class StateMachine:
    def __init__(self, names, arrows, initial, now_ms):
        self.names = names
        self.state = initial
        self.t_state = now_ms            # when the current state was entered
        self.last_why = "init"           # key of the last arrow taken (shown in telemetry)
        self.on_transition = None        # optional hook(sm, frm, to, why) - the simulator uses it
        self._during = {}                # {STATE: method} per-state bookkeeping, filled by the subclass
        # resolve each row's condition name to the bound method once (no lookups in the loop)
        self._arrows = tuple((frm, getattr(self, cond), to, why) for frm, cond, to, why, _ in arrows)
        allowed = set()
        for frm, to, why, _ in expand(arrows):
            allowed.add((frm, to, why))
        self._allowed = allowed

    # ------------------------------------------------------------------ engine
    def run(self, now_ms, inp, max_moves=1):
        """One pass. max_moves > 1 lets a pass follow a short chain of arrows."""
        for _ in range(max_moves):
            during = self._during.get(self.state)
            if during is not None:
                during(now_ms, inp)
            row = self._first_true_row(now_ms, inp)
            if row is None:
                return
            self._take(row, now_ms, inp)

    def _first_true_row(self, now_ms, inp):
        s = self.state
        for row in self._arrows:
            if s in row[0] and row[1](now_ms, inp):
                return row
        return None

    def _take(self, row, now_ms, inp):
        """Follow one arrow. Subclasses may override to add arrow actions (e.g. fault reporting)."""
        self._go(row[2], row[3], now_ms, inp)

    def _go(self, to, why, now_ms, inp=None):
        frm = self.state
        if (frm, to, why) not in self._allowed and (ANY, to, why) not in self._allowed:
            raise TransitionError("%s: %s -> %s (%s) is not an arrow in the table" % (
                type(self).__name__, self.names[frm], self.names[to], why))
        self.state = to
        self.t_state = now_ms
        self.last_why = why
        self._on_enter(to, now_ms, inp)
        if self.on_transition is not None:
            self.on_transition(self, frm, to, why)

    def _on_enter(self, state, now_ms, inp):
        """Entry actions for `state`, run once right after an arrow is taken."""
        pass

    # ------------------------------------------------------------------ helpers
    def in_state_ms(self, now_ms):
        """Time spent in the current state (wrap-safe)."""
        return utime.ticks_diff(now_ms, self.t_state)

    elapsed_ms = in_state_ms

    def state_name(self):
        return self.names[self.state]
