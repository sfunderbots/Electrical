# Mode control state machine: what the board is doing for the robot (idle, charging, auto-kick, damping).
#
# Source: Henry's chart flowcharts/chicker-mode-control-state-machine-v2.drawio, agreed element by element in
# docs/MODE-STATE-MACHINE-REVIEW.md ("element N" / note names below point there). State names are the chart's.
#
# How to read this file
#   ARROWS is the chart as a table: one row per arrow, checked top to bottom, first match wins.
#   Each row names a "condition" method (should this arrow be taken now?) and, if the arrow does something on
#   the way (the chart's action boxes such as "Enable Charging" or "de-arm kick / stop PWM"), the action
#   method of the same name prefixed with "do_" runs when the arrow is taken.
#   The ANY STATE rows sit at the top, so they win over a state's own arrows.
#
# Authorship (coupled review): this machine only WRITES its own outputs
#   chg_stop_mode_ctrl   1 = charge machine stops charging AND dumps the caps
#   chg_hold_mode_ctrl   1 = charge machine stops charging, keeps the caps charged (a veto: never starts it)
#   actions              requests for the kicker driver (fire, start / renew / stop PWM, set duty)
#   events               messages for the Pi
# and only READS the charge machine through chg_disable_chip_level. It never touches a pin.
#
# Commands: app.py decodes CAN frames and hands this machine ONE good command per loop pass, in the order they
# arrived (ModeInputs.command). Bad frames and heartbeats never reach the state machine (mode element 7).

from micropython import const
import utime
import protocol as P
from kicker import damp_duty
from sm_base import StateMachine, AnyState, expand

# ---------------------------------------------------------------------------------------- states
WAIT_FOR_BAT = const(0)               # "Wait for Bat"
START_BKBM = const(1)                 # "Start BKBM interrupt": waits for the first heartbeat
WAIT_FOR_MESSAGE = const(2)           # "Wait for Message" (idle or charging, nothing armed)
LOCKED = const(3)                     # "Locked. Message pi4 to substitute" (end state)
AUTOKICK_ENABLE_CHARGING = const(4)   # AUTOKICK "Enable Charging": waits for "last kick >= 500ms"
ARM_KICK = const(5)                   # "Arm Kick": waits for "HV >= 100V && kick assigned"
CHARGE_HOLD = const(6)                # "Charge Hold" -> GUARD -> "Kick with PW Assigned"
DAMP_ENABLE_CHARGING = const(7)       # DAMP "Enable Charging": waits for "last kick >= 500ms && HV >= 180"
DAMP_CHARGE_HOLD = const(8)           # DAMP "Charge Hold": waits for done == 0
INITIAL_KICK = const(9)               # "Initial Kick", then "wait for 5 x PW = 4ms"
DAMP_WAIT = const(10)                 # damp loop "Wait" (with HV Adjustment / Set Duty every 25 ms)
DAMP_STOP_PWM = const(11)             # low-bank kick: "Stop PWM", then "wait 50ms"

NAMES = ("WaitForBat", "StartBKBM", "WaitForMessage", "Locked", "AutokickEnableCharging", "ArmKick",
         "ChargeHold", "DampEnableCharging", "DampChargeHold", "InitialKick", "DampWait", "DampStopPWM")

# States where commands are not acted on (mode element 7: before the board is ready, and Locked)
NOT_ACCEPTING_COMMANDS = (WAIT_FOR_BAT, START_BKBM, LOCKED)

# Kicker actions handed to app.py (executed right after step())
ACT_ABORT = const(1)              # (ACT_ABORT,)                 pin low now, even mid-pulse (battery lost)
ACT_STOP_PWM = const(2)           # (ACT_STOP_PWM,)              stop a damping hold; a single kick pulse finishes
ACT_FIRE = const(3)               # (ACT_FIRE, width_us)
ACT_FIRE_REPLACING_PWM = const(4) # (ACT_FIRE_REPLACING_PWM, width_us)   kick out of a damping hold
ACT_PWM_START = const(5)          # (ACT_PWM_START, freq_hz, duty_pct, duration_us)
ACT_PWM_RENEW = const(6)          # (ACT_PWM_RENEW, duration_us)
ACT_PWM_DUTY = const(7)           # (ACT_PWM_DUTY, duty_pct)

# ---------------------------------------------------------------------------------------- arrows
_ANY_READY = AnyState(*NOT_ACCEPTING_COMMANDS)   # commands are handled from every other state

ARROWS = (
    # ---- ANY STATE rules, in priority order ------------------------------------------------------------
    # element 5: the charge machine disabled itself -> Locked (end state, beats everything)
    (AnyState(LOCKED), "chg_disable_1", LOCKED, "charge_disabled", "chg_disable_chip_level == 1"),
    # element 1: battery gone -> abort everything, even a kick in flight
    (AnyState(LOCKED, WAIT_FOR_BAT), "chg_ok_0", WAIT_FOR_BAT, "battery_lost", "chg_ok == 0"),
    # element 4: no frame from the Pi for 5 s -> Disable Charging, discharge -> de-arm, stop PWM -> Start BKBM
    (AnyState(LOCKED, WAIT_FOR_BAT, START_BKBM), "heartbeat_stopped", START_BKBM, "heartbeat_lost",
     "heartbeat == stops"),

    # ---- a good command arrived (element 7): de-arm kick -> Command Received -> one of these ---------------
    (_ANY_READY, "cmd_idle", WAIT_FOR_MESSAGE, "cmd_idle", "mode == MODE_IDLE"),
    (_ANY_READY, "cmd_chg", WAIT_FOR_MESSAGE, "cmd_chg", "mode == MODE_CHG"),
    (_ANY_READY, "cmd_autokick_pwm_running", DAMP_WAIT, "cmd_autokick_in_damp",
     "mode == MODE_AUTOKICK && PWM running"),
    (_ANY_READY, "cmd_autokick_pwm_not_running", AUTOKICK_ENABLE_CHARGING, "cmd_autokick",
     "mode == MODE_AUTOKICK && PWM not running"),
    (_ANY_READY, "cmd_damp_pwm_running", DAMP_WAIT, "cmd_damp_renew", "mode == MODE_DAMP && PWM running"),
    (_ANY_READY, "cmd_damp_pwm_not_running", DAMP_ENABLE_CHARGING, "cmd_damp", "mode == MODE_DAMP && PWM not running"),

    # ---- start-up (elements 2, 3, 6) ----------------------------------------------------------------------
    ((WAIT_FOR_BAT,), "chg_ok_1", START_BKBM, "battery_ok", "chg_ok == 1"),
    ((START_BKBM,), "heartbeat_ok", WAIT_FOR_MESSAGE, "heartbeat_ok", "heartbeat == OK"),

    # ---- AUTOKICK (elements 9-12) -------------------------------------------------------------------------
    ((AUTOKICK_ENABLE_CHARGING,), "kick_spacing_ok", ARM_KICK, "arm", "last kick >= KICK_MIN_INTERVAL_MS"),
    ((ARM_KICK,), "hv_ge_100_and_kick_assigned", CHARGE_HOLD, "kick_assigned", "HV >= 100V && kick assigned"),
    ((CHARGE_HOLD,), "kick_guard_ok", WAIT_FOR_MESSAGE, "kicked",
     "done == 0, GUARD: PW >= 100 us && PW <= 5000 us -> Kick with PW Assigned"),

    # ---- DAMP (elements 14-16) ----------------------------------------------------------------------------
    ((DAMP_ENABLE_CHARGING,), "kick_spacing_ok_and_hv_ge_180", DAMP_CHARGE_HOLD, "damp_ready",
     "last kick >= KICK_MIN_INTERVAL_MS && HV >= 180"),
    ((DAMP_CHARGE_HOLD,), "kick_guard_ok", INITIAL_KICK, "initial_kick", "done == 0"),
    ((INITIAL_KICK,), "waited_4ms", DAMP_WAIT, "start_pwm", "wait for 5 x PW = 4ms -> Start Damping PWM"),
    # kick out of the damp (elements 19, 20), checked before the timeout: the ball is there
    ((DAMP_WAIT,), "damp_kick_ready", WAIT_FOR_MESSAGE, "damp_kicked", "HV >= 100V && kick assigned -> GUARD"),
    ((DAMP_WAIT,), "damp_kick_bank_low", DAMP_STOP_PWM, "damp_kick_bank_low", "HV < 100V && kick assigned"),
    ((DAMP_WAIT,), "damp_timed_out", WAIT_FOR_MESSAGE, "damp_done", "time since last DAMP >= timeout || max hold"),
    ((DAMP_STOP_PWM,), "waited_50ms", ARM_KICK, "recharge_for_kick", "wait 50ms -> Enable Charging"),
)

TRANSITIONS = expand(ARROWS)          # used by the simulator's coverage report


class ModeInputs:
    """Read-only snapshot app.py fills every loop pass."""
    __slots__ = ("chg_ok", "chg_disable_chip_level", "heartbeat_ok", "command", "hv", "done",
                 "charge_pin_low", "pwm_running", "kicker_idle", "now_us")

    def __init__(self):
        self.chg_ok = 0                  # 1 = battery present
        self.chg_disable_chip_level = 0  # 1 = the charge machine is Disabled
        self.heartbeat_ok = False        # a frame from the Pi within HEARTBEAT_TIMEOUT_MS
        self.command = None              # the next good command (protocol.Command), or None
        self.hv = 0.0                    # bank voltage, V
        self.done = 0                    # LT3750 DONE pin: 0 = the charger is not running
        self.charge_pin_low = False      # the CHARGE pin is low right now (power stage)
        self.pwm_running = False         # "PWM running": a damping hold is on (kicker driver)
        self.kicker_idle = True          # nothing queued or running on the kick PIO
        self.now_us = 0                  # ticks_us at the start of this pass (the 4 ms wait)


class ModeSM(StateMachine):
    def __init__(self, cfg, now_ms):
        self.cfg = cfg
        StateMachine.__init__(self, NAMES, ARROWS, WAIT_FOR_BAT, now_ms)
        # ---- outputs read by app.py
        self.chg_stop_mode_ctrl = 1      # boot: no charging, caps dumped
        self.chg_hold_mode_ctrl = 0
        self.actions = []
        self.events = []
        # ---- the chart's variables
        self.mode = P.MODE_IDLE          # last commanded mode ("mode == MODE_..." on the chart)
        self.armed = 0                   # "armed": the break-beam may assign a kick
        self.kick_assigned = 0           # "kick assigned": set by the break-beam mini machine, latched
        self.kick_width_us = 0           # "PW" of the armed kick
        # ---- kick spacing (KICK_MIN_INTERVAL_MS counts from the last pulse, Initial Kick included)
        self._t_last_kick = None
        # ---- damp parameters (from the DAMP command) and timers
        self._damp_freq_hz = cfg.DAMP_FREQ_DEFAULT
        self._damp_duty_pct = cfg.DAMP_DUTY_DEFAULT
        self._damp_timeout_ms = cfg.DAMP_HOLD_MS_DEFAULT
        self._damp_initial_kick_us = cfg.DAMP_INITIAL_KICK_US
        self._t_initial_kick_ms = now_ms     # for "max hold"
        self._t_initial_kick_us = 0          # for the 4 ms wait
        self._t_last_damp = now_ms           # "time since last DAMP" (renew restarts it)
        self._t_last_adjust = now_ms         # "time since last HV adjustment"
        self._damp_v_start = 0.0
        self.duty_now = 0.0                  # current damping duty, for telemetry
        self._during = {DAMP_WAIT: self._hv_adjustment}

    # ==================================================================== called by app.py
    def step(self, now_ms, inp):
        """One loop pass. Follows up to 3 arrows (pass-through boxes cost no extra pass)."""
        self.run(now_ms, inp, max_moves=3)

    def accepts_commands(self):
        return self.state not in NOT_ACCEPTING_COMMANDS

    def update_breakbeam(self, beam_on):
        """Breakbeam Mini State Machine (element 13): ball in the beam while armed -> Assign Kick.
        "kick assigned" stays latched until the kick is de-armed (note KCK-2)."""
        if beam_on and self.armed and (self.mode == P.MODE_AUTOKICK or self.mode == P.MODE_DAMP):
            self.kick_assigned = 1

    def idle_like(self):
        """True when nothing may fire: before Idle, Locked, or idle / charging with nothing armed."""
        return self.state in NOT_ACCEPTING_COMMANDS or (self.state == WAIT_FOR_MESSAGE and not self.armed)

    # ==================================================================== conditions (one per arrow)
    # Each answers "should this arrow be taken now?" and changes nothing.

    # ANY STATE
    def chg_disable_1(self, now, i):
        return i.chg_disable_chip_level == 1

    def chg_ok_0(self, now, i):
        return i.chg_ok == 0

    def heartbeat_stopped(self, now, i):
        return not i.heartbeat_ok

    # commands (Command Received)
    def _cmd_is(self, i, mode):
        return i.command is not None and i.command.mode == mode

    def cmd_idle(self, now, i):
        return self._cmd_is(i, P.MODE_IDLE)

    def cmd_chg(self, now, i):
        return self._cmd_is(i, P.MODE_CHG)

    def cmd_autokick_pwm_running(self, now, i):
        return self._cmd_is(i, P.MODE_AUTOKICK) and i.pwm_running

    def cmd_autokick_pwm_not_running(self, now, i):
        return self._cmd_is(i, P.MODE_AUTOKICK) and not i.pwm_running

    def cmd_damp_pwm_running(self, now, i):
        return self._cmd_is(i, P.MODE_DAMP) and i.pwm_running

    def cmd_damp_pwm_not_running(self, now, i):
        return self._cmd_is(i, P.MODE_DAMP) and not i.pwm_running

    # start-up
    def chg_ok_1(self, now, i):
        return i.chg_ok == 1

    def heartbeat_ok(self, now, i):
        return i.heartbeat_ok

    # kick path
    def kick_spacing_ok(self, now, i):
        return (self._t_last_kick is None or
                utime.ticks_diff(now, self._t_last_kick) >= self.cfg.KICK_MIN_INTERVAL_MS)

    def hv_ge_100_and_kick_assigned(self, now, i):
        return i.hv >= self.cfg.KICK_MIN_HV and self.kick_assigned == 1

    def kick_guard_ok(self, now, i):
        # The kick GUARD: done == 0 (the LT3750 has stopped), CHARGE is low, the PIO is free. The PW range
        # (100-5000 us) was already checked when the command was decoded; the kicker driver checks it again.
        return i.done == 0 and i.charge_pin_low and i.kicker_idle

    # damp path
    def kick_spacing_ok_and_hv_ge_180(self, now, i):
        return self.kick_spacing_ok(now, i) and i.hv >= self.cfg.DAMP_START_V

    def waited_4ms(self, now, i):
        return (utime.ticks_diff(i.now_us, self._t_initial_kick_us) >= self.cfg.DAMP_SETTLE_US
                and i.kicker_idle)

    def damp_kick_ready(self, now, i):
        return self.kick_assigned == 1 and i.hv >= self.cfg.KICK_MIN_HV and i.done == 0 and i.charge_pin_low

    def damp_kick_bank_low(self, now, i):
        return self.kick_assigned == 1 and i.hv < self.cfg.KICK_MIN_HV

    def damp_timed_out(self, now, i):
        c = self.cfg
        return (utime.ticks_diff(now, self._t_last_damp) >= self._damp_timeout_ms or
                utime.ticks_diff(now, self._t_initial_kick_ms) >= c.DAMP_MAX_TOTAL_MS)

    def waited_50ms(self, now, i):
        return self.in_state_ms(now) >= self.cfg.DAMP_LOW_HV_STOP_WAIT_MS

    # ==================================================================== the chart's action boxes
    # Small helpers named after the boxes, used by the arrow actions below.
    def _de_arm_kick(self):
        self.armed = 0
        self.kick_assigned = 0

    def _stop_pwm(self):
        self.actions.append((ACT_STOP_PWM,))

    def _enable_charging(self):
        self.chg_stop_mode_ctrl = 0
        self.chg_hold_mode_ctrl = 0

    def _charge_hold(self):
        self.chg_hold_mode_ctrl = 1

    def _disable_charging_and_discharge(self):
        self.chg_stop_mode_ctrl = 1
        self.chg_hold_mode_ctrl = 0

    def _fire(self, now, width_us, replacing_pwm=False):
        self.actions.append((ACT_FIRE_REPLACING_PWM if replacing_pwm else ACT_FIRE, width_us))
        self._t_last_kick = now

    # ==================================================================== arrow actions
    def _take(self, row, now, i):
        why = row[3]
        action = getattr(self, "do_" + why, None)
        if action is not None:
            action(now, i)
        if i.command is not None and why.startswith("cmd_"):
            i.command = None             # each command is acted on exactly once
        self._go(row[2], why, now, i)

    # ANY STATE
    def do_charge_disabled(self, now, i):
        # Locked (element 5) stops everything on the kicker side: kick de-armed, any pulse or damping PWM cut now
        # (found by the state x event matrix: a damp running when the charge machine disabled itself kept going).
        # It sets no charge flags: the charge machine has already stopped charging and dumped the caps. Telling
        # the Pi: the charge machine's FAULT event carries the reason, app.py puts the fault in every STATUS frame.
        self.actions.append((ACT_ABORT,))
        self._de_arm_kick()

    def do_battery_lost(self, now, i):
        self.actions.append((ACT_ABORT,))
        self._de_arm_kick()
        self._disable_charging_and_discharge()

    def do_heartbeat_lost(self, now, i):
        self._disable_charging_and_discharge()      # "Disable Charging, discharge"
        self._de_arm_kick()                         # "De-arm kick and stop PWM"
        self._stop_pwm()
        self.events.append((P.EV_COMMS_LOST, 0, 0, 0))

    # commands (every good command de-arms first, except a DAMP renew: element 17)
    def do_cmd_idle(self, now, i):
        self._de_arm_kick()                         # "de-arm kick / stop PWM"
        self._stop_pwm()
        self._disable_charging_and_discharge()      # "Set Idle Mode (chg inhibited, discharging)"
        self.mode = P.MODE_IDLE

    def do_cmd_chg(self, now, i):
        self._de_arm_kick()                         # "de-arm kick / stop PWM"
        self._stop_pwm()
        self._enable_charging()                     # "Enable Charging"
        self.mode = P.MODE_CHG

    def do_cmd_autokick_in_damp(self, now, i):
        # element 18: arm inside the damp; no Enable Charging (the hold stays on), mode set back to MODE_DAMP.
        # The kick then waits for the break-beam, as on the chart. CAUTION, to test on the bench (mode review note
        # DMP-5): the held-out plunger may block the beam during a damp, so the beam may not tell us about the ball.
        self.armed = 1
        self.kick_assigned = 0
        self.kick_width_us = i.command.width_us
        self.mode = P.MODE_DAMP

    def do_cmd_autokick(self, now, i):
        self._de_arm_kick()
        self._enable_charging()                     # AUTOKICK "Enable Charging"
        self.kick_width_us = i.command.width_us
        self.mode = P.MODE_AUTOKICK

    def do_cmd_damp_renew(self, now, i):
        # element 17: "Renew Damp start variable". The timeout restarts from now, no new Initial Kick,
        # parameters kept, a queued kick stays armed.
        self._t_last_damp = now
        self.actions.append((ACT_PWM_RENEW, self._pwm_duration_us()))
        self.mode = P.MODE_DAMP

    def do_cmd_damp(self, now, i):
        self._de_arm_kick()
        self._enable_charging()                     # DAMP "Enable Charging"
        c = i.command
        self._damp_freq_hz = c.freq_hz
        self._damp_duty_pct = c.duty
        self._damp_timeout_ms = c.hold_ms
        self._damp_initial_kick_us = c.init_kick_us
        self.mode = P.MODE_DAMP

    # start-up
    def do_heartbeat_ok(self, now, i):
        # "Set Idle Mode (chg inhibited, discharging)" on the way into Wait for Message
        self._disable_charging_and_discharge()
        self.mode = P.MODE_IDLE

    # kick path
    def do_arm(self, now, i):
        self.armed = 1                              # "Arm Kick"

    def do_kick_assigned(self, now, i):
        self._charge_hold()                         # "Charge Hold"

    def do_kicked(self, now, i):
        self._fire(now, self.kick_width_us)         # "Kick with PW Assigned"
        self.events.append((P.EV_KICK_FIRED, self.kick_width_us, int(i.hv * 10), 0))
        self._de_arm_kick()                         # "de-arm kick / stop PWM"
        self._stop_pwm()
        self._enable_charging()                     # "Enable Charging" -> Wait for Message

    # damp path
    def do_damp_ready(self, now, i):
        self._charge_hold()                         # DAMP "Charge Hold"

    def do_initial_kick(self, now, i):
        self._fire(now, self._damp_initial_kick_us)  # "Initial Kick"
        self._t_initial_kick_ms = now
        self._t_initial_kick_us = i.now_us

    def do_start_pwm(self, now, i):
        # "Start Damping PWM": duty from the command, adjusted for HV every DAMP_ADJUST_PERIOD_MS
        self._t_last_damp = now
        self._t_last_adjust = now
        self._damp_v_start = i.hv
        d, _ = damp_duty(self.cfg, self._damp_duty_pct, self._damp_v_start, i.hv, 0)
        self.duty_now = d
        self.actions.append((ACT_PWM_START, self._damp_freq_hz, d, self._pwm_duration_us()))

    def do_damp_kicked(self, now, i):
        # element 19: stop the PWM and fire in the same PIO step, so the plunger is held until the kick
        self._fire(now, self.kick_width_us, replacing_pwm=True)
        self.events.append((P.EV_KICK_FIRED, self.kick_width_us, int(i.hv * 10), 0))
        self._end_damp_event(now, i)
        self._de_arm_kick()
        self._enable_charging()

    def do_damp_kick_bank_low(self, now, i):
        # element 20: bank too low to kick -> the damp ends here ("Stop PWM"); the kick stays assigned
        self._stop_pwm()
        self._end_damp_event(now, i)

    def do_damp_done(self, now, i):
        self._end_damp_event(now, i)
        self._de_arm_kick()                         # "de-arm kick / stop PWM" (an unused kick ends with the damp)
        self._stop_pwm()
        self._enable_charging()                     # "Enable Charging" -> Wait for Message

    def do_recharge_for_kick(self, now, i):
        self._enable_charging()                     # "Enable Charging"; Arm Kick then waits for HV >= 100 V

    # ==================================================================== damp loop bookkeeping
    def _hv_adjustment(self, now, i):
        """While in the damp loop's Wait: every DAMP_ADJUST_PERIOD_MS, "HV Adjustment" -> "Set Duty"."""
        if utime.ticks_diff(now, self._t_last_adjust) < self.cfg.DAMP_ADJUST_PERIOD_MS:
            return
        self._t_last_adjust = now
        held_ms = utime.ticks_diff(now, self._t_initial_kick_ms)
        d, _ = damp_duty(self.cfg, self._damp_duty_pct, self._damp_v_start, i.hv, held_ms)
        if d != self.duty_now:
            self.duty_now = d
            self.actions.append((ACT_PWM_DUTY, d))

    def _pwm_duration_us(self):
        # The kicker runs the PWM a little past the timeout (two segments) so that the state machine's own
        # timeout exit, not the driver, ends a normal damp. The driver still caps it at DAMP_MAX_TOTAL_MS.
        return (self._damp_timeout_ms + 2 * self.cfg.DAMP_ADJUST_PERIOD_MS) * 1000

    def _end_damp_event(self, now, i):
        self.events.append((P.EV_DAMP_DONE, utime.ticks_diff(now, self._t_initial_kick_ms),
                            int(self._damp_v_start * 10), int(i.hv * 10)))
