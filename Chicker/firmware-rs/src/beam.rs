//! Break-beam (ball detect) watcher on GPIO23.
//!
//! Sends `BeamState` on every debounced level change and `BeamBroken` when
//! the beam becomes active (ball present). Whether the state machine acts on
//! it is entirely its own decision (Armed + AutoBreakBeam mode only).

use embassy_rp::gpio::Input;
use embassy_time::Timer;

use crate::config;
use crate::state::{Event, EVENTS};

fn is_active(input: &Input<'_>) -> bool {
    if config::BEAM_ACTIVE_LOW {
        input.is_low()
    } else {
        input.is_high()
    }
}

#[embassy_executor::task]
pub async fn beam_task(mut input: Input<'static>) -> ! {
    let mut active = is_active(&input);
    let _ = EVENTS.try_send(Event::BeamState(active));

    loop {
        input.wait_for_any_edge().await;
        Timer::after(config::BEAM_DEBOUNCE).await;
        let now_active = is_active(&input);
        if now_active != active {
            active = now_active;
            let _ = EVENTS.try_send(Event::BeamState(active));
            if active {
                let _ = EVENTS.try_send(Event::BeamBroken);
            }
        }
    }
}
