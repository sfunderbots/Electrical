# Chicker firmware v2: what changed and why

For: Akhil, Steven, Matthew, and anyone picking up the chicker firmware.
Branch: `chicker-fw-v2-sim`. Status: complete in the simulator (all tests pass), **not yet run on a real board**.

## In one paragraph

The firmware was rebuilt from the ground up from Henry's two state-machine charts (charge, and mode control).
Each chart was reviewed element by element and every change was agreed before any code was written. The result
is two small state machines that do exactly what the charts say, a kicker driver that can no longer glitch, a power
stage that can never charge into the dump resistors, and a simulator that runs the real firmware (real CAN frames
in, real pin timings out) with 90 tests. The board still runs MicroPython.

## Where the specification lives

| Document | What it is |
|---|---|
| `flowcharts/chicker-charge-state-machine-v2.drawio` / `.png` | Henry's updated charge chart |
| `flowcharts/chicker-mode-control-state-machine-v2.drawio` / `.png` | Henry's updated mode control chart |
| `docs/CHARGE-STATE-MACHINE-REVIEW.md` | every charge chart element, what was agreed, and why |
| `docs/MODE-STATE-MACHINE-REVIEW.md` | every mode chart element, message handling, team items (T-1 to T-4) |
| `docs/COUPLED-STATE-MACHINE-REVIEW.md` | the signals between the two machines: who writes each one, who reads it |
| `docs/REVIEW-PLAN.md` | status, questions raised during coding, next steps, bench tests |

The code comments point to these ("element 17", "note KCK-2"), so any line of the state machines can be traced back
to the chart and to the decision behind it.

## What we started from

| Branch | Firmware |
|---|---|
| `main` | the older firmware: `main.py` (charging, kicking with the `PWM` peripheral, CAN on `0x2AA`) plus `pulses.py`, `dma_util.py`, `rp2_util.py`, `battery.py`, `high_voltage.py` |
| `robocup2026damping_henry` | the firmware that ran at RoboCup 2026: one `main.py` with charging, auto-kick and active damping (initial kick + PWM hold with HV adjustment). Merged into this branch; kept unchanged as `legacy/main_robocup2026.py` |

Known problems in those versions that v2 fixes:

* **The PWM / PIO glitch on high-power kicks.** The KICK pin was switched between the `PWM` peripheral, a PIO program
  and plain GPIO. Stopping a PWM slice or a PIO state machine freezes the pin at its current level (possibly high),
  and PWM duty writes only take effect at the next counter wrap.
* **The smoking dump resistors.** `~DISCHARGE` was a new pin and the code never drove it while charging, so the bank
  charged straight into the dump resistors. (Fixed on the RoboCup branch by `18b0320`; v2 makes it impossible, below.)
* **Charging decisions based on the HV reading** (e.g. `CHARGE_ALREADY_FULL_HV`), which went wrong on short DONE pulses.
* **Global flags shared by everything** (`chg_stop_mode_ctrl`, `not_dischg`, `charge_toggle_wait`, ...), written from
  several places, so one part of the code could undo another's decision.

## Folder layout

Everything that goes on the board sits directly in `Chicker/firmware/`, everything else is in a subfolder (see
`README.MD`). Copy every `.py` file in the folder plus `canbus/` to the board.

| File | Job |
|---|---|
| `main.py` | drives the power pins safe before anything else, runs the loop, lands safe on any exception |
| `config.py` | every constant, each tagged with where it came from (`[Henry]`, `[RC26]` RoboCup 2026, `[BENCH]` to measure, ...) |
| `charge_sm.py` | the charge state machine (Henry's charge chart) |
| `mode_sm.py` | the mode control state machine (Henry's mode chart, including the break-beam mini machine) |
| `sm_base.py` | the small engine both machines use: each chart arrow is one row of a table |
| `app.py` | one loop pass: sensors, CAN, break-beam, mode machine, kicker, charge machine, power pins, STATUS |
| `power_stage.py` | the only code that writes CHARGE and `~DISCHARGE` |
| `kicker.py` | the PIO program that owns the KICK pin from boot to shutdown |
| `protocol.py` | the CAN protocol (shared with the simulator and the bench tools) |
| `board.py`, `sensors.py`, `console.py` | pins / ADC / MCP2515 setup, ADC conversions, USB serial console |

## How the two state machines work together

Each machine only writes its own outputs (the "authorship rule"). The mode machine controls charging through exactly
two flags, and the charge machine tells the mode machine about a fault through exactly one:

| Signal | Written by | Read by | Meaning |
|---|---|---|---|
| `chg_stop_mode_ctrl` | mode | charge | 1 = stop charging **and dump** the caps (charge state Inhibited) |
| `chg_hold_mode_ctrl` | mode | charge | 1 = stop charging, **keep** the charge (charge state Not Charging). A veto: it can never start charging |
| `chg_disable_chip_level` | charge | mode, Pi | 1 = charging disabled for good (charge state Disabled) → mode state Locked |

No combination of the two mode flags can ask for charging while dumping. Only the charge machine decides the pins,
and the power stage below it enforces the dead time on its own.

## Behaviour changes

### Charging (`charge_sm.py`, charge review)

* Every decision is made on **timers and the DONE pin**, never on the HV reading (review note N-0).
* **DONE rising edge is latched** by a pin interrupt: 50 ms after CHARGE rises, "the charger started" means "DONE rose
  at any point", so a short top-off pulse that is already over still counts. No edge → Disabled (UVLO / thermal).
* At **4500 ms** after the toggle: DONE low → Assuming Charged; DONE still high → Disabled (too slow).
* Top-off every **5 s** (Charge Toggle: CHARGE low 50 ms, then a new cycle), as at RoboCup.
* New cycles never start closer than **500 ms** apart, measured from the real CHARGE edge.
* **2 s** wait after the battery appears before the first charge.
* **Overvoltage:** HV ≥ **220 V** on 3 readings in a row → Disabled. This is the only HV-based rule.
* **Disabled is one-way** (until a power cycle): CHARGE low, caps dumped, the reason sent to the Pi.
* `safe_chg` bench test (charge to ~50 V once, then Disabled) kept behind `SAFE_CHARGE = 0`.
* Removed: `CHARGE_ALREADY_FULL_HV`, the HV-based "no progress" / "not regulating" / "early DONE" faults, the
  "pack dropped" retry (the board is powered by the battery, so a dropped pack turns it off anyway).

### Mode control (`mode_sm.py`, mode review)

* **Start-up:** Wait for Bat → Start BKBM interrupt → waits for the **first heartbeat** from the Pi → idle (caps dumped).
* **Heartbeat:** any valid frame from the Pi counts; **5 s** without one → stop charging and dump, de-arm, stop the PWM,
  back to Start BKBM interrupt until the Pi is back. STATUS shows an early "heartbeat late" warning after 1 s.
* **Battery lost** (from any state except Locked) → abort everything, including a pulse in flight.
* **Locked** (charge machine disabled itself) → stop everything on the kicker side and tell the Pi (fault in every
  STATUS frame). Nothing leaves Locked.
* **AUTOKICK:** enable charging → arm once the last kick was ≥ `KICK_MIN_INTERVAL_MS` (500 ms) ago → when the ball
  breaks the beam and HV ≥ **100 V**: hold the charge (no dump), wait for DONE low → kick → recharge. **One kick per
  AUTOKICK command.** A ball that is already in the beam when the kick is armed is kicked straight away. **No ball
  within 2 s → the kick is de-armed** (as at RoboCup), and the Pi gets an ARM_EXPIRED event (not during a damp).
  **Repeating AUTOKICK while a kick is armed renews it** (2 s timer restarts, newest PW) without de-arming or touching
  charging, so the Pi keeps a kick armed by repeating AUTOKICK and it dies ~2 s after the Pi stops asking.
* **DAMP:** enable charging → once the last kick was ≥ 500 ms ago and HV ≥ **180 V**: hold the charge → wait for DONE
  low → Initial Kick (800 µs) → **4 ms** → PWM hold with the RoboCup HV adjustment every **25 ms** → ends at the
  timeout (from the command, default 3 s).
  * **Renew:** a DAMP while the PWM is running restarts the timeout (no new Initial Kick), up to a hard **6 s** maximum
    from the Initial Kick.
  * **Kick during a damp:** AUTOKICK while the PWM runs arms the kick inside the damp. The PWM keeps holding until the
    ball breaks the beam, then stops in the same PIO step as the kick pulse. If the bank is below 100 V by then:
    stop the PWM, wait 50 ms, recharge to 100 V, then kick. A kick that never gets a ball ends with the damp.
    **Caution:** the held-out plunger probably blocks the break-beam during a damp, so this may not work on the
    board; to test (mode review notes DMP-5, DMP-6).
* **Messages:** only good commands (known ID, right length, every value in range) are acted on, one per loop pass, in
  the order they arrived; nothing is dropped. A bad frame changes nothing. Every good command de-arms the kick,
  except the two renews (DAMP while damping, AUTOKICK while armed). Commands before the board is ready, or in Locked, are rejected, not saved for later.

### Kicker (`kicker.py`)

* **One PIO state machine owns the KICK pin from boot to shutdown** and drives it on every instruction, so it is low
  everywhere except the on-time, including while it waits. Kicks and the damping PWM are the same PIO program.
  This removes the PWM / PIO glitch: the pin never changes function.
* A damping hold is a stream of 25 ms segments: duty changes land on period boundaries, and if the main loop ever
  stalls the pin goes low by itself within 50 ms.
* Abort forces the pin low within one PIO cycle. Boot self-test checks the PIO program before anything can fire.
* Driver backstop: one damping hold never runs longer than `DAMP_MAX_TOTAL_MS` (6 s), however often it is renewed.

### Power stage (`power_stage.py`, review X-5)

* **The dump must be off for > 50 ms before CHARGE may go high**, and CHARGE must be low for > 50 ms before the dump
  turns on. If the charge machine asks too early, the pin simply waits. The 50 ms is measured from the real pin edges.
* Every pin write is read back; a mismatch forces both pins safe and stops the firmware.

### Break-beam

* The pin interrupt only records each change; the main loop decides (5 ms debounce, to tune: a flicker must not fire an armed kick). "ON" = ball present; which pin
  level that is lives in `config.py` (`BEAM_ACTIVE_LOW`), to be confirmed on the bench.

## CAN protocol (what the Pi side must know)

Block `0x2A0`–`0x2AF` (to confirm with the team that nothing else uses it, T-1). Little-endian.

| ID | Direction | Payload | Chart mode |
|---|---|---|---|
| `0x2A0` MODE | Pi → board | `[mode]` 0 = idle, 1 = charge | MODE_IDLE, MODE_CHG |
| `0x2A1` KICK | Pi → board | `[0, width_us u16, unused u16]` (100–5000 µs) | MODE_AUTOKICK |
| `0x2A2` DAMP | Pi → board | `[freq_hz u16, duty u16 (0.01 %), timeout_ms u16, initial_kick_us u16]` (0 = default) | MODE_DAMP |
| `0x2A4` HEARTBEAT | Pi → board | `[seq]` at **5 Hz** | — |
| `0x2A8` STATUS | board → Pi | `[mode_state, charge_state, flags, hv u16 (0.1 V), batt (0.1 V), fault, seq]` at 20 Hz | — |
| `0x2A9` EVENT | board → Pi | `[code, a u16, b u16, c u16, seq]`: kick fired, damp done, fault (+ reason, HV), command rejected, comms lost | — |
| `0x2AA` LEGACY | Pi → board | the old `[mode, value, duty]` frame, still accepted | mapped onto the above |

Changes for the Pi (mode review T-4): send a heartbeat at 5 Hz (R-1); the KICK frame is AUTOKICK only (kick when the ball breaks the beam,
as at RoboCup; trigger byte must be 0, there is no immediate kick); an armed kick still
expires after 2 s without a ball (fixed, `arm_ms` is ignored); the board needs a heartbeat before it acts on any command; commands before it is ready are rejected; one kick per
AUTOKICK. Still to decide with the team: reject messages on the bus (T-2), command sequence numbers echoed in STATUS
(T-3).

## Kept from before the review (not on the charts)

These parts of the code were written before the chart review restarted and are not part of either chart. They are
listed so they can be confirmed or removed:

| Item | Where | Status |
|---|---|---|
| Power-stage dead time + pin readback (X-5) | `power_stage.py` | **agreed** |
| Kicker boot self-test (failure: Pi told, every pulse refused) | `kicker.py`, `app.py` | **agreed** |
| Battery window with hysteresis (on above 14.7 V, off below 14.3 V, off above 17.0 V) | `sensors.py` | **agreed** |
| Hardware watchdog, 1 s, started 3 s after boot (window to stop `main.py` from Thonny), fed only while the loop is healthy | `app.py` | from the earlier design (Henry: run it all the time) |
| USB serial console (`charge`, `kick 1000`, `damp 30000 12`, `hb`, `status`, ...) through the same decoder as CAN | `console.py` | from the earlier design (no USB-CAN adapter on the bench) |
| Bench "dry fire" (fire the kicker with the bank empty) | `app.py`, `console.py` | from the earlier design |
| MCP2515 boot loopback self-test and hardware ID filter | `board.py` | from the earlier design |
| Legacy `0x2AA` frame | `protocol.py` | from the earlier design |
| Damping duty caps besides the 95 % maximum: predicted hold-power cap (`DAMP_MAX_HOLD_POWER_W`, placeholder) and per-period on-time cap (`DAMP_MAX_ON_US`) | `kicker.py` | **to confirm**: both rest on the solenoid model, which isn't trustworthy |

## Testing

```
python -m sim.run_tests --fast      # ~2 minutes, everything except the slow tests
python -m sim.run_tests             # ~13 minutes, everything (includes the every-event-in-every-state matrix)
```

The simulator replaces `machine`, `rp2`, `utime` and `canbus`, emulates the PIO program cycle by cycle, and models the
LT3750, the bank, the dump resistors and the battery. Commands go in as real CAN frames. A monitor checks safety rules
on every pin change (never charging while dumping, the 50 ms dead time both ways, never kicking while charging,
pulse length, no kick while idle / Locked, ...).

* **90 tests pass.** Every arrow of both charts is exercised (23/23 mode, 20/20 charge).
* Tests are grouped by level: protocol and sensors, kicker maths, PIO waveforms, power stage, charge (one test per
  charge review element), modes (one test per mode review element), **every event in every state** (12 mode states ×
  14 events and 11 charge states × 14 events: no exception, no safety violation, the board still answers), system
  (STATUS, console, clock wrap-around, crash → safe state, watchdog), random soak.
* Bugs the simulator found during the rebuild: the 500 ms GUARD was timed from the request instead of the real CHARGE
  edge; the power-stage dead time could come out at 49.99 ms; a damp kept running after the charge machine disabled
  itself (now Locked stops everything).
* **Limit:** the solenoid model is not trustworthy (its inductance really changes with plunger position and air gap).
  The simulator checks the logic; kick strength, damping force and how fast a hold drains the bank come from the bench.

## Not done yet

* `bench/bring_up.py` (the staged hardware bring-up) still uses the old charge ceiling and an immediate-kick option from an early protocol draft; its tests are
  skipped. It is rewritten once the bench charge ceiling / `safe_chg` question is decided (charge review element 9).
* Bench measurements: break-beam polarity, DONE pulse on a full bank (charge N-2), dump curve (~7 s for a full dump),
  damping duty and drain, full-charge time (tighten the 4500 ms check if a charge always takes < 3 s), kick spacing.
* Team items T-1 to T-4 and the Pi heartbeat (R-1).
* Ball detection during a damp (mode review DMP-5).

## Background facts kept from the earlier notes

* Dump resistors R44 / R48 are 2 kΩ each **in parallel** (1 kΩ); Henry measured a full dump at ~7 s.
* Full bank reads 206–212 V; the caps are rated 250 V.
* The board boots with every power pin at its reset-safe level (CHARGE low, dump on, KICK low) before anything
  else is imported, and any exception or Ctrl-C lands in the same safe state.
