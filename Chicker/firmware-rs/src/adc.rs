//! ADC monitor: capacitor-bank voltage (fast), battery and 5 V rail (slow).
//!
//! HV is sampled at ~500 Hz as a median-of-5 (kills LT3750 switching spikes)
//! followed by a light IIR low-pass. A filtered sample is sent to the state
//! machine every 20 ms; crossing the overvoltage threshold sends an
//! immediate fast-path event.

use embassy_rp::adc::{Adc, Async, Channel};
use embassy_time::{Duration, Instant, Ticker};

use crate::config;
use crate::state::{Event, EVENTS};

fn median5(mut v: [u16; 5]) -> u16 {
    v.sort_unstable();
    v[2]
}

fn send(ev: Event) {
    // Never block the sampling loop; the state machine drains fast and a
    // dropped sample is replaced 20 ms later.
    if EVENTS.try_send(ev).is_err() {
        log::warn!("event queue full, dropped ADC event");
    }
}

#[embassy_executor::task]
pub async fn adc_task(
    mut adc: Adc<'static, Async>,
    mut ch_hv: Channel<'static>,
    mut ch_batt: Channel<'static>,
    mut ch_v5: Channel<'static>,
) -> ! {
    let mut ticker = Ticker::every(Duration::from_millis(2));
    let mut hv_filt: i32 = -1; // seeded on first sample
    let mut over_since: Option<Instant> = None;
    let mut last_over_sent = Instant::MIN;
    let mut n: u32 = 0;

    loop {
        ticker.next().await;
        n = n.wrapping_add(1);

        let mut raw = [0u16; 5];
        let mut ok = true;
        for r in raw.iter_mut() {
            match adc.read(&mut ch_hv).await {
                Ok(v) => *r = v,
                Err(_) => {
                    ok = false;
                    break;
                }
            }
        }
        if !ok {
            // No sample sent -> the state machine sees stale HV and, if it
            // stays that way, faults and stops feeding the watchdog.
            continue;
        }

        let med = median5(raw) as i32;
        if hv_filt < 0 {
            hv_filt = med;
        } else {
            hv_filt += (med - hv_filt) >> 2;
        }
        let hv_mv = (hv_filt as u32) * config::HV_MV_NUM / 1000;

        // Fast path: overvoltage crossing (rate-limited to 1/s).
        if hv_mv > config::OVERVOLT_MV {
            if over_since.is_none() {
                over_since = Some(Instant::now());
            }
            if last_over_sent.elapsed() > Duration::from_secs(1) {
                last_over_sent = Instant::now();
                send(Event::Overvoltage { hv_mv });
            }
        } else {
            over_since = None;
        }

        if n % 10 == 0 {
            send(Event::HvSample { hv_mv });
        }

        if n % 50 == 0 {
            let batt = adc.read(&mut ch_batt).await.unwrap_or(0) as u32;
            let v5 = adc.read(&mut ch_v5).await.unwrap_or(0) as u32;
            send(Event::AuxSample {
                batt_mv: batt * config::BATT_MV_NUM / 1000,
                v5_mv: v5 * config::V5_MV_NUM / 1000,
            });
        }
    }
}
