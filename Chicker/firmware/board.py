# Real hardware binding: pins, ADCs, PIO kicker and the MCP2515. The only firmware file (with
# kicker.py) that touches machine / rp2 / canbus directly; the simulator swaps those modules out.

import machine
import utime
from machine import Pin, ADC
from kicker import Kicker


class CanPort:
    """MCP2515 at 250 kbps with optional hardware ID filtering and a boot loopback self-test."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.ok = False
        self.selftest_msg = "not run"
        self.tx_errors = 0
        self._can = None
        try:
            from canbus import Can, CanError, CanMsg
            self._CanMsg = CanMsg
            can = Can()
            if can.begin() != CanError.ERROR_OK:
                self.selftest_msg = "MCP2515 begin() failed"
                return
            mask = cfg.CAN_FILTER_MASK if cfg.CAN_HW_FILTER else 0
            can.init_mask(0, 0, mask)
            can.init_mask(1, 0, mask)
            for f in range(6):
                can.init_filter(f, 0, cfg.CAN_FILTER_ID)
            self._can = can
            if cfg.CAN_LOOPBACK_SELFTEST:
                self.ok, self.selftest_msg = self._loopback_test(can)
                can.can.setNormalMode()
            else:
                self.ok, self.selftest_msg = True, "skipped"
        except Exception as e:  # noqa - any driver error leaves CAN disabled, USB still works
            self.selftest_msg = "CAN init error: %r" % (e,)
            self.ok = False

    def _loopback_test(self, can):
        """In loopback mode nothing reaches the bus. An in-block frame must come back, an
        out-of-block frame must not (when hardware filtering is on)."""
        drv = can.can
        if drv.setLoopbackMode() != 0:
            return False, "loopback mode refused"
        while can.checkReceive():
            can.recv()
        probe = b"CHKR"
        can.send(self._CanMsg(self.cfg.CAN_ID_SELFTEST, probe))
        got = self._wait_frame(can, 20)
        if got is None or got.can_id != self.cfg.CAN_ID_SELFTEST or bytes(got.data) != probe:
            return False, "loopback frame did not come back"
        if self.cfg.CAN_HW_FILTER:
            can.send(self._CanMsg(0x123, b"X"))
            if self._wait_frame(can, 10) is not None:
                return False, "hardware filter let 0x123 through"
        return True, "ok"

    @staticmethod
    def _wait_frame(can, timeout_ms):
        t0 = utime.ticks_ms()
        while utime.ticks_diff(utime.ticks_ms(), t0) < timeout_ms:
            if can.checkReceive():
                return can.recv()[1]
        return None

    def recv(self):
        """Return (can_id, data) or None."""
        if self._can is None or not self._can.checkReceive():
            return None
        err, msg = self._can.recv()
        if msg is None or msg.data is None:
            return None
        return msg.can_id, msg.data

    def send(self, can_id, data):
        if self._can is None or not self.cfg.CAN_TX_ENABLED:
            return False
        try:
            if self._can.send(self._CanMsg(can_id, data)) != 0:
                self.tx_errors += 1
                return False
            return True
        except Exception:  # noqa - a TX error must never stop the loop
            self.tx_errors += 1
            return False


class Board:
    def __init__(self, cfg):
        # Power-stage pins first, in the same state as the reset pull-downs (main.py did this too).
        self.charge = Pin(cfg.PIN_CHARGE, Pin.OUT, value=0)
        self.not_discharge = Pin(cfg.PIN_NOT_DISCHARGE, Pin.OUT, value=0)
        self.chip = Pin(cfg.PIN_CHIP, Pin.OUT, value=0)
        self.kick_gpio = Pin(cfg.PIN_KICK, Pin.OUT, value=0)   # real KICK, held low as GPIO until the PIO takes it
        self.led = None
        if cfg.KICK_OUTPUT_PIN != cfg.PIN_CAN_LED:
            self.led = Pin(cfg.PIN_CAN_LED, Pin.OUT, value=0)
        self.done = Pin(cfg.PIN_DONE, Pin.IN)
        self.beam = Pin(cfg.PIN_BREAKBEAM, Pin.IN)
        self.shell = Pin(cfg.PIN_SHELL_OFF, Pin.IN)
        self.adc_hv = ADC(Pin(cfg.PIN_ADC_HV))
        self.adc_batt = ADC(Pin(cfg.PIN_ADC_BATT))
        self.adc_5v = ADC(Pin(cfg.PIN_ADC_5V))
        self.kicker = Kicker(cfg, cfg.KICK_OUTPUT_PIN, cfg.KICK_PIO_SM)
        self.can = CanPort(cfg)
        self.reset_cause = machine.reset_cause()
        self._hv_n = cfg.HV_OVERSAMPLE

    def hv_raw(self):
        acc = 0
        for _ in range(self._hv_n):
            acc += self.adc_hv.read_u16()
        return acc // self._hv_n

    def watchdog(self, timeout_ms):
        return machine.WDT(timeout=timeout_ms)

    def safe_shutdown(self, deadtime_ms=50):
        """Emergency path: CHARGE low and kick pin low now, dump the bank after the dead time."""
        try:
            self.charge.value(0)
            self.chip.value(0)
            self.kicker.shutdown()
        finally:
            self.charge.value(0)
            utime.sleep_ms(deadtime_ms + 10)
            self.not_discharge.value(0)
