"""Run the old RoboCup 2026 firmware (legacy/main_robocup2026.py) on the simulated board.

The old main.py is a module-level `while True` loop that also polls USB with select(); it is executed
unmodified in a private namespace with fake `sys` / `select` / `uasyncio`, and stopped by raising
from utime once the scenario time is over. Frames are delivered on the (fake) CAN bus at scheduled
times, exactly as the robot would send them.
"""

import os
import types
from . import world as W
from . import shims
from .harness import FW_DIR

LEGACY_MAIN = os.path.join(FW_DIR, "legacy", "main_robocup2026.py")


class _Stop(BaseException):
    pass


def run_legacy(schedule, seconds, physics=None, seed=0):
    """schedule: [(t_ms, can_id, bytes)] -> World after `seconds` of old-firmware runtime."""
    shims.install(FW_DIR)
    w = W.World(seed=seed, physics=physics)
    W.activate(w)
    import sys as real_sys
    import utime
    import machine

    if not hasattr(machine, "Timer"):
        machine.Timer = type("Timer", (), {"__init__": lambda self, *a, **k: None,
                                           "init": lambda self, *a, **k: None})
    pending = sorted(schedule)
    end_ns = int(seconds * 1e9)
    orig_ticks_ms = utime.ticks_ms

    def ticks_ms():
        while pending and w.now >= pending[0][0] * W.NS_PER_MS:
            _, cid, data = pending.pop(0)
            w.can.deliver(cid, data)
        if w.now >= end_ns:
            raise _Stop()
        return orig_ticks_ms()

    fake_sys = types.ModuleType("sys")
    fake_sys.stdin = object()
    fake_sys.exit = lambda code=0: (_ for _ in ()).throw(_Stop())
    fake_select = types.ModuleType("select")
    fake_select.select = lambda r, w_, x, t=0: ([], [], [])
    fake_uasyncio = types.ModuleType("uasyncio")
    fake_internal = types.ModuleType("canbus.internal")
    fake_internal.RXF = type("RXF", (), {"RXF0": 0, "RXF1": 1, "RXF2": 2, "RXF3": 3, "RXF4": 4, "RXF5": 5})
    fakes = {"sys": fake_sys, "select": fake_select, "uasyncio": fake_uasyncio, "canbus.internal": fake_internal}
    log = []

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def imp(name, g=None, l=None, fromlist=(), level=0):
        if name in fakes:
            return fakes[name]
        if name in ("battery", "high_voltage"):
            path = os.path.join(FW_DIR, "legacy", name + ".py")
            mod = types.ModuleType(name)
            mod.__dict__["__builtins__"] = builtins_ns
            exec(compile(open(path, encoding="utf-8").read(), path, "exec"), mod.__dict__)
            return mod
        return real_import(name, g, l, fromlist, level)

    import builtins
    builtins_ns = dict(vars(builtins))
    builtins_ns["__import__"] = imp
    builtins_ns["print"] = lambda *a, **k: log.append((w.now, " ".join(str(x) for x in a)))

    src = open(LEGACY_MAIN, encoding="utf-8").read()
    ns = {"__name__": "__legacy_main__", "__builtins__": builtins_ns}
    utime.ticks_ms = ticks_ms
    try:
        exec(compile(src, LEGACY_MAIN, "exec"), ns)
    except _Stop:
        pass
    finally:
        utime.ticks_ms = orig_ticks_ms
    w.legacy_log = log
    w.legacy_ns = ns
    return w
