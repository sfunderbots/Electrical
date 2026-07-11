# Chicker Firmware (Rust / Embassy)

Rust firmware for the Chicker V1.1 kicker board (bare RP2040). Replaces the
MicroPython firmware with a strict safety state machine, PIO-timed gate
pulses (8 ns granularity, zero trigger jitter) and a new CAN protocol.

## Safety model

The state machine in `src/state.rs` is the **single owner** of every pin
that can hurt the board (CHARGE, ~DISCHARGE, KICK, CHIP). Every other task
can only send events into a queue. Layers, outermost first:

1. **Hardware fail-safe** — with the MCU dead/resetting, all pads pull down:
   charging stops, the autodischarge network dumps the bank into the 35 W
   resistors. Disarmed in firmware is the exact same pin state.
2. **Hardware watchdog (1 s)** — fed only while HV telemetry is fresh; a hung
   loop or dead ADC resets the chip into state 1.
3. **Panic handler** — reclaims the four power-stage pins, forces them low,
   then lets the watchdog reboot.
4. **State machine guards** — fire only when Armed with fresh telemetry and a
   charged bank; charge paused 5 ms before every pulse; kick/chip mutually
   exclusive (state machine *and* pulse-driver interlock); width clamped to
   50–5000 µs; cooldown lockout after every fire; overvoltage (>225 V),
   charge-timeout and stale-ADC latch a Fault that only DISARM clears;
   CAN silence >1 s while armed auto-disarms (see `benchmode`).

States: `Disarmed → Armed{Manual|AutoBreakBeam} → Firing → Cooldown → Armed`,
plus latched `Faulted`. Boot enters Disarmed (bank dumping).

## Build & flash (USB, no probe needed)

```sh
./flash.sh    # build + reflash entirely over USB, no buttons
```

Flashing uses [picotool](https://github.com/raspberrypi/picotool), which
speaks the PICOBOOT USB protocol straight to the ROM bootloader — unlike
UF2 drag-and-drop (or `elf2uf2-rs -d`) it does not depend on the RPI-RP2
drive getting auto-mounted, which desktop Linux often doesn't do.

One-time setup (installs the Rust target, builds picotool from source, and
installs the udev rule from `udev/` — the only step that needs sudo):

```sh
./setup.sh
```

Once this firmware is running, `flash.sh` sends the console command
`bootloader` over the serial port — the chip reboots into the ROM
bootloader (CAN LED becomes the activity light) and picotool flashes and
restarts it. Only a blank/old board needs the buttons: hold BOOT, tap
RESET, release BOOT, then run `./flash.sh` (or `cargo run --release`).
The bootloader reboot is safe at any time — a chip reset returns every
pad to pull-down, which is the same charge-off/bank-dumping state as
Disarmed.

## Bench console (USB CDC)

The USB port enumerates as a serial device carrying the logs **and** a
console. Commands go through the exact same state machine as CAN:

```
arm manual | arm auto      disarm
kick <us>  | chip <us>     autofire <us> [kick|chip]
cooldown <ms>              benchmode on|off        status
canstat                    (MCP2515 CNF readback + error counters)
bootloader                 (reboot into the UF2 bootloader for reflash)
```

`benchmode on` disables the CAN-silence auto-disarm for probe-less bench
work — it logs loudly and should never be on in a robot.

## CAN protocol v0 (250 kbps, 11-bit IDs)

New canonical interface — the robot side should adopt this (the old
firmware's message system is deliberately not carried over). 250 kbps
matches the robot's existing shared motor/power bus; the 8 MHz MCP2515
allows up to 500 kbps if the whole bus is ever upgraded. IDs in
`src/config.rs`.

| ID    | Dir | Payload |
|-------|-----|---------|
| 0x310 | →board | ARM: `[0xA5, 0x5A, mode]`, mode 0=Manual 1=AutoBreakBeam |
| 0x311 | →board | DISARM (any payload) |
| 0x312 | →board | KICK: `[kind, width_lo, width_hi]`, kind 0=kick 1=chip, µs |
| 0x313 | →board | CONFIG: `[0, w_lo, w_hi, kind, 0]` autofire, `[1, ms u32-le]` cooldown |
| 0x314 | →board | HEARTBEAT — any valid frame feeds the comms watchdog |
| 0x320 | board→ | STATUS @10 Hz: `[state, fault, flags, hv_dV u16, batt_cV u16, seq]` |

flags: bit0 charging, bit1 DONE(raw), bit2 beam, bit3 shell_off,
bit4 hv_ready, bit5 benchmode. state: 0 Disarmed, 1 Armed/Manual,
2 Armed/Auto, 3 Firing, 4 Cooldown, 5 Faulted. fault: 1 overvolt,
2 charge-timeout, 3 pulse-busy, 4 adc-stale.

## Bench bring-up (safest first — HV disconnected/discharged for 1–4)

1. **No HV**: flash, open the serial port, check logs; meter: GPIO2/3/5 low,
   GPIO8 low at boot.
2. **No HV**: scope the fire test points (TP24/TP25 driver inputs,
   TP14/TP22 gates). `benchmode on`, `arm manual`, `kick 500` / `kick 5000`;
   verify widths, clamping (`kick 10` → 50 µs), cooldown lockout, and that
   kick+chip can't overlap.
3. ADC vs multimeter; calibrate `HV_MV_NUM` / `BATT_MV_NUM` in `config.rs`.
4. CAN against a 500 kbps dongle: STATUS at 10 Hz, ARM/KICK frames, stop
   traffic while armed → auto-disarm.
5. **First charge**: current-limited supply, temporarily lower
   `CHARGE_DONE_MV`/`RECHARGE_ON_MV` (e.g. 50 V/40 V); watch the ramp stop,
   note the DONE flag polarity in STATUS, disarm → bank bleeds.
6. Full voltage: 500 µs test fire first (solenoid secured!), scope gate +
   cap sag, then break-beam auto-fire; measure recharge time → tune cooldown.

## Provenance of hardware facts (the old firmware is NOT trusted)

The old MicroPython code was not used as a design reference — it has known
bugs (it even documented one of the LT3750 fault modes backwards). Every
constant in `src/config.rs` carries a source label:

- `[SCH]` from the KiCad schematics / netlist (primary source)
- `[DS]` from a component datasheet
- `[OLD-FW]` observed by the old firmware — **untrusted hint only**
- `[BENCH]` must be measured before being trusted

Wherever an `[OLD-FW]` fact influences behavior, the code fails safe (or at
worst fails loud) if the fact is wrong:

- **DONE (GPIO4)** high-while-charging / low-when-finished is backed by the
  **LT3750 datasheet** (open-collector NPN pulls low at target *and* on
  UVLO/thermal faults — the old firmware claimed faults drive it high,
  which is wrong). If the interpretation still fails on this board: cycles
  "completing" at low voltage log a loud warning, `MIN_CYCLE_INTERVAL`
  prevents rapid flyback toggling, the 215 V ADC backstop and 15 s timeout
  fault are DONE-independent.
- **Break-beam active-LOW** is `[OLD-FW]` only. If wrong, arming auto mode
  fires once at nothing (still guarded by charge/cooldown) — so check the
  STATUS beam flag with/without a ball *before* the first auto-mode arm.
- **Charge timing** (~2 s full charge, 206–211 V peaks) is `[OLD-FW]` and
  only used for comment context and generous timeout margins.
- **ADC factors** are `[SCH]`-derived; the old code merely happened to
  match (HV ×77.15, battery ×5.7). Their battery calibration (slope
  1.0342 / offset −0.53 V) is a hint to re-measure, not folded in. The
  5 V-rail divider is disputed between `[SCH]` and `[OLD-FW]` — telemetry
  only until measured.
- **Battery gate** (no charging below 14 V) is `[OLD-FW]`-inspired but
  conservative in either direction.
- **The CHIP channel (GPIO2) was never fired by any firmware.** Treat the
  first chip kick as untested-hardware bring-up: scope it at low bank
  voltage first.
- **Legacy CAN bus ran 250 kbps** (ID 0x2AA) per `[OLD-FW]`, confirmed by
  the robot's `setup_bot.sh`. This firmware keeps 250 kbps (new message
  format, same bitrate) so it can join the live bus without disturbing the
  motor controllers. With the old robot firmware still in place the board
  simply never receives a valid ARM frame and stays safely Disarmed
  (no charging, bank dumped).
- **MCP2515 post-RESET dead window**: after the SPI RESET instruction the
  chip ignores SPI for ~128 OSC1 cycles (16 µs at 8 MHz). Writes issued in
  that window are silently lost — this firmware was fast enough to hit it,
  leaving the chip in Normal mode with all-zero CNF and jamming the whole
  robot bus with wrong-bitrate retries (found live on the robot; the old
  Python firmware was far too slow to ever trigger it). Init now waits 2 ms
  after reset and readback-verifies CNF **before** entering Normal mode; on
  mismatch the board stays off-bus in Configuration mode and logs loudly.
  `canstat` on the console dumps the live registers.
- The old firmware had no watchdog and polled CAN; both are fixed here.

## Still to verify on the bench

- `HV_MV_NUM` / `BATT_MV_NUM` calibration against a multimeter
- SHELL_OFF polarity and whether it should gate arming (the old firmware
  never used it; currently telemetry-only)
- Real recharge time between kicks → tune `cooldown`

## Future work

- Solenoid active damping (PWM hold after the kick) — see
  `solenoid-active-damping/` for the physics; needs a PIO PWM program and
  duty ramp (bank sags ~30 V/s while holding).
