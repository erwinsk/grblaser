# SPDX-License-Identifier: MIT
"""GRBL palsu berbasis pty untuk menguji pengirim tanpa perangkat keras.

Meniru: buffer RX 127 byte (menghitung overflow), 'ok' per baris, '?' status,
Ctrl-X reset, '!' hold, '~' resume, $J jog, $$ dan $H/$X.
"""
import os
import pty
import threading
import time
import re


class FakeGrbl:
    def __init__(self, process_delay=0.002, alarm_on=None, error_on=None):
        self.master, self.slave = pty.openpty()
        self.port = os.ttyname(self.slave)
        self.delay = process_delay
        self.rx = bytearray()          # buffer RX (belum diproses)
        self.lines = []                # baris yang sudah diproses
        self.max_rx = 0
        self.overflow = False
        self.x = self.y = 0.0
        self.state = "Idle"
        self.alarm_on = alarm_on
        self.error_on = error_on
        self.alive = True
        self.pending_ok = 0
        self.lock = threading.Lock()
        self.t1 = threading.Thread(target=self._reader, daemon=True)
        self.t2 = threading.Thread(target=self._worker, daemon=True)
        self.t1.start()
        self.t2.start()

    def _w(self, s):
        try:
            os.write(self.master, s.encode())
        except OSError:
            pass

    def _reader(self):
        import select
        while self.alive:
            r, _, _ = select.select([self.master], [], [], 0.05)
            if not r:
                continue
            try:
                data = os.read(self.master, 1024)
            except OSError:
                break
            for b in data:
                if b == ord("?"):
                    self._w(f"<{self.state}|MPos:{self.x:.3f},{self.y:.3f},0.000|FS:0,0>\r\n")
                elif b == 0x18:
                    with self.lock:
                        self.rx.clear()
                        self.state = "Idle"
                    self._w("\r\nGrbl 1.1h ['$' for help]\r\n")
                elif b == ord("!"):
                    self.state = "Hold:0"
                elif b == ord("~"):
                    self.state = "Idle"
                elif b == 0x85:
                    pass
                else:
                    with self.lock:
                        self.rx.append(b)
                        self.max_rx = max(self.max_rx, len(self.rx))
                        if len(self.rx) > 127:
                            self.overflow = True

    def _worker(self):
        while self.alive:
            time.sleep(self.delay)
            if self.state.startswith("Hold"):
                continue
            with self.lock:
                if b"\n" not in self.rx:
                    continue
                i = self.rx.index(b"\n")
                line = bytes(self.rx[:i]).decode()
                del self.rx[:i + 1]
            self.lines.append(line)
            if self.error_on and self.error_on in line:
                self._w("error:20\r\n")
                continue
            if self.alarm_on and self.alarm_on in line:
                self._w("ALARM:1\r\n")
                self.state = "Alarm"
                continue
            if line.startswith("$$"):
                self._w("$30=1000\r\n$32=1\r\n")
            m = re.findall(r"([XY])(-?\d+\.?\d*)", line)
            if not line.startswith("$J") and not line.startswith("$"):
                for k, v in m:
                    if k == "X":
                        self.x = float(v)
                    else:
                        self.y = float(v)
            self._w("ok\r\n")

    def close(self):
        self.alive = False
        for fd in (self.master, self.slave):
            try:
                os.close(fd)
            except OSError:
                pass
