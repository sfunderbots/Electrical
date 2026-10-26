# Chicker Firmware (Main Publication is Solenoid Active Damping)

This firmware is to go hand in hand with the Chicker Board, designed in KiCad. It charges the HV capacitor bank with an
LT3750 flyback, kicks, and holds the plunger out for solenoid active damping. Version 2 (branch `chicker-fw-v2-sim`)
is a rewrite in MicroPython with a simulator that runs the real firmware before it ever touches a board.

## Folder layout

Everything that goes on the board sits directly in this folder (plus `canbus/`). Everything else is in a subfolder.

**On the board:**

| File | What |
|---|---|
| `main.py` | entry point: drives the power pins safe, runs the loop, safe state on any error |
| `config.py` | every constant (pins, timings, thresholds, limits) - nothing else |
| `app.py` | wires everything together once per loop: sensors -> commands -> mode -> kicker -> charge -> power pins -> report |
| `charge_sm.py` | charge state machine, Henry's charge chart (`docs/CHARGE-STATE-MACHINE-REVIEW.md`) |
| `mode_sm.py` | mode control state machine, Henry's mode chart (`docs/MODE-STATE-MACHINE-REVIEW.md`) |
| `sm_base.py` | the shared state-machine engine (each chart arrow is one row of a table) |
| `power_stage.py` | the only code that writes CHARGE and ~DISCHARGE (50 ms dead time both ways, readback) |
| `kicker.py` | the RP2040 PIO program (`@rp2.asm_pio`) that owns the KICK pin, plus its pure maths |
| `protocol.py` | CAN protocol (shared with the simulator and bench tools) |
| `board.py` | pins, ADCs, PIO, MCP2515 setup and self-tests |
| `sensors.py` | ADC conversions, battery window |
| `console.py` | USB serial commands (same decoder as CAN) |
| `canbus/` | MCP2515 driver (unchanged) |

**Not on the board:**

| Folder | What |
|---|---|
| `sim/` | simulator + test suite (fake `machine`/`rp2`/`utime`/`canbus`, PIO emulator, board physics) |
| `bench/` | hardware bring-up sweep over USB serial (rehearsable on the simulator) |
| `flowcharts/` | Henry's original charts and his updated `*-v2` charts |
| `docs/` | `CHANGES-v2.md` (start here: what changed and why), the three state-machine reviews (the spec the code is written from), `REVIEW-PLAN.md` (status), `NEXT-STEPS-2026-10-26.md` (plan for the next sessions) |
| `legacy/` | the RoboCup 2026 firmware, kept for reference |
| `to-test/` | ideas not in the firmware yet (reed switch, HV current sensing) |

## Run the simulator tests

From this folder (plain Python 3, no extra packages):

```
python -m sim.run_tests --fast      # everything except the long soak / bench rehearsal
python -m sim.run_tests             # everything
```

## Bench bring-up (real board)

```
pip install pyserial
python -m bench.bring_up --sim                      # rehearse first
python -m bench.bring_up --port COM5 --max-voltage 60
```

Close Thonny first (it holds the serial port). See the docstring in `bench/bring_up.py` for the stages.

## Deploy

Copy every `.py` file in this folder and the `canbus/` folder to the board (Thonny, or
`mpremote cp *.py :` and `mpremote cp -r canbus :`). The board boots into Wait for Bat with the bank dumped, waits for
the battery and the first heartbeat from the Pi, then idles until a command arrives over CAN or the USB console.

## License
This firmware is licensed under the open-sourced MIT License - see the [LICENSE](LICENSE) file for details.
