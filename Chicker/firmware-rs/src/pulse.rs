//! One-shot IGBT gate pulse generation on PIO0.
//!
//! Why PIO: the pulse width is generated entirely in the PIO clocked at
//! clk_sys (125 MHz, 8 ns per cycle). Once triggered, the width cannot be
//! disturbed by interrupts, USB traffic or the scheduler. Trigger latency is
//! ~3 PIO cycles (~24 ns), fixed.
//!
//! Program per state machine (SM0 = KICK on GPIO3, SM1 = CHIP on GPIO2):
//!
//! ```text
//! pull block        ; idle here forever, pin LOW, until the CPU pushes a word
//! mov x, osr        ; X = width in cycles - 2
//! set pins, 1       ; gate HIGH
//! loop: jmp x-- loop
//! set pins, 0       ; gate LOW, wrap back to the stalled pull
//! ```
//!
//! High time = (X + 2) cycles, so we push `width_us * 125 - 2`.
//! While idle the SM is stalled on `pull block` with the pin driven low -
//! there is no free-running code path that could glitch a gate.
//!
//! Never call `set_enable(false)` while a pulse may be in flight: freezing
//! the SM would hold the gate HIGH, which is the one thing this module must
//! make impossible.

use embassy_rp::peripherals::{PIN_2, PIN_3, PIO0};
use embassy_rp::pio::{Config, Direction, Pio, StateMachine};
use embassy_rp::gpio::Level;
use embassy_rp::Peri;
use embassy_time::{Duration, Instant};

use crate::config;
use crate::state::FireKind;

/// A pulse was refused because one may still be in flight.
#[derive(Debug, Clone, Copy)]
pub struct PulseBusy;

pub struct PulseGen {
    kick: StateMachine<'static, PIO0, 0>,
    chip: StateMachine<'static, PIO0, 1>,
    /// Software interlock: no new pulse (on either channel) until the
    /// previous one is over. Covers the mutual-exclusion requirement at the
    /// driver level, below the state machine.
    busy_until: Instant,
}

impl PulseGen {
    /// Takes the whole PIO0 block. Pins are driven low before their output
    /// is enabled, so bring-up cannot glitch the gates.
    pub fn new(pio: Pio<'static, PIO0>, kick_pin: Peri<'static, PIN_3>, chip_pin: Peri<'static, PIN_2>) -> Self {
        let Pio {
            mut common,
            mut sm0,
            mut sm1,
            ..
        } = pio;

        let prg = pio::pio_asm!(
            ".wrap_target",
            "pull block",
            "mov x, osr",
            "set pins, 1",
            "loop:",
            "jmp x-- loop",
            "set pins, 0",
            ".wrap",
        );
        let loaded = common.load_program(&prg.program);

        let kick_pin = common.make_pio_pin(kick_pin);
        let chip_pin = common.make_pio_pin(chip_pin);

        let mut cfg = Config::default(); // clock divider 1 -> 125 MHz
        cfg.use_program(&loaded, &[]);

        cfg.set_set_pins(&[&kick_pin]);
        sm0.set_config(&cfg);
        sm0.set_pins(Level::Low, &[&kick_pin]);
        sm0.set_pin_dirs(Direction::Out, &[&kick_pin]);
        sm0.set_enable(true);

        cfg.set_set_pins(&[&chip_pin]);
        sm1.set_config(&cfg);
        sm1.set_pins(Level::Low, &[&chip_pin]);
        sm1.set_pin_dirs(Direction::Out, &[&chip_pin]);
        sm1.set_enable(true);

        Self {
            kick: sm0,
            chip: sm1,
            busy_until: Instant::MIN,
        }
    }

    /// The sole fire primitive in the firmware. `state::Machine::guarded_fire`
    /// is its only caller. `width_us` must already be clamped by the caller;
    /// it is clamped again here out of paranoia.
    pub fn fire(&mut self, kind: FireKind, width_us: u32) -> Result<(), PulseBusy> {
        let width_us = width_us.clamp(config::PULSE_MIN_US, config::PULSE_MAX_US);
        let now = Instant::now();
        if now < self.busy_until {
            return Err(PulseBusy);
        }

        let cycles = width_us * config::PIO_CYCLES_PER_US - 2;
        let pushed = match kind {
            FireKind::Kick => self.kick.tx().try_push(cycles),
            FireKind::Chip => self.chip.tx().try_push(cycles),
        };
        if !pushed {
            // FIFO should always be empty here; treat a full FIFO as busy.
            return Err(PulseBusy);
        }
        self.busy_until = now + Duration::from_micros(width_us as u64) + Duration::from_micros(100);
        Ok(())
    }

    /// Drop any queued (not yet started) pulses. Called on disarm/fault.
    /// An already-started pulse (<= 5 ms) is left to finish on its own:
    /// aborting it would require disabling the SM, which could freeze the
    /// gate high.
    pub fn clear_queued(&mut self) {
        self.kick.clear_fifos();
        self.chip.clear_fifos();
    }
}
