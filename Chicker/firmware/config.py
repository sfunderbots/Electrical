# Chicker firmware v2 - every constant in one place. No logic lives in this file.
#
# Provenance tags:
#   [SCH]   KiCad schematic            [DS]    component datasheet
#   [RC26]  tuned at RoboCup 2026 (robocup2026damping_henry)
#   [RF]    measured by Steven on the bench (origin/rust-firmware, Jul 2026)
#   [NEW]   chosen for v2              [BENCH] must be (re)measured on the bench

FW_VERSION = "2.0.0-dev"

# ---------------------------------------------------------------------------
# Pins [SCH]
# ---------------------------------------------------------------------------
PIN_CHIP = 2            # chip IGBT gate driver - never fired, held low
PIN_KICK = 3            # kick IGBT gate driver
PIN_DONE = 4            # LT3750 DONE (open collector: high = charging)
PIN_CHARGE = 5          # LT3750 CHARGE (rising edge starts a cycle)
PIN_CAN_LED = 6
PIN_NOT_DISCHARGE = 8   # ~DISCHARGE: low = dump bank into the 1k resistors
PIN_SHELL_OFF = 9       # reed switch (digital for now, analog version in to-test/)
PIN_CAN_INT = 20
PIN_BREAKBEAM = 23
PIN_ADC_BATT = 26
PIN_ADC_5V = 27
PIN_ADC_HV = 29

# Pin the kick PIO program drives. Set to PIN_CAN_LED for an LED dry run:
# the real KICK pin is then held low as a plain GPIO.
KICK_OUTPUT_PIN = PIN_KICK
KICK_PIO_SM = 0          # PIO0 state machine 0

# ---------------------------------------------------------------------------
# ADC scaling
# ---------------------------------------------------------------------------
ADC_VREF = 3.3
HV_DIVIDER = (13.0 + 990.0) / 13.0       # [SCH] 3x330k over 13k, buffered by MCP6231
BATT_DIVIDER = (200.0 + 940.0) / 200.0   # [SCH]
V5_DIVIDER = (30.0 + 51.0) / 51.0        # [SCH per RF] disputed vs old firmware, telemetry only
HV_OVERSAMPLE = 4
HV_GAIN_TRIM = 1.0                       # [BENCH] multimeter calibration
HV_OFFSET_TRIM_V = 0.0                   # [BENCH]
BATT_GAIN_TRIM = 1.0                     # [BENCH]

# ---------------------------------------------------------------------------
# Battery (4S pack)
# ---------------------------------------------------------------------------
BATT_SAMPLE_MS = 500          # [Henry] chg_ok: battery read every 500 ms (charge element 1)
BATT_ON_MIN_V = 14.7          # [RC26] charging allowed above this...
BATT_OFF_MIN_V = 14.3         # [NEW] ...and keeps being allowed down to this (hysteresis)
BATT_MAX_V = 17.0             # [RC26]
BATT_DEBOUNCE_SAMPLES = 3     # [NEW] consecutive samples before the state flips

# ---------------------------------------------------------------------------
# Power stage interlock (CHARGE / ~DISCHARGE)
# ---------------------------------------------------------------------------
CHG_DISCHARGE_DEADTIME_MS = 50   # [NEW] dead time in both directions

# ---------------------------------------------------------------------------
# Charge state machine (Henry's charge chart; docs/CHARGE-STATE-MACHINE-REVIEW.md)
# Every charge decision is made on timers and the DONE pin, never on the HV reading (review note N-0).
# "time since last chg" = time since CHARGE last went high (the start of the current charge cycle).
# ---------------------------------------------------------------------------
BATT_SETTLE_MS = 2000            # [RC26] "New Charge from Bat: Wait for 2s"
NEW_CHARGE_WAIT_MS = 50          # [RC26] "New Charge: Wait for 50ms" (CHARGE held low)
CHARGE_SPACING_MS = 500          # [Henry] GUARD "time since last chg >= 500ms" before every new cycle
CHARGE_START_CHECK_MS = 50       # [RC26] "Charge Toggle Wait: wait for 50ms", then check the DONE edge latch
CHARGE_TIMEOUT_MS = 4500         # [Henry] GUARD "time since last chg >= 4500ms": done still 1 = too slow
TOPOFF_PERIOD_MS = 5000          # [Henry] GUARD "time since last chg >= 5000ms": toggle CHARGE to top off
CHARGE_TOGGLE_LOW_MS = 50        # [RC26] "Charge Toggle": CHARGE low this long (datasheet needs 20 us)
SAFE_CHARGE = 0                  # [Henry] safe_chg branch: 1 = charge to ~50 V once, then Disabled (bench)
SAFE_CHARGE_MS = 236             # [Henry] GUARD "time since last chg >= 236ms" (~50 V)
SAFE_CHARGE_WAIT_MS = 5000       # [Henry] "Charged to ~50V: wait 5s"
OVERVOLT_V = 220.0               # [Henry] X-1: ANY STATE HV >= 220 V -> Disabled (full bank is 206-212 V)
OVERVOLT_SAMPLES = 3             # [Henry] X-1 trips after this many readings in a row at or above OVERVOLT_V

# ---------------------------------------------------------------------------
# Kick
# ---------------------------------------------------------------------------
KICK_MIN_US = 100                # [Henry] kick GUARD "PW >= 100 us"
KICK_MAX_US = 5000               # [Henry] kick GUARD "PW <= 5000 us"
KICK_MIN_HV = 100.0              # [Henry] Arm Kick: "HV >= 100V && kick assigned" (mode element 10)
KICK_MIN_INTERVAL_MS = 500       # [Henry] "last kick >= 500ms": two pulses closer than this combine into one
                                 #         oversized kick. Starting guess, tune on the field (mode KCK-2)
KICK_TAIL_LOW_US = 1             # low time appended after a single pulse
BEAM_ACTIVE_LOW = True           # [RC26] pin level 0 = beam broken = "Breakbeam == ON" (confirm on the bench)
BEAM_DEBOUNCE_MS = 5             # [BENCH] beam must hold its level this long before it counts (Henry: it may
                                 #         flicker, and once armed the first "ON" fires the kick). Tune on the bench

# ---------------------------------------------------------------------------
# Damping
# ---------------------------------------------------------------------------
DAMP_START_V = 180.0             # [Henry] damp "HV >= 180" before Charge Hold (mode element 14)
DAMP_INITIAL_KICK_US = 800       # [RC26] Initial Kick pulse width (mode note DMP-2)
DAMP_SETTLE_US = 4000            # [RC26] "wait for 5 x PW = 4ms" between Initial Kick and Start Damping PWM
DAMP_HOLD_MS_DEFAULT = 3000      # [RC26] damp timeout when the DAMP command doesn't give one
DAMP_HOLD_MS_MAX = 5000          # largest timeout a DAMP command may ask for
DAMP_MAX_TOTAL_MS = 6000         # [Henry] "max hold": a damp never runs longer than this from its Initial Kick,
                                 #         however often it is renewed. A backstop, not a heating limit
DAMP_LOW_HV_STOP_WAIT_MS = 50    # [Henry] low-bank kick: "Stop PWM -> wait 50ms -> Enable Charging"
DAMP_FREQ_DEFAULT = 30000        # [RC26]
DAMP_FREQ_MIN = 1000
DAMP_FREQ_MAX = 40000            # [RC26] ">37 kHz is pushing it"
DAMP_DUTY_DEFAULT = 12.0         # [RC26] percent
DAMP_DUTY_MIN = 0.5              # percent
DAMP_DUTY_MAX = 95.0             # [RC26]
DAMP_MAX_ON_US = 200             # [NEW] per-period on-time cap
DAMP_ADJUST_PERIOD_MS = 25       # [RC26] "time since last HV adjustment >= DAMP_ADJUST_PERIOD_MS"
DAMP_SEGMENT_MS = DAMP_ADJUST_PERIOD_MS   # PIO segment length: a new duty lands on the next segment
DAMP_MIN_V = 60.0                # [RC26] lower clamp on HV inside the duty formula (not a stop, mode element 16)
# HV compensation: 0 = off, 1 = hold force (D0 * V_start / V), 2 = RoboCup 2026 formula (agreed, DMP-3:
# kept exactly as RoboCup ran it, whole-% rounding included; validate the real duty / power on the bench)
DAMP_COMP_MODE = 2
DAMP_COMP_MAX_GAIN = 2.0         # [NEW] hold-force gain cap
DAMP_LEGACY_NOMINAL_V = 210.0    # [RC26] mode 2 only
DAMP_LEGACY_EXP = 0.1            # [RC26] mode 2 only
DAMP_CATCH_MS = 0                # [RC26] 120 ms catch boost, unused there -> off
DAMP_CATCH_BOOST = 2.0           # [RC26] percent
# Power limit: predicted bank power P = (D*V)^2 / R_eff. Duty is clamped so P stays under the limit.
DAMP_R_EFF_OHM = 1.72            # [BENCH] fitted to the kick-drop table; re-fit after the 1 cm solenoid move
DAMP_MAX_HOLD_POWER_W = 500.0    # [BENCH] placeholder - set from the bench hold-power sweep

# ---------------------------------------------------------------------------
# CAN (MCP2515, 8 MHz crystal, 250 kbps shared robot bus)
# ---------------------------------------------------------------------------
CAN_ID_MODE = 0x2A0              # robot -> board  [mode]
CAN_ID_KICK = 0x2A1              # robot -> board  [trigger, width u16, arm_ms u16]
CAN_ID_DAMP = 0x2A2              # robot -> board  [freq u16, duty_0.01% u16, hold_ms u16, init_kick_us u16]
CAN_ID_HEARTBEAT = 0x2A4         # robot -> board  [seq]
CAN_ID_STATUS = 0x2A8            # board -> robot  periodic status
CAN_ID_EVENT = 0x2A9             # board -> robot  events
CAN_ID_LEGACY = 0x2AA            # old [mode, value u16, duty] frame
CAN_ID_SELFTEST = 0x2AF          # loopback self-test only, never on the bus
LEGACY_PROTOCOL_ENABLED = True
CAN_HW_FILTER = True             # MCP2515 accepts only 0x2A0-0x2AF
CAN_FILTER_MASK = 0x7F0
CAN_FILTER_ID = 0x2A0
CAN_RX_MAX_PER_LOOP = 8
CAN_TX_ENABLED = True
CAN_LOOPBACK_SELFTEST = True
STATUS_PERIOD_MS = 50            # 20 Hz status to the Pi
STATUS_MIN_GAP_MS = 5            # extra status frames on changes, rate limited
CAN_SEND_REJECT_EVENTS = True    # [T-2, pending team] send a CMD_REJECTED event for a bad / unusable command

# Heartbeat (mode elements 3 and 4, note HB-1). The Pi sends one every 200 ms (5 Hz). Any valid frame from
# the Pi counts. The board only acts on commands once a frame has arrived, and treats the link as lost after
# HEARTBEAT_TIMEOUT_MS without one (~25 missed frames).
HEARTBEAT_TIMEOUT_MS = 5000      # [Henry]
HEARTBEAT_LATE_MS = 1000         # [NEW] early warning only: STATUS "heartbeat OK" flag clears after this

# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------
WATCHDOG_MS = 1000               # 0 disables
WATCHDOG_START_DELAY_MS = 3000   # window to stop main.py from Thonny before it starts
HV_STALE_MS = 50                 # loop is unhealthy if the HV reading is older than this

# ---------------------------------------------------------------------------
# USB console / telemetry
# ---------------------------------------------------------------------------
USB_CONSOLE_ENABLED = True
USB_CAN_INJECT_ENABLED = True    # "can <id> <bytes>" goes through the CAN decoder
USB_TELEMETRY_PERIOD_MS = 100    # 0 = off
USB_LINE_MAX = 120
DRY_FIRE_MAX_V = 5.0             # bench dry fire only allowed with the bank below this
