"""Staged, voltage-ceiling-limited hardware bring-up for the chicker board.

    python -m bench.bring_up --sim                  # rehearse the whole procedure on the simulator first
    python -m bench.bring_up --port COM5            # real board over USB (pip install pyserial, close Thonny)
    python -m bench.bring_up --port COM5 --max-voltage 60 --skip-full

Stages (each needs a typed "y"; any anomaly -> IDLE, wait for the bank to bleed < 10 V, stop):
  1  identify       firmware version, kicker PIO self-test, CAN loopback self-test, reset cause, Idle, bank empty
  2  sensors        battery vs your multimeter, HV ~ 0, DONE low, break-beam block / unblock
  3  dry fire       no energy behind it: exact kick pulses, PWM bursts, 20 random aborts with pin readback
  4  first charge   30 V ceiling: charge starts, stops at the ceiling, then IDLE -> dump time constant (1 k x 2 mF = 2 s)
  5  ladder         30 -> 60 -> 100 -> 150 V: one 500 us kick per step, HV sag vs model
  6  hold power     damp holds at 60 V and 100 V over a duty sweep -> R_eff of the solenoid (new air gap) ->
                    predicted full-voltage hold power and how long the bank lasts
  7  full voltage   stop point 203-216 V, one 500 us kick (expect ~6.4 V sag), one short damp
  8  end            IDLE, bank below 10 V, logs written to bench/logs/<timestamp>/

While any stage runs, every telemetry line is checked: CHARGE and ~DISCHARGE both active, HV more than
10 V over the ceiling, an unexpected fault, or telemetry silent for 0.5 s -> abort.
"""

import argparse
import json
import math
import os
import random
import sys
import time

C_BANK = 0.002
R_COIL, L_COIL, V_CE = 1.72, 2.17e-3, 1.5        # fitted to the RoboCup kick-drop table (may predate the 1 cm move)
EV_KICK_FIRED, EV_DAMP_DONE, EV_FAULT = 2, 3, 5


class BenchAbort(Exception):
    pass


def predict_kick_sag(v0, width_us):
    v, i, dt = v0, 0.0, 1e-6
    for _ in range(int(width_us)):
        i += (v - V_CE - R_COIL * i) / L_COIL * dt
        v -= i * dt / C_BANK
    return v0 - v


# ============================================================================ operators
class ConsoleOperator:
    def confirm(self, msg):
        ans = input("\n>>> %s\n    type y to continue: " % msg).strip().lower()
        return ans == "y"

    def ask_float(self, msg):
        ans = input("\n>>> %s (Enter to skip): " % msg).strip()
        return float(ans) if ans else None

    def action(self, msg):
        input("\n>>> %s, then press Enter " % msg)


class SimOperator:
    """Answers for a rehearsal on the simulator (and lets tests inject 'operator mistakes')."""

    def __init__(self, link):
        self.link = link

    def confirm(self, msg):
        return True

    def ask_float(self, msg):
        if "battery" in msg.lower():
            return round(self.link.sim.w.phys.vin(), 2)
        return None

    def action(self, msg):
        m = msg.lower()
        if "block" in m and "unblock" not in m:
            self.link.sim.place_ball(0.005)
        elif "unblock" in m or "remove" in m:
            self.link.sim.remove_ball()


# ============================================================================ procedure
class BringUp:
    def __init__(self, link, operator, max_voltage=150.0, skip_full=False, damp_duty=12.0, log_dir=None):
        self.link = link
        self.op = operator
        self.max_voltage = max_voltage
        self.skip_full = skip_full
        self.damp_duty = damp_duty
        self.ceiling = 0.0
        self.expect_fault = False
        self.results = []
        self.log_dir = log_dir

    # ---------------------------------------------------------------- plumbing
    def say(self, msg):
        print(msg, flush=True)

    def record(self, stage, name, ok, **data):
        self.results.append(dict(stage=stage, name=name, ok=bool(ok), **data))
        self.say("  [%s] %s %s" % ("PASS" if ok else "FAIL", name,
                                  " ".join("%s=%s" % (k, _fmt(v)) for k, v in data.items())))
        if not ok:
            raise BenchAbort("%s: %s failed" % (stage, name))

    def check(self, link):
        t = link.tlm
        if t is None:
            return
        last = link.tlm_history[-1][0]
        if link.clock() - last > 0.5:
            raise BenchAbort("telemetry silent for > 0.5 s")
        if t.get("ch") == 1 and t.get("nd") == 0:
            raise BenchAbort("CHARGE and ~DISCHARGE both active")
        if t.get("hv", 0) > (self.ceiling or 230.0) + 10:
            raise BenchAbort("HV %.1f V above the %.0f V ceiling" % (t["hv"], self.ceiling))
        if t.get("fault", 0) and not self.expect_fault:
            raise BenchAbort("board reported fault %d" % t["fault"])

    def wait(self, pred, timeout_s, what):
        if not self.link.wait(pred, timeout_s, self.check):
            raise BenchAbort("timed out waiting for " + what)

    def cmd(self, line):
        self.link.send(line)
        self.link.pump(0.05)

    def tl(self, key, default=None):
        return (self.link.tlm or {}).get(key, default)

    def wait_event(self, code, n_before, timeout_s):
        self.wait(lambda l: len([e for e in l.events if e.get("code") == code]) > n_before, timeout_s,
                  "event %d" % code)
        return [e for e in self.link.events if e.get("code") == code][-1]

    def n_events(self, code):
        return len([e for e in self.link.events if e.get("code") == code])

    def charge_to(self, volts):
        self.ceiling = volts
        self.cmd("ceil %g" % volts if volts < 214 else "ceil off")
        self.cmd("charge")
        self.wait(lambda l: l.tlm and l.tlm.get("chg") == "Full", 15, "Full at %g V" % volts)
        self.link.wait(lambda l: False, 0.3, self.check)

    def dump(self, below=10.0, timeout_s=40):
        self.cmd("idle")
        self.wait(lambda l: l.tlm and l.tlm.get("hv", 999) < below, timeout_s, "bank below %g V" % below)

    def gate(self, msg):
        if not self.op.confirm(msg):
            raise BenchAbort("operator stopped at: " + msg)

    # ---------------------------------------------------------------- stages
    def s1_identify(self):
        self.say("\n== 1 identify")
        self.cmd("tlm 50")
        self.cmd("idle")
        self.wait(lambda l: l.tlm is not None, 3, "telemetry")
        self.cmd("info")
        self.wait(lambda l: any("fw=" in x for x in l.logs), 2, "info line")
        info = [x for x in self.link.logs if "fw=" in x][-1]
        self.say("  " + info)
        self.record("identify", "kicker PIO self-test", "kicker=True" in info)
        self.record("identify", "CAN loopback self-test", "can=True" in info, note=info.split("can=")[1][:40])
        self.record("identify", "board Idle", self.tl("mode") == "Idle", mode=self.tl("mode"))
        self.wait(lambda l: l.tlm["hv"] < 5, 40, "bank below 5 V")
        self.record("identify", "bank empty", self.tl("hv") < 5, hv=self.tl("hv"))

    def s2_sensors(self):
        self.say("\n== 2 sensors")
        meter = self.op.ask_float("Battery voltage on your multimeter")
        vb = self.tl("vb")
        if meter:
            self.record("sensors", "battery reading within 3 % of multimeter", abs(vb - meter) <= 0.03 * meter,
                        board=vb, meter=meter)
        self.record("sensors", "battery in charge window", 14.7 < vb <= 17.0, vb=vb)
        self.record("sensors", "HV ~ 0", self.tl("hv") < 5, hv=self.tl("hv"))
        self.record("sensors", "DONE low while idle", self.tl("done") == 0)
        self.op.action("Block the break-beam (ball or finger in the mouth)")
        self.wait(lambda l: l.tlm["beam"] == 1, 15, "beam broken")
        self.op.action("Unblock the break-beam")
        self.wait(lambda l: l.tlm["beam"] == 0, 15, "beam clear")
        self.record("sensors", "break-beam toggles", True)

    def s3_dry_fire(self):
        self.say("\n== 3 dry fire (bank empty, no energy behind the pulses)")
        self.gate("Dry fire: the bank is empty, but the solenoid may click. Kicker area clear?")
        k0 = self.tl("kicks")
        self.cmd("dry kick 500")
        self.wait(lambda l: l.tlm["kicks"] == k0 + 1 and l.tlm["kp"] == 0, 2, "dry kick done")
        self.record("dry fire", "single 500 us pulse", True)
        ab0 = self.tl("ab")
        rng = random.Random(1)
        for i in range(20):
            self.cmd("dry pwm %d %g 300" % (rng.choice((20000, 30000)), rng.uniform(2, 20)))
            self.link.wait(lambda l: False, rng.uniform(0.02, 0.2), self.check)
            self.cmd("dry abort")
            self.wait(lambda l: l.tlm["ab"] >= ab0 + i + 1, 2, "abort %d" % i)
            self.link.wait(lambda l: False, 0.06, self.check)
            if self.tl("kp") != 0:
                self.record("dry fire", "pin LOW after abort %d" % i, False, kp=self.tl("kp"))
        self.record("dry fire", "20 random aborts, pin LOW every time", True)
        self.record("dry fire", "no PWM underruns", self.tl("ur") == 0, ur=self.tl("ur"))

    def s4_first_charge(self):
        self.say("\n== 4 first charge (30 V ceiling) + discharge path")
        self.gate("First charge to 30 V. Solenoid secured, hands clear?")
        t0 = self.link.clock()
        self.charge_to(30)
        hv = self.tl("hv")
        self.record("first charge", "stops at the ceiling", 27 <= hv <= 33.5, hv=hv, t=self.link.clock() - t0)
        self.link.wait(lambda l: False, 2.0, self.check)
        self.record("first charge", "holds without creeping", self.tl("hv") <= 34, hv=self.tl("hv"))
        t_idle = self.link.clock()
        self.cmd("idle")
        self.wait(lambda l: l.tlm["hv"] < 4, 30,
                  "the discharge path to dump the bank (dump resistors / MOSFET?)")
        pts = [(t - t_idle, d["hv"]) for t, d in self.link.tlm_history if t >= t_idle + 0.15 and d["hv"] > 4]
        tau = _fit_tau(pts)
        self.record("first charge", "dump time constant ~ 2 s (1 k x 2 mF)", tau is not None and 1.2 <= tau <= 3.5,
                    tau_s=tau, r_dump_ohm=(tau / C_BANK) if tau else None)

    def s5_ladder(self):
        self.say("\n== 5 voltage ladder with test kicks")
        for v in [x for x in (30, 60, 100, 150) if x <= self.max_voltage]:
            self.gate("Charge to %d V and fire one 500 us kick. Kicker face clear?" % v)
            self.charge_to(v)
            n0 = self.n_events(EV_KICK_FIRED)
            self.cmd("kick 500 now")
            ev = self.wait_event(EV_KICK_FIRED, n0, 3)
            vb, va = ev["b"] / 10, ev["c"] / 10
            pred = predict_kick_sag(vb, 500)
            sag = vb - va
            ok = 0.3 * pred <= sag <= 3.0 * pred
            self.record("ladder", "%d V kick sag vs model" % v, ok, sag=sag, model=pred, ratio=sag / pred)
            self.dump(below=max(5, v * 0.1))

    def s6_hold_power(self):
        self.say("\n== 6 damping hold power (new air gap)")
        r_effs = []
        for v in [x for x in (60, 100) if x <= self.max_voltage]:
            for duty in (2.0, 4.0, 6.0, 8.0):
                self.gate("Damp hold at %d V, %.0f %% duty for 200 ms. Plunger area clear?" % (v, duty))
                self.charge_to(v)
                n0 = self.n_events(EV_DAMP_DONE)
                self.cmd("damp 30000 %g 200" % duty)
                ev = self.wait_event(EV_DAMP_DONE, n0, 5)
                hold_s = max(ev["a"], 1) / 1000.0
                v1, v2 = ev["b"] / 10, ev["c"] / 10
                p = 0.5 * C_BANK * (v1 * v1 - v2 * v2) / hold_s
                v_avg = (v1 + v2) / 2
                r_eff = (duty / 100 * v_avg) ** 2 / p if p > 0.05 else None
                if r_eff:
                    r_effs.append(r_eff)
                self.record("hold power", "%d V %.0f %%" % (v, duty), p >= 0, power_w=p, v_start=v1, v_end=v2,
                            r_eff_ohm=r_eff)
                self.dump(below=max(5, v * 0.1))
        if r_effs:
            r = sorted(r_effs)[len(r_effs) // 2]
            p_full = (self.damp_duty / 100 * 206) ** 2 / r
            hold_ms = 1000 * 0.5 * C_BANK * (206 ** 2 - 60 ** 2) / p_full
            self.say("  => R_eff ~ %.2f ohm. At %.1f %% duty and 206 V a hold draws ~%.0f W: the bank lasts ~%.0f ms."
                     % (r, self.damp_duty, p_full, hold_ms))
            self.say("     Put DAMP_R_EFF_OHM = %.2f in config.py and pick DAMP_MAX_HOLD_POWER_W from this." % r)
            self.results.append(dict(stage="hold power", name="summary", ok=True, r_eff_ohm=r,
                                     predicted_full_power_w=p_full, predicted_hold_ms=hold_ms))

    def s7_full(self):
        self.say("\n== 7 full voltage")
        self.gate("FULL VOLTAGE (~215 V). Solenoid secured, kicker face clear, everyone back?")
        self.charge_to(214)
        hv = self.tl("hv")
        self.record("full", "LT3750 stop point 203-216 V", 203 <= hv <= 216, hv=hv)
        n0 = self.n_events(EV_KICK_FIRED)
        self.cmd("kick 500 now")
        ev = self.wait_event(EV_KICK_FIRED, n0, 3)
        sag = (ev["b"] - ev["c"]) / 10
        self.record("full", "500 us kick sag (RoboCup table: ~6.4 V)", 3.5 <= sag <= 12, sag=sag)
        self.wait(lambda l: l.tlm["chg"] == "Full", 6, "recharge")
        self.gate("One %.0f %% damp hold for 300 ms at full voltage?" % self.damp_duty)
        n0 = self.n_events(EV_DAMP_DONE)
        self.cmd("damp 30000 %g 300" % self.damp_duty)
        ev = self.wait_event(EV_DAMP_DONE, n0, 6)
        hold_s = max(ev["a"], 1) / 1000.0
        p = 0.5 * C_BANK * ((ev["b"] / 10) ** 2 - (ev["c"] / 10) ** 2) / hold_s
        self.record("full", "damp hold measured", True, hold_ms=ev["a"], v_start=ev["b"] / 10,
                    v_end=ev["c"] / 10, power_w=p)

    # ---------------------------------------------------------------- run
    def run(self):
        ok = False
        reason = ""
        try:
            self.s1_identify()
            self.s2_sensors()
            self.s3_dry_fire()
            self.s4_first_charge()
            self.s5_ladder()
            self.s6_hold_power()
            if not self.skip_full and self.max_voltage >= 150:
                self.s7_full()
            ok = True
        except BenchAbort as e:
            reason = str(e)
            self.say("\n!! ABORT: " + reason)
        except KeyboardInterrupt:
            reason = "operator Ctrl-C"
            self.say("\n!! ABORT: " + reason)
        finally:
            self._safe_end()
            self._write_logs(ok, reason)
        self.say("\nRESULT: %s" % ("ALL STAGES PASSED" if ok else "STOPPED - " + reason))
        return ok

    def _safe_end(self):
        self.say("\n== end: IDLE, waiting for the bank to bleed below 10 V")
        try:
            self.ceiling = 0
            self.cmd("idle")
            if not self.link.wait(lambda l: l.tlm and l.tlm.get("hv", 999) < 10, 40):
                self.say("!! bank did not fall below 10 V within 40 s - DO NOT TOUCH, check the dump path")
            else:
                self.say("  bank at %.1f V" % self.tl("hv"))
        except Exception as e:      # noqa
            self.say("!! could not confirm the bank is empty (%r) - DO NOT TOUCH the board" % (e,))

    def _write_logs(self, ok, reason):
        if not self.log_dir:
            return
        os.makedirs(self.log_dir, exist_ok=True)
        with open(os.path.join(self.log_dir, "summary.json"), "w") as f:
            json.dump(dict(ok=ok, reason=reason, results=self.results), f, indent=1, default=str)
        with open(os.path.join(self.log_dir, "board_output.txt"), "w") as f:
            f.write("\n".join(self.link.logs))
        self.say("  logs: " + self.log_dir)


def _fit_tau(pts):
    pts = [(t, v) for t, v in pts if v > 0]
    if len(pts) < 5:
        return None
    n = len(pts)
    sx = sum(t for t, _ in pts)
    sy = sum(math.log(v) for _, v in pts)
    sxx = sum(t * t for t, _ in pts)
    sxy = sum(t * math.log(v) for t, v in pts)
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    return -1.0 / slope if slope < 0 else None


def _fmt(v):
    return "%.3g" % v if isinstance(v, float) else v


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", help="serial port of the board (e.g. COM5)")
    ap.add_argument("--sim", action="store_true", help="rehearse on the simulator")
    ap.add_argument("--max-voltage", type=float, default=150.0)
    ap.add_argument("--skip-full", action="store_true")
    ap.add_argument("--damp-duty", type=float, default=12.0)
    a = ap.parse_args(argv)
    from bench.link import SerialLink, SimLink
    if a.sim:
        link = SimLink()
        op = SimOperator(link)
    elif a.port:
        link = SerialLink(a.port)
        op = ConsoleOperator()
    else:
        ap.error("give --port COMx or --sim")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", ("sim-" if a.sim else "") + stamp)
    ok = BringUp(link, op, a.max_voltage, a.skip_full, a.damp_duty, log_dir).run()
    link.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
