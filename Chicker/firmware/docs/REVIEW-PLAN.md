# Review plan: where we are and what's next

Last updated: 2026-10-25. Process: go through Henry's charts one element at a time; Henry approves each change and
updates his local chart; the review files are the record; code is only written from them.

Review files: `CHARGE-STATE-MACHINE-REVIEW.md`, `MODE-STATE-MACHINE-REVIEW.md`, `COUPLED-STATE-MACHINE-REVIEW.md`.

## Done so far

- **Charge chart:** all elements reviewed (1–12), plus the new `chg_hold_mode_ctrl` / Not Charging (5a, N-5).
  Henry's chart is updated.
- **Mode chart, elements 1–13:** battery, BKBM start, heartbeat (5 s, de-arm + stop PWM on loss), Locked (only tells
  the Pi), Set Idle Mode, message handling (MSG-1/2), MODE_IDLE / MODE_CHG, the whole AUTOKICK path (HV >= 100 V,
  Hold Charge, GUARD before the kick, spacing gates the arm, one kick per command), and the break-beam mini machine.
- **Flags:** `chg_stop_mode_ctrl` = stop + dump; `chg_hold_mode_ctrl` = veto, stop only (coupled review table).

## Tomorrow, in order

**Progress 2026-10-26:** items 1–5 done (mode elements 14–16, message diagram, notes DMP-2 / DMP-3, MSG-3:
kick during a damp = arm inside the damp loop, like renew; PWM deadline rule dropped for a kicker-driver limit).
HV adjustment kept as RoboCup (validate duty / power on the bench, DMP-3). Item 6 (coupled pass) done the same day: rows brought up to date, no new conflicts. Item 7 decided: X-1 at
220 V → Disabled, X-2 / X-3 / X-4 dropped, X-5 kept. **The review is complete; next is "After the review".**

1. **Damp step 1:** Command Received → MODE_DAMP → Enable Charging → `HV >= 180` → Hold Charge. Open questions:
   * keep `HV >= 180` (`DAMP_START_V`) or lower it?
   * add `time since last kick >= KICK_MIN_INTERVAL_MS` to this arrow (the Initial Kick is a kick)?
2. **Damp step 2:** Hold Charge → `done == 0` → Initial Kick → wait (y) time → Start Damping PWM. Values for (y) and
   the initial kick PW.
3. **Damp loop:** Wait ⇄ HV Adjustment ⇄ Set Duty; timeout → de-arm / stop PWM → Enable Charging. Write in:
   renew (timeout restarts from the latest DAMP), `DAMP_MAX_TOTAL_MS`, HV floor, what (x) is.
4. **Kick during a damp** (MSG-3, KCK-1, DMP-1, element 13 point 4):
   * through the AUTOKICK states (Henry's preference) or a branch inside the damp loop?
   * PWM stops only at the break-beam trip, not when the kick is requested;
   * break-beam condition must include the damp;
   * charging stays on hold during the damp (no Enable Charging mid-damp);
   * MSG-3 (a): damp times out with a kick queued → de-arm? (b): beam already tripped → kick straight away?
5. **Leftovers on the mode chart:**
   * ANY STATE "incoming message" box → "de-arm kick" only (no stop PWM). On Henry's chart?
   * Set Idle Mode also stops the PWM (asked, not yet answered).
6. **Coupled review pass:** check every shared signal against what the mode review now says.
7. **Open topic:** stuck HV readings (charge review X-1 to X-5, plus the mode chart's HV uses: kick 100 V, damp 180 V,
   damp floor, duty).

## Code rebuild (2026-10-26): done

* Henry's updated charts are in `flowcharts/*-v2.drawio` (+ PNGs).
* Board files now sit directly in this folder (`main.py`, `config.py`, `app.py`, `charge_sm.py`, `mode_sm.py`,
  `sm_base.py`, `power_stage.py`, `kicker.py`, `protocol.py`, `board.py`, `sensors.py`, `console.py`) plus `canbus/`;
  everything not on the board is in a subfolder.
* `charge_sm.py` and `mode_sm.py` rewritten from the three reviews: each chart arrow is one table row, state names are
  the chart's, comments point at the review elements.
* Simulator: 90 tests pass, 0 fail (latest run); every arrow of both machines is exercised. Tests are organised by review element.
  `sim/drawio.py` (old chart generator) removed; `test_l0_flowcharts.py` now checks every code state has a box on
  Henry's charts.
* Bugs the simulator found during the rebuild (fixed): the 500 ms GUARD was timed from the request instead of the real
  CHARGE edge (cycles could be 499 ms apart); the power stage's 50 ms dead time could come out 49.99 ms.

## Raised with Henry (found while coding)

1. **Kick GUARD also waits for CHARGE low >= 5 ms** (`PRE_FIRE_SETTLE_MS`). Not on the chart; its origin can't be
   traced (tagged as Steven's bench value, not found in his branch). **Removed** (Henry): the GUARD is now exactly
   the chart's (done == 0, CHARGE low, PW range).
2. Kick during a damp has no spacing check: **not a problem** (Henry: while damping the plunger is held, no kicking).
3. Simulated solenoid drains the bank fast: **the solenoid model is not trustworthy** (air gap, inductance changing with
   position). The simulator's solenoid is only there to exercise the logic; damp / kick energy comes from the bench.
4. Pi-side protocol changes: **on the team list** (mode review T-4).
5. Kicker self-test failure (report, refuse pulses): **OK**.
6. Battery window: **keep 14.7 V on / 14.3 V off / 17.0 V max**, now written the same way in every document.
7. `DAMP_MAX_TOTAL_MS = 6000`: **fine as a backstop**; heating is not a concern (the hold drains the caps and stops).
8. **Found by the state × event matrix test:** a damp running when the charge machine disabled itself (e.g.
   overvoltage) kept its PWM going in Locked. **Changed** (Henry): Locked stops everything on the kicker side.
9. **The break-beam during a damp is probably unreliable** (the held-out plunger may block it). **Logic unchanged**
   (Henry): the kick during a damp still waits for the beam. Note DMP-5: test it; idea of stopping the PWM when the AI
   says kick, then reading the beam; ball detection from the HV / current signature (DMP-6).


10. **Arm timeout put back** (Henry: "definitely include the 2s timeout, it's dangerous otherwise"): an AUTOKICK with
    no ball expires after 2 s, as at RoboCup (mode element 21). Not during a damp (element 22, rejected). **Henry to
    add it to the mode chart.** Whether 2 s suits the AI: team item T-5.
11. **AUTOKICK renew** (Henry's chart): an AUTOKICK while a kick is armed restarts the 2 s timer and takes the new
    PW, without de-arming or touching charging (mode element 23). Chart: add a "Renew kick timer (+ new PW)" box on
    the renew arrow, and start the 2 s timeout arrow from "wait for kick". Message handling (#7) rewritten to match.

## Tabled for later (Henry, 2026-10-26)

* **Damping PWM precision.** The PIO itself is exact (full 125 MHz clock, 8 ns steps: ~4,200 steps per period at
  30 kHz, the same as the hardware PWM; only a 40 ns longer low gap once every 25 ms when the next segment loads).
  Where precision is lost:
  1. the RoboCup HV-adjustment formula rounds the duty down to a whole percent (`int(...)`): 333 ns steps at 30 kHz,
     ~40x coarser than the PIO. Dropping the `int()` gives full resolution;
  2. a new duty lands on the next 25 ms segment, i.e. 25-50 ms after it is computed (the RoboCup PWM peripheral took
     one period). Shorter segments (e.g. 5 ms) would cut that, at some main-loop cost;
  3. the two extra duty caps in `kicker.py` (hold-power cap: ~14 % max at full voltage; 200 us per-period on-time cap:
     20 % max at 1 kHz). Recommendation: remove both.

## Next

Plan for the next sessions (board on USB, then the bench): `docs/NEXT-STEPS-2026-10-26.md`.


* Henry: check the code against the charts (start with `charge_sm.py` / `mode_sm.py`; the top of each file explains
  how to read them).
* Rewrite `bench/bring_up.py` for the reviewed firmware (its 3 rehearsal tests are skipped): needs the decision on
  the bench charge ceiling / `safe_chg` (charge element 9, "Not on the chart").
* Bench tests: break-beam polarity and debounce (`BEAM_DEBOUNCE_MS`); the break-beam during a damp (DMP-5); LT3750
  DONE pulse on a full bank (charge N-2); dump curve (~7 s); damp HV-adjustment duty and how fast a hold drains the
  bank (DMP-3); full-charge time (tighten `CHARGE_TIMEOUT_MS` if always < 3 s); kick spacing tuning; HV ball-hit
  captures (`to-test/hv-current-sensing/README.md`).
* Team items: T-1 (CAN IDs), T-2 (reject messages), T-3 (command sequence numbers / ack), T-4 (Pi-side protocol
  changes), R-1 (Pi heartbeat 5 Hz).
