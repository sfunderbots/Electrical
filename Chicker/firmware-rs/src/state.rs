//! The safety state machine — single owner of everything that can hurt the
//! board: CHARGE, ~DISCHARGE, the pulse generator and the hardware watchdog.
//!
//! Every other task (CAN, USB console, ADC, break beam) can only *send
//! events* into `EVENTS`; nothing else in the firmware can touch a
//! power-stage pin. All transitions happen in `handle()`, output pins are
//! derived from the current state in `apply_outputs()`, and the one and only
//! path to a gate pulse is `guarded_fire()`.
//!
//! ```text
//! Disarmed ──CmdArm──► Armed{mode} ──fire──► Firing ──► Cooldown ──► Armed
//!     ▲                    │                                  │
//!     └────CmdDisarm───────┴── any state                      │
//! Faulted(..) ◄── Overvoltage / ChargeTimeout / AdcStale ─────┘
//!     (latched; only CmdDisarm clears it)
//! ```
//!
//! Disarmed and Faulted both mean: CHARGE low, ~DISCHARGE low (the bank
//! dumps into the 35 W resistors), fire lines idle. This matches the
//! hardware fail-safe: with the MCU dead or resetting, the pads pull down
//! and the board reaches the same state on its own.

use embassy_rp::gpio::{Input, Output};
use embassy_rp::watchdog::Watchdog;
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::channel::Channel;
use embassy_sync::watch::Watch;
use embassy_time::{Duration, Instant, Ticker, Timer};

use crate::charger::Charger;
use crate::config;
use crate::pulse::PulseGen;

// ---------------------------------------------------------------------------
// Shared plumbing
// ---------------------------------------------------------------------------

/// All tasks funnel into this; only `state_task` receives.
pub static EVENTS: Channel<CriticalSectionRawMutex, Event, 16> = Channel::new();

/// Latest telemetry snapshot; CAN status TX reads it at 10 Hz.
pub static STATUS: Watch<CriticalSectionRawMutex, Status, 3> = Watch::new();

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Fires only on explicit KICK commands ("fire at nothing").
    Manual,
    /// Fires the preset pulse when the break beam reports a ball.
    AutoBreakBeam,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FireKind {
    Kick,
    Chip,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Fault {
    /// HV_SENSE above OVERVOLT_MV: the LT3750 failed to stop itself.
    OverVoltage = 1,
    /// A charge cycle ran too long: dead flyback or broken HV sense.
    ChargeTimeout = 2,
    /// The pulse driver refused a fire that the state machine allowed.
    PulseBusy = 3,
    /// HV telemetry stopped arriving while armed.
    AdcStale = 4,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    Disarmed,
    Armed { mode: Mode },
    Firing,
    Cooldown { mode: Mode, until: Instant },
    Faulted(Fault),
}

#[derive(Debug, Clone, Copy)]
pub enum Event {
    CmdArm(Mode),
    CmdDisarm,
    CmdFire { kind: FireKind, width_us: u32 },
    CmdSetAutofire { kind: FireKind, width_us: u32 },
    CmdSetCooldownMs(u32),
    CmdBenchMode(bool),
    /// Log a full status line (console `status` command).
    CmdLogStatus,
    BeamBroken,
    BeamState(bool),
    HvSample { hv_mv: u32 },
    AuxSample { batt_mv: u32, v5_mv: u32 },
    Overvoltage { hv_mv: u32 },
    /// Any valid CAN frame arrived; feeds the comms watchdog.
    CanActivity,
}

/// Telemetry snapshot, encoded into the STATUS CAN frame.
#[derive(Debug, Clone, Copy)]
pub struct Status {
    pub state_code: u8, // 0 Disarmed, 1 Armed/Manual, 2 Armed/Auto, 3 Firing, 4 Cooldown, 5 Faulted
    pub fault_code: u8, // Fault enum value, 0 = none
    pub flags: u8,      // bit0 charging, bit1 done_raw, bit2 beam, bit3 shell_off, bit4 hv_ready, bit5 benchmode
    pub hv_mv: u32,
    pub batt_mv: u32,
}

#[derive(Debug, Clone, Copy)]
pub enum FireRefused {
    NotArmed,
    CapTooLow,
    HvStale,
    PulseBusy,
}

// ---------------------------------------------------------------------------
// The machine
// ---------------------------------------------------------------------------

pub struct Machine {
    state: State,
    charge: Charger,
    /// ~DISCHARGE: high = hold charge, low = dump. Low in Disarmed/Faulted.
    discharge_n: Output<'static>,
    /// LT3750 DONE, polarity unverified — telemetry/logging only.
    done_in: Input<'static>,
    shell_off_in: Input<'static>,
    pulses: PulseGen,
    watchdog: Watchdog,

    autofire_kind: FireKind,
    autofire_us: u32,
    cooldown: Duration,
    bench_mode: bool,

    hv_mv: u32,
    hv_at: Instant,
    batt_mv: u32,
    v5_mv: u32,
    beam_active: bool,
    last_can: Instant,
    last_tick: Instant,
    last_done_raw: bool,
    last_fire: Instant,
    last_cycle_start: Instant,
    /// DONE was seen HIGH during the current charge cycle, i.e. the flyback
    /// really started switching. Required before DONE-low counts as
    /// completion — otherwise a dead/faulted LT3750 (which holds DONE low
    /// per the datasheet) would look "instantly done" and we'd restart
    /// cycles forever instead of hitting the timeout fault.
    cycle_saw_charging: bool,
    done_rise_warned: bool,
    batt_warned: bool,
}

impl Machine {
    pub fn new(
        charge: Output<'static>,
        discharge_n: Output<'static>,
        done_in: Input<'static>,
        shell_off_in: Input<'static>,
        pulses: PulseGen,
        watchdog: Watchdog,
    ) -> Self {
        Self {
            state: State::Disarmed,
            charge: Charger::new(charge),
            discharge_n,
            done_in,
            shell_off_in,
            pulses,
            watchdog,
            autofire_kind: FireKind::Kick,
            autofire_us: config::DEFAULT_AUTOFIRE_US,
            cooldown: Duration::from_millis(config::DEFAULT_COOLDOWN_MS as u64),
            bench_mode: false,
            hv_mv: 0,
            hv_at: Instant::MIN,
            batt_mv: 0,
            v5_mv: 0,
            beam_active: false,
            last_can: Instant::MIN,
            last_tick: Instant::MIN,
            last_done_raw: false,
            last_fire: Instant::MIN,
            last_cycle_start: Instant::MIN,
            cycle_saw_charging: false,
            done_rise_warned: false,
            batt_warned: false,
        }
    }

    pub async fn run(mut self) -> ! {
        const TICK_PERIOD: Duration = Duration::from_millis(50);
        log::info!("state: boot -> Disarmed (charge off, bank dumping)");
        let mut ticker = Ticker::every(TICK_PERIOD);
        loop {
            // Feed the hardware watchdog only while telemetry is alive. If
            // this loop or the ADC dies, the chip resets within 1 s, pads
            // pull down, the bank bleeds off. A short boot grace covers the
            // window before the first sample; if no sample EVER arrives the
            // grace runs out and the chip deliberately reset-loops.
            let boot_grace =
                self.hv_at == Instant::MIN && Instant::now() < Instant::MIN + Duration::from_secs(3);
            if self.hv_at.elapsed() < config::ADC_STALE || boot_grace {
                self.watchdog.feed(config::HW_WATCHDOG);
            }

            // The ticker must live outside the select: a fresh
            // `Timer::after(..)` per iteration would be reset by every event,
            // and the 20 ms ADC sample stream would starve it forever —
            // silently disabling all of tick()'s housekeeping (this happened;
            // the CAN-silence auto-disarm never fired on the bench).
            match embassy_futures::select::select(EVENTS.receive(), ticker.next()).await {
                embassy_futures::select::Either::First(ev) => self.handle(ev).await,
                embassy_futures::select::Either::Second(()) => {}
            }

            // Run housekeeping on elapsed time, not on who won the select, so
            // event pressure can never starve it.
            if self.last_tick.elapsed() >= TICK_PERIOD {
                self.last_tick = Instant::now();
                self.tick();
            }

            self.tick_charge().await;
            self.apply_outputs();
            STATUS.sender().send(self.snapshot());
        }
    }

    async fn handle(&mut self, ev: Event) {
        match ev {
            Event::CmdArm(mode) => match self.state {
                State::Faulted(f) => {
                    log::warn!("arm refused: latched fault {:?}; send DISARM first", f)
                }
                _ => {
                    if self.hv_at.elapsed() > config::ADC_STALE {
                        log::warn!("arm refused: no fresh HV telemetry");
                    } else {
                        // Re-arming while armed just switches mode.
                        log::info!("armed, mode {:?}", mode);
                        self.state = State::Armed { mode };
                        // Don't let the comms watchdog trip on the very next tick.
                        self.last_can = Instant::now();
                    }
                }
            },

            Event::CmdDisarm => {
                log::info!("disarmed (bank dumping)");
                self.safe_down();
                self.state = State::Disarmed;
            }

            Event::CmdFire { kind, width_us } => {
                if let Err(e) = self.guarded_fire(kind, width_us).await {
                    log::warn!("fire refused: {:?}", e);
                }
            }

            Event::BeamBroken => {
                if let State::Armed { mode: Mode::AutoBreakBeam } = self.state {
                    let (kind, width) = (self.autofire_kind, self.autofire_us);
                    log::info!("break beam -> auto fire {:?} {} us", kind, width);
                    if let Err(e) = self.guarded_fire(kind, width).await {
                        log::warn!("auto fire refused: {:?}", e);
                    }
                }
            }

            Event::BeamState(active) => self.beam_active = active,

            Event::HvSample { hv_mv } => {
                self.hv_mv = hv_mv;
                self.hv_at = Instant::now();
            }

            Event::AuxSample { batt_mv, v5_mv } => {
                self.batt_mv = batt_mv;
                self.v5_mv = v5_mv;
            }

            Event::Overvoltage { hv_mv } => match self.state {
                State::Disarmed | State::Faulted(_) => {
                    // Already dumping; nothing more to switch off.
                    log::error!("overvoltage while inactive: {} mV", hv_mv);
                }
                _ => {
                    log::error!("OVERVOLTAGE {} mV -> fault, dumping bank", hv_mv);
                    self.fault(Fault::OverVoltage);
                }
            },

            Event::CanActivity => self.last_can = Instant::now(),

            Event::CmdSetAutofire { kind, width_us } => {
                self.autofire_us = width_us.clamp(config::PULSE_MIN_US, config::PULSE_MAX_US);
                self.autofire_kind = kind;
                log::info!("autofire = {:?} {} us", kind, self.autofire_us);
            }

            Event::CmdSetCooldownMs(ms) => {
                // Floor keeps back-to-back full-power fires apart even if a
                // bad config frame arrives.
                let ms = ms.clamp(100, 60_000);
                self.cooldown = Duration::from_millis(ms as u64);
                log::info!("cooldown = {} ms", ms);
            }

            Event::CmdBenchMode(on) => {
                self.bench_mode = on;
                if on {
                    log::warn!("BENCH MODE: CAN-silence auto-disarm DISABLED");
                } else {
                    log::info!("bench mode off");
                    self.last_can = Instant::now();
                }
            }

            Event::CmdLogStatus => {
                log::info!(
                    "state={:?} hv={}.{:03} V batt={}.{:03} V 5v={}.{:03} V charging={} done_raw={} beam={} bench={}",
                    self.state,
                    self.hv_mv / 1000, self.hv_mv % 1000,
                    self.batt_mv / 1000, self.batt_mv % 1000,
                    self.v5_mv / 1000, self.v5_mv % 1000,
                    self.charge.charging(),
                    self.done_raw(),
                    self.beam_active,
                    self.bench_mode,
                );
            }
        }
    }

    /// The one and only path to a gate pulse.
    async fn guarded_fire(&mut self, kind: FireKind, width_us: u32) -> Result<(), FireRefused> {
        let State::Armed { mode } = self.state else {
            return Err(FireRefused::NotArmed);
        };
        if self.hv_at.elapsed() > config::ADC_STALE {
            return Err(FireRefused::HvStale);
        }
        if self.hv_mv < config::FIRE_MIN_MV {
            return Err(FireRefused::CapTooLow);
        }
        let width_us = width_us.clamp(config::PULSE_MIN_US, config::PULSE_MAX_US);

        // Stop the flyback and let it settle before dumping the bank into
        // the coil.
        self.charge.pause();
        self.apply_outputs();
        Timer::after(config::PRE_FIRE_SETTLE).await;

        let hv_before = self.hv_mv;
        self.state = State::Firing;
        if self.pulses.fire(kind, width_us).is_err() {
            // Should be unreachable: the state machine already serializes
            // fires. Treat it as a real problem.
            self.fault(Fault::PulseBusy);
            return Err(FireRefused::PulseBusy);
        }

        // Awaiting here intentionally blocks all event processing until the
        // pulse is over: no reentrancy, no double fire.
        Timer::after(Duration::from_micros(width_us as u64) + config::POST_FIRE_SETTLE).await;

        self.last_fire = Instant::now();
        self.state = State::Cooldown {
            mode,
            until: Instant::now() + self.cooldown,
        };
        log::info!(
            "fired {:?} {} us (hv {} mV before)",
            kind,
            width_us,
            hv_before
        );
        Ok(())
    }

    /// 50 ms housekeeping: cooldown expiry, comms watchdog, charge timeout,
    /// stale-telemetry fault, DONE edge logging.
    fn tick(&mut self) {
        if let State::Cooldown { mode, until } = self.state {
            if Instant::now() >= until {
                self.state = State::Armed { mode };
            }
        }

        let armed_like = matches!(
            self.state,
            State::Armed { .. } | State::Cooldown { .. } | State::Firing
        );

        if armed_like {
            if !self.bench_mode && self.last_can.elapsed() > config::CAN_SILENCE_DISARM {
                log::warn!("CAN silent while armed -> auto-disarm");
                self.safe_down();
                self.state = State::Disarmed;
            }

            if self.hv_at.elapsed() > config::ADC_FAULT_AFTER {
                log::error!("HV telemetry stale while armed -> fault");
                self.fault(Fault::AdcStale);
            }
        }

        if self.charge.charging() && self.charge.cycle_elapsed() > config::CHARGE_TIMEOUT {
            log::error!("charge cycle exceeded timeout -> fault");
            self.fault(Fault::ChargeTimeout);
        }

        let done = self.done_raw();
        if done != self.last_done_raw {
            log::info!("LT3750 DONE raw level -> {} (hv {} mV)", done, self.hv_mv);
            self.last_done_raw = done;
        }
    }

    /// Charge hysteresis, evaluated after every event.
    ///
    /// The LT3750 self-terminates each cycle at its resistor-set target.
    /// DONE ([DS]: high while switching, pulled low when finished OR on an
    /// internal LT3750 fault) is the primary end-of-cycle signal; the ADC
    /// backstop, MIN_CYCLE_INTERVAL and the charge-timeout fault sit behind
    /// it so a wrong DONE interpretation degrades to loud logs, not damage.
    async fn tick_charge(&mut self) {
        let may_charge = matches!(self.state, State::Armed { .. } | State::Cooldown { .. });

        if !may_charge {
            if self.charge.charging() {
                self.charge.pause();
            }
            return;
        }

        if self.charge.charging() {
            // done_raw() == false means the DONE net is HIGH = switching.
            if !self.done_raw() {
                self.cycle_saw_charging = true;
            }
            let elapsed = self.charge.cycle_elapsed();
            if elapsed > config::DONE_SETTLE && self.cycle_saw_charging && self.done_raw() {
                if self.hv_mv < config::RECHARGE_ON_MV {
                    // Finished far below target: DONE interpretation or HV
                    // calibration is suspect — say so instead of pretending
                    // the bank is charged.
                    log::warn!(
                        "charge cycle 'complete' (DONE) at only {} mV — check DONE polarity / HV cal",
                        self.hv_mv
                    );
                } else {
                    log::info!("charge cycle complete (DONE) at {} mV", self.hv_mv);
                }
                self.charge.pause();
            } else if self.hv_mv >= config::CHARGE_BACKSTOP_MV {
                log::warn!("charge stopped by ADC backstop at {} mV", self.hv_mv);
                self.charge.pause();
            } else if elapsed > config::DONE_RISE_WARN
                && !self.cycle_saw_charging
                && !self.done_rise_warned
            {
                self.done_rise_warned = true;
                log::warn!(
                    "DONE never rose after CHARGE — LT3750 fault (UVLO/thermal) or dead flyback; will fault at charge timeout"
                );
            }
            // A cycle where DONE never goes high keeps `charging` set until
            // the CHARGE_TIMEOUT fault in tick().
        } else if self.hv_mv < config::RECHARGE_ON_MV
            && self.hv_at.elapsed() < config::ADC_STALE
            && self.last_fire.elapsed() > config::POST_FIRE_CHARGE_HOLDOFF
            && self.last_cycle_start.elapsed() > config::MIN_CYCLE_INTERVAL
        {
            if self.batt_mv < config::BATT_MIN_FOR_CHARGE_MV {
                if !self.batt_warned {
                    self.batt_warned = true;
                    log::warn!(
                        "charge blocked: battery {} mV < {} mV minimum",
                        self.batt_mv,
                        config::BATT_MIN_FOR_CHARGE_MV
                    );
                }
                return;
            }
            self.batt_warned = false;
            log::info!("charge cycle start ({} mV)", self.hv_mv);
            self.cycle_saw_charging = false;
            self.done_rise_warned = false;
            self.last_cycle_start = Instant::now();
            self.charge.begin_cycle().await;
        }
    }

    /// Everything off, queued pulses dropped. Used by disarm and faults.
    fn safe_down(&mut self) {
        self.charge.pause();
        self.pulses.clear_queued();
    }

    fn fault(&mut self, f: Fault) {
        self.safe_down();
        self.state = State::Faulted(f);
    }

    /// Output pins derived from state — the single place they are written
    /// outside of the fire/charge-cycle sequences.
    fn apply_outputs(&mut self) {
        match self.state {
            State::Disarmed | State::Faulted(_) => {
                // charge.pause() has run (safe_down / tick_charge); dump the bank.
                self.discharge_n.set_low();
            }
            State::Armed { .. } | State::Firing | State::Cooldown { .. } => {
                self.discharge_n.set_high();
            }
        }
    }

    fn done_raw(&self) -> bool {
        let level_high = self.done_in.is_high();
        if config::DONE_ACTIVE_LOW {
            !level_high
        } else {
            level_high
        }
    }

    fn snapshot(&self) -> Status {
        let (state_code, fault_code) = match self.state {
            State::Disarmed => (0, 0),
            State::Armed { mode: Mode::Manual } => (1, 0),
            State::Armed { mode: Mode::AutoBreakBeam } => (2, 0),
            State::Firing => (3, 0),
            State::Cooldown { .. } => (4, 0),
            State::Faulted(f) => (5, f as u8),
        };
        let shell_off = if config::SHELL_OFF_ACTIVE_LOW {
            self.shell_off_in.is_low()
        } else {
            self.shell_off_in.is_high()
        };
        let mut flags = 0u8;
        flags |= (self.charge.charging() as u8) << 0;
        flags |= (self.done_raw() as u8) << 1;
        flags |= (self.beam_active as u8) << 2;
        flags |= (shell_off as u8) << 3;
        flags |= ((self.hv_mv >= config::HV_READY_MV) as u8) << 4;
        flags |= (self.bench_mode as u8) << 5;
        Status {
            state_code,
            fault_code,
            flags,
            hv_mv: self.hv_mv,
            batt_mv: self.batt_mv,
        }
    }
}

#[embassy_executor::task]
pub async fn state_task(machine: Machine) -> ! {
    machine.run().await
}
