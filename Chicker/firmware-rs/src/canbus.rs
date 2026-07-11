//! MCP2515 CAN link: interrupt-driven RX -> events, 10 Hz STATUS TX.
//!
//! The MCP2515 hangs off SPI0 with its own 8 MHz crystal (bus max 500 kbps).
//! With the `mcp2515` crate feature, the Read-RX-Buffer SPI instruction
//! auto-clears the RXnIF flag, so draining both buffers releases the INT
//! line by itself.

use embassy_futures::select::{select, Either};
use embassy_rp::gpio::{Input, Output};
use embassy_rp::peripherals::SPI0;
use embassy_rp::spi::{Blocking, Spi};
use embassy_time::{Delay, Duration, Instant, Ticker, Timer};
use embedded_can::nb::Can;
use embedded_hal_bus::spi::ExclusiveDevice;
use mcp25xx::registers::{
    OperationMode, Register, CANINTE, CANSTAT, CNF1, CNF2, CNF3, EFLG, REC, RXB0CTRL, RXB1CTRL,
    RXM, TEC,
};
use mcp25xx::MCP25xx;
use portable_atomic::{AtomicBool, Ordering};

use crate::config;
use crate::protocol;
use crate::state::{EVENTS, STATUS};

type SpiDev = ExclusiveDevice<Spi<'static, SPI0, Blocking>, Output<'static>, Delay>;

/// Set by the console `canstat` command; the CAN task answers with a full
/// controller diagnostic on its next tick.
pub static DIAG_REQ: AtomicBool = AtomicBool::new(false);

fn log_diag(mcp: &mut MCP25xx<SpiDev>, tx_drops: u32) {
    let mut cnf = [0u8; 3];
    let _ = mcp.read_registers(CNF3::ADDRESS, &mut cnf);
    let canstat = mcp
        .read_register::<CANSTAT>()
        .map(|r| r.into_bytes()[0])
        .unwrap_or(0xFF);
    let tec = mcp.read_register::<TEC>().map(|r| r.0).unwrap_or(0xFF);
    let rec = mcp.read_register::<REC>().map(|r| r.0).unwrap_or(0xFF);
    let eflg = mcp
        .read_register::<EFLG>()
        .map(|r| r.into_bytes()[0])
        .unwrap_or(0xFF);
    // Read CNF twice: as a burst and as three single-register reads. If they
    // disagree, the multi-byte SPI path is broken, not the configuration.
    let s3 = mcp.read_register::<CNF3>().map(|r| r.into_bytes()[0]).unwrap_or(0xFF);
    let s2 = mcp.read_register::<CNF2>().map(|r| r.into_bytes()[0]).unwrap_or(0xFF);
    let s1 = mcp.read_register::<CNF1>().map(|r| r.into_bytes()[0]).unwrap_or(0xFF);
    log::info!(
        "canstat: uptime={}s CNF3/2/1 burst={:02x} {:02x} {:02x} single={:02x} {:02x} {:02x} (want 85 b1 00) CANSTAT={:02x} TEC={} REC={} EFLG={:02x} tx_drops={}",
        Instant::now().as_secs(),
        cnf[0],
        cnf[1],
        cnf[2],
        s3,
        s2,
        s1,
        canstat,
        tec,
        rec,
        eflg,
        tx_drops,
    );
}

async fn init_mcp(mcp: &mut MCP25xx<SpiDev>) -> Result<(), ()> {
    mcp.reset().map_err(|_| ())?;
    // [DS] after RESET the MCP2515 ignores SPI for ~128 OSC1 cycles (16 us
    // at 8 MHz). Writes issued inside that window are silently lost — this
    // firmware is fast enough to hit it (the old MicroPython one never was),
    // which left the chip in Normal mode with all-zero CNF, jamming the
    // shared motor bus with wrong-bitrate retries. Wait it out.
    Timer::after_millis(2).await;

    let cnf = config::CAN_BITRATE_CNF;
    mcp.set_bitrate(cnf).map_err(|_| ())?;
    // Accept everything into both RX buffers; RXB0 rolls over into RXB1 so
    // back-to-back frames aren't lost while we drain.
    mcp.write_register(RXB0CTRL::new().with_rxm(RXM::ReceiveAny).with_bukt(true))
        .map_err(|_| ())?;
    mcp.write_register(RXB1CTRL::new().with_rxm(RXM::ReceiveAny))
        .map_err(|_| ())?;
    // Interrupt on either RX buffer being full -> INT pin goes low.
    mcp.write_register(CANINTE::new().with_rx0ie(true).with_rx1ie(true))
        .map_err(|_| ())?;

    // Verify the bit timing actually landed BEFORE going on-bus: a node in
    // Normal mode with wrong timing corrupts every frame for everyone else.
    // On mismatch we stay in Configuration mode (silent, harmless) and the
    // caller retries loudly.
    let want = [
        cnf.cnf3.into_bytes()[0],
        cnf.cnf2.into_bytes()[0],
        cnf.cnf1.into_bytes()[0],
    ];
    let mut got = [0u8; 3];
    mcp.read_registers(CNF3::ADDRESS, &mut got).map_err(|_| ())?;
    if got != want {
        log::error!(
            "MCP2515 CNF verify failed: got {:02x?} want {:02x?} — staying off the bus",
            got,
            want
        );
        return Err(());
    }

    mcp.set_mode(OperationMode::NormalOperation).map_err(|_| ())?;
    Ok(())
}

#[embassy_executor::task]
pub async fn can_task(spi_dev: SpiDev, mut int: Input<'static>, mut led: Output<'static>) -> ! {
    let mut mcp = MCP25xx { spi: spi_dev };

    // Give the 8 MHz oscillator time after power-up, then retry init until
    // the controller responds — CAN must not take the rest of the firmware
    // down with it.
    Timer::after_millis(10).await;
    loop {
        match init_mcp(&mut mcp).await {
            Ok(()) => break,
            Err(()) => {
                log::error!("MCP2515 init failed, retrying");
                Timer::after_millis(500).await;
            }
        }
    }
    // Read back what actually landed in the chip: a corrupted SPI write to
    // the CNF registers would silently put the wrong bitrate on the wire,
    // which corrupts every frame on the shared motor bus.
    let mut cnf = [0u8; 3];
    let _ = mcp.read_registers(CNF3::ADDRESS, &mut cnf);
    let canstat = mcp
        .read_register::<CANSTAT>()
        .map(|r| r.into_bytes()[0])
        .unwrap_or(0xFF);
    log::info!(
        "MCP2515 up, 250 kbps (8 MHz xtal): CNF3/2/1 = {:02x} {:02x} {:02x} (want 85 b1 00), CANSTAT = {:02x} (top 3 bits 000 = normal mode)",
        cnf[0],
        cnf[1],
        cnf[2],
        canstat,
    );

    let mut status_rx = STATUS.receiver().unwrap();
    let mut tx_tick = Ticker::every(config::STATUS_PERIOD);
    let mut seq: u8 = 0;
    let mut tx_drops: u32 = 0;
    let mut last_tx_diag = Instant::MIN;

    loop {
        if DIAG_REQ.swap(false, Ordering::Relaxed) {
            log_diag(&mut mcp, tx_drops);
        }
        match select(int.wait_for_low(), tx_tick.next()).await {
            Either::First(()) => {
                // Drain every pending frame; reading clears the flags and
                // releases INT.
                loop {
                    let Ok(status) = mcp.read_status() else { break };
                    let buf = if status.rx0if() {
                        mcp25xx::RxBuffer::RXB0
                    } else if status.rx1if() {
                        mcp25xx::RxBuffer::RXB1
                    } else {
                        break;
                    };
                    match mcp.read_rx_buffer(buf) {
                        Ok(frame) => {
                            if let Some(events) = protocol::decode(&frame) {
                                led.toggle();
                                for ev in events {
                                    if EVENTS.try_send(ev).is_err() {
                                        log::warn!("event queue full, dropped CAN cmd");
                                    }
                                }
                            }
                        }
                        Err(_) => break,
                    }
                }
            }
            Either::Second(()) => {
                if let Some(st) = status_rx.try_get() {
                    seq = seq.wrapping_add(1);
                    if let Some(frame) = protocol::encode_status(&st, seq) {
                        match mcp.transmit(&frame) {
                            Ok(_) => {
                                if tx_drops > 0 {
                                    log::info!("CAN TX flowing again after {} drops", tx_drops);
                                    tx_drops = 0;
                                }
                            }
                            Err(nb::Error::WouldBlock) => {
                                // All 3 TX buffers stuck pending = nothing on
                                // the bus is ACKing us. Rate-limit the warning
                                // and attach the chip's error counters, which
                                // tell "no ACK" (TEC pegged ~128, REC low)
                                // apart from "we corrupt the bus / bad
                                // bitrate" (REC climbing too).
                                tx_drops += 1;
                                if last_tx_diag.elapsed() > Duration::from_secs(2) {
                                    last_tx_diag = Instant::now();
                                    let tec = mcp.read_register::<TEC>().map(|r| r.0).unwrap_or(0xFF);
                                    let rec = mcp.read_register::<REC>().map(|r| r.0).unwrap_or(0xFF);
                                    let eflg = mcp
                                        .read_register::<EFLG>()
                                        .map(|r| r.into_bytes()[0])
                                        .unwrap_or(0xFF);
                                    log::warn!(
                                        "CAN TX buffers full ({} dropped): TEC={} REC={} EFLG={:02x}",
                                        tx_drops,
                                        tec,
                                        rec,
                                        eflg,
                                    );
                                }
                            }
                            Err(nb::Error::Other(_)) => log::warn!("CAN TX error"),
                        }
                    }
                }
            }
        }
    }
}
