# Bench capture: charge the bank, hold the plunger out with the damping PWM, record the bank voltage fast while a
# ball is rolled into the plunger, then dump the bank. The capture is printed over USB for host_analyse.py.
#
# !! HIGH VOLTAGE. Bench only, solenoid clamped, hands and face clear of the kicker. Start at 60 V. !!
#
# Uses the real firmware's Board, PowerStage (the CHARGE / dump interlock, X-5) and Kicker (PIO), so copy this file,
# hv_dma_capture.py and the firmware's .py files + canbus/ to the board. Then:
#   1. stop the firmware: Ctrl-C (or Thonny Stop) within 3 s of boot, before the watchdog starts; or rename main.py
#   2. run a capture from the PC and save it to a file, e.g.
#        mpremote exec "import bench_impact_capture as b; b.run(target_v=60)" > hold_60V_ball1.txt
#      or in the Thonny shell:  import bench_impact_capture as b; b.run(target_v=60)   (then save the shell output)
#   3. python host_analyse.py hold_60V_ball1.txt
#
# Modes (README.md, "Test plan"):
#   run(mode="dumped")   bank at 0 V, no PWM: the ADC / sense-chain noise floor
#   run(mode="charged")  bank charged and held, no PWM: noise + the bleed through the HV divider
#   run(mode="hold")     initial kick + damping PWM at a CONSTANT duty (no HV adjustment, so no duty steps); roll the
#                        ball in after "GO". Run it with no ball too, to see what a hold alone looks like.

import gc
import utime
import config as cfg
import sensors
from hv_dma_capture import HvCapture

MAX_TARGET_V = 205.0          # never charge past this here (full bank is 206-212 V)
OVERSHOOT_V = 15.0            # abort and dump if HV goes this far over the target
CHARGE_TIMEOUT_MS = 4000      # a full charge takes ~1.6 s
DUMP_DONE_V = 5.0
DUMP_TIMEOUT_MS = 30000


def run(target_v=60.0, mode="hold", freq_hz=30000, duty_pct=12.0, hold_ms=1500, initial_kick=True,
        rate_hz=50000, capture_s=0.8, countdown_s=3):
    if target_v > MAX_TARGET_V:
        raise ValueError("target_v is limited to %.0f V on this bench script" % MAX_TARGET_V)
    gc.collect()
    cap = HvCapture(cfg.PIN_ADC_HV, rate_hz, int(rate_hz * capture_s))   # big buffer first, while RAM is free
    from board import Board                        # firmware modules (importing Board sets up every pin safely)
    from power_stage import PowerStage
    board = Board(cfg)
    power = PowerStage(cfg, board.charge, board.not_discharge, utime.ticks_ms())
    kicker = board.kicker
    meta = {"mode": mode, "target_v": target_v, "freq_hz": freq_hz, "duty_pct": duty_pct, "hold_ms": hold_ms,
            "initial_kick_us": cfg.DAMP_INITIAL_KICK_US if initial_kick else 0}
    try:
        _say("kicker self-test: %s (%s)" % (kicker.ok, kicker.selftest_msg))
        if mode != "dumped":
            vb = sensors.batt_volts(cfg, board.adc_batt.read_u16())
            if not (cfg.BATT_ON_MIN_V < vb <= cfg.BATT_MAX_V):
                raise RuntimeError("battery %.2f V is outside %.1f-%.1f V" % (vb, cfg.BATT_ON_MIN_V, cfg.BATT_MAX_V))
            _charge_to(board, power, target_v)
        meta["hv_before"] = round(_hv(board), 2)
        _countdown(countdown_s, mode)
        if mode != "hold":
            cap.start()                           # nothing else to do: just record
        elif cap.has_dma():
            _capture_kick_and_hold(kicker, cap, meta, freq_hz, duty_pct, hold_ms, initial_kick)
        else:
            _hold_then_poll(kicker, cap, meta, freq_hz, duty_pct, hold_ms, initial_kick)
        cap.wait()
        kicker.stop_pwm()
        meta["used_dma"] = cap.used_dma
        cap.release()
        meta["hv_after"] = round(_hv(board), 2)
        meta["rate_hz"] = cap.rate_hz
        meta["n"] = cap.n
        meta["adc_vref"] = cfg.ADC_VREF
        meta["hv_divider"] = cfg.HV_DIVIDER
        _print_capture(cap, meta)
    finally:
        # Whatever went wrong above, the bank is always dumped: each step here is tried on its own.
        for step in (kicker.abort, cap.release):
            try:
                step()
            except Exception as e:      # noqa
                _say("cleanup step failed (%r), dumping anyway" % (e,))
        _dump(board, power)


# ---------------------------------------------------------------------------------------------- helpers
def _hv(board):
    return sensors.hv_volts(cfg, board.hv_raw())


def _say(msg):
    print("# " + msg)


def _sample_at(t0_us, rate_hz):
    return utime.ticks_diff(utime.ticks_us(), t0_us) * rate_hz // 1_000_000


def _charge_to(board, power, target_v):
    """CHARGE high (through the power stage's interlock) until HV reaches the target, then CHARGE low with the
    dump OFF so the bank holds its charge. The LT3750 would stop at ~210 V by itself; we stop it earlier."""
    _say("charging to %.0f V" % target_v)
    t0 = utime.ticks_ms()
    while True:
        now = utime.ticks_ms()
        hv = _hv(board)
        if hv >= target_v:
            break
        if utime.ticks_diff(now, t0) > CHARGE_TIMEOUT_MS:
            raise RuntimeError("did not reach %.0f V in %d ms (HV %.1f V)" % (target_v, CHARGE_TIMEOUT_MS, hv))
        power.apply(True, False, now)          # dump off first, CHARGE only after the 50 ms dead time
    power.apply(False, False, utime.ticks_ms())
    utime.sleep_ms(50)
    hv = _hv(board)
    if hv > target_v + OVERSHOOT_V:
        raise RuntimeError("HV %.1f V overshot the %.0f V target" % (hv, target_v))
    _say("charged: %.1f V, charger off, dump off (holding)" % hv)


def _capture_kick_and_hold(kicker, cap, meta, freq_hz, duty_pct, hold_ms, initial_kick):
    """DMA records in the background: start recording, 20 ms of plain bank as a reference, Initial Kick, 4 ms,
    then the PWM hold, feeding the kicker's PIO until the capture is full."""
    t0 = utime.ticks_us()
    cap.start()
    utime.sleep_ms(20)
    if initial_kick:
        kicker.fire(cfg.DAMP_INITIAL_KICK_US)
        meta["kick_at_sample"] = _sample_at(t0, cap.rate_hz)
        utime.sleep_us(cfg.DAMP_SETTLE_US)
    if not kicker.start_pwm(freq_hz, duty_pct, hold_ms * 1000):
        raise RuntimeError("kicker refused to start the PWM")
    meta["pwm_at_sample"] = _sample_at(t0, cap.rate_hz)
    while cap.running():
        kicker.service()                          # keep the PIO fed: the hold must not stutter during the capture


def _hold_then_poll(kicker, cap, meta, freq_hz, duty_pct, hold_ms, initial_kick):
    """No DMA (old MicroPython): the capture blocks the CPU, so start the hold first, then capture while feeding the
    kicker from inside the polling loop. The kick itself is not in the capture in this mode."""
    _say("no rp2.DMA: polling capture (slower, uneven); the hold starts before the capture")
    if initial_kick:
        kicker.fire(cfg.DAMP_INITIAL_KICK_US)
        utime.sleep_us(cfg.DAMP_SETTLE_US)
    if not kicker.start_pwm(freq_hz, duty_pct, hold_ms * 1000):
        raise RuntimeError("kicker refused to start the PWM")
    meta["pwm_at_sample"] = 0
    cap.start(service=kicker.service)


def _countdown(seconds, mode):
    if mode != "hold":
        _say("capturing")
        return
    for k in range(seconds, 0, -1):
        _say("roll the ball in after GO ... %d" % k)
        utime.sleep_ms(1000)
    _say("GO")


def _dump(board, power):
    """Charger off, dump on (the power stage adds its 50 ms dead time), wait until the bank is safe."""
    t0 = utime.ticks_ms()
    while True:
        now = utime.ticks_ms()
        power.apply(False, True, now)
        hv = _hv(board)
        if power.discharging and hv < DUMP_DONE_V:
            _say("bank dumped: %.1f V" % hv)
            return
        if utime.ticks_diff(now, t0) > DUMP_TIMEOUT_MS:
            _say("!! bank still at %.1f V after %d s: DO NOT TOUCH, check the dump path" % (hv, DUMP_TIMEOUT_MS // 1000))
            return
        utime.sleep_ms(20)


def _print_capture(cap, meta):
    print("BEGIN_CAPTURE")
    print("META " + " ".join("%s=%s" % (k, v) for k, v in meta.items()))
    for line in cap.lines():
        print(line)
    print("END_CAPTURE errors=%d" % cap.errors)
