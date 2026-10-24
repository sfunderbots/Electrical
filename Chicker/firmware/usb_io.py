# Non-blocking USB serial I/O (MicroPython). The simulator replaces this module.

import sys
import select

_poll = select.poll()
_poll.register(sys.stdin, select.POLLIN)


def read_char():
    """One character if one is waiting, else None. Never blocks."""
    if _poll.poll(0):
        return sys.stdin.read(1)
    return None


def write(line):
    print(line)
