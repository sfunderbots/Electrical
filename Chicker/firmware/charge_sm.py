# Charge state machine - decides when the LT3750 runs. Owns nothing but its own state.
#
# Inputs come in through step(); the only output is "want CHARGE high". The power stage decides
# whether that is actually allowed (interlock with ~DISCHARGE), and the mode machine can only say
# "charging allowed / not allowed" - it never touches anything in here.
#
# Charging is driven by the HV measurement: top off when the bank sags below RECHARGE_BELOW_V,
# stop at a bench ceiling, check that HV really rises, and check that DONE falls at a plausible
# voltage. DONE (LT3750 open collector, high while switching) confirms each cycle; timeouts are
# only backstops.

from micropython import const
import utime
import protocol as P
from sm_base import StateMachine, ANY

NO_BATTERY = const(0)
BATT_SETTLE = const(1)
INHIBITED = const(2)
GAP = const(3)
STARTING = const(4)
CHARGING = const(5)
FULL = const(6)
FAULT_WAIT = const(7)
LOCKED = const(8)

NAMES = ("NoBattery", "BattSettle", "Inhibited", "Gap", "Starting",
         "Charging", "Full", "FaultWait", "Locked")

TRANSITIONS = (
    (ANY, LOCKED, "overvolt", "HV >= OVERVOLT_V (225 V): latch, bank gets dumped"),
    (ANY, NO_BATTERY, "batt_lost", "battery not OK (except FaultWait / Locked)"),
    (ANY, NO_BATTERY, "batt_dropped", "cycle ended early because the pack just dropped out: not a charger fault"),
    (ANY, INHIBITED, "inhibit", "mode disallows charging: CHARGE low now, mid-cycle included"),
    (NO_BATTERY, BATT_SETTLE, "batt_ok", "battery OK (debounced, hysteresis)"),
    (BATT_SETTLE, GAP, "settled", "BATT_SETTLE_MS elapsed, charging allowed"),
    (BATT_SETTLE, INHIBITED, "settled_inhibited", "BATT_SETTLE_MS elapsed, charging not allowed"),
    (INHIBITED, GAP, "allowed", "mode allows charging"),
    (GAP, STARTING, "start", "CHARGE low >= 50 ms, discharge released >= dead time, >= 500 ms since last cycle"),
    (STARTING, CHARGING, "done_high", "DONE rose: flyback switching"),
    (STARTING, FULL, "already_full", "no DONE within 200 ms but HV >= 206 V"),
    (STARTING, FULL, "ceiling", "HV >= bench ceiling"),
    (STARTING, FAULT_WAIT, "no_start", "no DONE within 200 ms and HV low"),
    (STARTING, LOCKED, "no_start", "no_start and too many consecutive faults"),
    (CHARGING, FULL, "done", "DONE fell and HV >= 190 V: normal finish"),
    (CHARGING, FULL, "ceiling", "HV >= bench ceiling"),
    (CHARGING, FAULT_WAIT, "early_done", "DONE fell but HV < 190 V"),
    (CHARGING, FAULT_WAIT, "no_progress", "HV rose < 5 V in 1 s while below 150 V"),
    (CHARGING, FAULT_WAIT, "no_regulation", "HV >= 220 V backstop"),
    (CHARGING, FAULT_WAIT, "timeout", "cycle longer than 6 s"),
    (CHARGING, LOCKED, "early_done", "fault and too many consecutive faults"),
    (CHARGING, LOCKED, "no_progress", "fault and too many consecutive faults"),
    (CHARGING, LOCKED, "no_regulation", "fault and too many consecutive faults"),
    (CHARGING, LOCKED, "timeout", "fault and too many consecutive faults"),
    (FULL, GAP, "sagged", "HV < 205 V (or ceiling - 3 V): top off"),
    (FAULT_WAIT, GAP, "retry", "30 s elapsed, charging allowed"),
    (FAULT_WAIT, INHIBITED, "retry_inhibited", "30 s elapsed, charging not allowed"),
    (FAULT_WAIT, INHIBITED, "cleared", "IDLE command clears faults"),
    (LOCKED, INHIBITED, "cleared", "IDLE command clears faults"),
)

_FAULT_WHY = {
    P.F_CHG_NO_START: "no_start",
    P.F_CHG_EARLY_DONE: "early_done",
    P.F_CHG_NO_PROGRESS: "no_progress",
    P.F_CHG_NO_REGULATION: "no_regulation",
    P.F_CHG_TIMEOUT: "timeout",
}


class ChargeSM(StateMachine):
    def __init__(self, cfg, now_ms):
        StateMachine.__init__(self, NAMES, TRANSITIONS, NO_BATTERY, now_ms)
        self.cfg = cfg
        self.fault = P.F_NONE          # active fault (FaultWait / Locked)
        self.last_fault = P.F_NONE
        self.consecutive_faults = 0
        self.cycles = 0
        self.ceiling = None            # bench charge ceiling in volts, None = off
        self.events = []               # (event, a, b, c) for the app to forward
        self._t_last_start = None
        self._t_done_low = None
        self._t_progress = now_ms
        self._hv_progress = 0.0
        self._batt_v_now = 99.0

    # --- read-only status for the rest of the firmware -----------------------------------
    def charging(self):
        return self.state == STARTING or self.state == CHARGING

    def full(self):
        return self.state == FULL

    def locked(self):
        return self.state == LOCKED

    def recharge_threshold(self):
        c = self.cfg
        if self.ceiling is None:
            return c.RECHARGE_BELOW_V
        return min(c.RECHARGE_BELOW_V, self.ceiling - c.CEILING_HYST_V)

    # --- requests from outside (both only ever lower risk) --------------------------------
    def clear_faults(self, now_ms):
        if self.state == FAULT_WAIT or self.state == LOCKED:
            self._go(INHIBITED, "cleared", now_ms)
        self.fault = P.F_NONE
        self.consecutive_faults = 0

    def set_ceiling(self, volts):
        c = self.cfg
        if volts is None:
            self.ceiling = None
        else:
            self.ceiling = min(max(float(volts), c.CEILING_MIN_V), c.CEILING_MAX_V)

    # --- one pass -------------------------------------------------------------------------
    def step(self, now_ms, batt_ok, allow, done, hv, permitted, charge_low_ms, batt_v_now=99.0):
        """Return True when CHARGE should be high.

        batt_ok        battery monitor verdict
        allow          mode machine allows charging (and the kicker is idle)
        done           DONE pin high (LT3750 switching)
        hv             bank voltage, V
        permitted      power stage: ~DISCHARGE released for >= dead time
        charge_low_ms  how long the CHARGE pin has been low (0 while high)
        batt_v_now     latest raw battery reading (sampled every loop while charging)
        """
        c = self.cfg
        s = self.state
        self._batt_v_now = batt_v_now

        if hv >= c.OVERVOLT_V and s != LOCKED:
            self._fault(now_ms, P.F_OVERVOLT, "overvolt", True)
            return False
        if s == LOCKED:
            return False
        if not batt_ok:
            if s != NO_BATTERY and s != FAULT_WAIT:
                self._go(NO_BATTERY, "batt_lost", now_ms)
            return False
        if not allow and (s == GAP or s == STARTING or s == CHARGING or s == FULL):
            self._go(INHIBITED, "inhibit", now_ms)
            return False

        if s == NO_BATTERY:
            self._go(BATT_SETTLE, "batt_ok", now_ms)

        elif s == BATT_SETTLE:
            if self.elapsed_ms(now_ms) >= c.BATT_SETTLE_MS:
                if allow:
                    self._go(GAP, "settled", now_ms)
                else:
                    self._go(INHIBITED, "settled_inhibited", now_ms)

        elif s == INHIBITED:
            if allow:
                self._go(GAP, "allowed", now_ms)

        elif s == GAP:
            spaced = (self._t_last_start is None or
                      utime.ticks_diff(now_ms, self._t_last_start) >= c.MIN_CYCLE_INTERVAL_MS)
            if permitted and charge_low_ms >= c.CHARGE_LOW_MIN_MS and spaced:
                self._t_last_start = now_ms
                self._t_done_low = None
                self._t_progress = now_ms
                self._hv_progress = hv
                self._go(STARTING, "start", now_ms)

        elif s == STARTING:
            el = self.elapsed_ms(now_ms)
            if self.ceiling is not None and hv >= self.ceiling:
                self._go(FULL, "ceiling", now_ms)
            elif done and el >= c.DONE_MIN_MS:
                self._go(CHARGING, "done_high", now_ms)
            elif el >= c.DONE_RISE_TIMEOUT_MS:
                if hv >= c.CHARGE_ALREADY_FULL_V:
                    self._finished(now_ms, "already_full")
                else:
                    self._fault(now_ms, P.F_CHG_NO_START, "no_start", False)

        elif s == CHARGING:
            self._step_charging(now_ms, done, hv)

        elif s == FULL:
            if hv < self.recharge_threshold():
                self._go(GAP, "sagged", now_ms)

        elif s == FAULT_WAIT:
            if self.elapsed_ms(now_ms) >= c.CHARGE_RETRY_MS:
                self.fault = P.F_NONE
                if allow:
                    self._go(GAP, "retry", now_ms)
                else:
                    self._go(INHIBITED, "retry_inhibited", now_ms)

        return self.state == STARTING or self.state == CHARGING

    def _step_charging(self, now_ms, done, hv):
        c = self.cfg
        if hv >= c.CHARGE_BACKSTOP_V:
            self._fault(now_ms, P.F_CHG_NO_REGULATION, "no_regulation", False)
            return
        if self.ceiling is not None and hv >= self.ceiling:
            self._go(FULL, "ceiling", now_ms)
            return
        if not done:
            if self._t_done_low is None:
                self._t_done_low = now_ms
            elif utime.ticks_diff(now_ms, self._t_done_low) >= c.DONE_LOW_DEBOUNCE_MS:
                if hv >= c.DONE_PLAUSIBLE_MIN_V:
                    self._finished(now_ms, "done")
                else:
                    self._fault(now_ms, P.F_CHG_EARLY_DONE, "early_done", False)
                return
        else:
            self._t_done_low = None
        if self.elapsed_ms(now_ms) >= c.CHARGE_TIMEOUT_MS:
            self._fault(now_ms, P.F_CHG_TIMEOUT, "timeout", False)
            return
        if utime.ticks_diff(now_ms, self._t_progress) >= c.PROGRESS_WINDOW_MS:
            if hv < c.PROGRESS_CHECK_BELOW_V and hv - self._hv_progress < c.PROGRESS_MIN_RISE_V:
                self._fault(now_ms, P.F_CHG_NO_PROGRESS, "no_progress", False)
                return
            self._t_progress = now_ms
            self._hv_progress = hv

    def _finished(self, now_ms, why):
        self.consecutive_faults = 0
        self.cycles += 1
        self._go(FULL, why, now_ms)

    def _fault(self, now_ms, code, why, lock):
        if not lock and self._batt_v_now <= self.cfg.BATT_OFF_MIN_V:
            # the LT3750 stopped because the pack dropped out (unplugged / deep sag), not a charger fault
            self._go(NO_BATTERY, "batt_dropped", now_ms)
            return
        self.fault = code
        self.last_fault = code
        self.consecutive_faults += 1
        self.events.append((P.EV_FAULT, code, self.state, 0))
        if lock or self.consecutive_faults >= self.cfg.MAX_CONSECUTIVE_CHARGE_FAULTS:
            self._go(LOCKED, why, now_ms)
        else:
            self._go(FAULT_WAIT, why, now_ms)
