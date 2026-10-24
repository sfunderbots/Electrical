"""Level 1: sensor conversions, battery verdict, CAN protocol encode/decode (pure code)."""

import random
from sim.harness import make_cfg, Sim

cfg = make_cfg()
import sensors          # noqa: E402  (firmware modules import after the shims are installed)
import protocol as P    # noqa: E402


def test_hv_conversion_matches_divider_within_one_lsb():
    lsb_v = 3.3 / 4095 * cfg.HV_DIVIDER        # ~62 mV of bank per ADC count
    for q in range(0, 4096, 37):
        raw = (q << 4) | (q >> 8)
        v = sensors.hv_volts(cfg, raw)
        assert abs(v - q / 4095 * 3.3 * cfg.HV_DIVIDER) <= lsb_v, (q, v)


def test_hv_reading_tracks_simulated_bank():
    s = Sim()
    s.charge()
    s.run_until(lambda: s.charge_state == "Full", 6000, "full")
    s.run(10)
    assert abs(s.app.hv - s.hv) < 1.0, (s.app.hv, s.hv)


def test_battery_window_and_hysteresis():
    m = sensors.BatteryMonitor(cfg)
    feed = lambda v, n=5: [m.update(v) for _ in range(n)][-1]
    assert not feed(14.6)                        # below the turn-on threshold
    assert feed(15.0)                            # inside the window
    assert feed(14.5)                            # hysteresis: stays on down to 14.3
    assert not feed(14.2)
    assert not feed(14.6)                        # needs 14.7 again to come back
    assert feed(16.9)
    assert not feed(17.5)                        # over-voltage pack
    assert not feed(0.5)                         # unplugged


def test_battery_debounce_ignores_single_dips():
    m = sensors.BatteryMonitor(cfg)
    for _ in range(5):
        m.update(15.5)
    for _ in range(50):
        m.update(15.5)
        m.update(13.0)                           # one-sample dip every other sample
        assert m.ok


def test_protocol_roundtrip_all_commands():
    c, r = P.decode(cfg.CAN_ID_MODE, P.encode_mode(P.MODE_CHARGE), cfg)
    assert r == 0 and c[0].kind == P.CMD_MODE and c[0].mode == P.MODE_CHARGE
    c, r = P.decode(cfg.CAN_ID_KICK, P.encode_kick(1234, P.TRIGGER_NOW, 1500), cfg)
    assert r == 0 and (c[0].width_us, c[0].trigger, c[0].arm_ms) == (1234, P.TRIGGER_NOW, 1500)
    c, r = P.decode(cfg.CAN_ID_DAMP, P.encode_damp(30000, 12.34, 2500, 700), cfg)
    assert r == 0 and c[0].freq_hz == 30000 and abs(c[0].duty - 12.34) < 1e-9
    assert (c[0].hold_ms, c[0].init_kick_us) == (2500, 700)
    c, r = P.decode(cfg.CAN_ID_HEARTBEAT, P.encode_heartbeat(77), cfg)
    assert r == 0 and c[0].kind == P.CMD_HEARTBEAT and c[0].seq == 77


def test_protocol_rejects_bad_frames():
    assert P.decode(cfg.CAN_ID_MODE, b"", cfg)[1] == P.REJ_BAD_LENGTH
    assert P.decode(cfg.CAN_ID_MODE, b"\x07", cfg)[1] == P.REJ_BAD_VALUE
    assert P.decode(cfg.CAN_ID_KICK, b"\x00\x10", cfg)[1] == P.REJ_BAD_LENGTH
    assert P.decode(cfg.CAN_ID_KICK, b"\x05\xe8\x03\x00\x00", cfg)[1] == P.REJ_BAD_VALUE
    assert P.decode(cfg.CAN_ID_DAMP, b"\x30\x75\x00", cfg)[1] == P.REJ_BAD_LENGTH
    assert P.decode(0x123, b"\x00", cfg)[1] == P.REJ_UNKNOWN_ID


def test_protocol_fuzz_never_raises():
    rng = random.Random(1)
    ids = list(range(0x2A0, 0x2B0)) + [0x000, 0x123, 0x7FF]
    for _ in range(20000):
        cid = rng.choice(ids)
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 9)))
        cmds, reason = P.decode(cid, data, cfg)
        assert (cmds and reason == 0) or (not cmds and reason != 0)


def test_legacy_frames_map_onto_v2_commands():
    # the RoboCup frames from the old main.py comments
    c, _ = P.decode(0x2AA, P.encode_legacy(1, 1000, 0), cfg)         # autokick 1000 us
    assert [x.kind for x in c] == [P.CMD_MODE, P.CMD_KICK] and c[0].implied and c[1].width_us == 1000
    c, _ = P.decode(0x2AA, P.encode_legacy(2, 30000, 12), cfg)       # damp 30 kHz 12 %
    assert c[1].kind == P.CMD_DAMP and c[1].freq_hz == 30000 and c[1].duty == 12.0
    c, _ = P.decode(0x2AA, P.encode_legacy(0, 0, 0), cfg)
    assert c[0].mode == P.MODE_IDLE
    c, _ = P.decode(0x2AA, P.encode_legacy(3, 0, 0), cfg)
    assert c[0].mode == P.MODE_CHARGE and not c[0].implied
    c, _ = P.decode(0x2AA, P.encode_legacy(1, 0, 0), cfg)            # width 0: charge only, no 1 us kick
    assert [x.kind for x in c] == [P.CMD_MODE]
    off = make_cfg(LEGACY_PROTOCOL_ENABLED=False)
    assert P.decode(0x2AA, P.encode_legacy(1, 1000, 0), off)[1] == P.REJ_UNKNOWN_ID


def test_status_and_event_frames_roundtrip():
    d = P.encode_status(4, 6, 0xA5, 212.34, 15.84, 3, 200)
    st = P.decode_status(d)
    assert len(d) == 8 and st["hv"] == 212.3 and st["batt"] == 15.8 and st["flags"] == 0xA5
    assert P.decode_status(P.encode_status(0, 0, 0, 9999, 99, 0, 0))["batt"] == 25.5   # saturates
    ev = P.decode_event(P.encode_event(P.EV_KICK_FIRED, 1000, 2149, 2010, 9))
    assert (ev["code"], ev["a"], ev["b"], ev["c"], ev["seq"]) == (P.EV_KICK_FIRED, 1000, 2149, 2010, 9)
