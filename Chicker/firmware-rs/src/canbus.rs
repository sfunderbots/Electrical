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
use embassy_time::{Delay, Ticker, Timer};
use embedded_can::nb::Can;
use embedded_hal_bus::spi::ExclusiveDevice;
use mcp25xx::registers::{OperationMode, CANINTE, RXB0CTRL, RXB1CTRL, RXM};
use mcp25xx::{Config as McpConfig, MCP25xx};

use crate::config;
use crate::protocol;
use crate::state::{EVENTS, STATUS};

type SpiDev = ExclusiveDevice<Spi<'static, SPI0, Blocking>, Output<'static>, Delay>;

fn init_mcp(mcp: &mut MCP25xx<SpiDev>) -> Result<(), ()> {
    // Accept everything into both RX buffers; RXB0 rolls over into RXB1 so
    // back-to-back frames aren't lost while we drain.
    let cfg = McpConfig::default()
        .mode(OperationMode::NormalOperation)
        .bitrate(config::CAN_BITRATE_CNF)
        .receive_buffer_0(RXB0CTRL::new().with_rxm(RXM::ReceiveAny).with_bukt(true))
        .receive_buffer_1(RXB1CTRL::new().with_rxm(RXM::ReceiveAny));
    mcp.apply_config(&cfg).map_err(|_| ())?;
    // Interrupt on either RX buffer being full -> INT pin goes low.
    mcp.write_register(CANINTE::new().with_rx0ie(true).with_rx1ie(true))
        .map_err(|_| ())?;
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
        match init_mcp(&mut mcp) {
            Ok(()) => break,
            Err(()) => {
                log::error!("MCP2515 init failed, retrying");
                Timer::after_millis(500).await;
            }
        }
    }
    log::info!("MCP2515 up, 500 kbps (8 MHz xtal)");

    let mut status_rx = STATUS.receiver().unwrap();
    let mut tx_tick = Ticker::every(config::STATUS_PERIOD);
    let mut seq: u8 = 0;

    loop {
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
                            Ok(_) => {}
                            Err(nb::Error::WouldBlock) => {
                                log::warn!("CAN TX buffers full, status dropped")
                            }
                            Err(nb::Error::Other(_)) => log::warn!("CAN TX error"),
                        }
                    }
                }
            }
        }
    }
}
