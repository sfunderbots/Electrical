# Charge state machine: decides when the LT3750 flyback charger runs and when the caps are dumped.
#
# Source: Henry's chart flowcharts/chicker-charge-state-machine-v2.drawio, agreed element by element in
# docs/CHARGE-STATE-MACHINE-REVIEW.md ("element N" below points there). State names are the chart's names.
#
# How to read this file
#   ARROWS is the chart as a table: one row per arrow, checked top to bottom, first match wins.
#   Each row names a small "condition" method further down that answers "should this arrow be taken now?".
#   The ANY STATE rows sit at the top, so they win over a state's own arrows (charge review, ANY STATE rules).
#
# Inputs (ChargeInputs, filled by app.py every loop pass, chart names):
#   chg_ok               1 = battery present (battery monitor: 14.7-17.0 V, debounced)
#   chg_stop_mode_ctrl   1 = stop charging AND dump the caps            (written only by the mode machine)
#   chg_hold_mode_ctrl   1 = stop charging, keep the caps charged       (written only by the mode machine)
#   done                 LT3750 DONE pin right now: 1 = charging, 0 = finished / stopped / faulted
#   done_rose            1 = DONE had a rising edge since CHARGE last went high (latched by a pin interrupt)
#   charge_pin           the CHARGE pin as the power stage actually drives it (1 = high)
#   hv                   bank voltage in volts (only used for the overvoltage rule X-1)
#
# Outputs (read by app.py after step()):
#   want_charge          True = CHARGE pin should be high
#   want_dump            True = the dump (~DISCHARGE low) should be on
#   chg_disable_chip_level   1 once Disabled (never cleared until reboot); the mode machine goes to Locked
#   fault                why it is Disabled (protocol F_* code, sent to the Pi)
# This machine never touches a pin itself: the power stage (power_stage.py) drives CHARGE and ~DISCHARGE
# and adds its own 50 ms dead time so the two can never be on together (X-5).
#
# Every charge decision is made on TIMERS and the DONE pin, never on the HV reading (review note N-0).

from micropython import const
import utime
import protocol as P
from sm_base import StateMachine, AnyState, expand

# ---------------------------------------------------------------------------------------- states
WAIT_FOR_BAT = const(0)                  # "Wait for Bat"
STARTUP = const(1)                       # "Startup (reset vars)"
NEW_CHARGE_FROM_BAT = const(2)           # "New Charge from Bat" (wait 2 s after the battery appears)
CHARGE_TOGGLE_RESET_FROM_BAT = const(3)  # "Charge Toggle Reset from Bat" (waits at the 500 ms GUARD)
INHIBITED = const(4)                     # "Inhibited": stop charging and dump the caps
NOT_CHARGING = const(5)                  # "Not Charging": stop charging, keep the caps charged
NEW_CHARGE = const(6)                    # "New Charge" (CHARGE low 50 ms)
CHARGE_TOGGLE_RESET = const(7)           # "Charge Toggle Reset" (waits at the 500 ms GUARD)
CHARGE_TOGGLE_WAIT = const(8)            # "Charge Toggle Wait": CHARGE high, did the charger start?
CHARGE_STARTED = const(9)                # "Charge Started": charging, wait for the 4500 ms check
CHARGED_TO_50V = const(10)               # "Charged to ~50V" (safe_chg bench test only)
ASSUMING_CHARGED = const(11)             # "Assuming Charged": cycle verified, wait for the 5 s top-off
CHARGE_TOGGLE = const(12)                # "Charge Toggle": CHARGE low 50 ms, then a new cycle
DISABLED = const(13)                     # "Disabled, set chg_disable_chip_level = 1" (one-way)

NAMES = ("WaitForBat", "Startup", "NewChargeFromBat", "ChargeToggleResetFromBat", "Inhibited",
         "NotCharging", "NewCharge", "ChargeToggleReset", "ChargeToggleWait", "ChargeStarted",
         "ChargedTo50V", "AssumingCharged", "ChargeToggle", "Disabled")

# What each state does with the two power-stage pins: state -> (CHARGE high?, dump on?)
# CHARGE stays high through Charge Started and Assuming Charged: the LT3750 stops by itself at its target,
# and the next cycle starts with the Charge Toggle (CHARGE low 50 ms, then high again).
PINS = {
    WAIT_FOR_BAT: (False, True),          # no battery: dump (the hardware's own safe state)
    STARTUP: (False, False),
    NEW_CHARGE_FROM_BAT: (False, False),
    CHARGE_TOGGLE_RESET_FROM_BAT: (False, False),
    INHIBITED: (False, True),             # stop charging and dump
    NOT_CHARGING: (False, False),         # stop charging, keep the charge
    NEW_CHARGE: (False, False),
    CHARGE_TOGGLE_RESET: (False, False),
    CHARGE_TOGGLE_WAIT: (True, False),
    CHARGE_STARTED: (True, False),
    CHARGED_TO_50V: (False, False),
    ASSUMING_CHARGED: (True, False),
    CHARGE_TOGGLE: (False, False),
    DISABLED: (False, True),              # faulty board: dump the caps (element 12)
}

# ---------------------------------------------------------------------------------------- arrows
# (from states, condition, to, why, chart label). First true row wins. Disabled has no arrows out.
ARROWS = (
    # ---- ANY STATE rules, in priority order --------------------------------------------------------
    # X-1 overvoltage first: safety beats everything (3 readings in a row, see _count_overvolt_readings)
    (AnyState(DISABLED), "hv_too_high", DISABLED, "overvoltage", "HV >= 220V (3 readings)"),
    # element 1: battery gone -> Wait for Bat. Beats the two mode-machine rules below.
    (AnyState(DISABLED, WAIT_FOR_BAT), "chg_ok_0", WAIT_FOR_BAT, "battery_lost", "chg_ok == 0"),
    # element 4: stop charging and dump. Not from Wait for Bat (battery rule wins there).
    (AnyState(DISABLED, WAIT_FOR_BAT, INHIBITED), "chg_stop_1", INHIBITED, "stop", "chg_stop_mode_ctrl == 1"),
    # element 5a: stop charging, keep the charge
    (AnyState(DISABLED, NOT_CHARGING), "hold_requested", NOT_CHARGING, "hold",
     "chg_ok == 1 && chg_stop_mode_ctrl == 0 && chg_hold_mode_ctrl == 1"),

    # ---- battery start-up path (elements 2, 3) -------------------------------------------------------
    ((WAIT_FOR_BAT,), "chg_ok_1_and_chg_stop_0", STARTUP, "battery_ok", "chg_ok == 1 && chg_stop_mode_ctrl == 0"),
    ((STARTUP,), "always", NEW_CHARGE_FROM_BAT, "vars_reset", ""),
    ((NEW_CHARGE_FROM_BAT,), "waited_2s", CHARGE_TOGGLE_RESET_FROM_BAT, "battery_settled", "Wait for 2s"),

    # ---- mode-machine path (elements 5, 5a, 6) -------------------------------------------------------
    ((INHIBITED,), "chg_ok_1_and_chg_stop_0", NOT_CHARGING, "stop_released", "chg_ok == 1 && chg_stop_mode_ctrl == 0"),
    ((NOT_CHARGING,), "chg_hold_0", NEW_CHARGE, "hold_released", "chg_hold_mode_ctrl == 0"),
    ((NEW_CHARGE,), "waited_50ms_charge_low", CHARGE_TOGGLE_RESET, "charge_low_done", "Wait for 50ms"),

    # ---- GUARD before every new cycle (element 7) -----------------------------------------------------
    ((CHARGE_TOGGLE_RESET, CHARGE_TOGGLE_RESET_FROM_BAT), "cycle_spacing_ok", CHARGE_TOGGLE_WAIT, "toggle",
     "GUARD: time since last chg >= 500ms"),

    # ---- did the charger start? (element 8) -----------------------------------------------------------
    ((CHARGE_TOGGLE_WAIT,), "waited_50ms_done_rose", CHARGE_STARTED, "started", "wait for 50ms, done == 1, latched"),
    ((CHARGE_TOGGLE_WAIT,), "waited_50ms_no_edge", DISABLED, "uvlo_thermal", "wait for 50ms, done == 0, latched"),

    # ---- safe_chg bench test (element 9, off unless SAFE_CHARGE = 1) ---------------------------------
    ((CHARGE_STARTED,), "safe_charge_time_reached", CHARGED_TO_50V, "safe_charge_done",
     "safe_chg == 1, GUARD: time since last chg >= 236ms"),
    ((CHARGED_TO_50V,), "waited_5s", DISABLED, "safe_charge_test", "wait 5s"),

    # ---- has it charged? (element 10) ----------------------------------------------------------------
    ((CHARGE_STARTED,), "checked_4500ms_done_0", ASSUMING_CHARGED, "charged",
     "safe_chg == 0, GUARD: time since last chg >= 4500ms, done == 0"),
    ((CHARGE_STARTED,), "checked_4500ms_done_1", DISABLED, "too_slow",
     "safe_chg == 0, GUARD: time since last chg >= 4500ms, done == 1"),

    # ---- 5 s top-off (element 11) --------------------------------------------------------------------
    ((ASSUMING_CHARGED,), "topoff_time_reached", CHARGE_TOGGLE, "topoff", "GUARD: time since last chg >= 5000ms"),
    ((CHARGE_TOGGLE,), "waited_50ms_charge_low", CHARGE_TOGGLE_WAIT, "retoggle", "CHARGE low 50ms"),
)

TRANSITIONS = expand(ARROWS)          # used by the simulator's coverage report

# Arrows into Disabled and the reason sent to the Pi ("Charging Disabled" bubbles on the chart).
FAULT_FOR_ARROW = {
    "overvoltage": P.F_OVERVOLT,
    "uvlo_thermal": P.F_CHG_UVLO_THERMAL,
    "too_slow": P.F_CHG_TOO_SLOW,
    "safe_charge_test": P.F_SAFE_CHARGE_TEST,
}


class ChargeInputs:
    """Everything the charge machine reads, filled by app.py every loop pass (chart names)."""
    __slots__ = ("chg_ok", "chg_stop_mode_ctrl", "chg_hold_mode_ctrl", "done", "done_rose", "charge_pin", "hv")

    def __init__(self):
        self.chg_ok = 0
        self.chg_stop_mode_ctrl = 1
        self.chg_hold_mode_ctrl = 0
        self.done = 0
        self.done_rose = 0
        self.charge_pin = 0
        self.hv = 0.0


class ChargeSM(StateMachine):
    def __init__(self, cfg, now_ms):
        self.cfg = cfg
        StateMachine.__init__(self, NAMES, ARROWS, WAIT_FOR_BAT, now_ms)
        self.want_charge = False
        self.want_dump = True
        self.chg_disable_chip_level = 0
        self.fault = P.F_NONE
        self.cycles = 0                    # charge cycles that reached Assuming Charged
        self.events = []                   # (event, a, b, c) for app.py to send to the Pi
        self.clear_done_latch = False      # tells app.py to clear the DONE edge latch before CHARGE rises
        self._t_last_chg = None            # when CHARGE last went high ("time since last chg"); None = never
        self._waiting_for_rise = False     # in Charge Toggle Wait until the power stage has really raised CHARGE
        self._overvolt_readings = 0        # X-1: readings in a row at or above OVERVOLT_V
        self._during = {CHARGE_TOGGLE_WAIT: self._note_charge_rise}

    # ==================================================================== called by app.py
    def step(self, now_ms, inp):
        """One loop pass. Follows up to 3 arrows (so pass-through boxes like Startup cost no extra pass)."""
        self._count_overvolt_readings(inp.hv)
        self.run(now_ms, inp, max_moves=3)
        self.want_charge, self.want_dump = PINS[self.state]

    def charging(self):
        return self.want_charge

    def disabled(self):
        return self.state == DISABLED

    # ==================================================================== conditions (one per arrow)
    # Each answers "should this arrow be taken now?" and changes nothing.

    # ANY STATE
    def hv_too_high(self, now, i):
        return self._overvolt_readings >= self.cfg.OVERVOLT_SAMPLES

    def chg_ok_0(self, now, i):
        return i.chg_ok == 0

    def chg_stop_1(self, now, i):
        return i.chg_stop_mode_ctrl == 1

    def hold_requested(self, now, i):
        return i.chg_ok == 1 and i.chg_stop_mode_ctrl == 0 and i.chg_hold_mode_ctrl == 1

    # start-up and mode paths
    def chg_ok_1_and_chg_stop_0(self, now, i):
        return i.chg_ok == 1 and i.chg_stop_mode_ctrl == 0

    def always(self, now, i):
        return True

    def waited_2s(self, now, i):
        return self.in_state_ms(now) >= self.cfg.BATT_SETTLE_MS

    def chg_hold_0(self, now, i):
        return i.chg_hold_mode_ctrl == 0

    def waited_50ms_charge_low(self, now, i):
        # New Charge and Charge Toggle both hold CHARGE low for 50 ms (NEW_CHARGE_WAIT_MS = CHARGE_TOGGLE_LOW_MS)
        limit = self.cfg.NEW_CHARGE_WAIT_MS if self.state == NEW_CHARGE else self.cfg.CHARGE_TOGGLE_LOW_MS
        return self.in_state_ms(now) >= limit

    # GUARD (element 7)
    def cycle_spacing_ok(self, now, i):
        return self._t_last_chg is None or self._ms_since_last_chg(now) >= self.cfg.CHARGE_SPACING_MS

    # Charge Toggle Wait (element 8): the DONE rising edge is LATCHED by a pin interrupt, so a short top-off
    # pulse that is already over by the 50 ms check still counts as "the charger started".
    def _waited_50ms_since_rise(self, now):
        return not self._waiting_for_rise and self._ms_since_last_chg(now) >= self.cfg.CHARGE_START_CHECK_MS

    def waited_50ms_done_rose(self, now, i):
        return self._waited_50ms_since_rise(now) and i.done_rose == 1

    def waited_50ms_no_edge(self, now, i):
        # No edge = the LT3750 never started: UVLO, thermal shutdown, no power, or a broken CHARGE / DONE line
        return self._waited_50ms_since_rise(now) and i.done_rose == 0

    # Charge Started (elements 9, 10)
    def safe_charge_time_reached(self, now, i):
        return self.cfg.SAFE_CHARGE and self._ms_since_last_chg(now) >= self.cfg.SAFE_CHARGE_MS

    def waited_5s(self, now, i):
        return self.in_state_ms(now) >= self.cfg.SAFE_CHARGE_WAIT_MS

    def checked_4500ms_done_0(self, now, i):
        return (not self.cfg.SAFE_CHARGE and self._ms_since_last_chg(now) >= self.cfg.CHARGE_TIMEOUT_MS
                and i.done == 0)

    def checked_4500ms_done_1(self, now, i):
        return (not self.cfg.SAFE_CHARGE and self._ms_since_last_chg(now) >= self.cfg.CHARGE_TIMEOUT_MS
                and i.done == 1)

    # Assuming Charged (element 11)
    def topoff_time_reached(self, now, i):
        return self._ms_since_last_chg(now) >= self.cfg.TOPOFF_PERIOD_MS

    # ==================================================================== helpers
    def _ms_since_last_chg(self, now):
        return utime.ticks_diff(now, self._t_last_chg)

    def _note_charge_rise(self, now, i):
        """While in Charge Toggle Wait: start "time since last chg" when the CHARGE pin is really high. The power
        stage may hold it back a moment (its 50 ms dead time), and the 500 ms GUARD must count real cycles."""
        if self._waiting_for_rise and i.charge_pin == 1:
            self._waiting_for_rise = False
            self._t_last_chg = now

    def _count_overvolt_readings(self, hv):
        if hv >= self.cfg.OVERVOLT_V:
            self._overvolt_readings += 1
        else:
            self._overvolt_readings = 0

    # ==================================================================== arrow and entry actions
    def _take(self, row, now, i):
        to, why = row[2], row[3]
        if to == DISABLED:
            # Element 12: set chg_disable_chip_level (never cleared) and tell the Pi why.
            self.fault = FAULT_FOR_ARROW[why]
            self.chg_disable_chip_level = 1
            self.events.append((P.EV_FAULT, self.fault, int(i.hv * 10), self.state))
        self._go(to, why, now, i)

    def _on_enter(self, state, now, i):
        if state == CHARGE_TOGGLE_WAIT:
            # CHARGE goes high from this pass: arm a fresh DONE edge latch and start timing once the pin is up
            self._waiting_for_rise = True
            self.clear_done_latch = True
        elif state == STARTUP:
            # "reset vars": forget the last cycle so the GUARD lets the first cycle start straight away.
            # chg_disable_chip_level is never reset here (Disabled is one-way).
            self._t_last_chg = None
        elif state == ASSUMING_CHARGED:
            self.cycles += 1
