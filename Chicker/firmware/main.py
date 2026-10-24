# Chicker firmware v2 entry point.
#
# Boot: every power-stage pin is driven to its reset-safe level before anything else is imported
# (CHARGE low, ~DISCHARGE low = dumping, KICK/CHIP low). The board then sits in Idle until the robot
# (or the USB console) sends MODE CHARGE. Any exception - including Ctrl-C / Thonny "Stop" - lands in
# the safe state: kick pin low, CHARGE low, bank dumping.
#
# Old firmware (RoboCup 2026) is kept in legacy/ for reference.

from machine import Pin
import config

Pin(config.PIN_CHARGE, Pin.OUT, value=0)
Pin(config.PIN_NOT_DISCHARGE, Pin.OUT, value=0)
Pin(config.PIN_KICK, Pin.OUT, value=0)
Pin(config.PIN_CHIP, Pin.OUT, value=0)

import sys
from board import Board
from app import App


def run(cfg=config):
    board = None
    try:
        board = Board(cfg)
        app = App(board, cfg)
        while True:
            app.step()
    except BaseException as e:      # noqa - includes KeyboardInterrupt on purpose
        try:
            if board is not None:
                board.safe_shutdown(cfg.CHG_DISCHARGE_DEADTIME_MS)
        finally:
            Pin(cfg.PIN_CHARGE, Pin.OUT, value=0)
            Pin(cfg.PIN_NOT_DISCHARGE, Pin.OUT, value=0)
            Pin(cfg.PIN_KICK, Pin.OUT, value=0)
        print("chicker stopped, outputs safe:", repr(e))
        if not isinstance(e, KeyboardInterrupt):
            sys.print_exception(e)


if __name__ == "__main__":
    run()
