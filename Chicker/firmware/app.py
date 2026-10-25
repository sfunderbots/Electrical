# Wires the board, the two state machines, the power stage and the kicker together.
#
# One main-loop pass (step), always in this order:
#   1. read the sensors (HV every pass, battery every 500 ms)
#   2. read CAN frames: heartbeats refresh the heartbeat timer, good commands join the command queue
#   3. Breakbeam Mini State Machine (mode element 13)
#   4. mode machine step (one queued command per pass), then carry out its kicker requests
#   5. charge machine step, then the power stage drives CHARGE / ~DISCHARGE
#   6. report to the Pi (STATUS / EVENT frames) and the USB console, feed the watchdog
#
# This is the only place information moves between the two machines, and it only moves the agreed signals
# (docs/COUPLED-STATE-MACHINE-REVIEW.md): chg_stop_mode_ctrl and chg_hold_mode_ctrl from the mode machine to
# the charge machine, chg_disable_chip_level the other way. Neither machine touches a pin.

import machine
import utime
from machine import Pin
import protocol as P
import sensors
import console
from power_stage import PowerStage
from charge_sm import ChargeSM, ChargeInputs
from mode_sm import (ModeSM, ModeInputs, ACT_ABORT, ACT_STOP_PWM, ACT_FIRE, ACT_FIRE_REPLACING_PWM,
                             ACT_PWM_START, ACT_PWM_RENEW, ACT_PWM_DUTY)

COMMAND_QUEUE_MAX = 8      # good commands waiting for the mode machine (one is handled per loop pass)


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
        self.mode_in = ModeInputs()
        self.charge_in = ChargeInputs()

        # ---- sensor readings
        self.hv = 0.0
        self.v5 = 0.0
        self._t_hv = now
        self._t_batt = utime.ticks_add(now, -cfg.BATT_SAMPLE_MS)

        # ---- heartbeat (note HB-1): any valid frame from the Pi counts
        self._t_last_frame = None          # None until the first valid frame
        self._last_hb_seq = None
        self.hb_gaps = 0                   # heartbeat sequence numbers skipped (diagnostics only)
        self.hb_repeats = 0

        # ---- good commands waiting for the mode machine, oldest first (mode element 7)
        self._commands = []
        self.cmd_queue_overflows = 0

        # ---- DONE rising-edge latch (charge element 8): set by the pin interrupt, cleared by the charge
        #      machine each time CHARGE goes high
        self._done_rose = 0
        board.done.irq(trigger=Pin.IRQ_RISING, handler=self._done_rose_irq)

        # ---- break-beam (element 13): the interrupt records each change, the main loop decides
        self._beam_on_level = 0 if cfg.BEAM_ACTIVE_LOW else 1
        self._beam_level = board.beam.value()
        self._t_beam_edge = now
        self.beam_on = False
        board.beam.irq(trigger=Pin.IRQ_RISING | Pin.IRQ_FALLING, handler=self._beam_irq)

        # ---- reporting
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
        self.dry_fire_until = None         # bench dry fire running until this tick (kicker pulses in Idle on purpose)
        self.console = None
        if cfg.USB_CONSOLE_ENABLED:
            self.console = console.Console(cfg, self)

        self._event(P.EV_BOOT, board.reset_cause, 200, 0)
        if not board.kicker.ok:
            # Not on the charts: a kicker that fails its boot self-test refuses every pulse. Tell the Pi.
            self._event(P.EV_FAULT, P.F_KICKER_SELFTEST, 0, 0)
            self._log("kicker self-test failed: " + board.kicker.selftest_msg)
        if not board.can.ok:
            self._event(P.EV_FAULT, P.F_CAN_INIT, 0, 0)
            self._log("CAN: " + board.can.selftest_msg + " (USB console still works)")

    # ==================================================================== pin interrupts (kept tiny)
    def _done_rose_irq(self, pin):
        self._done_rose = 1

    def _beam_irq(self, pin):
        self._beam_level = pin.value()
        self._t_beam_edge = utime.ticks_ms()

    # ==================================================================== main loop pass
    def step(self):
        t0 = utime.ticks_us()
        now = utime.ticks_ms()
        b = self.b
        cfg = self.cfg
        kicker = b.kicker

        # 1. sensors
        self._read_sensors(now)

        # 2. CAN (and USB console) frames
        self._poll_can()
        if self.console is not None:
            self.console.poll()

        # 3. Breakbeam Mini State Machine: "Breakbeam == ON" once the level has been steady for the debounce
        steady = utime.ticks_diff(now, self._t_beam_edge) >= cfg.BEAM_DEBOUNCE_MS
        if steady:
            self.beam_on = self._beam_level == self._beam_on_level
        self.mode.update_breakbeam(self.beam_on)
        if b.led is not None:
            b.led.value(1 if self.beam_on else 0)          # "LED on" / "LED off"

        # 4. mode machine
        mi = self.mode_in
        mi.chg_ok = 1 if self.batt.ok else 0
        mi.chg_disable_chip_level = self.charge.chg_disable_chip_level
        mi.heartbeat_ok = self._heartbeat_age_ms(now) < cfg.HEARTBEAT_TIMEOUT_MS
        mi.hv = self.hv
        mi.done = b.done.value()
        mi.charge_pin_low = not self.power.charge_on
        mi.pwm_running = kicker.pwm_running
        mi.kicker_idle = kicker.idle()
        mi.now_us = utime.ticks_us()
        mi.command = self._commands.pop(0) if self._commands else None
        self.mode.step(now, mi)
        if mi.command is not None:
            # A higher-priority rule (battery, heartbeat, Locked) moved the machine on before the command was
            # handled: it is dropped, not saved for later.
            self._reject(mi.command, self._not_accepted_reason())
            mi.command = None
        if not self.mode.accepts_commands():
            for cmd in self._commands:
                self._reject(cmd, self._not_accepted_reason())
            del self._commands[:]
        self._run_kicker_actions(now)
        kicker.service()

        # 5. charge machine, then the pins
        ci = self.charge_in
        ci.chg_ok = 1 if self.batt.ok else 0
        ci.chg_stop_mode_ctrl = self.mode.chg_stop_mode_ctrl
        ci.chg_hold_mode_ctrl = self.mode.chg_hold_mode_ctrl
        ci.done = b.done.value()
        ci.done_rose = self._done_rose
        ci.charge_pin = 1 if self.power.charge_on else 0
        ci.hv = self.hv
        self.charge.step(now, ci)
        if self.charge.clear_done_latch:
            # A new cycle starts this pass: clear the latch BEFORE CHARGE rises so only this cycle's edge counts
            self.charge.clear_done_latch = False
            st = machine.disable_irq()
            self._done_rose = 0
            machine.enable_irq(st)
        self.power.apply(self.charge.want_charge, self.charge.want_dump, now)

        # 6. report, watchdog
        self._forward_events()
        self._report(now)
        self._watchdog(now)

        dt = utime.ticks_diff(utime.ticks_us(), t0)
        if dt > self._loop_max_window:
            self._loop_max_window = dt

    # ==================================================================== sensors
    def _read_sensors(self, now):
        cfg = self.cfg
        self.hv = sensors.hv_volts(cfg, self.b.hv_raw())
        self._t_hv = now
        if utime.ticks_diff(now, self._t_batt) >= cfg.BATT_SAMPLE_MS:
            self._t_batt = now
            self.batt.update(sensors.batt_volts(cfg, self.b.adc_batt.read_u16()))
            self.v5 = sensors.v5_volts(cfg, self.b.adc_5v.read_u16())

    # ==================================================================== incoming frames
    def _poll_can(self):
        for _ in range(self.cfg.CAN_RX_MAX_PER_LOOP):
            f = self.b.can.recv()
            if f is None:
                return
            self.inject_frame(f[0], f[1], "can")

    def inject_frame(self, can_id, data, source):
        """Every frame, from CAN or the USB console, enters here (mode element 7, message handling)."""
        now = utime.ticks_ms()
        cmds, reason = P.decode(can_id, data, self.cfg)
        if reason:
            # Bad command: count it, maybe report it, and change nothing (nothing is de-armed)
            self.rx_rejected += 1
            if reason != P.REJ_UNKNOWN_ID and self.cfg.CAN_SEND_REJECT_EVENTS:
                self._event(P.EV_CMD_REJECTED, can_id, reason, 0)
            return
        self.rx_ok += 1
        self._t_last_frame = now                         # any valid frame refreshes the heartbeat
        for cmd in cmds:
            if cmd.kind == P.CMD_HEARTBEAT:
                self._note_heartbeat_seq(cmd.seq)        # heartbeats never reach the state machine
            elif not self.mode.accepts_commands():
                self._reject(cmd, self._not_accepted_reason())   # not acted on, not saved for later
            elif len(self._commands) >= COMMAND_QUEUE_MAX:
                self.cmd_queue_overflows += 1
                self._reject(cmd, P.REJ_KICKER_UNAVAILABLE)
            else:
                self._commands.append(cmd)

    def _note_heartbeat_seq(self, seq):
        """Diagnostics only: count skipped and repeated heartbeat numbers (packet loss), never act on them."""
        last = self._last_hb_seq
        if last is not None:
            step = (seq - last) & 0xFF
            if step == 0:
                self.hb_repeats += 1
            elif step > 1:
                self.hb_gaps += step - 1
        self._last_hb_seq = seq

    def _heartbeat_age_ms(self, now):
        if self._t_last_frame is None:
            return 1 << 30
        return utime.ticks_diff(now, self._t_last_frame)

    def _not_accepted_reason(self):
        return P.REJ_FAULTED if self.charge.disabled() else P.REJ_NOT_READY

    def _reject(self, cmd, reason):
        self.rx_rejected += 1
        if self.cfg.CAN_SEND_REJECT_EVENTS:
            self._event(P.EV_CMD_REJECTED, cmd.can_id, reason, cmd.kind)

    # ==================================================================== kicker requests from the mode machine
    def _run_kicker_actions(self, now):
        kicker = self.b.kicker
        for a in self.mode.actions:
            op = a[0]
            if op == ACT_ABORT:
                ok = kicker.abort()
            elif op == ACT_STOP_PWM:
                ok = kicker.stop_pwm()
            elif op == ACT_FIRE or op == ACT_FIRE_REPLACING_PWM:
                # Last line of defence: never pulse the gate while CHARGE is high
                if self.power.charge_on:
                    ok = False
                elif op == ACT_FIRE:
                    ok = kicker.fire(a[1])
                else:
                    ok = kicker.fire_replacing_pwm(a[1])
            elif op == ACT_PWM_START:
                ok = (not self.power.charge_on) and kicker.start_pwm(a[1], a[2], a[3])
            elif op == ACT_PWM_RENEW:
                ok = kicker.renew_pwm(a[1])
            else:   # ACT_PWM_DUTY
                kicker.set_duty(a[1])
                ok = True
            if not ok:
                self._log("kicker refused action %d" % op)
                self._event(P.EV_FAULT, P.F_KICKER_READBACK if op <= ACT_STOP_PWM else P.F_INTERNAL, op, 0)
        del self.mode.actions[:]

    def set_ceiling(self, volts):
        """Bench charge ceiling: deferred (charge review, "Not on the chart"). Kept so the console command
        answers instead of failing."""
        self._log("charge ceiling is not available in this firmware (deferred, see the charge review)")

    # ==================================================================== reporting
    def _event(self, code, a, b, c):
        self._event_seq = (self._event_seq + 1) & 0xFF
        self.b.can.send(self.cfg.CAN_ID_EVENT, P.encode_event(code, a, b, c, self._event_seq))
        console.write("E code=%d a=%d b=%d c=%d t=%d" % (code, a, b, c, utime.ticks_ms()))

    def _forward_events(self):
        for ev in self.mode.events:
            self._event(ev[0], ev[1], ev[2], ev[3])
        del self.mode.events[:]
        for ev in self.charge.events:
            self._event(ev[0], ev[1], ev[2], ev[3])
        del self.charge.events[:]

    def status_flags(self, now):
        cfg = self.cfg
        f = 0
        if self.beam_on:
            f |= P.FL_BEAM
        if self.hv >= cfg.KICK_MIN_HV:
            f |= P.FL_KICK_READY
        if self.hv >= cfg.DAMP_START_V:
            f |= P.FL_DAMP_READY
        if self.mode.armed:
            f |= P.FL_ARMED
        if self.power.charge_on:
            f |= P.FL_CHARGING
        if self.power.discharging:
            f |= P.FL_DISCHARGING
        if self._heartbeat_age_ms(now) < cfg.HEARTBEAT_LATE_MS:
            f |= P.FL_HEARTBEAT_OK
        if self.b.shell.value():
            f |= P.FL_SHELL
        return f

    def _fault_code(self):
        # Locked (mode element 5) tells the Pi through this byte in every STATUS frame
        return self.charge.fault

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
        m = self.mode
        console.write("T t=%d mode=%s/%d chg=%s hv=%.1f vb=%.2f v5=%.2f done=%d beam=%d ch=%d nd=%d kp=%d "
                      "armed=%d assigned=%d duty=%.2f fault=%d kicks=%d ab=%d ur=%d rx=%d rej=%d txe=%d "
                      "hbgap=%d lp=%d" % (
                          now, m.state_name(), m.mode, self.charge.state_name(), self.hv, self.batt.volts,
                          self.v5, self.b.done.value(), 1 if self.beam_on else 0,
                          1 if self.power.charge_on else 0, 0 if self.power.discharging else 1,
                          k.pin_level(), m.armed, m.kick_assigned, m.duty_now, self._fault_code(), k.pulses,
                          k.aborts, k.underruns, self.rx_ok, self.rx_rejected, self.b.can.tx_errors,
                          self.hb_gaps, self.loop_max_us))

    def print_info(self):
        b = self.b
        self._log("fw=%s reset_cause=%d kicker=%s (%s) can=%s (%s) sys_hz=%d wdt=%s" % (
            self.cfg.FW_VERSION, b.reset_cause, b.kicker.ok, b.kicker.selftest_msg,
            b.can.ok, b.can.selftest_msg, b.kicker.sys_hz, self.wdt is not None))

    def _log(self, msg):
        console.write("L " + msg)

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
        """Fire the kicker with no energy behind it: board idle, bank dumped below DRY_FIRE_MAX_V."""
        k = self.b.kicker
        if what == "abort":
            self._log("dry abort -> pin %d" % (0 if k.abort() else 1))
            return
        if not self.mode.idle_like() or not self.power.discharging or self.hv > self.cfg.DRY_FIRE_MAX_V:
            self._log("dry fire refused: needs the board idle and HV <= %.1f V" % self.cfg.DRY_FIRE_MAX_V)
            return
        now = utime.ticks_ms()
        if what == "kick":
            self.dry_fire_until = utime.ticks_add(now, 20)
            self._log("dry kick %s" % k.fire(int(args[0])))
        elif what == "pwm":
            self.dry_fire_until = utime.ticks_add(now, int(args[2]) + 20)
            self._log("dry pwm %s" % k.start_pwm(int(args[0]), float(args[1]), int(args[2]) * 1000))
