# Ball-impact detector: spots the moment a ball hits the held plunger, from the bank voltage alone.
#
# Runs unchanged on the board (MicroPython) and on a PC (CPython), so the code that is tuned on logged captures is
# the code that later runs live. No numpy; constant work per sample (running sums, no re-fitting).
#
# Idea (README.md, "How it works"):
#   During a damping hold the charger and the dump are both off, so only the solenoid (plus the ~1 MOhm HV divider)
#   draws from the bank. The bank voltage then falls along a smooth line. A ball hitting the plunger changes the coil
#   current for a few milliseconds, which moves a small extra charge dQ in or out of the bank: the voltage STEPS off
#   its line by dV = dQ / C and then carries on falling. Looking for that step (instead of the current spike itself)
#   uses all the samples during and after the hit, so it sees much smaller hits through the ADC noise.
#
# Every sample (after averaging / decimation):
#   1. baseline line   least-squares line through the "baseline" window: the samples BASELINE_S .. GAP_S old
#   2. step            mean of the newest RECENT_S of samples minus the line's prediction for those same samples
#   3. noise level     slow average of |step| while nothing is happening (the threshold adapts to each board)
#   4. trigger         |step| > K_SIGMA * noise for MIN_SAMPLES samples in a row -> one impact event; then nothing
#                      until the hit has passed through the baseline window (so the line is clean again)
#
# Usage:
#   det = ImpactDetector(sample_rate_hz=2500)
#   for v in volts:                      # bank voltage, one value per decimated sample
#       ev = det.feed(v)
#       if ev: print(ev)                 # (sample_index, step_volts, estimated_charge_coulombs)


class ImpactDetector:
    def __init__(self, sample_rate_hz, c_bank_f=0.002, baseline_s=0.04, gap_s=0.004, recent_s=0.004,
                 k_sigma=6.0, min_samples=2, min_step_v=0.05):
        """
        sample_rate_hz   rate of the values passed to feed() (after any averaging / decimation)
        c_bank_f         bank capacitance (2 x 1000 uF), only used to turn the step into a charge
        baseline_s       length of the window the straight line is fitted to
        gap_s            samples skipped between the baseline window and the newest samples (keeps the start of a
                         hit out of the line)
        recent_s         newest samples averaged and compared with the line
        k_sigma          trigger level, in multiples of the measured noise. 6 = no false triggers in 50 s of
                         synthetic hold, catches 0.1 A x 3 ms hits every time (tune on real captures)
        min_samples      the step must stay over the threshold this many samples in a row
        min_step_v       never trigger on a smaller step, however quiet the signal looks
        """
        fs = float(sample_rate_hz)
        self.c = c_bank_f
        self.nb = max(int(baseline_s * fs), 4)       # baseline samples
        self.ng = int(gap_s * fs)                     # gap samples
        self.nr = max(int(recent_s * fs), 1)          # recent samples
        self.size = self.nb + self.ng + self.nr
        self.k_sigma = k_sigma
        self.min_samples = min_samples
        self.min_step = min_step_v
        self._buf = [0.0] * self.size                 # ring buffer, oldest first from _head
        # least-squares constants for a line through x = 0 .. nb-1
        nb = self.nb
        self._xm = (nb - 1) / 2.0
        self._sxx = sum((k - self._xm) ** 2 for k in range(nb))
        # x positions of the recent samples, relative to the baseline window: nb + ng .. size-1
        self._xr = (nb + self.ng + self.size - 1) / 2.0
        self.reset_state()

    def reset_state(self):
        self._head = 0           # index of the oldest sample in the ring
        self._count = 0
        self._s0 = 0.0           # sum of baseline samples
        self._s1 = 0.0           # sum of k * v over the baseline samples (k = 0 for the oldest)
        self._sr = 0.0           # sum of recent samples
        self.noise = None        # V, average |step| while quiet
        self.step = 0.0          # V, latest step (newest samples minus the line)
        self._over = 0
        self._peak = 0.0
        self._quiet_until = 0

    def threshold(self):
        if self.noise is None:
            return None
        return max(self.k_sigma * self.noise, self.min_step)

    def feed(self, volts):
        """One bank-voltage sample. Returns (index, step_V, charge_C) when an impact is detected, else None."""
        size, nb, ng, nr = self.size, self.nb, self.ng, self.nr
        b = self._buf
        if self._count < size:
            # filling up: just store, keep the sums right
            k = self._count
            b[k] = volts
            if k < nb:
                self._s0 += volts
                self._s1 += k * volts
            elif k >= nb + ng:
                self._sr += volts
            self._count += 1
            return None
        # slide the window by one: the oldest baseline sample leaves, the oldest gap sample joins the baseline,
        # the oldest recent sample joins the gap, the new sample joins the recent window
        h = self._head
        v_out = b[h]                                  # leaves the baseline window
        v_in_base = b[(h + nb) % size]                # gap -> baseline
        v_out_recent = b[(h + nb + ng) % size]        # recent -> gap (or straight to baseline if ng == 0)
        self._s1 = self._s1 - (self._s0 - v_out) + (nb - 1) * v_in_base
        self._s0 = self._s0 - v_out + v_in_base
        self._sr = self._sr - v_out_recent + volts
        b[h] = volts
        self._head = (h + 1) % size
        self._count += 1
        # 1-2. line through the baseline window, step of the recent mean against the line
        mean_b = self._s0 / nb
        slope = (self._s1 - self._xm * self._s0) / self._sxx       # volts per sample
        predicted = mean_b + slope * (self._xr - self._xm)
        step = self._sr / nr - predicted
        self.step = step
        # 3. noise level, learned while quiet (plain mean for the first samples, then a slow average)
        thr = self.threshold()
        quiet = thr is None or (abs(step) < thr and self._over == 0)
        if quiet and self._count >= self._quiet_until:
            learned = self._count - size
            a = max(1.0 / (learned + 1), 1.0 / (4 * size))
            self.noise = abs(step) if self.noise is None else self.noise + (abs(step) - self.noise) * a
        if self._count < 2 * size:
            return None                               # still learning the noise
        # 4. trigger
        if self._count < self._quiet_until:
            return None
        if abs(step) > thr:
            self._over += 1
            if abs(step) > abs(self._peak):
                self._peak = step
            if self._over == self.min_samples:
                self._over = 0
                peak, self._peak = self._peak, 0.0
                self._quiet_until = self._count + size    # wait until the hit has left the baseline window
                return (self._count - 1, peak, peak * self.c)
        else:
            self._over = 0
            self._peak = 0.0
        return None


def decimate(samples, factor):
    """Average consecutive blocks of `factor` samples (cuts ADC noise by about sqrt(factor))."""
    out = []
    acc = 0.0
    for k, x in enumerate(samples):
        acc += x
        if (k + 1) % factor == 0:
            out.append(acc / factor)
            acc = 0.0
    return out
