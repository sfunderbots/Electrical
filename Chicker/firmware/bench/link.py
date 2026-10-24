"""Talk to the chicker board over USB serial (or to the simulator with the same interface).

The board prints:
    T key=value ...      telemetry (period set with "tlm <ms>")
    E code=.. a=.. ...   events (kick fired, damp done, fault, ...)
    L ...                log lines,   C ...   console replies
Commands are console lines (see console.py): "charge", "kick 1000 now", "ceil 30", ...

    link = SerialLink("COM5")          # pip install pyserial; close Thonny first (it holds the port)
    link = SimLink()                   # same API, runs the firmware on the simulator
"""

import time


def parse_kv(line):
    out = {}
    for tok in line.split()[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            try:
                out[k] = float(v) if ("." in v or v.lstrip("-").isdigit()) else v
            except ValueError:
                out[k] = v
    return out


class Link:
    """Common logic: line buffering, telemetry/event history, waiting with a timeout."""

    def __init__(self):
        self.tlm = None              # last telemetry dict
        self.tlm_history = []        # (host_time, dict)
        self.events = []             # dicts
        self.logs = []               # raw lines
        self.on_line = None          # optional hook(line)

    # --- to implement ---------------------------------------------------------------
    def _write(self, line):
        raise NotImplementedError

    def _read_lines(self, max_wait_s):
        raise NotImplementedError

    def clock(self):
        return time.monotonic()

    # --- API --------------------------------------------------------------------------
    def send(self, line):
        self._write(line)

    def pump(self, max_wait_s=0.05):
        for line in self._read_lines(max_wait_s):
            line = line.strip()
            if not line:
                continue
            self.logs.append(line)
            if self.on_line:
                self.on_line(line)
            if line.startswith("T "):
                self.tlm = parse_kv(line)
                self.tlm_history.append((self.clock(), self.tlm))
            elif line.startswith("E "):
                self.events.append(parse_kv(line))

    def wait(self, pred, timeout_s, check=None):
        """Pump until pred(link) is true. check(link) runs on every pass and may raise to abort."""
        t_end = self.clock() + timeout_s
        while self.clock() < t_end:
            self.pump()
            if check:
                check(self)
            if pred(self):
                return True
        return False

    def close(self):
        pass


class SerialLink(Link):
    def __init__(self, port, baud=115200):
        Link.__init__(self)
        import serial                      # pip install pyserial
        self.ser = serial.Serial(port, baud, timeout=0.02)
        self._buf = b""
        self.ser.write(b"\r\n")

    def _write(self, line):
        self.ser.write((line + "\r\n").encode())

    def _read_lines(self, max_wait_s):
        t_end = time.monotonic() + max_wait_s
        lines = []
        while True:
            chunk = self.ser.read(4096)
            if chunk:
                self._buf += chunk
                *done, self._buf = self._buf.split(b"\n")
                lines += [d.decode(errors="replace") for d in done]
            if lines or time.monotonic() >= t_end:
                return lines

    def close(self):
        self.ser.close()


class SimLink(Link):
    """Same API on the simulator. `operator` hooks let a bench script be rehearsed end to end."""

    def __init__(self, sim=None, step_ms=20, **sim_kwargs):
        Link.__init__(self)
        if sim is None:
            from sim.harness import Sim
            sim = Sim(**sim_kwargs)
        self.sim = sim
        self.step_ms = step_ms
        self._seen = 0

    def clock(self):
        return self.sim.w.now / 1e9

    def _write(self, line):
        self.sim.usb(line)

    def _read_lines(self, max_wait_s):
        self.sim.run(self.step_ms)
        out = self.sim.w.usb_out
        new = [l for _, l in out[self._seen:]]
        self._seen = len(out)
        return new
