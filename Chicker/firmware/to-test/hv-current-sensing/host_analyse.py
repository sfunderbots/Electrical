"""Analyse captures from bench_impact_capture.py on the PC.

    python host_analyse.py hold_60V_ball1.txt [more files ...] [--decimate 20] [--k 6] [--plot]

For each capture it prints:
  * the noise of the raw samples and of the averaged samples (in volts of bank voltage)
  * the bank current during the capture (from the slope of the voltage) before and after the PWM starts
  * every impact the detector finds (time, step in volts, charge), with the kick / PWM times for reference
--plot draws the voltage, the detector's step signal and its threshold (needs matplotlib: pip install matplotlib).
The detector is the same impact_detector.py that runs on the board, so settings tuned here carry over.
"""

import sys
from impact_detector import ImpactDetector, decimate


def read_captures(path):
    """Yield (meta dict, list of counts) for every BEGIN_CAPTURE ... END_CAPTURE block in the file."""
    meta, counts, inside = None, None, False
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line == "BEGIN_CAPTURE":
            inside, meta, counts = True, {}, []
        elif line.startswith("END_CAPTURE"):
            inside = False
            yield meta, counts
        elif inside and line.startswith("META "):
            for item in line[5:].split():
                k, _, v = item.partition("=")
                try:
                    meta[k] = float(v) if "." in v or v.isdigit() else v
                except ValueError:
                    meta[k] = v
        elif inside and line:
            counts.extend(int(x) for x in line.split(",") if x)


def to_volts(counts, meta):
    scale = float(meta.get("adc_vref", 3.3)) / 4095.0 * float(meta.get("hv_divider", 1003.0 / 13.0))
    return [c * scale for c in counts]


def rms(xs):
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / len(xs)) ** 0.5


def slope_current(volts, rate_hz, c_bank=0.002):
    """Average bank current over a stretch: -C * (least-squares slope of V)."""
    n = len(volts)
    if n < 3:
        return float("nan")
    xm = (n - 1) / 2.0
    vm = sum(volts) / n
    sxy = sum((k - xm) * (v - vm) for k, v in enumerate(volts))
    sxx = sum((k - xm) ** 2 for k in range(n))
    return -c_bank * sxy / sxx * rate_hz


def analyse(path, factor, k_sigma, plot):
    for idx, (meta, counts) in enumerate(read_captures(path)):
        rate = float(meta.get("rate_hz", 50000))
        v = to_volts(counts, meta)
        vd = decimate(v, factor)
        fs = rate / factor
        print("== %s capture %d: mode=%s target=%s V, %d samples at %.0f Hz (%s), errors flagged: see file" % (
            path, idx, meta.get("mode"), meta.get("target_v"), len(v), rate,
            "DMA" if str(meta.get("used_dma")) == "True" else "POLLING"))
        print("   noise: raw %.3f V rms, averaged x%d %.3f V rms (first 20 ms)" % (
            rms(v[:max(int(0.02 * rate), 10)]), factor, rms(vd[:max(int(0.02 * fs), 4)])))
        pwm = int(meta.get("pwm_at_sample", 0) or 0)
        if pwm:
            print("   bank current before PWM %.3f A, during hold %.3f A" % (
                slope_current(v[:pwm], rate), slope_current(v[pwm + int(0.01 * rate):], rate)))
        det = ImpactDetector(sample_rate_hz=fs, k_sigma=k_sigma)
        start = (pwm + int(0.01 * rate)) // factor if pwm else 0      # skip the kick / PWM start itself
        steps, thr = [], []
        for j, x in enumerate(vd):
            ev = det.feed(x) if j >= start else None
            steps.append(det.step)
            thr.append(det.threshold() or 0.0)
            if ev:
                t = ev[0] + start
                print("   IMPACT at %.1f ms: step %+.3f V, charge %+.2f mC" % (1000.0 * t / fs, ev[1], 1000 * ev[2]))
        for key in ("kick_at_sample", "pwm_at_sample", "pwm_stopped_at_sample"):
            if key in meta:
                print("   %s: %.1f ms" % (key.replace("_at_sample", ""), 1000.0 * float(meta[key]) / rate))
        if plot:
            _plot(path, idx, v, rate, vd, fs, steps, thr, start)


def _plot(path, idx, v, rate, vd, fs, steps, thr, start):
    import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(2, 1, sharex=True)
    a1.plot([1000.0 * k / rate for k in range(len(v))], v, lw=0.3, label="raw")
    a1.plot([1000.0 * k / fs for k in range(len(vd))], vd, lw=1.0, label="averaged")
    a1.set_ylabel("bank V")
    a1.legend()
    t = [1000.0 * (k) / fs for k in range(len(steps))]
    a2.plot(t, steps, lw=0.8, label="step vs line")
    a2.plot(t, thr, "r--", lw=0.6, label="threshold")
    a2.plot(t, [-x for x in thr], "r--", lw=0.6)
    a2.set_ylabel("V")
    a2.set_xlabel("ms")
    a2.legend()
    fig.suptitle("%s capture %d" % (path, idx))
    plt.show()


def main(argv):
    factor, k_sigma, plot, files = 20, 6.0, False, []
    it = iter(argv)
    for a in it:
        if a == "--decimate":
            factor = int(next(it))
        elif a == "--k":
            k_sigma = float(next(it))
        elif a == "--plot":
            plot = True
        else:
            files.append(a)
    if not files:
        print(__doc__)
        return 1
    for f in files:
        analyse(f, factor, k_sigma, plot)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
