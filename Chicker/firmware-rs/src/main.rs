//! Chicker kicker-board firmware.
//!
//! Boot order is safety-critical and must not be reordered:
//!   1. The power-stage pins (CHARGE, ~DISCHARGE, KICK, CHIP) are claimed
//!      and driven low before anything else runs. (RP2040 pads reset to
//!      pull-down, so the window before this is passively safe too.)
//!   2. The hardware watchdog starts before the tasks are spawned; if the
//!      state machine ever stops feeding it, the chip resets, pads return
//!      to pull-down, charging stops and the bank bleeds through the
//!      autodischarge network.

#![no_std]
#![no_main]

mod adc;
mod beam;
mod canbus;
mod charger;
mod config;
mod protocol;
mod pulse;
mod selftest;
mod state;
mod usb;

use embassy_executor::Spawner;
use embassy_rp::adc::{Adc, Channel as AdcChannel, Config as AdcConfig, InterruptHandler as AdcIrq};
use embassy_rp::bind_interrupts;
use embassy_rp::gpio::{Input, Level, Output, Pull};
use embassy_rp::peripherals::{PIO0, USB};
use embassy_rp::pio::{InterruptHandler as PioIrq, Pio};
use embassy_rp::spi::{Config as SpiConfig, Spi};
use embassy_rp::usb::{Driver, InterruptHandler as UsbIrq};
use embassy_rp::watchdog::Watchdog;
use embassy_time::Delay;
use embedded_hal_bus::spi::ExclusiveDevice;

use crate::pulse::PulseGen;
use crate::state::Machine;

bind_interrupts!(struct Irqs {
    USBCTRL_IRQ => UsbIrq<USB>;
    ADC_IRQ_FIFO => AdcIrq;
    PIO0_IRQ_0 => PioIrq<PIO0>;
});

#[embassy_executor::main]
async fn main(spawner: Spawner) {
    let p = embassy_rp::init(Default::default());

    // ---- 1. Safe the power stage before anything else. ----
    let charge = Output::new(p.PIN_5, Level::Low); // LT3750 CHARGE off
    let discharge_n = Output::new(p.PIN_8, Level::Low); // bank dumping
    let pulses = PulseGen::new(Pio::new(p.PIO0, Irqs), p.PIN_3, p.PIN_2); // gates low

    // ---- 2. Watchdog before tasks. ----
    let mut watchdog = Watchdog::new(p.WATCHDOG);
    watchdog.start(config::HW_WATCHDOG);

    // ---- 3. Everything else. ----
    let done_in = Input::new(p.PIN_4, Pull::None);
    let shell_off_in = Input::new(p.PIN_9, Pull::Up);
    let beam_in = Input::new(p.PIN_23, Pull::Up);

    let adc = Adc::new(p.ADC, Irqs, AdcConfig::default());
    let ch_hv = AdcChannel::new_pin(p.PIN_29, Pull::None);
    let ch_batt = AdcChannel::new_pin(p.PIN_26, Pull::None);
    let ch_v5 = AdcChannel::new_pin(p.PIN_27, Pull::None);

    let mut spi_cfg = SpiConfig::default();
    spi_cfg.frequency = config::MCP2515_SPI_HZ;
    let spi = Spi::new_blocking(p.SPI0, p.PIN_18, p.PIN_19, p.PIN_16, spi_cfg);
    let can_cs = Output::new(p.PIN_17, Level::High);
    let spi_dev = ExclusiveDevice::new(spi, can_cs, Delay).unwrap();
    let can_int = Input::new(p.PIN_20, Pull::Up);
    let can_led = Output::new(p.PIN_6, Level::Low);

    let usb_driver = Driver::new(p.USB, Irqs);

    let machine = Machine::new(charge, discharge_n, done_in, shell_off_in, pulses, watchdog);

    spawner.spawn(usb::usb_task(usb_driver).unwrap());
    spawner.spawn(adc::adc_task(adc, ch_hv, ch_batt, ch_v5).unwrap());
    spawner.spawn(beam::beam_task(beam_in).unwrap());
    spawner.spawn(canbus::can_task(spi_dev, can_int, can_led).unwrap());
    spawner.spawn(state::state_task(machine).unwrap());
    spawner.spawn(selftest::selftest_task().unwrap());
}

/// Last-resort panic handler: reclaim the four power-stage pins from
/// whatever peripheral owns them (the fire pins belong to PIO0), force them
/// low, then stop feeding the watchdog so the chip reboots into Disarmed
/// within a second.
#[panic_handler]
fn panic(_info: &core::panic::PanicInfo) -> ! {
    use embassy_rp::pac;

    const CHIP: usize = 2; // fire chip solenoid
    const KICK: usize = 3; // fire kick solenoid
    const CHARGE: usize = 5; // LT3750 enable
    const DISCHARGE_N: usize = 8; // low = dump the bank

    let mask: u32 = (1 << CHIP) | (1 << KICK) | (1 << CHARGE) | (1 << DISCHARGE_N);

    // Drive low via SIO first, then switch the pad function to SIO so the
    // level takes effect regardless of the previous owner.
    pac::SIO.gpio_out(0).value_clr().write_value(mask);
    pac::SIO.gpio_oe(0).value_set().write_value(mask);
    for pin in [CHIP, KICK, CHARGE, DISCHARGE_N] {
        pac::IO_BANK0
            .gpio(pin)
            .ctrl()
            .write(|w| w.set_funcsel(pac::io::vals::Gpio0ctrlFuncsel::SIO_0 as _));
    }

    loop {
        cortex_m::asm::wfe();
    }
}
