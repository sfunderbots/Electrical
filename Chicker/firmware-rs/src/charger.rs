//! LT3750 flyback charger control.
//!
//! The LT3750 does the actual regulation: a rising edge on CHARGE starts a
//! charge cycle, the part switches until the resistor-programmed output
//! target (~210 V) is reached, then stops on its own and flags DONE. The MCU
//! only decides *when* cycles run, and provides software backstops on top
//! (see `state.rs`): CHARGE is forced low at `CHARGE_DONE_MV`, a cycle
//! running longer than `CHARGE_TIMEOUT` latches a fault, and `OVERVOLT_MV`
//! latches a fault and dumps the bank.

use embassy_rp::gpio::Output;
use embassy_time::{Instant, Timer};

pub struct Charger {
    pin: Output<'static>,
    /// Set while a charge cycle we started may still be running.
    charging: bool,
    started: Instant,
}

impl Charger {
    /// `pin` must already be constructed low (done first thing in main).
    pub fn new(pin: Output<'static>) -> Self {
        Self {
            pin,
            charging: false,
            started: Instant::MIN,
        }
    }

    /// Start a charge cycle: guarantee a clean rising edge on CHARGE.
    pub async fn begin_cycle(&mut self) {
        self.pin.set_low();
        Timer::after_millis(2).await;
        self.pin.set_high();
        self.charging = true;
        self.started = Instant::now();
    }

    /// Stop charging immediately (fire sequence, cycle complete, disarm,
    /// any fault).
    pub fn pause(&mut self) {
        self.pin.set_low();
        self.charging = false;
    }

    pub fn charging(&self) -> bool {
        self.charging
    }

    /// How long the current cycle has been running.
    pub fn cycle_elapsed(&self) -> embassy_time::Duration {
        if self.charging {
            self.started.elapsed()
        } else {
            embassy_time::Duration::from_ticks(0)
        }
    }
}
