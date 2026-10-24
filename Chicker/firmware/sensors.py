# ADC conversions and the battery verdict. Pure apart from the raw reads done in app.py.


def raw_to_volts(cfg, raw_u16, divider, gain=1.0, offset=0.0):
    return raw_u16 * cfg.ADC_VREF / 65535.0 * divider * gain + offset


def hv_volts(cfg, raw_u16):
    return raw_to_volts(cfg, raw_u16, cfg.HV_DIVIDER, cfg.HV_GAIN_TRIM, cfg.HV_OFFSET_TRIM_V)


def batt_volts(cfg, raw_u16):
    return raw_to_volts(cfg, raw_u16, cfg.BATT_DIVIDER, cfg.BATT_GAIN_TRIM)


def v5_volts(cfg, raw_u16):
    return raw_to_volts(cfg, raw_u16, cfg.V5_DIVIDER)


class BatteryMonitor:
    """Charging allowed inside the battery window, with hysteresis and debounce.

    Old firmware flipped charge_ok on a single sample at 14.7 V; charging itself sags the pack by a
    few hundred mV, so it could chatter. Here it turns on above BATT_ON_MIN_V, stays on down to
    BATT_OFF_MIN_V, and needs BATT_DEBOUNCE_SAMPLES agreeing samples to change its mind.
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.ok = False
        self.volts = 0.0
        self._streak = 0

    def update(self, volts):
        c = self.cfg
        self.volts = volts
        low = c.BATT_OFF_MIN_V if self.ok else c.BATT_ON_MIN_V
        want = low < volts <= c.BATT_MAX_V
        if want != self.ok:
            self._streak += 1
            if self._streak >= c.BATT_DEBOUNCE_SAMPLES:
                self.ok = want
                self._streak = 0
        else:
            self._streak = 0
        return self.ok
