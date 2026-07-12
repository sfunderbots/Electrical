//! Built-in bring-up self-test (DUT test).
//!
//! Runs the low-voltage bring-up sequence from the board itself, needing no
//! external instruments: the HV ADC is the voltmeter, the LT3750's
//! resistor-set target is the reference, and the HV sag after each pulse is
//! the proof that real current went through the solenoid.
//!
//! It drives the SAME event queue the console and CAN use — no privileged
//! path, every state-machine guard stays active — and watches the STATUS
//! broadcast for results. A latched fault, a stage timeout, or a user
//! `disarm` aborts the run; the board is always left disarmed with the
//! charge ceiling cleared.
//!
//! Console: `selftest` (35 V ceiling), `selftest <20-60>` (volts),
//! `selftest full` (real ~210 V target — only after a low-voltage PASS).
//! Stages:
//!   1. static checks (disarmed, bank empty, battery present, inputs)
//!   2. charge to target (rate + stop point)
//!   3. KICK fire   -> HV sag proves the kick IGBT/solenoid path
//!   4. recharge    -> post-fire hysteresis works
//!   5. CHIP fire   -> HV sag proves the chip path (never factory-tested!)
//!   6. disarm      -> bank dump decay proves the discharge path

use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::channel::Channel;
use embassy_time::{Duration, Instant, Timer};

use crate::state::{Event, FireKind, Mode, Status, EVENTS, STATUS};

/// Bank ceiling for a plain `selftest`, in millivolts.
pub const DEFAULT_CEILING_MV: u32 = 35_000;
const TEST_PULSE_US: u32 = 1000;
/// Minimum post-pulse sag that counts as "the solenoid really fired".
const MIN_SAG_MV: u32 = 500;
const CHARGE_STAGE_TIMEOUT: Duration = Duration::from_secs(20);

/// Some(ceiling_mv) = guarded low-voltage run; None = full-voltage run.
pub static SELFTEST: Channel<CriticalSectionRawMutex, Option<u32>, 2> = Channel::new();

type StatusRx = embassy_sync::watch::Receiver<'static, CriticalSectionRawMutex, Status, 3>;

enum Abort {
    Timeout(&'static str),
    Fault(u8),
    Disarmed,
}

const STATE_DISARMED: u8 = 0;
const STATE_ARMED_MANUAL: u8 = 1;
const STATE_FAULTED: u8 = 5;
const FLAG_CHARGING: u8 = 1 << 0;

async fn send(ev: Event) {
    EVENTS.send(ev).await;
}

/// Wait until `ok(status)` holds. Aborts on fault, timeout, or (unless the
/// test expects it) a disarm — which is also the user's abort button.
async fn wait_for(
    rx: &mut StatusRx,
    timeout: Duration,
    disarm_expected: bool,
    what: &'static str,
    ok: impl Fn(&Status) -> bool,
) -> Result<Status, Abort> {
    let deadline = Instant::now() + timeout;
    loop {
        let now = Instant::now();
        if now >= deadline {
            return Err(Abort::Timeout(what));
        }
        match embassy_futures::select::select(rx.changed(), Timer::at(deadline)).await {
            embassy_futures::select::Either::First(s) => {
                if s.state_code == STATE_FAULTED {
                    return Err(Abort::Fault(s.fault_code));
                }
                if !disarm_expected && s.state_code == STATE_DISARMED {
                    return Err(Abort::Disarmed);
                }
                if ok(&s) {
                    return Ok(s);
                }
            }
            embassy_futures::select::Either::Second(()) => return Err(Abort::Timeout(what)),
        }
    }
}

async fn cleanup() {
    send(Event::CmdDisarm).await;
    send(Event::CmdSetChargeCeiling(None)).await;
    send(Event::CmdBenchMode(false)).await;
}

async fn fire_and_measure(
    rx: &mut StatusRx,
    kind: FireKind,
    hv_before: u32,
) -> Result<u32, Abort> {
    send(Event::CmdFire {
        kind,
        width_us: TEST_PULSE_US,
    })
    .await;
    // Pulse (1 ms) + cooldown entry + a few ADC/IIR periods to settle.
    Timer::after_millis(400).await;
    let s = wait_for(rx, Duration::from_secs(1), false, "timeout: no status after fire", |_| true).await?;
    Ok(hv_before.saturating_sub(s.hv_mv))
}

async fn run(rx: &mut StatusRx, ceiling: Option<u32>) -> Result<(), &'static str> {
    // The value the bank must reach to call the charge stage done.
    let target_mv = match ceiling {
        Some(c) => c.saturating_sub(2_000),
        None => crate::config::RECHARGE_ON_MV,
    };

    // Stage 1: static checks.
    let Some(s) = rx.try_get() else {
        return Err("no telemetry yet");
    };
    if s.state_code != STATE_DISARMED {
        return Err("not disarmed — disarm first");
    }
    if s.hv_mv > 10_000 {
        return Err("bank not empty (> 10 V) — wait for dump");
    }
    if s.batt_mv < crate::config::BATT_MIN_FOR_CHARGE_MV {
        return Err("battery too low/absent — the flyback needs the pack");
    }
    log::info!(
        "selftest: [1/6] static checks OK (batt {} mV, hv {} mV, beam {}, done_raw {})",
        s.batt_mv,
        s.hv_mv,
        (s.flags >> 2) & 1,
        (s.flags >> 1) & 1,
    );

    let what_fires = if crate::config::CHIP_INSTALLED {
        "BOTH SOLENOIDS"
    } else {
        "the KICK solenoid"
    };
    match ceiling {
        Some(c) => log::warn!(
            "selftest: will charge to {} mV and FIRE {} in 3 s — 'disarm' aborts",
            c,
            what_fires
        ),
        None => log::warn!(
            "selftest: FULL-VOLTAGE run — will charge to the real target and FIRE {} in 3 s — 'disarm' aborts",
            what_fires
        ),
    }
    Timer::after_secs(3).await;

    send(Event::CmdBenchMode(true)).await;
    send(Event::CmdSetChargeCeiling(ceiling)).await;
    send(Event::CmdArm(Mode::Manual)).await;

    // STATUS snapshots emitted while the commands above are still queued
    // show Disarmed; only once Armed has been seen does Disarmed mean the
    // user aborted. So: tolerate Disarmed until the arm lands.
    wait_for(rx, Duration::from_secs(2), true, "timeout: arm never took", |s| {
        s.state_code == STATE_ARMED_MANUAL
    })
    .await
    .map_err(abort_msg)?;

    // Stage 2: charge.
    let t0 = Instant::now();
    let s = wait_for(rx, CHARGE_STAGE_TIMEOUT, false, "timeout: charge never reached target", |s| {
        s.hv_mv >= target_mv
    })
    .await
    .map_err(abort_msg)?;
    let charge_ms = t0.elapsed().as_millis();
    log::info!(
        "selftest: [2/6] charge OK ({} mV in {} ms)",
        s.hv_mv,
        charge_ms
    );
    // Let the cycle terminate; the bank must then hold (not run away).
    Timer::after_millis(600).await;
    let s = wait_for(rx, Duration::from_secs(1), false, "timeout: no status after charge", |_| true)
        .await
        .map_err(abort_msg)?;
    // The flyback slews ~200 mV/ms at low bank voltage and HV samples arrive
    // every 20 ms through an IIR filter, so a low ceiling overshoots by a
    // few volts before the pause lands — sampling physics, not a fault
    // (measured: +3.4 V at a 35 V ceiling). Allow 10% + 5 V; at full voltage
    // the overvoltage fault is the real guardian.
    let ceiling_check = ceiling.unwrap_or(crate::config::CHARGE_BACKSTOP_MV);
    if s.hv_mv > ceiling_check + ceiling_check / 10 + 5_000 {
        return Err("bank overshot the target — HV cal or charge stop suspect");
    }
    let hv_charged = s.hv_mv;

    // Stage 3: kick fire.
    let sag = fire_and_measure(rx, FireKind::Kick, hv_charged)
        .await
        .map_err(abort_msg)?;
    if sag < MIN_SAG_MV {
        return Err("KICK fired but HV barely sagged — kick IGBT/solenoid path suspect");
    }
    log::info!("selftest: [3/6] KICK fire OK (sag {} mV)", sag);

    // Stage 4: recharge (cooldown 500 ms + cycle interval floor apply).
    let t0 = Instant::now();
    let s = wait_for(rx, CHARGE_STAGE_TIMEOUT, false, "timeout: recharge never completed", |s| {
        s.hv_mv >= target_mv && s.flags & FLAG_CHARGING == 0
    })
    .await
    .map_err(abort_msg)?;
    log::info!(
        "selftest: [4/6] recharge OK ({} mV in {} ms)",
        s.hv_mv,
        t0.elapsed().as_millis()
    );
    let hv_charged = s.hv_mv;

    // Stage 5: chip fire — this channel has never been driven on this board.
    if crate::config::CHIP_INSTALLED {
        let sag = fire_and_measure(rx, FireKind::Chip, hv_charged)
            .await
            .map_err(abort_msg)?;
        if sag < MIN_SAG_MV {
            return Err("CHIP fired but HV barely sagged — chip IGBT/solenoid path suspect");
        }
        log::info!("selftest: [5/6] CHIP fire OK (sag {} mV)", sag);
    } else {
        log::info!("selftest: [5/6] CHIP skipped (config::CHIP_INSTALLED = false)");
    }

    // Stage 6: disarm and watch the bank dump.
    send(Event::CmdDisarm).await;
    let t0 = Instant::now();
    let floor = 5_000.max(hv_charged / 20);
    wait_for(rx, Duration::from_secs(45), true, "timeout: bank did not dump", |s| {
        s.hv_mv <= floor
    })
    .await
    .map_err(abort_msg)?;
    log::info!(
        "selftest: [6/6] dump OK ({} -> <= {} mV in {} ms)",
        hv_charged,
        floor,
        t0.elapsed().as_millis()
    );

    send(Event::CmdSetChargeCeiling(None)).await;
    send(Event::CmdBenchMode(false)).await;
    Ok(())
}

fn abort_msg(a: Abort) -> &'static str {
    match a {
        Abort::Timeout(what) => what, // call sites phrase these as "timeout: ..."
        Abort::Fault(1) => "FAULT: overvoltage",
        Abort::Fault(2) => "FAULT: charge timeout",
        Abort::Fault(3) => "FAULT: pulse busy",
        Abort::Fault(4) => "FAULT: adc stale",
        Abort::Fault(_) => "FAULT",
        Abort::Disarmed => "aborted (disarmed)",
    }
}

#[embassy_executor::task]
pub async fn selftest_task() -> ! {
    let mut rx = STATUS.receiver().unwrap();
    loop {
        let ceiling = SELFTEST.receive().await;
        log::info!("selftest: START");
        match run(&mut rx, ceiling).await {
            Ok(()) if crate::config::CHIP_INSTALLED => {
                log::info!("selftest: PASS (all 6 stages)")
            }
            Ok(()) => log::info!("selftest: PASS (5 stages, chip skipped)"),
            Err(msg) => {
                cleanup().await;
                log::error!("selftest: FAIL — {}", msg);
            }
        }
    }
}
