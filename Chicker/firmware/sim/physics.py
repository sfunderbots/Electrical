"""Electrical + mechanical model of the chicker board.

Calibration status (keep honest - the notebook prints this):
  [FIT]  LT3750 charge power P = k * V^alpha fitted to the old firmware's 236 ms -> 50 V and
         1108 ms -> 160 V observations; stop at 214.9 V indicated (Steven's bench reading).
  [FIT]  Solenoid R = 1.72 ohm, L = 2.17 mH fitted to the kick-drop table in the RoboCup main.py
         (500-2500 us at 210 V, within ~1 V). May predate the 1 cm solenoid move -> re-fit on the bench.
  [SCH]  2 x 1000 uF bank, 1 k dump (2 x 2 k 35 W in parallel), HV divider 13k / 1003k with RC filter.
  [GUESS] Plunger / ball mechanics: 60 g plunger, 45 g ball (from the damping write-up), force constant
         and return spring chosen so that ~12 % duty at 210 V / 30 kHz just holds the plunger out (the
         RoboCup 2026 tuning point). Use for relative comparisons only.
  [GUESS] Dump resistor thermal model (lumped, no heatsink): only meant to flag sustained dissipation.
"""

import math

DEFAULTS = dict(
    # bank / charger
    C=2000e-6,
    R_bleed=1.003e6,           # HV divider is the main bleed path
    lt_target_v=210.0,         # Henry: full bank reads 206-212 V (Steven's board read 214.9 V)
    lt_k=1.159, lt_alpha=0.67, lt_pmax=60.0, lt_eff=0.8,
    lt_vin_min=10.0,           # LT3750 UVLO on VCC
    lt_fault="ok",             # ok | uvlo | no_regulation | early_done | done_stuck_high | done_stuck_low |
                               # done_follows_charge | slow
    lt_early_done_v=120.0,
    lt_done_in_shutdown=0,     # DONE level while CHARGE is low (datasheet: NPN state; old fw observed 0)
    # dump
    R_dump=1000.0,             # D3: 2 x 2k in parallel
    dump_n=2,
    dump_rth=12.0,             # K/W per resistor  [GUESS]
    dump_cth=4.0,              # J/K per resistor  [GUESS]
    t_amb=25.0,
    dump_temp_limit=125.0,
    # solenoid
    R_coil=1.72, L_coil=2.17e-3, V_ce=1.5, V_diode=0.9,
    # plunger / ball [GUESS]
    m_plunger=0.060, stroke=0.012, dLdx=0.045,
    spring_f0=2.0, spring_k=200.0, plunger_friction=0.05,
    stop_restitution=0.2,
    m_ball=0.045, contact_k=4e4, contact_c=8.0,
    beam_zone=0.025,           # ball surface within this of the retracted head -> beam broken
    # battery
    batt_v=15.8, batt_r=0.08, batt_present=True, quiescent_a=0.15,
    v5=5.0,
    # ADC
    adc_noise_lsb=1.0, hv_tau=128e-6, adc_offset_v=0.0,
)


class Physics:
    def __init__(self, rng, **overrides):
        p = dict(DEFAULTS)
        for k, v in overrides.items():
            if k not in p:
                raise KeyError("unknown physics parameter %s" % k)
            p[k] = v
        self.p = p
        self.rng = rng
        self.v = 0.0                  # bank voltage
        self.i = 0.0                  # coil current
        self.hv_filt = 0.0            # HV sense after the RC filter
        self.lt_charging = False
        self.lt_done_latched = False
        self.charge_pin = 0
        self.ndis_pin = 0
        self.dump_on = True
        self.t_res = [p["t_amb"]] * p["dump_n"]
        self.batt_i = 0.0
        # mechanics
        self.x = 0.0                  # plunger extension, 0 = retracted
        self.xv = 0.0
        self.ball = None              # [position of ball surface, velocity] or None
        self.ball_max_v = 0.0
        # bookkeeping
        self.e_dump = 0.0
        self.e_kick = 0.0
        self.e_charge = 0.0
        self.max_v = 0.0
        self.max_i = 0.0
        self.max_temp = p["t_amb"]

    # ------------------------------------------------------------------ inputs
    def set_charge_pin(self, level):
        p = self.p
        rising = level and not self.charge_pin
        self.charge_pin = level
        if not level:
            self.lt_charging = False
            self.lt_done_latched = False
        elif rising and self.vin() >= p["lt_vin_min"] and p["lt_fault"] not in ("uvlo",):
            if self.v < p["lt_target_v"] or p["lt_fault"] == "no_regulation":
                self.lt_charging = True
            self.lt_done_latched = False

    def set_ndis_pin(self, level):
        self.ndis_pin = level
        self.dump_on = level == 0

    def place_ball(self, pos, vel=0.0):
        self.ball = [pos, vel]
        self.ball_max_v = abs(vel)

    # ------------------------------------------------------------------ outputs
    def vin(self):
        p = self.p
        if not p["batt_present"]:
            return 0.0
        return p["batt_v"] - p["batt_r"] * self.batt_i

    def done_level(self):
        f = self.p["lt_fault"]
        if f == "done_stuck_high":
            return 1
        if f == "done_stuck_low":
            return 0
        if f == "done_follows_charge":          # DONE never falls while CHARGE is held high
            return self.charge_pin
        if self.lt_charging:
            return 1
        if not self.charge_pin:
            return self.p["lt_done_in_shutdown"]
        return 0

    def beam_broken(self):
        return self.ball is not None and self.ball[0] < self.p["beam_zone"]

    def adc_hv_volts(self):
        return min(max(self.hv_filt * 13.0 / 1003.0 + self.p["adc_offset_v"], 0.0), 3.3)

    def adc_batt_volts(self):
        return min(self.vin() * 200.0 / 1140.0, 3.3)

    def adc_5v_volts(self):
        return min(self.p["v5"] * 51.0 / 81.0, 3.3)

    def busy(self):
        """True while something fast is happening (needs small time steps)."""
        return (self.i > 1e-3 or abs(self.xv) > 1e-4 or self.x > 1e-6 or
                (self.ball is not None and abs(self.ball[1]) > 1e-4 and self.ball[0] < 0.2))

    # ------------------------------------------------------------------ integration
    def integrate(self, dt, kick):
        """Advance dt seconds with the KICK gate held at `kick` (0/1). Called piecewise."""
        if dt <= 0:
            return
        p = self.p
        # split long pieces so the electrical/mechanical coupling stays accurate
        max_piece = 20e-6 if (kick or self.busy()) else 1e-3
        while dt > 0:
            h = min(dt, max_piece)
            self._piece(h, kick)
            dt -= h

    def _piece(self, h, kick):
        p = self.p
        v = self.v
        # --- solenoid (analytic over the piece, bank voltage and back-EMF held constant) ---
        R, L = p["R_coil"], p["L_coil"]
        tau = L / R
        bemf = 0.5 * p["dLdx"] * self.i * self.xv
        i0 = self.i
        q_bank = 0.0
        if kick and v > p["V_ce"]:
            a = (v - p["V_ce"] - bemf) / R
            e = math.exp(-h / tau)
            i1 = a + (i0 - a) * e
            q_bank = a * h + (i0 - a) * tau * (1 - e)       # integral of i dt
            i_avg = q_bank / h
        elif i0 > 0:
            a = -(p["V_diode"] + bemf) / R
            if a >= 0:
                a = -1e-9
            t_zero = tau * math.log((i0 - a) / (-a))
            if t_zero >= h:
                e = math.exp(-h / tau)
                i1 = a + (i0 - a) * e
                i_avg = (a * h + (i0 - a) * tau * (1 - e)) / h
            else:
                i1 = 0.0
                i_avg = (a * t_zero + (i0 - a) * tau * (1 - math.exp(-t_zero / tau))) / h
        else:
            i1 = 0.0
            i_avg = 0.0
        self.i = max(i1, 0.0)
        self.max_i = max(self.max_i, self.i)
        i_rms2 = (i0 * i0 + i0 * self.i + self.i * self.i) / 3.0

        # --- bank: charger in, dump / bleed / kick out ---
        p_chg = 0.0
        if self.lt_charging:
            p_chg = min(p["lt_k"] * max(v, 3.0) ** p["lt_alpha"], p["lt_pmax"])
            if p["lt_fault"] == "slow":
                p_chg *= 0.3
        p_dump = v * v / p["R_dump"] if self.dump_on else 0.0
        p_bleed = v * v / p["R_bleed"]
        e = 0.5 * p["C"] * v * v + (p_chg - p_dump - p_bleed) * h - v * q_bank
        self.v = math.sqrt(max(e, 0.0) * 2 / p["C"])
        self.e_dump += p_dump * h
        self.e_kick += v * q_bank
        self.e_charge += p_chg * h
        self.max_v = max(self.max_v, self.v)
        self.batt_i = p["quiescent_a"] + (p_chg / p["lt_eff"] / max(self.vin(), 1.0) if p_chg else 0.0)

        if self.lt_charging:
            if p["lt_fault"] == "early_done" and self.v >= p["lt_early_done_v"]:
                self.lt_charging = False
            elif p["lt_fault"] != "no_regulation" and self.v >= p["lt_target_v"]:
                self.lt_charging = False
            if self.vin() < p["lt_vin_min"]:
                self.lt_charging = False

        # --- dump resistor temperature ---
        n = p["dump_n"]
        for k in range(n):
            t = self.t_res[k]
            t += (p_dump / n - (t - p["t_amb"]) / p["dump_rth"]) / p["dump_cth"] * h
            self.t_res[k] = t
            if t > self.max_temp:
                self.max_temp = t

        # --- HV sense RC filter ---
        a = 1.0 - math.exp(-h / p["hv_tau"])
        self.hv_filt += (self.v - self.hv_filt) * a

        # --- plunger + ball ---
        if self.busy() or self.ball is not None:
            self._mechanics(h, i_rms2)

    def _mechanics(self, h, i2):
        p = self.p
        f = 0.5 * p["dLdx"] * i2 - (p["spring_f0"] + p["spring_k"] * self.x)
        contact = 0.0
        if self.ball is not None:
            b, bv = self.ball
            pen = self.x - b
            if pen > 0:
                contact = max(p["contact_k"] * pen + p["contact_c"] * (self.xv - bv), 0.0)
        f -= contact
        f -= p["plunger_friction"] * self.xv * 50.0
        self.xv += f / p["m_plunger"] * h
        self.x += self.xv * h
        if self.x < 0:
            self.x = 0.0
            if self.xv < 0:
                self.xv = -self.xv * p["stop_restitution"]
                if self.xv < 1e-3:
                    self.xv = 0.0
        elif self.x > p["stroke"]:
            self.x = p["stroke"]
            if self.xv > 0:
                self.xv = -self.xv * p["stop_restitution"]
        if self.ball is not None:
            b, bv = self.ball
            bv += contact / p["m_ball"] * h
            b += bv * h
            self.ball = [b, bv]
            if bv > self.ball_max_v:
                self.ball_max_v = bv

    def summary(self):
        return dict(v=self.v, i=self.i, x=self.x, e_dump=self.e_dump, e_kick=self.e_kick,
                    e_charge=self.e_charge, max_v=self.max_v, max_i=self.max_i, max_temp=self.max_temp,
                    ball_max_v=self.ball_max_v)
