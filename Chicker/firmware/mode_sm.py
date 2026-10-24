# Mode state machine - what the board is doing for the robot (idle / charged / kick / damp).
#
# Ownership rule (the main bug class in the old firmware): this machine never touches the charge
# machine, the power stage or the kicker. Its outputs are plain values the app reads after step():
#   charge_allowed   may the charge machine run?
#   discharge        should the bank be dumped?
#   actions          kicker requests (fire / start PWM / set duty / stop / abort)
#   events           things to report to the robot
#   clear_faults_req ask the app to clear charge faults (MODE IDLE)
# Its inputs are a read-only snapshot (Inputs) filled by the app every loop.

from micropython import const
import utime
import protocol as P
import kickmath
from sm_base import StateMachine, ANY

IDLE = const(0)
FAULT = const(1)
READY = const(2)
KICK_ARMED = const(3)
KICK_PREP = const(4)
KICK_COOLDOWN = const(5)
DAMP_WAIT_HV = const(6)
DAMP_PREP = const(7)
DAMP_EXTEND = const(8)
DAMP_HOLD = const(9)

NAMES = ("Idle", "Fault", "Ready", "KickArmed", "KickPrep", "KickCooldown",
         "DampWaitHV", "DampPrep", "DampExtend", "DampHold")

# Kicker actions handed to the app
ACT_ABORT = const(1)      # (ACT_ABORT,)                         stop now, including a pulse in flight
ACT_STOP_PWM = const(2)   # (ACT_STOP_PWM,)                      stop damping PWM; a single pulse finishes
ACT_FIRE = const(3)       # (ACT_FIRE, width_us)
ACT_PWM_START = const(4)  # (ACT_PWM_START, freq_hz, duty_pct, duration_us)
ACT_PWM_DUTY = const(5)   # (ACT_PWM_DUTY, duty_pct)

_DAMP_STATES = (DAMP_WAIT_HV, DAMP_PREP, DAMP_EXTEND, DAMP_HOLD)
_KICK_STATES = (KICK_ARMED, KICK_PREP)


def _cancel_rows():
    rows = []
    for s in _DAMP_STATES:
        rows.append((s, READY, "cmd_charge", "MODE CHARGE cancels the damp (PWM stopped)"))
        rows.append((s, KICK_ARMED, "cmd_kick_beam", "KICK (break-beam) replaces the damp"))
        rows.append((s, KICK_PREP, "cmd_kick_now", "KICK (now / ball present) replaces the damp"))
    for s in _KICK_STATES:
        rows.append((s, READY, "cmd_charge", "MODE CHARGE disarms"))
        rows.append((s, DAMP_WAIT_HV, "cmd_damp", "DAMP replaces the armed kick"))
        rows.append((s, READY, "expired", "arm time elapsed"))
    return tuple(rows)


TRANSITIONS = (
    (ANY, IDLE, "cmd_idle", "MODE IDLE: abort kicker, dump bank, clear faults"),
    (ANY, IDLE, "comms_lost", "no heartbeat for HEARTBEAT_TIMEOUT_MS"),
    (ANY, FAULT, "charge_locked", "charge machine locked (repeated faults / overvoltage)"),
    (ANY, FAULT, "kicker_fault", "kicker self-test or abort readback failed"),
    (IDLE, READY, "cmd_charge", "MODE CHARGE"),
    (READY, KICK_ARMED, "cmd_kick_beam", "KICK, trigger = break-beam"),
    (READY, KICK_PREP, "cmd_kick_now", "KICK, trigger = now (or ball already in the beam)"),
    (READY, DAMP_WAIT_HV, "cmd_damp", "DAMP"),
    (KICK_ARMED, KICK_PREP, "beam", "break-beam broken (main-loop path)"),
    (KICK_ARMED, KICK_PREP, "cmd_kick_now", "re-armed with trigger = now"),
    (KICK_ARMED, KICK_COOLDOWN, "fast_fire", "break-beam IRQ fired the pre-validated kick"),
    (KICK_PREP, KICK_ARMED, "cmd_kick_beam", "re-armed with trigger = break-beam, no ball"),
    (KICK_PREP, KICK_COOLDOWN, "fire", "charging stopped, charger quiet >= 5 ms: FIRE (any bank voltage)"),
    (KICK_COOLDOWN, READY, "cooled", "KICK_COOLDOWN_MS elapsed and pulse finished"),
    (KICK_COOLDOWN, KICK_ARMED, "pending_kick_beam", "KICK received during cooldown"),
    (KICK_COOLDOWN, KICK_PREP, "pending_kick_now", "KICK (now) received during cooldown"),
    (KICK_COOLDOWN, DAMP_WAIT_HV, "pending_damp", "DAMP received during cooldown"),
    (DAMP_WAIT_HV, DAMP_PREP, "hv_ready", "HV >= DAMP_START_V: pause charging"),
    (DAMP_WAIT_HV, READY, "hv_timeout", "HV not ready within DAMP_READY_TIMEOUT_MS"),
    (DAMP_PREP, DAMP_EXTEND, "fire_initial", "charger quiet >= 5 ms: initial extension kick"),
    (DAMP_EXTEND, DAMP_HOLD, "settled", "DAMP_SETTLE_US after the initial kick: start PWM hold"),
    (DAMP_HOLD, READY, "hold_done", "hold time elapsed"),
    (DAMP_HOLD, READY, "hv_floor", "HV < DAMP_MIN_V: nothing useful left"),
) + _cancel_rows()


class Inputs:
    """Read-only snapshot the app fills every loop."""
    __slots__ = ("hv", "beam", "charge_quiet_ms", "kicker_idle", "kicker_pwm_running", "ceiling")

    def __init__(self):
        self.hv = 0.0
        self.beam = False
        self.charge_quiet_ms = 0
        self.kicker_idle = True
        self.kicker_pwm_running = False
        self.ceiling = None


class _Pending:
    __slots__ = ("kind", "trigger", "width_us", "arm_ms", "freq_hz", "duty", "hold_ms", "init_kick_us")


class ModeSM(StateMachine):
    def __init__(self, cfg, now_ms):
        StateMachine.__init__(self, NAMES, TRANSITIONS, IDLE, now_ms)
        self.cfg = cfg
        self.charge_allowed = False
        self.discharge = True
        self.actions = []
        self.events = []
        self.clear_faults_req = False
        self.fault = P.F_NONE
        # heartbeat
        self._t_rx = now_ms
        self._hb_seen = False
        self._comms_lost_reported = False
        # kick
        self._kick_width = 0
        self._kick_trigger = P.TRIGGER_BEAM
        self._arm_until = now_ms
        self._kick_hv_before = 0.0
        self._kick_fired_width = 0
        self._pending = None
        # damp
        self._damp_freq = cfg.DAMP_FREQ_DEFAULT
        self._damp_duty = cfg.DAMP_DUTY_DEFAULT
        self._damp_hold_ms = cfg.DAMP_HOLD_MS_DEFAULT
        self._damp_init_us = cfg.DAMP_INITIAL_KICK_US
        self._t_extend_us = 0
        self._t_hold = now_ms
        self._t_seg = now_ms
        self._v_start = 0.0
        self.duty_now = 0.0
        self.duty_limited = False

    # ------------------------------------------------------------------ helpers
    def armed(self):
        return self.state == KICK_ARMED or self.state == KICK_PREP

    def heartbeat_ok(self, now_ms):
        c = self.cfg
        enforced = c.HEARTBEAT_MODE == 2 or (c.HEARTBEAT_MODE == 1 and self._hb_seen)
        return (not enforced) or utime.ticks_diff(now_ms, self._t_rx) < c.HEARTBEAT_TIMEOUT_MS

    def kick_ready_v(self, ceiling):
        c = self.cfg
        if ceiling is None:
            return c.KICK_READY_V
        return min(c.KICK_READY_V, ceiling - c.CEILING_HYST_V)

    def damp_start_v(self, ceiling):
        c = self.cfg
        if ceiling is None:
            return c.DAMP_START_V
        return min(c.DAMP_START_V, ceiling - c.CEILING_HYST_V)

    def damp_min_v(self, ceiling):
        """Hold ends below this. Scales down under a bench ceiling so low-voltage damp tests still run."""
        c = self.cfg
        if ceiling is None:
            return c.DAMP_MIN_V
        return min(c.DAMP_MIN_V, ceiling * c.DAMP_MIN_V_CEILING_FRAC)

    def _reject(self, cmd, reason):
        self.events.append((P.EV_CMD_REJECTED, cmd.can_id, reason, cmd.kind))

    def raise_fault(self, code, why, now_ms):
        """Called by the app for hardware faults (kicker, power stage)."""
        if self.state != FAULT:
            self.fault = code
            self.actions.append((ACT_ABORT,))
            self.events.append((P.EV_FAULT, code, 0, 0))
            self._go(FAULT, why, now_ms)

    # ------------------------------------------------------------------ commands
    def on_command(self, cmd, now_ms):
        c = self.cfg
        self._t_rx = now_ms
        self._comms_lost_reported = False
        k = cmd.kind
        if k == P.CMD_HEARTBEAT:
            self._hb_seen = True
            return
        s = self.state

        if k == P.CMD_MODE and cmd.mode == P.MODE_IDLE:
            self.actions.append((ACT_ABORT,))
            self.clear_faults_req = True
            self.fault = P.F_NONE
            self._pending = None
            if s != IDLE:
                self._go(IDLE, "cmd_idle", now_ms)
            return
        if s == FAULT:
            self._reject(cmd, P.REJ_FAULTED)
            return

        if k == P.CMD_MODE:            # MODE CHARGE
            if cmd.implied:            # legacy kick/damp frame: only wakes the board from Idle
                if s == IDLE:
                    self._go(READY, "cmd_charge", now_ms)
                return
            self._pending = None
            if s == IDLE:
                self._go(READY, "cmd_charge", now_ms)
            elif s in _DAMP_STATES:
                self.actions.append((ACT_STOP_PWM,))
                self._go(READY, "cmd_charge", now_ms)
            elif s in _KICK_STATES:
                self._go(READY, "cmd_charge", now_ms)
            return

        if s == IDLE:
            self._reject(cmd, P.REJ_NOT_CHARGING)
            return

        if k == P.CMD_KICK:
            if not (c.KICK_MIN_US <= cmd.width_us <= c.KICK_MAX_US):
                self._reject(cmd, P.REJ_OUT_OF_RANGE)
                return
            arm_ms = cmd.arm_ms if cmd.arm_ms else c.KICK_ARM_DEFAULT_MS
            if arm_ms > c.KICK_ARM_MAX_MS:
                self._reject(cmd, P.REJ_OUT_OF_RANGE)
                return
            if s == KICK_COOLDOWN:
                self._store_pending(cmd, arm_ms)
                return
            if s in _DAMP_STATES:
                self.actions.append((ACT_STOP_PWM,))
            self._arm(cmd.trigger, cmd.width_us, arm_ms, now_ms)
            return

        if k == P.CMD_DAMP:
            freq = cmd.freq_hz if cmd.freq_hz else c.DAMP_FREQ_DEFAULT
            hold = cmd.hold_ms if cmd.hold_ms else c.DAMP_HOLD_MS_DEFAULT
            init = cmd.init_kick_us if cmd.init_kick_us else c.DAMP_INITIAL_KICK_US
            if (not (c.DAMP_FREQ_MIN <= freq <= c.DAMP_FREQ_MAX)
                    or not (c.DAMP_DUTY_MIN <= cmd.duty <= c.DAMP_DUTY_MAX)
                    or hold > c.DAMP_HOLD_MS_MAX
                    or not (c.KICK_MIN_US <= init <= c.KICK_MAX_US)):
                self._reject(cmd, P.REJ_OUT_OF_RANGE)
                return
            if s == KICK_COOLDOWN:
                p = _Pending()
                p.kind = P.CMD_DAMP
                p.freq_hz, p.duty, p.hold_ms, p.init_kick_us = freq, cmd.duty, hold, init
                self._pending = p
                return
            in_damp = s in _DAMP_STATES
            self._damp_freq, self._damp_duty = freq, cmd.duty
            self._damp_hold_ms, self._damp_init_us = hold, init
            if not in_damp:            # READY / armed kick: (re)start a damp; same-mode frames only update params
                self._go(DAMP_WAIT_HV, "cmd_damp", now_ms)

    def _store_pending(self, cmd, arm_ms):
        p = _Pending()
        p.kind = P.CMD_KICK
        p.trigger, p.width_us, p.arm_ms = cmd.trigger, cmd.width_us, arm_ms
        self._pending = p

    def _arm(self, trigger, width_us, arm_ms, now_ms, why_prefix="cmd"):
        self._kick_width = width_us
        self._kick_trigger = trigger
        self._arm_until = utime.ticks_add(now_ms, arm_ms)
        target = KICK_PREP if trigger == P.TRIGGER_NOW else KICK_ARMED
        why = why_prefix + ("_kick_now" if target == KICK_PREP else "_kick_beam")
        if self.state != target:
            self._go(target, why, now_ms)

    # ------------------------------------------------------------------ fast path
    def fast_fire_width(self, now_ms, inp):
        """Width the break-beam IRQ may fire right now, or 0. Re-evaluated every loop."""
        if (self.state == KICK_ARMED
                and utime.ticks_diff(self._arm_until, now_ms) > 0
                and inp.charge_quiet_ms >= self.cfg.PRE_FIRE_SETTLE_MS
                and inp.hv >= self.cfg.KICK_MIN_FIRE_V
                and inp.kicker_idle):
            return self._kick_width
        return 0

    def on_fast_fired(self, now_ms, hv_before):
        """The IRQ fired a kick. Normally from KickArmed; a command may have moved us on meanwhile."""
        self._kick_hv_before = hv_before
        self._kick_fired_width = self._kick_width
        if self.state == KICK_ARMED:
            self._go(KICK_COOLDOWN, "fast_fire", now_ms)
        else:
            self.events.append((P.EV_KICK_FIRED, self._kick_width, int(hv_before * 10), 0))

    # ------------------------------------------------------------------ one pass
    def step(self, now_ms, now_us, inp, charge_lock_fault):
        """charge_lock_fault: the charge machine's fault code while it is Locked, else 0."""
        c = self.cfg
        if charge_lock_fault and self.state != FAULT and self.state != IDLE:
            self.raise_fault(charge_lock_fault, "charge_locked", now_ms)
        if not self.heartbeat_ok(now_ms) and self.state != IDLE and self.state != FAULT:
            self.actions.append((ACT_ABORT,))
            self._pending = None
            if not self._comms_lost_reported:
                self.events.append((P.EV_COMMS_LOST, 0, 0, 0))
                self._comms_lost_reported = True
            self._go(IDLE, "comms_lost", now_ms)

        s = self.state
        if s == KICK_ARMED:
            if utime.ticks_diff(now_ms, self._arm_until) >= 0:
                self.events.append((P.EV_ARM_EXPIRED, self._kick_width, 0, 0))
                self._go(READY, "expired", now_ms)
            elif inp.beam:
                self._go(KICK_PREP, "beam", now_ms)

        s = self.state
        if s == KICK_PREP:
            if utime.ticks_diff(now_ms, self._arm_until) >= 0:
                self.events.append((P.EV_ARM_EXPIRED, self._kick_width, 0, 0))
                self._go(READY, "expired", now_ms)
            elif (inp.hv >= c.KICK_MIN_FIRE_V and inp.charge_quiet_ms >= c.PRE_FIRE_SETTLE_MS
                    and inp.kicker_idle):
                self.actions.append((ACT_FIRE, self._kick_width))
                self._kick_hv_before = inp.hv
                self._kick_fired_width = self._kick_width
                self._go(KICK_COOLDOWN, "fire", now_ms)

        elif s == KICK_COOLDOWN:
            if self.elapsed_ms(now_ms) >= c.KICK_COOLDOWN_MS and inp.kicker_idle:
                self.events.append((P.EV_KICK_FIRED, self._kick_fired_width,
                                    int(self._kick_hv_before * 10), int(inp.hv * 10)))
                p = self._pending
                self._pending = None
                if p is None:
                    self._go(READY, "cooled", now_ms)
                elif p.kind == P.CMD_KICK:
                    self._arm(p.trigger, p.width_us, p.arm_ms, now_ms, "pending")
                else:
                    self._damp_freq, self._damp_duty = p.freq_hz, p.duty
                    self._damp_hold_ms, self._damp_init_us = p.hold_ms, p.init_kick_us
                    self._go(DAMP_WAIT_HV, "pending_damp", now_ms)

        elif s == DAMP_WAIT_HV:
            if inp.hv >= self.damp_start_v(inp.ceiling):
                self._go(DAMP_PREP, "hv_ready", now_ms)
            elif self.elapsed_ms(now_ms) >= c.DAMP_READY_TIMEOUT_MS:
                self.events.append((P.EV_DAMP_ABORTED, P.REJ_HV_NOT_READY, int(inp.hv * 10), 0))
                self._go(READY, "hv_timeout", now_ms)

        elif s == DAMP_PREP:
            if inp.charge_quiet_ms >= c.PRE_FIRE_SETTLE_MS and inp.kicker_idle:
                self.actions.append((ACT_FIRE, self._damp_init_us))
                self._t_extend_us = now_us
                self._v_start = inp.hv
                self._go(DAMP_EXTEND, "fire_initial", now_ms)

        elif s == DAMP_EXTEND:
            if utime.ticks_diff(now_us, self._t_extend_us) >= c.DAMP_SETTLE_US and inp.kicker_idle:
                self._v_start = inp.hv
                d, lim = kickmath.damp_duty(c, self._damp_duty, self._v_start, inp.hv, 0)
                self.duty_now, self.duty_limited = d, lim
                self.actions.append((ACT_PWM_START, self._damp_freq, d, self._damp_hold_ms * 1000))
                self._t_hold = now_ms
                self._t_seg = now_ms
                self._go(DAMP_HOLD, "settled", now_ms)

        elif s == DAMP_HOLD:
            el = self.elapsed_ms(now_ms)
            if inp.hv < self.damp_min_v(inp.ceiling):
                self._end_damp(now_ms, inp, "hv_floor")
            elif el >= self._damp_hold_ms or (el >= c.DAMP_SEGMENT_MS and not inp.kicker_pwm_running):
                self._end_damp(now_ms, inp, "hold_done")
            elif utime.ticks_diff(now_ms, self._t_seg) >= c.DAMP_SEGMENT_MS:
                self._t_seg = now_ms
                d, lim = kickmath.damp_duty(c, self._damp_duty, self._v_start, inp.hv, el)
                self.duty_now, self.duty_limited = d, lim
                self.actions.append((ACT_PWM_DUTY, d))

        s = self.state
        self.discharge = s == IDLE or s == FAULT
        # Charging is never on while a kick fires: KickPrep stops it immediately (mid-cycle too),
        # the pulse waits PRE_FIRE_SETTLE_MS of quiet, then fires at whatever voltage the bank has.
        self.charge_allowed = s == READY or s == KICK_ARMED or s == DAMP_WAIT_HV

    def _end_damp(self, now_ms, inp, why):
        self.actions.append((ACT_STOP_PWM,))
        self.events.append((P.EV_DAMP_DONE, self.elapsed_ms(now_ms),
                            int(self._v_start * 10), int(inp.hv * 10)))
        self._go(READY, why, now_ms)
