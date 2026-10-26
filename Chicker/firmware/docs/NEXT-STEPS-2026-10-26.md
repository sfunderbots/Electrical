# NEXT STEPS (written 2026-10-26)

Plan for the next session(s), probably after Henry's 4 Nov midterm. Nothing here is implemented yet.

## Where things stand

* Branch `chicker-fw-v2-sim`, last pushed commit `52fbe01`.
* The firmware is rebuilt from Henry's reviewed charts (`docs/*-STATE-MACHINE-REVIEW.md`, `flowcharts/*-v2.*`).
* Simulator: 90 tests pass, 0 fail, 3 skipped (bench rehearsal); every arrow of both charts exercised.
  Full log: `sim/results/test-run-2026-10-26.txt`.
* Summary for the team: `docs/CHANGES-v2.md`. Status and open items: `docs/REVIEW-PLAN.md`.
* **Never run on the real board yet.** The simulator runs the firmware under normal desktop Python, not MicroPython
  on an RP2040, so everything below is about closing that gap safely.

## Session 1: Suite 1, the board on USB only (Claude runs it)

No battery connected, so the LT3750 has no supply and no high voltage can be made.

### Before starting (Henry)

- [ ] Board, USB cable, multimeter at hand; **battery disconnected**.
- [ ] **Check with the meter that the cap bank reads 0 V** before plugging in (it may hold charge from an earlier
      session, and without a battery the dump circuit may not be able to empty it).
- [ ] Tell Claude the COM port (Device Manager → Ports) and **close Thonny** (it holds the port).
- [ ] OK for Claude to `pip install mpremote` and to back up then replace the files on the board.

How Claude talks to the board: `mpremote` from the terminal (copy files, run code, capture everything the board
prints). Henry keeps using Thonny the rest of the time; the two just can't hold the port at the same time.

### Steps

1. **Back up the board.** Copy every file currently on it into a backup folder on the PC first.
2. **Identity.** MicroPython version, whether `rp2.DMA` exists (needed for the HV ball-hit captures), CPU clock
   (`machine.freq()`), free RAM after boot, reset cause.
3. **Deploy for testing.** Every `.py` file in `Chicker/firmware/` plus `canbus/`, with the watchdog off for this
   session (`WATCHDOG_MS = 0`): the watchdog arms 1 s after a 3 s boot window and would reset the board whenever the PC
   interrupts it. The watchdog itself gets its own deliberate test (step 8).
4. **Does it run on MicroPython?** Import every module, boot the firmware, check for errors that the simulator can't
   catch: syntax MicroPython doesn't support, `micropython.const`, integer / clock-wrap behaviour, memory.
5. **Self-tests on real hardware** (Henry: all pins can be set, all tests can run): the kicker PIO self-test, the
   MCP2515 CAN loopback, pin read-back of CHARGE / ~DISCHARGE / KICK at their boot levels.
6. **Real timing.** Main-loop pass time, worst case and jitter; must stay well under the 5 ms break-beam debounce and
   the 25 ms PWM segments. RAM use over a few minutes (no leak).
7. **The state machines on the chip, with fake inputs** (agreed with Henry: test the functionality on real hardware
   rather than in the simulator). Two levels:
   - **7a. Scripted inputs (cheap, first).** Call the charge and mode machines directly with hand-made input snapshots
     ("DONE rises at 12 ms", "HV reads 225 V", "battery gone", "a DAMP frame arrives") and check every state and
     failure path: battery loss, heartbeat loss, UVLO / too slow / overvoltage → Disabled → Locked, bad frames, command
     spam, AUTOKICK / DAMP renew, the 2 s arm timeout, the 6 s max-hold backstop. No physics needed; proves the logic
     runs the same on the RP2040 as in the simulator.
   - **7b. A small fake plant on the chip (Henry's idea).** The state machines only work if something answers back
     (a CHARGE rise needs a DONE edge and a rising HV), so a tiny model runs on the RP2040 in place of the real HV ADC
     reading and DONE pin, reacting to what the firmware actually drives: CHARGE high → bank voltage rises towards
     ~210 V with DONE high, DONE drops at the target; dump on → voltage decays (~7 s full dump); kick → voltage drops;
     plus scripted battery present / absent, break-beam, and LT3750 faults (no DONE edge, DONE stuck). The **whole
     firmware** then runs end to end on the chip: the real main loop, PIO kicker, CAN loopback and USB console, with
     realistic inputs. Roughly 150 lines of MicroPython (the simulator's physics cut down to a few equations), plugged in
     where `board.py` reads the HV ADC and the DONE pin. Keep it light so it doesn't slow the loop (check the loop time
     with and without it), and give it an option to leave the real CHARGE / dump pins alone (fake pins too) for extra
     safety; with no battery, toggling CHARGE into an unpowered LT3750 is harmless anyway.
8. **Safety paths on the board.** The no-battery path for real (Wait for Bat, commands rejected "not ready", CHARGE
   low, dump on, STATUS / telemetry still sent); an injected crash → `main.py` leaves every pin safe; the watchdog
   starts after 3 s and resets a hung loop. A few short dry kicker pulses only (to check the pin), so pins aren't
   driven into unpowered chips more than needed.

### To decide before / at the start of Session 1

* **How to get past Wait for Bat with no battery** (Henry's question 5). Henry's RoboCup code had a `SIM_MODE` that
  faked the DONE signal. Options:
  - **(a) No bypass needed for most of Suite 1.** Step 7 runs the state machines on the chip with fake inputs, so the
    whole logic (every state, every failure) is covered without pretending a battery exists. **Recommended first.**
  - **(b) A bench-only fake battery for the whole firmware:** a config flag, off by default, plus a USB console command
    that injects a battery reading. It must refuse to act whenever a real battery is measured, so it can never be left
    on by mistake on a robot. With it, the firmware leaves Wait for Bat and the charge machine toggles CHARGE into an
    unpowered LT3750 (harmless with no battery, but it does drive the pin).
  - **(c) Henry's way:** temporarily skip Wait for Bat in the code for USB testing. Simplest, but it is a code change
    that can be forgotten; (b) does the same job with a guard.
* Which supplies run from USB: Henry says everything can be tested, so the CAN chip and gate drivers are taken to be
  powered with USB alone. Confirm when connected (the CAN loopback result will show it).

## Session 2: Suite 2, real hardware with the battery (Henry runs it)

Henry has one board, so every step that adds energy is run by Henry, one stage at a time, with a confirmation prompt
before each. Claude prepares the script, rehearses it on the simulator, and reads the output Henry sends back.

* **Needs first:** the decision on the bench charge ceiling / `safe_chg` (charge review element 9, "Not on the chart").
  Then rewrite `bench/bring_up.py` for the reviewed firmware (its 3 rehearsal tests are skipped until then).
* Stages (from the earlier plan, to update): identify → sensors → dry fire (bank empty) → first charge at a low
  voltage + dump time → voltage ladder with test kicks → damp → full voltage. Automatic abort on anything unexpected;
  the bank is always dumped at the end.
* Bench measurements on the list (`REVIEW-PLAN.md`): break-beam polarity and debounce; the break-beam during a damp
  (DMP-5); DONE pulse on a full bank (charge N-2); dump curve (~7 s); damp HV-adjustment duty and how fast a hold
  drains the bank (DMP-3); full-charge time (tighten `CHARGE_TIMEOUT_MS` if always < 3 s); kick spacing tuning.
* **HV ball-hit captures** (`to-test/hv-current-sensing/README.md`): noise floor → charged → hold without ball → hold
  with ball → bounces. Needs `rp2.DMA` (checked in Session 1) and the midplate with the two feedback resistors on its
  5 V buck.

## Decisions waiting for Henry

| Item | Where |
|---|---|
| Bench charge ceiling / `safe_chg` (blocks the bench script) | charge review element 9 |
| Remove the two extra damping duty caps (hold-power cap, 200 us on-time cap) | REVIEW-PLAN "Tabled for later" |
| Damp PWM precision: drop the whole-% rounding? shorter PWM segments (faster duty updates)? | REVIEW-PLAN "Tabled for later" |
| Wait for Bat bypass for USB testing: (a), (b) or (c) above | this document |
| Small chart fixes: "5s?" label, leftover `chg_disable_chip_level == 0` label, legend (`chg_stop`, add `chg_hold`); charge chart optional labels | REVIEW-PLAN / mode review |

## For the team (whenever someone talks to them)

* T-1 CAN IDs don't clash; T-2 reject messages on the bus or not; T-3 command sequence numbers / ack; T-4 Pi-side
  protocol changes; T-5 does the 2 s arm timeout fit how the AI sends AUTOKICK (it should repeat AUTOKICK while it
  wants a kick); R-1 Pi heartbeat at 5 Hz. Details: mode review, "Changes needed outside the board".
* Point Akhil, Steven and Matthew at `docs/CHANGES-v2.md`.
