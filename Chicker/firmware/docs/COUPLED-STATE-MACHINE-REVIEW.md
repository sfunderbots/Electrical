# Coupled state machine review: what passes between the charge and mode machines

Every signal that crosses between the two machines (or comes from shared hardware), with exactly one **author**
(the only code allowed to write it) and its **receivers**. The question for each one: can it ever make the other
machine do something wrong?

**Authorship rule (key, everything breaks down otherwise):** each machine only writes its own outputs. The mode
machine does **not** control charging; its only levers on the charger are `chg_stop_mode_ctrl` (stop and dump) and `chg_hold_mode_ctrl` (stop, keep the charge). The charge machine
does not control modes; its only lever on the mode machine is `chg_disable_chip_level`.

| Signal | Author (writes it) | Receiver(s) (only read it) | Meaning | Agreed so far | Open questions / risks |
|---|---|---|---|---|---|
| `chg_stop_mode_ctrl` | Mode machine | Charge machine | 1 = stop charging **and dump the caps** (charge machine → Inhibited), 0 = charging allowed | Set **only** by the mode machine. **1 (dump):** Wait for Bat, Start BKBM interrupt, heartbeat loss (Disable Charging), Set Idle Mode (mode elements 1, 3, 4, 6). **0:** every Enable Charging (mode elements 8, 9, 14, and after a kick / damp). The separate "kicker busy" rule in app.py is removed. | Settled. Kicks and damps use `chg_hold_mode_ctrl`, not this flag. |
| `chg_hold_mode_ctrl` (new) | Mode machine | Charge machine | 1 = stop charging, **keep the caps charged** (charge machine → Not Charging, no dump), 0 = no opinion | Agreed (charge element 5a, on Henry's updated chart). A veto: can only stop charging, never start it. `chg_stop_mode_ctrl` wins if both are 1. | Agreed: set by "Hold Charge" (was "Disable Charging") before the AUTOKICK kick and before the damp's Initial Kick; cleared by every Enable Charging. |
| `chg_disable_chip_level` | Charge machine (output flag, set to 1 on entering Disabled, never cleared until reboot) | Mode machine (must stop → Locked); the Pi (STATUS fault byte + FAULT event with the reason) | 1 = charging is permanently disabled: faulty board | Agreed (charge review element 12, mode review element 5). Purely reactive on the mode side: Locked only tells the Pi and sets nothing; the charge machine has already stopped charging and dumped in Disabled. | — |
| `chg_ok` | Battery monitor (on above 14.7 V, off below 14.3 V or above 17.0 V, 3-sample debounce, read every 500 ms) | Charge machine (element 1); mode machine (its element 1) | 1 = a battery is present | Agreed on both sides. They agree: mode Wait for Bat sets `chg_stop_mode_ctrl = 1`, so the charge machine can't leave its own Wait for Bat (needs `chg_stop_mode_ctrl == 0`) until the mode machine has been through Idle and an Enable Charging. | A stuck battery reading: charge review open topic. |
| HV (bank voltage, ADC) | HV sense hardware / ADC | Charge machine (only the open-topic X items); mode machine (kick arm `HV >= 100V`, damp start `HV >= 180`, damp duty formula; STATUS kick-ready flag) | Bank voltage | Charge cycle decisions don't use HV (charge review N-0). Mode uses agreed (mode elements 10, 14, 16); no damp HV floor. | Stuck-high / stuck-low behaviour: charge review open topic (stuck HV → never kicks / never damps is an accepted risk so far, KCK-1). |
| DONE pin | LT3750 | Charge machine only | 1 = charger switching | Rising edge latched after each toggle (charge element 8). | — |
| CHARGE pin | Power stage, from the charge machine's "charge" output | LT3750 | High = charge cycle running | Only the power stage writes the pin (X-5 interlock, agreed). | — |
| `~DISCHARGE` pin (dump) | Power stage, from the **charge machine** (Inhibited and Disabled dump) | Autodischarge circuit | Low = bank dumping | The mode machine has no separate dump output: it dumps by setting `chg_stop_mode_ctrl = 1` (authorship rule). | C-1 solved (hold flag). Interlock with CHARGE: X-5 (agreed). |
| Kicker (KICK pin, PIO) | Mode machine requests (fire / PWM / stop); the kicker driver executes | — | Kick and damp pulses | **Before any kick or Initial Kick:** Hold Charge (`chg_hold_mode_ctrl = 1`) and `done == 0` (mode elements 11, 12, 15). **After:** Enable Charging; the charge machine always goes through New Charge (CHARGE low 50 ms), so charging restarts ≥ 50 ms after a kick. That covers the dropped "time since last kick >= 50ms" GUARD row (charge element 7). PWM stopped by every exit from the damp states; kicker-driver limit `DAMP_MAX_TOTAL_MS` as a backstop (mode DMP-3). | — |

## Why the two charge flags are `chg_stop` (stop + dump) and `chg_hold` (stop only)

* `chg_stop_mode_ctrl` is the main control, as before: 0 = charge (no dump), 1 = stop charging **and** dump.
* `chg_hold_mode_ctrl` is a **veto**: it can only ever say "stop charging", never "go charge". 1 = stop charging
  (keep the charge) even if `chg_stop_mode_ctrl` says charge; 0 = no opinion (ignored). If `chg_stop_mode_ctrl` is
  already 1, the hold changes nothing (the dump carries on).

Not "stop" and "dump" as independent flags: then "dump = 1, stop = 0" would mean **charging while dumping**
(the smoking-resistor case). Henry kept one flag originally for exactly this reason. With these two, every
combination is safe:

| `chg_stop_mode_ctrl` | `chg_hold_mode_ctrl` | Charge machine state | CHARGE | Dump |
|---|---|---|---|---|
| 0 | 0 | charging states (New Charge … Assuming Charged) | cycles | off |
| 0 | 1 | Not Charging | off | off |
| 1 | 0 | Inhibited | off | on |
| 1 | 1 | Inhibited (stop wins) | off | on |

Only the charge machine drives the pins, and each of its states sets both. The X-5 power-stage interlock (agreed) is the pin-level backstop below that.

## Open conflicts

| # | Conflict | Status |
|---|---|---|
| C-1 | **Disable Charging before a kick dumps the bank.** The mode chart sets Disable Charging (`chg_stop_mode_ctrl = 1`) before Arm Kick (AUTOKICK) and before the Initial Kick (DAMP). With Inhibited = dump, the bank starts falling through the autodischarge circuit (a full dump takes **~7 s**, Henry's measurement; how fast it falls from 210 V to 180 V is to be measured on the bench, not assumed). Effects: the `HV >= 180V` arm / damp checks fail once the bank drops below 180 V; a kick at 180 V has only ~73 % of the energy of one at 210 V ((180/210)²); an armed robot waiting for the ball ends up with an empty bank. **Also:** a Pi spamming AUTOKICK would alternate Enable Charging and Disable Charging (DAMP no longer does: a DAMP during a damp just renews it, mode MSG-2), i.e. charge → dump → charge, putting ~44 J into the dump resistors per full dump (the resistors that smoked before). Needs the dump resistors' power rating (R44 / R48) to judge. Kicks themselves may repeat freely (mode MSG-2). The RoboCup code avoided this because `chg_stop_mode_ctrl = 1` there only stopped charging (damp set `not_dischg = 1` to hold the charge). | **Solved:** new flag `chg_hold_mode_ctrl` (stop charging, keep the charge), set by Hold Charge before a kick and before the damp's Initial Kick, kept through the damp. Hold never dumps, so the spam case is gone too: a spammed AUTOKICK goes Enable Charging → Hold Charge with no dump in between. Only Idle / heartbeat loss dump. |
