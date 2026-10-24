# Shared plumbing for the state machines.
#
# Every machine declares TRANSITIONS = ((from, to, why, description), ...). The step code may only
# move along a declared transition; anything else raises TransitionError, which main.py turns into
# the safe state. The flowcharts in flowcharts/ are generated from the same tables, and the
# simulator reports which transitions were never exercised.

import utime

ANY = -1   # "from any state" in a TRANSITIONS table


class TransitionError(Exception):
    pass


class StateMachine:
    def __init__(self, names, transitions, initial, now_ms):
        self.names = names
        self.state = initial
        self.t_state = now_ms
        self.last_why = "init"
        self.on_transition = None     # optional hook(sm, frm, to, why), used by the simulator
        allowed = set()
        for frm, to, why, _ in transitions:
            allowed.add((frm, to, why))
        self._allowed = allowed

    def _go(self, to, why, now_ms):
        frm = self.state
        if (frm, to, why) not in self._allowed and (ANY, to, why) not in self._allowed:
            raise TransitionError("%s: %s -> %s (%s) not in table" % (
                type(self).__name__, self.names[frm], self.names[to], why))
        self.state = to
        self.t_state = now_ms
        self.last_why = why
        if self.on_transition is not None:
            self.on_transition(self, frm, to, why)

    def elapsed_ms(self, now_ms):
        return utime.ticks_diff(now_ms, self.t_state)

    def state_name(self):
        return self.names[self.state]
