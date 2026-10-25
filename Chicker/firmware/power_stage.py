# Power stage interlock - the only code allowed to write CHARGE and ~DISCHARGE.
#
# Rules (each one has a simulator test):
#   1. CHARGE can only go high once ~DISCHARGE has been high (dump off) for >= dead time.
#   2. ~DISCHARGE can only go low (dump on) once CHARGE has been low for >= dead time.
#   3. Going safe writes CHARGE low first; going live releases ~DISCHARGE first.
#   4. Every write is read back; a mismatch forces both pins safe and raises PowerStageError.
# Boot state = CHARGE low, ~DISCHARGE low (dumping): the same as the RP2040's reset pull-downs.

import utime


class PowerStageError(Exception):
    pass


class PowerStage:
    def __init__(self, cfg, charge_pin, not_discharge_pin, now_ms):
        self.cfg = cfg
        self._chg = charge_pin
        self._nd = not_discharge_pin
        self._chg.value(0)
        self._nd.value(0)
        self.charge_on = False
        self.discharging = True
        self.t_charge_low = now_ms     # read by the break-beam IRQ (fast-fire interlock)
        self._t_dump_off = now_ms
        self.readback_ok = True
        self._verify()

    # Dead-time checks are strict (>): ticks_ms has 1 ms resolution, so ">= 50" could pass after only
    # 49.x ms of real time (found by the simulator). "> 50" guarantees at least 50 ms.
    def charge_permitted(self, now_ms):
        return (not self.discharging and
                utime.ticks_diff(now_ms, self._t_dump_off) > self.cfg.CHG_DISCHARGE_DEADTIME_MS)

    def charge_low_ms(self, now_ms):
        if self.charge_on:
            return 0
        return utime.ticks_diff(now_ms, self.t_charge_low)

    def apply(self, want_charge, want_discharge, now_ms):
        dead = self.cfg.CHG_DISCHARGE_DEADTIME_MS
        if want_discharge:
            want_charge = False
        # The dead-time clocks are read at the moment each pin is written, not taken from now_ms (the start of the
        # loop pass): a pin can change later in the pass, after a millisecond tick, and the dead time must be
        # measured from the real edge (found by the simulator's soak test: 49.99 ms instead of 50).
        if not want_charge and self.charge_on:
            self._chg.value(0)
            self.charge_on = False
            self.t_charge_low = utime.ticks_ms()
        if want_discharge:
            if not self.discharging and not self.charge_on and \
                    utime.ticks_diff(now_ms, self.t_charge_low) > dead:
                self._nd.value(0)
                self.discharging = True
        elif self.discharging:
            self._nd.value(1)
            self.discharging = False
            self._t_dump_off = utime.ticks_ms()
        if want_charge and not self.charge_on and self.charge_permitted(now_ms):
            self._chg.value(1)
            self.charge_on = True
        self._verify()

    def _verify(self):
        if self._chg.value() != (1 if self.charge_on else 0) or \
                self._nd.value() != (0 if self.discharging else 1):
            self.readback_ok = False
            self.force_safe()
            raise PowerStageError("CHARGE/~DISCHARGE readback mismatch")

    def force_safe(self):
        """Emergency: CHARGE low, then dump. No dead time - only used when something is already wrong."""
        self._chg.value(0)
        self._nd.value(0)
        self.charge_on = False
        self.discharging = True
