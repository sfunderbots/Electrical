//! USB CDC serial: log output plus a line-based bench console.
//!
//! The console has no privileged path — every command becomes the same
//! `Event` the CAN link produces, and goes through the same state machine.
//!
//! Commands:
//!   arm manual | arm auto
//!   disarm
//!   kick <us>            fire the kick solenoid (Armed only)
//!   chip <us>            fire the chip solenoid (Armed only)
//!   autofire <us> [kick|chip]
//!   cooldown <ms>
//!   benchmode on|off     disables the CAN-silence auto-disarm (bench only)
//!   ceiling <V>|off      bench charge ceiling (can only lower the target)
//!   selftest [V|full]    built-in bring-up test, see selftest.rs
//!   status
//!   bootloader           reboot into the USB UF2 bootloader (RPI-RP2)

use core::cell::RefCell;

use embassy_rp::peripherals::USB;
use embassy_rp::usb::Driver;
use embassy_sync::blocking_mutex::raw::CriticalSectionRawMutex;
use embassy_sync::blocking_mutex::Mutex;
use embassy_usb_logger::ReceiverHandler;

use crate::state::{Event, FireKind, Mode, EVENTS};

static LINE: Mutex<CriticalSectionRawMutex, RefCell<heapless::Vec<u8, 64>>> =
    Mutex::new(RefCell::new(heapless::Vec::new()));

pub struct Console;

impl ReceiverHandler for Console {
    fn new() -> Self {
        Console
    }

    async fn handle_data(&self, data: &[u8]) {
        for &b in data {
            let line: Option<heapless::Vec<u8, 64>> = LINE.lock(|l| {
                let mut l = l.borrow_mut();
                match b {
                    b'\r' | b'\n' => {
                        if l.is_empty() {
                            None
                        } else {
                            let full = l.clone();
                            l.clear();
                            Some(full)
                        }
                    }
                    _ => {
                        if l.push(b).is_err() {
                            l.clear(); // overlong garbage; start over
                        }
                        None
                    }
                }
            });
            if let Some(line) = line {
                if let Ok(s) = core::str::from_utf8(&line) {
                    if parse_line(s.trim()) == Action::Bootloader {
                        // Deliberately bypasses the state machine: the chip
                        // reset returns every pad to its pull-down (charge
                        // off, bank dumping) — the exact pin state Disarmed
                        // drives — before the bootloader comes up.
                        log::warn!("rebooting into USB bootloader (RPI-RP2)");
                        embassy_time::Timer::after_millis(100).await; // flush log
                        embassy_rp::rom_data::reset_to_usb_boot(1 << 6, 0); // CAN LED = activity light
                    }
                }
            }
        }
    }
}

#[derive(PartialEq)]
enum Action {
    None,
    Bootloader,
}

fn send(ev: Event) {
    if EVENTS.try_send(ev).is_err() {
        log::warn!("event queue full, console command dropped");
    }
}

fn parse_line(line: &str) -> Action {
    let mut words = line.split_whitespace();
    let cmd = words.next().unwrap_or("");
    let arg1 = words.next();
    let arg2 = words.next();

    match (cmd, arg1) {
        ("arm", Some("manual")) => send(Event::CmdArm(Mode::Manual)),
        ("arm", Some("auto")) => send(Event::CmdArm(Mode::AutoBreakBeam)),
        ("disarm", _) => send(Event::CmdDisarm),
        ("kick", Some(us)) | ("chip", Some(us)) => match us.parse::<u32>() {
            Ok(width_us) => {
                let kind = if cmd == "kick" {
                    FireKind::Kick
                } else {
                    FireKind::Chip
                };
                send(Event::CmdFire { kind, width_us });
            }
            Err(_) => log::warn!("usage: {} <microseconds>", cmd),
        },
        ("autofire", Some(us)) => match us.parse::<u32>() {
            Ok(width_us) => {
                let kind = match arg2 {
                    Some("chip") => FireKind::Chip,
                    _ => FireKind::Kick,
                };
                send(Event::CmdSetAutofire { kind, width_us });
            }
            Err(_) => log::warn!("usage: autofire <us> [kick|chip]"),
        },
        ("cooldown", Some(ms)) => match ms.parse::<u32>() {
            Ok(ms) => send(Event::CmdSetCooldownMs(ms)),
            Err(_) => log::warn!("usage: cooldown <ms>"),
        },
        ("benchmode", Some("on")) => send(Event::CmdBenchMode(true)),
        ("benchmode", Some("off")) => send(Event::CmdBenchMode(false)),
        ("status", _) => send(Event::CmdLogStatus),
        ("canstat", _) => crate::canbus::DIAG_REQ.store(true, portable_atomic::Ordering::Relaxed),
        ("ceiling", Some("off")) => send(Event::CmdSetChargeCeiling(None)),
        ("ceiling", Some(v)) => match v.parse::<u32>() {
            Ok(volts) if (5..=250).contains(&volts) => {
                send(Event::CmdSetChargeCeiling(Some(volts * 1000)))
            }
            _ => log::warn!("usage: ceiling <volts 5-250>|off"),
        },
        ("selftest", arg) => {
            let req = match arg {
                None => Some(crate::selftest::DEFAULT_CEILING_MV),
                Some("full") => None,
                Some(v) => match v.parse::<u32>() {
                    Ok(volts) if (20..=200).contains(&volts) => Some(volts * 1000),
                    _ => {
                        log::warn!("usage: selftest [volts 20-200|full]");
                        return Action::None;
                    }
                },
            };
            if crate::selftest::SELFTEST.try_send(req).is_err() {
                log::warn!("selftest already queued/running");
            }
        }
        ("bootloader", _) => return Action::Bootloader,
        ("", _) => {}
        _ => log::warn!(
            "unknown cmd. try: arm manual|auto, disarm, kick <us>, chip <us>, autofire <us> [kick|chip], cooldown <ms>, benchmode on|off, ceiling <V>|off, selftest [V|full], status, canstat, bootloader"
        ),
    }
    Action::None
}

#[embassy_executor::task]
pub async fn usb_task(driver: Driver<'static, USB>) {
    embassy_usb_logger::run!(2048, log::LevelFilter::Info, driver, Console);
}
