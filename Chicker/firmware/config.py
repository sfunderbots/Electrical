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
BATT_SAMPLE_MS = 100
BATT_ON_MIN_V = 14.7          # [RC26] charging allowed above this...
BATT_OFF_MIN_V = 14.3         # [NEW] ...and keeps being allowed down to this (hysteresis)
BATT_MAX_V = 17.0             # [RC26]
BATT_DEBOUNCE_SAMPLES = 3     # [NEW] consecutive samples before the state flips

# ---------------------------------------------------------------------------
# Power stage interlock (CHARGE / ~DISCHARGE)
# ---------------------------------------------------------------------------
CHG_DISCHARGE_DEADTIME_MS = 50   # [NEW] dead time in both directions

# ---------------------------------------------------------------------------
# Charge state machine (HV-measurement based)
# ---------------------------------------------------------------------------
BATT_SETTLE_MS = 2000            # [RC26] battery plugged in -> first charge
CHARGE_LOW_MIN_MS = 50           # [RC26] CHARGE low time before a new rising edge
MIN_CYCLE_INTERVAL_MS = 500      # [RF] never start cycles closer than this
DONE_MIN_MS = 2                  # ignore DONE for this long after CHARGE rises
DONE_RISE_TIMEOUT_MS = 200       # [NEW] DONE must rise within this (or the bank is already full)
CHARGE_ALREADY_FULL_V = 206.0    # [RC26] no DONE pulse seen but bank is this full -> treat as full
DONE_LOW_DEBOUNCE_MS = 2
DONE_PLAUSIBLE_MIN_V = 190.0     # [NEW] DONE falling below this is an LT3750 fault, not a finish
PROGRESS_WINDOW_MS = 1000        # [NEW] HV must rise PROGRESS_MIN_RISE_V per window...
PROGRESS_MIN_RISE_V = 5.0
PROGRESS_CHECK_BELOW_V = 150.0   # ...while below this voltage
CHARGE_BACKSTOP_V = 220.0        # [RF] LT3750 stops at ~214.9 V indicated; above this it is not regulating
OVERVOLT_V = 225.0               # [RF] latched fault, bank dumped
CHARGE_TIMEOUT_MS = 6000         # [NEW] full charge is ~1.6 s
RECHARGE_BELOW_V = 205.0         # [NEW] top-off threshold (replaces the 5 s re-toggle)
CHARGE_RETRY_MS = 30000          # [RC26]
MAX_CONSECUTIVE_CHARGE_FAULTS = 3
CEILING_HYST_V = 3.0             # bench ceiling: recharge below ceiling - this
CEILING_MIN_V = 10.0
CEILING_MAX_V = 214.0

# ---------------------------------------------------------------------------
# Kick
# ---------------------------------------------------------------------------
KICK_MIN_US = 50                 # [RF]
KICK_MAX_US = 5000               # [RC26] hard clamp
KICK_READY_V = 180.0             # [RC26] KICK_READY_HV - now only the STATUS 'kick ready' flag for the robot
KICK_MIN_FIRE_V = 0.0            # [NEW] a requested kick fires at any bank voltage above this (0 = always)
KICK_ARM_DEFAULT_MS = 2000       # [RC26] AUTOKICK_EXPIRE_THRESH_MS
KICK_ARM_MAX_MS = 10000
KICK_COOLDOWN_MS = 100           # [RC26] also the post-kick charge hold-off
PRE_FIRE_SETTLE_MS = 5           # [RF] CHARGE low this long before any gate pulse
KICK_TAIL_LOW_US = 1             # low time appended after a single pulse
BEAM_ACTIVE_LOW = True           # [RC26] 0 = ball present / beam broken
BEAM_FAST_FIRE = True            # fire from the break-beam IRQ when pre-validated

# ---------------------------------------------------------------------------
# Damping
# ---------------------------------------------------------------------------
DAMP_START_V = 206.0             # [RC26] DAMP_MIN_START_HV
DAMP_READY_TIMEOUT_MS = 5000     # [NEW] give up waiting for DAMP_START_V
DAMP_INITIAL_KICK_US = 800       # [RC26]
DAMP_SETTLE_US = 4000            # [RC26] 5 x initial kick
DAMP_HOLD_MS_DEFAULT = 3000      # [RC26]
DAMP_HOLD_MS_MAX = 5000
DAMP_FREQ_DEFAULT = 30000        # [RC26]
DAMP_FREQ_MIN = 1000
DAMP_FREQ_MAX = 40000            # [RC26] ">37 kHz is pushing it"
DAMP_DUTY_DEFAULT = 12.0         # [RC26] percent
DAMP_DUTY_MIN = 0.5              # percent
DAMP_DUTY_MAX = 95.0             # [RC26]
DAMP_MAX_ON_US = 200             # [NEW] per-period on-time cap
DAMP_SEGMENT_MS = 25             # [RC26] DAMP_ADJUST_PERIOD_MS = PIO segment length
DAMP_MIN_V = 60.0                # [RC26] hold ends below this bank voltage
DAMP_MIN_V_CEILING_FRAC = 0.3    # [NEW] under a bench ceiling the floor is min(DAMP_MIN_V, 0.3 x ceiling)
# HV compensation: 0 = off, 1 = hold force (D0 * V_start / V), 2 = RoboCup formula
DAMP_COMP_MODE = 1
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

# Heartbeat: 0 = off, 1 = enforce after the first one is seen, 2 = strict from boot
HEARTBEAT_MODE = 1
HEARTBEAT_TIMEOUT_MS = 300       # robot sends at 20 Hz

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
