"""PIO assembler capture + cycle-accurate emulator for the subset of PIO the firmware uses.

asm_pio() runs the firmware's real @rp2.asm_pio function body with the DSL names injected (the same
way MicroPython does), so the emulator executes the exact program text from kicker.py.
Counting loops (`jmp x-- self`) are fast-forwarded, so long pulses cost one step, not 625 000.

Modelled: side-set (mandatory / optional), delays, pull block/noblock, mov, set, jmp (x--, y--,
!x, !y, always), wrap, TX FIFO (4 or 8 deep), exec() of encoded or string instructions,
active(0) freezing the pins, re-init clearing the FIFO, SMx_ADDR / EXECCTRL / PINCTRL register reads.
"""

PIO0_BASE = 0x50200000
SM_STRIDE = 0x18
REG_EXECCTRL = 0x0CC
REG_ADDR = 0x0D4
REG_PINCTRL = 0x0DC
MASK32 = 0xFFFFFFFF


class PIOError(Exception):
    pass


# --------------------------------------------------------------------------- assembler capture
class Instr:
    def __init__(self, op, *args):
        self.op = op
        self.args = args
        self.side_val = None
        self.delay = 0

    def side(self, v):
        self.side_val = v
        return self

    def __getitem__(self, d):
        self.delay = d
        return self

    def __repr__(self):
        return "%s%r side=%s" % (self.op, self.args, self.side_val)


class Program:
    def __init__(self, instrs, labels, wrap_target, wrap, kw):
        self.instrs = instrs
        self.labels = labels
        self.wrap_target = wrap_target
        self.wrap = wrap
        self.sideset_init = kw.get("sideset_init")
        self.fifo_join = kw.get("fifo_join", 0)
        n_side = sum(1 for i in instrs if i.side_val is not None)
        if self.sideset_init is None:
            self.sideset_count = 0
        elif isinstance(self.sideset_init, tuple):
            self.sideset_count = len(self.sideset_init)
        else:
            self.sideset_count = 1
        # MicroPython makes side-set optional unless every instruction has one
        self.sideset_opt = self.sideset_count > 0 and n_side != len(instrs)
        for i in instrs:
            if i.delay > (31 >> (self.sideset_count + self.sideset_opt)):
                raise PIOError("delay too large for side-set config")


_OPERANDS = {
    "pins": "pins", "x": "x", "y": "y", "null": "null", "isr": "isr", "osr": "osr",
    "pindirs": "pindirs", "x_dec": "x--", "y_dec": "y--", "not_x": "!x", "not_y": "!y",
    "block": "block", "noblock": "noblock",
}
_DSL = ("wrap_target", "wrap", "label", "pull", "mov", "jmp", "set", "nop") + tuple(_OPERANDS)

JOIN_TX = 1
JOIN_RX = 2
OUT_LOW = 0
OUT_HIGH = 1


def asm_pio(**kw):
    def deco(fn):
        instrs, labels = [], {}
        marks = {"wrap_target": 0, "wrap": None}

        def add(op, *a):
            ins = Instr(op, *a)
            instrs.append(ins)
            return ins

        ns = dict(_OPERANDS)
        ns["wrap_target"] = lambda: marks.__setitem__("wrap_target", len(instrs))
        ns["wrap"] = lambda: marks.__setitem__("wrap", len(instrs) - 1)
        ns["label"] = lambda name: labels.__setitem__(name, len(instrs))
        ns["pull"] = lambda mode="block", *rest: add("pull", mode)
        ns["mov"] = lambda dst, src: add("mov", dst, src)
        ns["jmp"] = lambda a, b=None: add("jmp", a, b) if b is not None else add("jmp", "always", a)
        ns["set"] = lambda dst, val: add("set", dst, val)
        ns["nop"] = lambda: add("mov", "y", "y")
        g = fn.__globals__
        saved = {k: g[k] for k in ns if k in g}
        g.update(ns)
        try:
            fn()
        finally:
            for k in ns:
                if k in saved:
                    g[k] = saved[k]
                else:
                    g.pop(k, None)
        wrap = marks["wrap"] if marks["wrap"] is not None else len(instrs) - 1
        if len(instrs) > 32:
            raise PIOError("program longer than 32 instructions")
        return Program(instrs, labels, marks["wrap_target"], wrap, kw)
    return deco


# --------------------------------------------------------------------------- emulator
class StateMachineEmu:
    """One PIO0 state machine. Time is integer nanoseconds from the world clock."""

    def __init__(self, world, sm_id):
        self.world = world
        self.sm_id = sm_id
        self.prog = None
        self.enabled = False
        self.pin = None
        self.level = 0
        self.inits = 0

    # ---- configuration -------------------------------------------------------------
    def init(self, prog, freq, sideset_base):
        if self.enabled:
            self.sync()
        self.prog = prog
        if 1_000_000_000 % freq:
            raise PIOError("emulator needs a whole number of ns per cycle")
        self.cyc_ns = 1_000_000_000 // freq
        self.offset = 32 - len(prog.instrs)      # pico-sdk loads programs at the top of instruction memory
        self.pc = prog.wrap_target if False else 0
        self.x = self.y = self.isr = self.osr = 0
        self.fifo = []
        self.depth = 8 if prog.fifo_join == JOIN_TX else 4
        self.enabled = False
        self.stalled = False
        self.t_next = self.world.now
        self.pin = sideset_base
        self.inits += 1
        init_level = 0
        if isinstance(prog.sideset_init, tuple):
            init_level = prog.sideset_init[0]
        elif prog.sideset_init is not None:
            init_level = prog.sideset_init
        self.world.gpio_set_function(sideset_base, "pio", self)
        self._set_level(init_level, self.world.now)

    def active(self, v=None):
        if v is None:
            return self.enabled
        self.sync()
        if v and not self.enabled:
            self.enabled = True
            self.t_next = max(self.t_next, self.world.now)
        elif not v:
            self.enabled = False                 # pin keeps its level: this is the freeze hazard

    # ---- CPU-side access -----------------------------------------------------------------
    def put(self, word):
        self.sync()
        if len(self.fifo) >= self.depth:
            raise PIOError("sm.put() would block: TX FIFO full")
        self.fifo.append(int(word) & MASK32)
        if self.stalled:
            self.stalled = False
            self.t_next = max(self.t_next, self.world.now)

    def tx_fifo(self):
        self.sync()
        return len(self.fifo)

    def exec(self, instr):
        self.sync()
        if isinstance(instr, str):
            raise PIOError("string exec not modelled; firmware uses encoded words")
        word = int(instr)
        if word >> 13 != 0:
            raise PIOError("only JMP is modelled for exec")
        cond = (word >> 5) & 7
        if cond != 0:
            raise PIOError("only unconditional exec JMP is modelled")
        addr = word & 0x1F
        p = self.prog
        side_bits = p.sideset_count + p.sideset_opt
        field = (word >> 8) & 0x1F
        if p.sideset_count:
            side_field = field >> (5 - side_bits)
            if p.sideset_opt:
                if side_field >> p.sideset_count:
                    self._set_level(side_field & ((1 << p.sideset_count) - 1), self.world.now)
            else:
                self._set_level(side_field, self.world.now)
        local = addr - self.offset
        if not 0 <= local < len(p.instrs):
            raise PIOError("exec JMP outside the program (%d)" % addr)
        self.pc = local
        self.stalled = False
        self.t_next = self.world.now + self.cyc_ns

    def reg_read(self, reg):
        p = self.prog
        if reg == REG_ADDR:
            return self.offset + self.pc
        if reg == REG_EXECCTRL:
            return (1 << 30) if p.sideset_opt else 0
        if reg == REG_PINCTRL:
            return ((p.sideset_count + p.sideset_opt) << 29) | (self.pin << 10)
        return 0

    # ---- execution -------------------------------------------------------------------------
    def sync(self):
        self.world.sync_pio()

    def _set_level(self, v, t):
        if v != self.level:
            self.level = v
            self.world.pio_pin_change(self, self.pin, v, t)

    def advance(self, t_end):
        """Execute every instruction that starts at or before t_end."""
        if not self.enabled or self.prog is None:
            return
        p = self.prog
        ins = p.instrs
        cyc = self.cyc_ns
        while self.t_next <= t_end:
            if self.stalled:
                return
            i = ins[self.pc]
            if i.side_val is not None:
                self._set_level(i.side_val, self.t_next)
            op = i.op
            nxt = self.pc + 1
            jumped = False
            cost = 1 + i.delay
            if op == "pull":
                if self.fifo:
                    self.osr = self.fifo.pop(0)
                elif i.args[0] == "noblock":
                    self.osr = self.x
                else:
                    self.stalled = True
                    return
            elif op == "mov":
                dst, src = i.args
                val = {"x": self.x, "y": self.y, "isr": self.isr, "osr": self.osr, "null": 0}[src]
                setattr(self, dst, val)
            elif op == "set":
                dst, val = i.args
                if dst == "pins":
                    self._set_level(val & 1, self.t_next)
                elif dst in ("x", "y"):
                    setattr(self, dst, val)
            elif op == "jmp":
                cond, target = i.args
                tgt = p.labels[target] if isinstance(target, str) else target
                if cond == "x--" and tgt == self.pc and i.delay == 0:
                    # counting loop: runs x+1 times, jumps while x != 0
                    avail = (t_end - self.t_next) // cyc + 1
                    n = min(self.x + 1, avail)
                    self.t_next += n * cyc
                    self.x = (self.x - n) & MASK32
                    if self.x == MASK32:          # fell through on the last iteration
                        self.pc = nxt if nxt <= p.wrap else p.wrap_target
                        if self.pc == nxt and self.pc - 1 == p.wrap:
                            self.pc = p.wrap_target
                    continue
                if cond == "always":
                    take = True
                elif cond == "x--":
                    take = self.x != 0
                    self.x = (self.x - 1) & MASK32
                elif cond == "y--":
                    take = self.y != 0
                    self.y = (self.y - 1) & MASK32
                elif cond == "!x":
                    take = self.x == 0
                elif cond == "!y":
                    take = self.y == 0
                else:
                    raise PIOError("jmp condition %s not modelled" % cond)
                if take:
                    nxt = tgt
                    jumped = True
            else:
                raise PIOError("op %s not modelled" % op)
            if not jumped and self.pc == p.wrap:
                nxt = p.wrap_target
            self.pc = nxt
            self.t_next += cost * cyc
