# Chicker CAN protocol v2 (+ legacy 0x2AA). Pure functions, no hardware access.
# Imported by the firmware, the simulator and the bench tools so both ends always agree.
#
# All multi-byte fields are little-endian. IDs live in config.py.
#
# robot -> board
#   MODE       [mode]                                  0 = IDLE (dump), 1 = CHARGE (charge and hold)
#   KICK       [trigger, width_us u16, arm_ms u16]     trigger 0 = on break-beam, 1 = now; arm_ms 0 = default
#   DAMP       [freq_hz u16, duty u16, hold_ms u16, init_kick_us u16]
#                                                      duty in 0.01 % units; hold_ms / init_kick_us 0 = default
#   HEARTBEAT  [seq]
#   LEGACY     [mode, value u16, duty]                 old frame, mapped onto the commands above
# board -> robot
#   STATUS     [mode_state, charge_state, flags, hv_dV u16, batt_dV, fault, seq]
#   EVENT      [code, a u16, b u16, c u16, seq]

MODE_IDLE = 0
MODE_CHARGE = 1

TRIGGER_BEAM = 0
TRIGGER_NOW = 1

# Command kinds produced by decode()
CMD_MODE = 1
CMD_KICK = 2
CMD_DAMP = 3
CMD_HEARTBEAT = 4

# Rejection reasons (EVENT CMD_REJECTED, field b)
REJ_BAD_LENGTH = 1
REJ_BAD_VALUE = 2
REJ_OUT_OF_RANGE = 3
REJ_NOT_CHARGING = 4
REJ_FAULTED = 5
REJ_UNKNOWN_ID = 6
REJ_HV_NOT_READY = 7
REJ_KICKER_UNAVAILABLE = 8

# Fault codes (STATUS fault byte, EVENT FAULT)
F_NONE = 0
F_CHG_NO_START = 1        # DONE never rose and bank not full (LT3750 UVLO/thermal or DONE path)
F_CHG_EARLY_DONE = 2      # DONE fell well below target (datasheet fault behaviour)
F_CHG_NO_PROGRESS = 3     # HV not rising while charging (dead flyback or HV sense)
F_CHG_NO_REGULATION = 4   # HV above the backstop: LT3750 did not stop
F_CHG_TIMEOUT = 5
F_OVERVOLT = 6
F_KICKER_SELFTEST = 10
F_KICKER_READBACK = 11    # kick pin not low after an abort
F_POWER_READBACK = 12     # CHARGE / ~DISCHARGE readback mismatch
F_CAN_INIT = 13
F_INTERNAL = 14

# Events
EV_BOOT = 1               # a = reset cause, b = FW major*100 + minor
EV_KICK_FIRED = 2         # a = width_us, b = hv_before dV, c = hv_after dV
EV_DAMP_DONE = 3          # a = hold_ms actual, b = hv_start dV, c = hv_end dV
EV_ARM_EXPIRED = 4
EV_FAULT = 5              # a = fault code
EV_CMD_REJECTED = 6       # a = CAN id, b = reason
EV_COMMS_LOST = 7
EV_DAMP_ABORTED = 8       # a = reason (REJ_*)

# STATUS flags
FL_BEAM = 0x01            # ball present / beam broken
FL_KICK_READY = 0x02      # a kick right now would fire without waiting for charge
FL_DAMP_READY = 0x04
FL_ARMED = 0x08
FL_CHARGING = 0x10        # CHARGE pin high
FL_DISCHARGING = 0x20     # ~DISCHARGE low (bank dumping)
FL_HEARTBEAT_OK = 0x40
FL_SHELL = 0x80           # reed switch raw level (analog version in to-test/)


class Command:
    __slots__ = ("kind", "mode", "trigger", "width_us", "arm_ms", "freq_hz",
                 "duty", "hold_ms", "init_kick_us", "seq", "can_id", "implied")

    def __init__(self, kind, can_id):
        self.kind = kind
        self.can_id = can_id
        self.mode = 0
        self.trigger = 0
        self.width_us = 0
        self.arm_ms = 0
        self.freq_hz = 0
        self.duty = 0.0          # percent
        self.hold_ms = 0
        self.init_kick_us = 0
        self.seq = 0
        self.implied = False     # legacy: MODE CHARGE implied by a kick/damp frame


def _u16(d, i):
    return d[i] | (d[i + 1] << 8)


def _mode(can_id, mode, implied=False):
    c = Command(CMD_MODE, can_id)
    c.mode = mode
    c.implied = implied
    return c


def decode(can_id, data, cfg):
    """Return (commands, reject_reason). commands is a list (empty on reject)."""
    n = len(data)
    if can_id == cfg.CAN_ID_MODE:
        if n < 1:
            return [], REJ_BAD_LENGTH
        if data[0] not in (MODE_IDLE, MODE_CHARGE):
            return [], REJ_BAD_VALUE
        return [_mode(can_id, data[0])], 0

    if can_id == cfg.CAN_ID_KICK:
        if n < 5:
            return [], REJ_BAD_LENGTH
        if data[0] not in (TRIGGER_BEAM, TRIGGER_NOW):
            return [], REJ_BAD_VALUE
        c = Command(CMD_KICK, can_id)
        c.trigger = data[0]
        c.width_us = _u16(data, 1)
        c.arm_ms = _u16(data, 3)
        return [c], 0

    if can_id == cfg.CAN_ID_DAMP:
        if n < 8:
            return [], REJ_BAD_LENGTH
        c = Command(CMD_DAMP, can_id)
        c.freq_hz = _u16(data, 0)
        c.duty = _u16(data, 2) / 100.0
        c.hold_ms = _u16(data, 4)
        c.init_kick_us = _u16(data, 6)
        return [c], 0

    if can_id == cfg.CAN_ID_HEARTBEAT:
        c = Command(CMD_HEARTBEAT, can_id)
        c.seq = data[0] if n else 0
        return [c], 0

    if can_id == cfg.CAN_ID_LEGACY and cfg.LEGACY_PROTOCOL_ENABLED:
        return _decode_legacy(can_id, data, cfg)

    return [], REJ_UNKNOWN_ID


def _decode_legacy(can_id, data, cfg):
    # Old frame: [mode, value u16, duty]. 0 idle, 1 autokick (value = width us),
    # 2 damp (value = freq Hz, duty = whole %), 3 charge. Old firmware clamped instead of rejecting.
    if len(data) < 4:
        return [], REJ_BAD_LENGTH
    mode = data[0]
    value = _u16(data, 1)
    if mode == 0:
        return [_mode(can_id, MODE_IDLE)], 0
    if mode == 3:
        return [_mode(can_id, MODE_CHARGE)], 0
    if mode == 1:
        cmds = [_mode(can_id, MODE_CHARGE, True)]
        if value:
            c = Command(CMD_KICK, can_id)
            c.trigger = TRIGGER_BEAM
            c.width_us = min(max(value, cfg.KICK_MIN_US), cfg.KICK_MAX_US)
            cmds.append(c)
        return cmds, 0
    if mode == 2:
        c = Command(CMD_DAMP, can_id)
        c.freq_hz = min(max(value, cfg.DAMP_FREQ_MIN), cfg.DAMP_FREQ_MAX)
        c.duty = min(max(float(data[3]), cfg.DAMP_DUTY_MIN), cfg.DAMP_DUTY_MAX)
        return [_mode(can_id, MODE_CHARGE, True), c], 0
    return [], REJ_BAD_VALUE


# ---------------------------------------------------------------------------
# Encoders (robot / bench side)
# ---------------------------------------------------------------------------
def _le16(v):
    v = int(v) & 0xFFFF
    return (v & 0xFF, v >> 8)


def encode_mode(mode):
    return bytes((mode,))


def encode_kick(width_us, trigger=TRIGGER_BEAM, arm_ms=0):
    return bytes((trigger,) + _le16(width_us) + _le16(arm_ms))


def encode_damp(freq_hz, duty_pct, hold_ms=0, init_kick_us=0):
    return bytes(_le16(freq_hz) + _le16(int(duty_pct * 100 + 0.5)) + _le16(hold_ms) + _le16(init_kick_us))


def encode_heartbeat(seq):
    return bytes((seq & 0xFF,))


def encode_legacy(mode, value, duty):
    return bytes((mode,) + _le16(value) + (duty & 0xFF,))


# ---------------------------------------------------------------------------
# Board -> robot
# ---------------------------------------------------------------------------
def encode_status(mode_state, charge_state, flags, hv_v, batt_v, fault, seq):
    hv = int(hv_v * 10 + 0.5)
    hv = 0 if hv < 0 else (65535 if hv > 65535 else hv)
    bt = int(batt_v * 10 + 0.5)
    bt = 0 if bt < 0 else (255 if bt > 255 else bt)
    return bytes((mode_state & 0xFF, charge_state & 0xFF, flags & 0xFF,
                  hv & 0xFF, hv >> 8, bt, fault & 0xFF, seq & 0xFF))


def decode_status(d):
    return {"mode_state": d[0], "charge_state": d[1], "flags": d[2],
            "hv": _u16(d, 3) / 10.0, "batt": d[5] / 10.0, "fault": d[6], "seq": d[7]}


def encode_event(code, a=0, b=0, c=0, seq=0):
    return bytes((code & 0xFF,) + _le16(a) + _le16(b) + _le16(c) + (seq & 0xFF,))


def decode_event(d):
    return {"code": d[0], "a": _u16(d, 1), "b": _u16(d, 3), "c": _u16(d, 5), "seq": d[7]}
