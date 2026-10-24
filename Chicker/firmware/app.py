# Wires the board, the two state machines, the power stage and the kicker together.
#
# This is the ONLY place information moves between the machines, once per loop, in a fixed order:
#   sense -> commands -> mode.step -> kicker actions -> charge.step -> power.apply -> report
# The mode machine never writes charge state and vice versa; they see each other only through the
# values passed here. Hardware rules that span both (no charging while the kicker is busy or right
# after a kick) are enforced here as well, so neither machine can get them wrong.

import machine
import utime
from machine import Pin
import protocol as P
import sensors
import kickmath
import usb_io
from power_stage import PowerStage
from charge_sm import ChargeSM
from mode_sm import ModeSM, Inputs, ACT_ABORT, ACT_STOP_PWM, ACT_FIRE, ACT_PWM_START, ACT_PWM_DUTY
import mode_sm as M


class App:
    def __init__(self, board, cfg):
        self.b = board
        self.cfg = cfg
        now = utime.ticks_ms()
        self._t_boot = now
        self.power = PowerStage(cfg, board.charge, board.not_discharge, now)
        self.charge = ChargeSM(cfg, now)
        self.mode = ModeSM(cfg, now)
        self.batt = sensors.BatteryMonitor(cfg)
        self.inp = Inputs()
        self.ceiling = None

        self.hv = 0.0
        self.v5 = 0.0
        self.batt_v_now = 0.0
        self._t_hv = now
        self._t_batt = utime.ticks_add(now, -cfg.BATT_SAMPLE_MS)

        # Fast-fire path (break-beam hard IRQ). Shared fields, written only with IRQs disabled.
        self._fast_words = None
        self._fast_fired = False
        self._fast_hv = 0.0
        self._settle_ms = cfg.PRE_FIRE_SETTLE_MS
        self._beam_level = 0 if cfg.BEAM_ACTIVE_LOW else 1
        self._t_last_fire = utime.ticks_add(now, -cfg.KICK_COOLDOWN_MS)

        # Reporting
        self._status_seq = 0
        self._event_seq = 0
        self._t_status = now
        self._last_status = None
        self.tlm_period_ms = cfg.USB_TELEMETRY_PERIOD_MS
        self._t_tlm = now
        self.rx_ok = 0
        self.rx_rejected = 0
        self.loop_max_us = 0
        self._loop_max_window = 0

        self.wdt = None
        self.console = None
        if cfg.USB_CONSOLE_ENABLED:
            from console import Console
            self.console = Console(cfg, self)

        trig = Pin.IRQ_FALLING if cfg.BEAM_ACTIVE_LOW else Pin.IRQ_RISING
        if cfg.BEAM_FAST_FIRE:
            board.beam.irq(trigger=trig, handler=self._beam_irq, hard=True)

        self._event(P.EV_BOOT, board.reset_cause, 200, 0)
        if not board.kicker.ok:
            self.mode.raise_fault(P.F_KICKER_SELFTEST, "kicker_fault", now)
            self._log("kicker self-test failed: " + board.kicker.selftest_msg)
        if not board.can.ok:
            self._event(P.EV_FAULT, P.F_CAN_INIT, 0, 0)
            self._log("CAN: " + board.can.selftest_msg + " (USB console still works)")

    # ==================================================================== break-beam IRQ
    def _beam_irq(self, pin):
        # Hard IRQ: no allocation. Re-checks every hard interlock itself.
        w = self._fast_words
        if w is None or pin.value() != self._beam_level:
            return
        pw = self.power
        if pw.charge_on or utime.ticks_diff(utime.ticks_ms(), pw.t_charge_low) < self._settle_ms:
            return
        k = self.b.kicker
        if not k.idle():
            return
        self._fast_words = None
        k.push(w)
        self._fast_fired = True
        self._fast_hv = self.hv

    # ==================================================================== main loop pass
    def step(self):
        t0 = utime.ticks_us()
        now = utime.ticks_ms()
        b = self.b
        cfg = self.cfg
        k = b.kicker

        self._sense(now)

        st = machine.disable_irq()
        fired = self._fast_fired
        self._fast_fired = False
        fast_hv = self._fast_hv
        machine.enable_irq(st)
        if fired:
            self._t_last_fire = now
            self.mode.on_fast_fired(now, fast_hv)

        self._poll_can(now)
        if self.console is not None:
            self.console.poll()

        inp = self.inp
        inp.hv = self.hv
        inp.beam = b.beam.value() == self._beam_level
        inp.charge_quiet_ms = self.power.charge_low_ms(now)
        inp.kicker_idle = k.idle()
        inp.kicker_pwm_running = k.pwm_running
        inp.ceiling = self.ceiling
        lock_fault = self.charge.last_fault if self.charge.locked() else 0
        self.mode.step(now, utime.ticks_us(), inp, lock_fault)

        if self.mode.clear_faults_req:
            self.mode.clear_faults_req = False
            self.charge.clear_faults(now)
        self._run_actions(now)
        k.service()

        # Charging: mode permission AND kicker idle AND not right after a kick (cross-machine rule).
        allow = (self.mode.charge_allowed and k.idle() and
                 utime.ticks_diff(now, self._t_last_fire) >= cfg.KICK_COOLDOWN_MS)
        want = self.charge.step(now, self.batt.ok, allow, b.done.value() == 1, self.hv,
                                self.power.charge_permitted(now), self.power.charge_low_ms(now),
                                self.batt_v_now)
        st = machine.disable_irq()
        if want and not self.power.charge_on:
            self._fast_words = None          # fast fire off before CHARGE can rise
            if not k.idle():
                want = False
        try:
            self.power.apply(want, self.mode.discharge, now)
        finally:
            machine.enable_irq(st)

        width = self.mode.fast_fire_width(now, inp) if cfg.BEAM_FAST_FIRE else 0
        words = k.pulse_words(width) if width else None
        st = machine.disable_irq()
        self._fast_words = words if (words is not None and not self.power.charge_on) else None
        machine.enable_irq(st)

        self._forward_events()
        self._report(now)
        self._watchdog(now)
        if b.led is not None:
            b.led.value(1 if (self.power.charge_on or k.pwm_running) else 0)

        dt = utime.ticks_diff(utime.ticks_us(), t0)
        if dt > self._loop_max_window:
            self._loop_max_window = dt

    # ==================================================================== pieces
    def _sense(self, now):
        cfg = self.cfg
        self.hv = sensors.hv_volts(cfg, self.b.hv_raw())
        self._t_hv = now
        if utime.ticks_diff(now, self._t_batt) >= cfg.BATT_SAMPLE_MS:
            self._t_batt = now
            self.batt_v_now = sensors.batt_volts(cfg, self.b.adc_batt.read_u16())
            self.batt.update(self.batt_v_now)
            self.v5 = sensors.v5_volts(cfg, self.b.adc_5v.read_u16())
        elif self.charge.charging():
            # every loop while charging: a pack dropping out must not look like a charger fault
            self.batt_v_now = sensors.batt_volts(cfg, self.b.adc_batt.read_u16())

    def _poll_can(self, now):
        for _ in range(self.cfg.CAN_RX_MAX_PER_LOOP):
            f = self.b.can.recv()
            if f is None:
                return
            self.inject_frame(f[0], f[1], "can")

    def inject_frame(self, can_id, data, source):
        """Every command, from CAN or USB, enters here."""
        now = utime.ticks_ms()
        cmds, reason = P.decode(can_id, data, self.cfg)
        if reason:
            self.rx_rejected += 1
            if reason != P.REJ_UNKNOWN_ID:
                self._event(P.EV_CMD_REJECTED, can_id, reason, 0)
            return
        self.rx_ok += 1
        for cmd in cmds:
            self.mode.on_command(cmd, now)
        # abort / stop requests from a command take effect now, not at the end of the loop pass
        self._run_actions(now)

    def _run_actions(self, now):
        k = self.b.kicker
        for a in self.mode.actions:
            op = a[0]
            if op == ACT_ABORT or op == ACT_STOP_PWM:
                ok = k.abort() if op == ACT_ABORT else k.stop_pwm()
                if not ok:
                    self.mode.raise_fault(P.F_KICKER_READBACK, "kicker_fault", now)
            elif op == ACT_FIRE:
                if self.power.charge_on or not k.fire(a[1]):
                    self._log("fire refused (charge on or kicker busy)")
                    self.mode.raise_fault(P.F_INTERNAL, "kicker_fault", now)
                else:
                    self._t_last_fire = now
            elif op == ACT_PWM_START:
                if self.power.charge_on or not k.start_pwm(a[1], a[2], a[3]):
                    self.mode.raise_fault(P.F_INTERNAL, "kicker_fault", now)
                else:
                    self._t_last_fire = now
            elif op == ACT_PWM_DUTY:
                k.set_duty(a[1])
        del self.mode.actions[:]
        if self.b.kicker.pwm_running:
            self._t_last_fire = now          # no charging until the hold is over + cooldown

    def set_ceiling(self, volts):
        self.charge.set_ceiling(volts)
        self.ceiling = self.charge.ceiling
        self._log("ceiling = %s" % (self.ceiling,))

    # ==================================================================== reporting
    def _event(self, code, a, b, c):
        self._event_seq = (self._event_seq + 1) & 0xFF
        self.b.can.send(self.cfg.CAN_ID_EVENT, P.encode_event(code, a, b, c, self._event_seq))
        usb_io.write("E code=%d a=%d b=%d c=%d t=%d" % (code, a, b, c, utime.ticks_ms()))

    def _forward_events(self):
        for ev in self.mode.events:
            self._event(ev[0], ev[1], ev[2], ev[3])
        del self.mode.events[:]
        for ev in self.charge.events:
            self._event(ev[0], ev[1], ev[2], ev[3])
        del self.charge.events[:]

    def status_flags(self, now):
        m = self.mode
        f = 0
        if self.inp.beam:
            f |= P.FL_BEAM
        if self.hv >= m.kick_ready_v(self.ceiling) and m.state not in (M.IDLE, M.FAULT):
            f |= P.FL_KICK_READY
        if self.hv >= m.damp_start_v(self.ceiling) and m.state not in (M.IDLE, M.FAULT):
            f |= P.FL_DAMP_READY
        if m.armed():
            f |= P.FL_ARMED
        if self.power.charge_on:
            f |= P.FL_CHARGING
        if self.power.discharging:
            f |= P.FL_DISCHARGING
        if m.heartbeat_ok(now):
            f |= P.FL_HEARTBEAT_OK
        if self.b.shell.value():
            f |= P.FL_SHELL
        return f

    def _fault_code(self):
        return self.mode.fault or self.charge.fault

    def _report(self, now):
        cfg = self.cfg
        flags = self.status_flags(now)
        key = (self.mode.state, self.charge.state, flags, self._fault_code())
        since = utime.ticks_diff(now, self._t_status)
        if since >= cfg.STATUS_PERIOD_MS or (key != self._last_status and since >= cfg.STATUS_MIN_GAP_MS):
            self._t_status = now
            self._last_status = key
            self._status_seq = (self._status_seq + 1) & 0xFF
            self.b.can.send(cfg.CAN_ID_STATUS, P.encode_status(
                self.mode.state, self.charge.state, flags, self.hv, self.batt.volts,
                self._fault_code(), self._status_seq))
        if self.tlm_period_ms and utime.ticks_diff(now, self._t_tlm) >= self.tlm_period_ms:
            self._t_tlm = now
            self.print_telemetry()
            self.loop_max_us = self._loop_max_window
            self._loop_max_window = 0

    def print_telemetry(self):
        now = utime.ticks_ms()
        k = self.b.kicker
        usb_io.write("T t=%d mode=%s chg=%s hv=%.1f vb=%.2f v5=%.2f done=%d beam=%d ch=%d nd=%d kp=%d "
                     "duty=%.2f fault=%d kicks=%d ab=%d ur=%d rx=%d rej=%d txe=%d lp=%d ceil=%s" % (
                         now, self.mode.state_name(), self.charge.state_name(), self.hv, self.batt.volts,
                         self.v5, self.b.done.value(), 1 if self.inp.beam else 0,
                         1 if self.power.charge_on else 0, 0 if self.power.discharging else 1,
                         k.pin_level(), self.mode.duty_now, self._fault_code(), k.pulses, k.aborts,
                         k.underruns, self.rx_ok, self.rx_rejected, self.b.can.tx_errors,
                         self.loop_max_us, self.ceiling))

    def print_info(self):
        b = self.b
        self._log("fw=%s reset_cause=%d kicker=%s (%s) can=%s (%s) sys_hz=%d wdt=%s" % (
            self.cfg.FW_VERSION, b.reset_cause, b.kicker.ok, b.kicker.selftest_msg,
            b.can.ok, b.can.selftest_msg, b.kicker.sys_hz, self.wdt is not None))

    def _log(self, msg):
        usb_io.write("L " + msg)

    # ==================================================================== watchdog
    def _watchdog(self, now):
        cfg = self.cfg
        if not cfg.WATCHDOG_MS:
            return
        if self.wdt is None:
            if utime.ticks_diff(now, self._t_boot) >= cfg.WATCHDOG_START_DELAY_MS:
                self.wdt = self.b.watchdog(cfg.WATCHDOG_MS)
                self._log("watchdog started (%d ms)" % cfg.WATCHDOG_MS)
            return
        healthy = (utime.ticks_diff(now, self._t_hv) <= cfg.HV_STALE_MS and self.power.readback_ok)
        if healthy:
            self.wdt.feed()

    # ==================================================================== bench dry fire
    def dry_fire(self, what, args):
        """Fire the kicker with no energy behind it: Idle, bank dumped below DRY_FIRE_MAX_V."""
        k = self.b.kicker
        if what == "abort":
            self._log("dry abort -> pin %d" % (0 if k.abort() else 1))
            return
        if self.mode.state != M.IDLE or not self.power.discharging or self.hv > self.cfg.DRY_FIRE_MAX_V:
            self._log("dry fire refused: needs Idle and HV <= %.1f V" % self.cfg.DRY_FIRE_MAX_V)
            return
        if what == "kick":
            self._log("dry kick %s" % k.fire(int(args[0])))
        elif what == "pwm":
            self._log("dry pwm %s" % k.start_pwm(int(args[0]), float(args[1]), int(args[2]) * 1000))
