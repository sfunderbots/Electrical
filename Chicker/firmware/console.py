# USB serial command console - lets the bench / a laptop drive the board with no robot or CAN adapter.
#
# Command shortcuts (kick, damp, idle, ...) are encoded with protocol.py and injected through the
# SAME decoder and mode machine as a real CAN frame. Only the transport differs.
#
#   can <id> <b0> [b1 ...]        raw frame, hex (e.g. "can 2A1 00 E8 03 00 00")
#   idle | charge                 MODE frames
#   kick <us> [now] [arm_ms]      KICK frame (default trigger: break-beam)
#   damp <hz> <duty%> [hold_ms] [init_kick_us]
#   hb                            one heartbeat
#   legacy <mode> <value> <duty>  old 0x2AA frame
#   ceil <volts> | ceil off       bench charge ceiling (only ever lowers the target)
#   tlm <ms>                      telemetry period, 0 = off
#   status | info | help
#   dry kick <us> | dry pwm <hz> <duty%> <ms> | dry abort
#                                 bench only: fires the kicker directly, allowed only in Idle with
#                                 the bank dumped below DRY_FIRE_MAX_V (no energy behind it)

import protocol as P
import usb_io


class Console:
    def __init__(self, cfg, app):
        self.cfg = cfg
        self.app = app
        self._buf = []

    def poll(self):
        for _ in range(64):
            ch = usb_io.read_char()
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
        usb_io.write("C " + msg)

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
            trigger = P.TRIGGER_NOW if "now" in a[2:] else P.TRIGGER_BEAM
            nums = [int(x) for x in a[2:] if x != "now"]
            self._frame(c.CAN_ID_KICK, P.encode_kick(int(a[1]), trigger, nums[0] if nums else 0))
        elif cmd == "damp":
            hold = int(a[3]) if len(a) > 3 else 0
            init = int(a[4]) if len(a) > 4 else 0
            self._frame(c.CAN_ID_DAMP, P.encode_damp(int(a[1]), float(a[2]), hold, init))
        elif cmd == "hb":
            self._frame(c.CAN_ID_HEARTBEAT, P.encode_heartbeat(0))
        elif cmd == "legacy":
            self._frame(c.CAN_ID_LEGACY, P.encode_legacy(int(a[1]), int(a[2]), int(a[3])))
        elif cmd == "ceil":
            self.app.set_ceiling(None if a[1] == "off" else float(a[1]))
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
