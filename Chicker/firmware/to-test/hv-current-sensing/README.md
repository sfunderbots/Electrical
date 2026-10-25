# Seeing the ball hit the plunger from the HV reading

Goal: during a damping hold, tell when the ball hits the held-out plunger using only the bank-voltage (HV) reading,
because the break-beam is blocked by the plunger during a damp (mode review note DMP-5). Later this could let the
board kick the moment the ball arrives (if the robot is already aimed), instead of waiting for the AI.

Status: **research + test code, not part of the firmware.** The detector is checked on synthetic data; the real
signal size and shape must be measured on the bench (Henry has the parts once the midplate's 5 V buck has its two
feedback resistors).

## Files

| File | Runs on | What it does |
|---|---|---|
| `impact_detector.py` | board and PC | the detector (same code in both places) |
| `test_impact_detector.py` | PC | checks the detector on synthetic traces: `python test_impact_detector.py` |
| `hv_dma_capture.py` | board | records HV fast: ADC free-running, DMA into a buffer (MicroPython 1.22+; slower polling fallback otherwise) |
| `bench_impact_capture.py` | board | charge, hold the plunger with the damping PWM, record while the ball is rolled in, dump; prints the capture |
| `host_analyse.py` | PC | reads saved captures, prints noise / hold current / impacts, optional plots |
| `claude-current-fromHV.txt` | – | the earlier sketch (current from the HV slope on the second core); superseded by the files above |

## The sense chain (from `Project Files/HV-feedback.kicad_sch`)

* Divider: 3 × 330 kΩ (R53-55) over 13 kΩ (R56): bank voltage ÷ 77.15. At the ADC, 1 count = 3.3 V / 4096 × 77.15
  ≈ **62 mV of bank voltage**.
* Filter: 10 nF (C29 / C41). Across the 13 kΩ leg this gives RC ≈ (990 kΩ ∥ 13 kΩ) × 10 nF ≈ **128 µs**
  (−3 dB ≈ 1.2 kHz). **Check on the board which node each 10 nF sits on**: if one is directly on the ADC pin after the
  op-amp, that changes nothing; if both are in the divider, the filter is slower.
* Buffer: MCP6231 op-amp (300 kHz gain-bandwidth) as a follower, 3.3 V clamp diodes (D19, D20) into GPIO29.
* So the analog path passes anything slower than about a millisecond: a hit lasting a few ms gets through.

The limit is the **RP2040 ADC**: 12 bits but only about 9 effective bits (noise ≈ 2-3 counts rms ≈ 0.15-0.2 V of bank
voltage per sample), plus a few known bad codes (differential non-linearity). One reading per firmware loop pass is
far too noisy and too slow. The ADC can sample up to 500 kS/s on its own, though: free-running into its FIFO, emptied
by DMA, the CPU doing nothing. Averaging N samples cuts the noise by about √N.

## How it works

During a damping hold the charger is off (Charge Hold) and the dump is off, so only the solenoid (and the ~1 MΩ
divider, ~0.2 mA) draws from the 2 mF bank. The bank voltage then falls along a smooth line: slope = −I / C.

When the ball hits, it pushes the plunger back against the magnetic pull. The air gap and the coil inductance L(x)
change fast, which induces a voltage in the coil (≈ i · dL/dt). For a few milliseconds the coil draws a different
current from the bank, then settles at the plunger's new position. The ball's kinetic energy goes into the coil
circuit: that is what "damping" is.

**Size, rough:** a 46 g ball at 2 m/s carries ½mv² ≈ 0.09 J. Absorbed over ~3 ms that is ~30 W, i.e. ~0.2 A at 150 V.
Whether the bank sees it as **more** or **less** current (and how much of the energy recirculates in the coil's
freewheel diode instead) has to be measured. The detector works either way.

**Why look for a step, not a spike:** a current spike has to be read from the slope of a noisy signal over a few ms:
about ±0.03 A of noise with this ADC, so a 0.2 A × 3 ms hit is only just visible. But the hit also moves a fixed
charge ΔQ (≈ 0.4 mC) in or out of the bank, so afterwards the voltage sits **ΔQ / C ≈ 0.2 V off its line**, and stays
there. Comparing the average of the newest few ms with a straight line fitted to the previous ~40 ms uses every sample
during and after the hit: noise ≈ 0.01 V, so a signal-to-noise ratio of ~15.

**Detector (`impact_detector.py`)**, once per averaged sample (2.5 kS/s):
1. fit a line to the samples 4-44 ms old (running sums, constant work per sample)
2. step = mean of the newest 4 ms − the line's prediction for them
3. learn the noise level (average |step|) while nothing is happening
4. impact = |step| > 6 × noise (and > 0.05 V) for 2 samples in a row; then ignore one window so the hit leaves the line

What the synthetic tests show (50 kS/s capture averaged ×20, real sense-chain model, 200 V bank, 0.1 A hold):

| Hit (3 ms pulse) | Caught |
|---|---|
| 0.05 A | 0 / 20 |
| 0.08 A | 13 / 20 |
| 0.10 A and up | 20 / 20 |

No false triggers in 50 s of hold without a ball, none from slow changes in the hold current, at most one from a
lasting step in it (e.g. a duty change), and a bounce shows up as two hits. The hit is reported ~3 ms after it starts.

## Test plan (bench)

**Safety:** high voltage. Solenoid clamped, kicker face clear, start at 60 V, never above 205 V in this script.
`bench_impact_capture.py` always dumps the bank at the end, also after an error or Ctrl-C.

Setup: copy the firmware (`*.py` + `canbus/`) plus `hv_dma_capture.py`, `impact_detector.py` and
`bench_impact_capture.py` to the board. Stop the firmware within 3 s of boot (before its watchdog starts) or rename
`main.py`. Check the MicroPython version (`import sys; sys.version`): 1.22 or newer has `rp2.DMA`.

Each run prints one capture; save it with `mpremote exec "..." > file.txt` (or copy the Thonny shell), then
`python host_analyse.py file.txt --plot`.

1. **Noise floor**: `b.run(mode="dumped")`. Bank at 0 V. Gives the ADC + sense-chain noise.
2. **Charged, no hold**: `b.run(target_v=60, mode="charged")`, then 100 and 150 V. Noise at voltage, and the bleed slope
   (should be ~V / 1 MΩ / C, about 0.05 V/s at 100 V).
3. **Hold, no ball**: `b.run(target_v=60, mode="hold")` at 60 / 100 / 150 V. The kick, the PWM start, the hold current
   (from the slope), and what a quiet hold looks like to the detector (no impacts should be reported).
4. **Hold + ball**: same, roll the ball into the plunger after "GO" (a ramp at a fixed height gives repeatable
   speeds). Several speeds, several runs each. Do the impacts stand out? Which sign? How big (charge, step)?
5. **Bounces and misses**: a ball that bounces off, a ball that glances, the robot being bumped without a ball.

Captures are 0.8 s at 50 kS/s (80 KB of RAM, allocated first). Settings to try: `rate_hz` 25-200 kS/s (shorter
`capture_s` at high rates), `duty_pct`, `freq_hz`.

## Then

* **The signal is clear:** run the detector live. A DMA ring buffer keeps the ADC streaming, the second core averages
  ×20 and feeds `impact_detector.py`, and the mode machine gets a "ball hit" input during a damp. That is a chart
  change (Henry: kicking on the hit only makes sense if the robot is already aimed), to agree in the mode review.
* **The signal is too small:** a current-sense resistor on the coil sees the plunger directly. In each PWM on-time
  the coil current rises at V / L(x), so the plunger position can be read every period (30 kHz). Or the reed switch
  (`to-test/reed-switch`) for a simple "plunger pushed back".

## Unknowns to settle on the bench

* the sign, size and length of a real hit (the solenoid model in the simulator is not trustworthy)
* the real hold current (sets the noise of the baseline line and how fast a hold drains the bank)
* where the two 10 nF capacitors sit (filter speed)
* whether the MicroPython on the boards has `rp2.DMA`
