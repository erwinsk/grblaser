# SPDX-License-Identifier: MIT
"""Marlin palsu berbasis pty: 'ok' per baris, M114 -> posisi, G0/G1 mengubah posisi."""
import os
import pty
import re
import select
import threading


class FakeMarlin:
    def __init__(self):
        self.master, self.slave = pty.openpty()
        self.port = os.ttyname(self.slave)
        self.lines = []
        self.x = self.y = 0.0
        self.alive = True
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _w(self, s):
        try:
            os.write(self.master, s.encode())
        except OSError:
            pass

    def _run(self):
        buf = b""
        while self.alive:
            r, _, _ = select.select([self.master], [], [], 0.05)
            if not r:
                continue
            try:
                buf += os.read(self.master, 1024)
            except OSError:
                break
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                ln = raw.decode(errors="ignore").strip()
                if not ln:
                    continue
                self.lines.append(ln)
                if ln.startswith("M114"):
                    self._w(f"X:{self.x:.2f} Y:{self.y:.2f} Z:0.00 E:0.00 Count X:0 Y:0 Z:0\r\nok\r\n")
                    continue
                m = re.match(r"G[01]\b", ln)
                if m:
                    for ax in "XY":
                        mm = re.search(ax + r"(-?[\d.]+)", ln)
                        if mm:
                            setattr(self, ax.lower(), float(mm.group(1)))
                self._w("ok\r\n")

    def close(self):
        self.alive = False
