//! Every tunable constant in one place.
//!
//! Source labels — the old firmware had many bugs, so provenance matters:
//!   [SCH]    derived from the KiCad schematics / production netlist
//!   [DS]     from a component datasheet (LT3750, MCP2515, ...)
//!   [OLD-FW] observed/claimed by the old MicroPython firmware — UNTRUSTED
//!            until bench-verified; wherever an [OLD-FW] fact steers
//!            behavior, the code is written to fail safe (or at worst fail
//!            loud) if the fact turns out to be wrong
//!   [BENCH]  must be measured on the bench before being trusted

use embassy_time::Duration;
use mcp25xx::registers::CNF;

// ---------------------------------------------------------------------------
// Kick pulse
// ---------------------------------------------------------------------------

/// Hard clamp on the IGBT gate pulse. The board is characterized for
/// 500-5000 us; anything under 500 us is allowed for bench work but the
/// absolute ceiling protects the solenoid and IGBT.
pub const PULSE_MIN_US: u32 = 50;
pub const PULSE_MAX_US: u32 = 5000;

/// Default pulse used by break-beam auto-fire until CONFIG changes it.
pub const DEFAULT_AUTOFIRE_US: u32 = 2000;

/// Lockout after every fire. Also gives the bank time to recharge.
pub const DEFAULT_COOLDOWN_MS: u32 = 500;

/// CHARGE must be low this long before the gate pulse starts.
pub const PRE_FIRE_SETTLE: Duration = Duration::from_millis(5);
/// Extra wait after the pulse before leaving the Firing state.
pub const POST_FIRE_SETTLE: Duration = Duration::from_millis(2);

/// PIO runs at clk_sys = 125 MHz (embassy default with the 12 MHz crystal).
pub const PIO_CYCLES_PER_US: u32 = 125;

// ---------------------------------------------------------------------------
// High-voltage thresholds (millivolts on the capacitor bank)
// ---------------------------------------------------------------------------

/// Fault: the LT3750 should have stopped well below this on its own.
/// ([OLD-FW] logs show real-world peaks of 206-211 V; [SCH] target ~210 V.)
pub const OVERVOLT_MV: u32 = 225_000;
/// Software backstop: force CHARGE low at/above this even if DONE never
/// falls. Above the [OLD-FW]-observed 211 V peak, below the overvoltage
/// fault. Independent of DONE, so it holds even if DONE handling is wrong.
pub const CHARGE_BACKSTOP_MV: u32 = 215_000;
/// Start a new charge cycle when the bank sags below this while armed.
pub const RECHARGE_ON_MV: u32 = 195_000;
/// Never start charge cycles closer together than this. Defensive: if the
/// DONE interpretation is wrong on real hardware, cycles would "complete"
/// instantly — this floor turns that failure into slow, loudly-logged
/// cycling instead of rapid relay-style toggling of the flyback.
pub const MIN_CYCLE_INTERVAL: Duration = Duration::from_millis(500);
/// Refuse to fire below this (nothing useful would happen).
/// Deliberately low so bench tests at reduced voltage still fire.
pub const FIRE_MIN_MV: u32 = 15_000;
/// "hv_ready" telemetry flag threshold.
pub const HV_READY_MV: u32 = RECHARGE_ON_MV;

/// A single charge cycle taking longer than this means a dead flyback or a
/// broken HV sense path -> latched fault. [OLD-FW] logs put a full
/// 0 -> 210 V charge at ~2 s, so 15 s is generous either way.
pub const CHARGE_TIMEOUT: Duration = Duration::from_secs(15);

/// Ignore the DONE level for this long after starting a cycle: it takes a
/// few ms to rise after CHARGE asserts. [OLD-FW] hit a race here; the
/// constant is conservative and harmless if their observation was wrong.
pub const DONE_SETTLE: Duration = Duration::from_millis(50);

/// If DONE has not risen (flyback switching) this long into a cycle,
/// something is wrong (LT3750 UVLO/thermal fault pulls DONE low per [DS]).
/// Warn early; the CHARGE_TIMEOUT fault is the enforcement behind it.
pub const DONE_RISE_WARN: Duration = Duration::from_millis(200);

/// Don't start a recharge cycle for this long after a kick. [OLD-FW]
/// enforced >= 50 ms between kick and charge; conservative if wrong.
pub const POST_FIRE_CHARGE_HOLDOFF: Duration = Duration::from_millis(100);

/// Don't run the flyback from a flat (or absent) battery. [OLD-FW] gated
/// charging below 14.7 V on the 4S pack; slightly lower here so a bench
/// supply at 15 V works. Lower it further for reduced-voltage bench tests.
pub const BATT_MIN_FOR_CHARGE_MV: u32 = 14_000;

// ---------------------------------------------------------------------------
// ADC scaling (integer math: mv = raw * NUM / 1000)
// ---------------------------------------------------------------------------
// 3.3 V / 4096 counts = 0.8057 mV per count at the ADC pin.

/// [SCH] HV divider 13k / (330k*3 + 13k) = 0.012961 -> 62.16 mV of bus per
/// count. ([OLD-FW] independently used the same x77.15 factor.) [BENCH]:
/// calibrate against a multimeter; 990k-string tolerance dominates.
pub const HV_MV_NUM: u32 = 62_160;
/// [SCH] Battery divider 200k / (940k + 200k) = 0.17544 -> 4.593 mV per
/// count. ([OLD-FW] used the same x5.7; their bench cal also found slope
/// 1.0342 / offset -0.53 V — treat as a hint, re-measure before folding in.)
pub const BATT_MV_NUM: u32 = 4_593;
/// [SCH] 5V rail divider 51k / (30k + 51k) = 0.6296 -> 1.280 mV per count.
/// [OLD-FW] used a completely different factor and claimed the divider was
/// installed backwards on the board — telemetry only, [BENCH] before trust.
pub const V5_MV_NUM: u32 = 1_280;

/// HV sample considered stale after this (arming refused / fault while armed,
/// and the hardware watchdog stops being fed).
pub const ADC_STALE: Duration = Duration::from_millis(100);
/// Stale-while-armed grace before latching Faulted(AdcStale).
pub const ADC_FAULT_AFTER: Duration = Duration::from_millis(200);

// ---------------------------------------------------------------------------
// Watchdogs
// ---------------------------------------------------------------------------

/// Hardware watchdog period. On reset all pads return to pull-down: charge
/// stops, the autodischarge network dumps the bank, firmware boots Disarmed.
pub const HW_WATCHDOG: Duration = Duration::from_millis(1000);

/// Auto-disarm when armed and no valid CAN frame arrives for this long.
/// Disabled by the console command `benchmode on` (loudly logged).
pub const CAN_SILENCE_DISARM: Duration = Duration::from_secs(1);

// ---------------------------------------------------------------------------
// CAN bus
// ---------------------------------------------------------------------------
// This protocol v0 is the NEW canonical interface for the chicker (the old
// firmware's message system is deliberately not carried over); the robot
// side should be updated to match protocol.rs.
// The MCP2515 crystal is 8 MHz, which caps this node at 500 kbps — every
// other node on the bus must run the same bitrate.
// [OLD-FW] the old deployed firmware ran the bus at 250 kbps (ID 0x2AA).
// If legacy nodes must coexist during migration, switch to CNF_250K_BPS.

pub const CAN_BITRATE_CNF: CNF = mcp25xx::bitrates::clock_8mhz::CNF_500K_BPS;

/// SPI clock to the MCP2515 (its max is 10 MHz).
pub const MCP2515_SPI_HZ: u32 = 8_000_000;

// Command frames (robot -> chicker)
pub const CAN_ID_ARM: u16 = 0x310;
pub const CAN_ID_DISARM: u16 = 0x311;
pub const CAN_ID_KICK: u16 = 0x312;
pub const CAN_ID_CONFIG: u16 = 0x313;
pub const CAN_ID_HEARTBEAT: u16 = 0x314;
// Telemetry (chicker -> robot)
pub const CAN_ID_STATUS: u16 = 0x320;

/// ARM frames must carry these two bytes first so that a corrupted or
/// misaddressed frame cannot arm the board.
pub const ARM_MAGIC: [u8; 2] = [0xA5, 0x5A];

/// STATUS broadcast period.
pub const STATUS_PERIOD: Duration = Duration::from_millis(100);

// ---------------------------------------------------------------------------
// Break beam
// ---------------------------------------------------------------------------

/// [OLD-FW] (main.py:171): the line reads LOW when the beam is broken /
/// ball present. If wrong, the failure mode is an unwanted (but guarded)
/// fire when arming auto mode — so [BENCH]: check the STATUS beam flag
/// with/without a ball BEFORE the first auto-mode arm at voltage.
pub const BEAM_ACTIVE_LOW: bool = true;
/// Debounce/qualification time after an edge before the level is believed.
pub const BEAM_DEBOUNCE: Duration = Duration::from_millis(2);

// ---------------------------------------------------------------------------
// Misc inputs
// ---------------------------------------------------------------------------

/// [DS] LT3750 DONE is open-collector: the internal NPN pulls the pin LOW
/// when the target voltage is reached AND on internal faults (UVLO,
/// thermal shutdown). With the net pulled up, the pin therefore reads HIGH
/// while a cycle is switching and LOW when finished (or faulted).
/// [OLD-FW] observed the same high-while-charging behavior on this board
/// (main.py:497) — but note their fault-polarity comment was WRONG (they
/// claimed faults drive it high; the datasheet says low).
/// So "done" = pin LOW. Used as the primary end-of-cycle signal, with the
/// ADC backstop, MIN_CYCLE_INTERVAL and the charge timeout behind it in
/// case this interpretation fails on real hardware. [BENCH] confirm in
/// bring-up step 5.
pub const DONE_ACTIVE_LOW: bool = true;

/// SHELL_OFF has a 100k pull-up; a closed switch pulls it low.
/// Reported in telemetry; not yet gating arm (decide after cross-reference).
pub const SHELL_OFF_ACTIVE_LOW: bool = true;
