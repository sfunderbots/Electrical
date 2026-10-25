# USB serial command console - lets the bench / a laptop drive the board with no robot or CAN adapter.
#
# Command shortcuts (kick, damp, idle, ...) are encoded with protocol.py and injected through the
# SAME decoder and mode machine as a real CAN frame. Only the transport differs.
#
#   can <id> <b0> [b1 ...]        raw frame, hex (e.g. "can 2A1 00 E8 03 00 00")
#   idle | charge                 MODE frames (MODE_IDLE / MODE_CHG)
#   kick <us>                     KICK frame = MODE_AUTOKICK: kick with this pulse width when the ball breaks the beam
#   damp <hz> <duty%> [timeout_ms] [init_kick_us]     MODE_DAMP
#   hb                            one heartbeat (the board needs one at least every 5 s, like the Pi sends)
#   legacy <mode> <value> <duty>  old 0x2AA frame
#   ceil ...                      bench charge ceiling: deferred (answers with a message)
#   tlm <ms>                      telemetry period, 0 = off
#   status | info | help
#   dry kick <us> | dry pwm <hz> <duty%> <ms> | dry abort
#                                 bench only: fires the kicker directly, allowed only in Idle with
#                                 the bank dumped below DRY_FIRE_MAX_V (no energy behind it)

import sys
import protocol as P

# ---- non-blocking USB serial I/O (the simulator replaces read_char / write) ----------------------
_poll = None


def read_char():
    """One character from USB if one is waiting, else None. Never blocks."""
    global _poll
    if _poll is None:
        import select
        _poll = select.poll()
        _poll.register(sys.stdin, select.POLLIN)
    if _poll.poll(0):
        return sys.stdin.read(1)
    return None


def write(line):
    """One line to USB (telemetry "T ...", events "E ...", logs "L ...", replies "C ...")."""
    print(line)


class Console:
    def __init__(self, cfg, app):
        self.cfg = cfg
        self.app = app
        self._buf = []
        self._hb_seq = 0

    def poll(self):
        for _ in range(64):
            ch = read_char()
            if ch is None:
                return
            if ch == "\n" or ch == "\r":
                line = "".join(self._buf).strip()
                self._buf = []
                if line:
                    self.handle(line)
            elif len(self._buf) < self.cfg.USB_LINE_MAX:
                self._buf.append(ch)

    def _out(self, msg):
        write("C " + msg)

    def handle(self, line):
        try:
            self._handle(line.split())
        except (ValueError, IndexError) as e:
            self._out("error: %s (%s)" % (line, e))

    def _frame(self, can_id, data):
        self.app.inject_frame(can_id, data, "usb")

    def _handle(self, a):
        c = self.cfg
        cmd = a[0].lower()
        if cmd == "can":
            if not c.USB_CAN_INJECT_ENABLED:
                self._out("can injection disabled in config")
                return
            self._frame(int(a[1], 16), bytes([int(x, 16) for x in a[2:]]))
        elif cmd == "idle":
            self._frame(c.CAN_ID_MODE, P.encode_mode(P.MODE_IDLE))
        elif cmd == "charge":
            self._frame(c.CAN_ID_MODE, P.encode_mode(P.MODE_CHARGE))
        elif cmd == "kick":
            self._frame(c.CAN_ID_KICK, P.encode_kick(int(a[1]), P.TRIGGER_BEAM))
        elif cmd == "damp":
            hold = int(a[3]) if len(a) > 3 else 0
            init = int(a[4]) if len(a) > 4 else 0
            self._frame(c.CAN_ID_DAMP, P.encode_damp(int(a[1]), float(a[2]), hold, init))
        elif cmd == "hb":
            self._hb_seq = (self._hb_seq + 1) & 0xFF
            self._frame(c.CAN_ID_HEARTBEAT, P.encode_heartbeat(self._hb_seq))
        elif cmd == "legacy":
            self._frame(c.CAN_ID_LEGACY, P.encode_legacy(int(a[1]), int(a[2]), int(a[3])))
        elif cmd == "ceil":
            self.app.set_ceiling(None)
        elif cmd == "tlm":
            self.app.tlm_period_ms = int(a[1])
        elif cmd == "status":
            self.app.print_telemetry()
        elif cmd == "info":
            self.app.print_info()
        elif cmd == "dry":
            self.app.dry_fire(a[1], [x for x in a[2:]])
        elif cmd == "help":
            self._out("can idle charge kick damp hb legacy ceil tlm status info dry help")
        else:
            self._out("unknown command: " + cmd)
