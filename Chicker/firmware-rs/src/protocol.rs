//! CAN protocol v0 — encode/decode between frames and state-machine events.
//!
//! All IDs, the bitrate and the ARM magic live in `config.rs`; they are
//! placeholders until reconciled with the rest of the robot's bus.
//!
//! robot -> chicker
//!   0x310 ARM        [0xA5, 0x5A, mode]           mode 0=Manual 1=AutoBreakBeam
//!   0x311 DISARM     (any payload)
//!   0x312 KICK       [kind, width_lo, width_hi]   kind 0=kick 1=chip, width in us
//!   0x313 CONFIG     [key, v0, v1, v2, v3]        key 0=autofire width (+kind in v2)
//!                                                 key 1=cooldown ms
//!   0x314 HEARTBEAT  (any payload)                only resets the comms watchdog
//! chicker -> robot
//!   0x320 STATUS     [state, fault, flags, hv_dV_lo, hv_dV_hi,
//!                     batt_cV_lo, batt_cV_hi, seq]  at 10 Hz

use embedded_can::{Frame, Id, StandardId};
use mcp25xx::CanFrame;

use crate::config;
use crate::state::{Event, FireKind, Mode, Status};

/// Decode an inbound frame. `Some(vec)` = recognized (feeds the comms
/// watchdog); the events inside get forwarded to the state machine.
pub fn decode(frame: &CanFrame) -> Option<heapless::Vec<Event, 2>> {
    let Id::Standard(id) = frame.id() else {
        return None;
    };
    let id = id.as_raw();
    let data = frame.data();
    let mut out: heapless::Vec<Event, 2> = heapless::Vec::new();

    match id {
        x if x == config::CAN_ID_ARM => {
            if data.len() >= 3 && data[0..2] == config::ARM_MAGIC {
                let mode = match data[2] {
                    0 => Mode::Manual,
                    1 => Mode::AutoBreakBeam,
                    _ => return None,
                };
                let _ = out.push(Event::CmdArm(mode));
            } else {
                log::warn!("ARM frame without magic bytes ignored");
                return None;
            }
        }
        x if x == config::CAN_ID_DISARM => {
            let _ = out.push(Event::CmdDisarm);
        }
        x if x == config::CAN_ID_KICK => {
            if data.len() >= 3 {
                let kind = match data[0] {
                    0 => FireKind::Kick,
                    1 => FireKind::Chip,
                    _ => return None,
                };
                let width_us = u16::from_le_bytes([data[1], data[2]]) as u32;
                let _ = out.push(Event::CmdFire { kind, width_us });
            } else {
                return None;
            }
        }
        x if x == config::CAN_ID_CONFIG => {
            if data.len() >= 5 {
                match data[0] {
                    0 => {
                        let width_us = u16::from_le_bytes([data[1], data[2]]) as u32;
                        let kind = match data[3] {
                            0 => FireKind::Kick,
                            1 => FireKind::Chip,
                            _ => return None,
                        };
                        let _ = out.push(Event::CmdSetAutofire { kind, width_us });
                    }
                    1 => {
                        let ms = u32::from_le_bytes([data[1], data[2], data[3], data[4]]);
                        let _ = out.push(Event::CmdSetCooldownMs(ms));
                    }
                    _ => return None,
                }
            } else {
                return None;
            }
        }
        x if x == config::CAN_ID_HEARTBEAT => {
            // CanActivity alone; appended below for every recognized frame.
        }
        _ => return None,
    }

    let _ = out.push(Event::CanActivity);
    Some(out)
}

/// Encode the 10 Hz STATUS broadcast.
pub fn encode_status(st: &Status, seq: u8) -> Option<CanFrame> {
    let hv_dv = (st.hv_mv / 100).min(u16::MAX as u32) as u16; // decivolts
    let batt_cv = (st.batt_mv / 10).min(u16::MAX as u32) as u16; // centivolts
    let data = [
        st.state_code,
        st.fault_code,
        st.flags,
        hv_dv.to_le_bytes()[0],
        hv_dv.to_le_bytes()[1],
        batt_cv.to_le_bytes()[0],
        batt_cv.to_le_bytes()[1],
        seq,
    ];
    let id = StandardId::new(config::CAN_ID_STATUS)?;
    CanFrame::new(id, &data)
}
