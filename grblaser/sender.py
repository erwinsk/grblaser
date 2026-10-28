# SPDX-License-Identifier: MIT
"""Pengirim serial GRBL 1.1: streaming character-counting (buffer RX 127 byte),
polling status '?', jog, pause/resume/stop (realtime), homing, unlock."""
from __future__ import annotations

import re
import threading
import time
from collections import deque

import serial
import serial.tools.list_ports

from PyQt5.QtCore import QObject, pyqtSignal

RX_BUFFER = 127
STATUS_RE = re.compile(r"<([^|>]+)(?:\|([^>]*))?>")

ALARMS = {
    1: "Batas keras tercapai", 2: "Gerakan di luar jangkauan (soft limit)",
    3: "Reset saat bergerak - posisi hilang, lakukan homing", 4: "Probe gagal",
    5: "Probe gagal", 6: "Homing gagal: reset", 7: "Homing gagal: pintu terbuka",
    8: "Homing gagal: limit tidak lepas", 9: "Homing gagal: limit tidak ditemukan",
}
ERRORS = {
    1: "Huruf G-code tidak ada", 2: "Nilai tidak valid", 3: "Perintah $ tidak dikenal",
    8: "Perintah $ hanya saat idle", 9: "Terkunci (alarm) - gunakan Buka Kunci ($X) atau Home",
    20: "Perintah G-code tidak didukung", 22: "Feed belum diatur", 33: "Target gerak tidak valid",
}


def list_ports():
    return [p.device for p in sorted(serial.tools.list_ports.comports(), key=lambda p: p.device)]


class GrblSender(QObject):
    connected_changed = pyqtSignal(bool)
    log = pyqtSignal(str, str)            # arah ('tx'|'rx'|'info'|'err'), teks
    status = pyqtSignal(dict)             # state, mpos, wpos, feed, spindle, buf
    progress = pyqtSignal(int, int)       # terkirim/ok, total
    job_state = pyqtSignal(str)           # idle|running|paused|done|stopped|error
    alarm = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ser = None
        self._rx_thread = None
        self._tx_thread = None
        self._alive = False
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._lines = []
        self._sent = 0
        self._acked = 0
        self._pending = deque()   # panjang (byte) tiap baris yang belum di-ack
        self._pend_bytes = 0
        self._running = False
        self._paused = False
        self._stop_req = False
        self._hold_state = False
        self.state = "Disconnected"
        self.wco = (0.0, 0.0, 0.0)
        self.mpos = (0.0, 0.0, 0.0)
        self.wpos = (0.0, 0.0, 0.0)
        self._last_status = 0.0
        self.error_count = 0
        self.settings = {}
        self._awaiting_banner = False
        self._banner_deadline = 0.0
        self._quiet = False
        self._last_prog = 0.0

    # -- koneksi -----------------------------------------------------------
    @property
    def connected(self):
        return self.ser is not None and self.ser.is_open

    def connect(self, port, baud=115200):
        self.disconnect()
        try:
            self.ser = serial.Serial(port, baud, timeout=0.05, write_timeout=2)
        except Exception as e:
            self.ser = None
            self.log.emit("err", f"Gagal membuka {port}: {e}")
            return False
        self._alive = True
        # Tunggu banner "Grbl ..." hasil soft-reset sebelum status dianggap valid;
        # kalau tidak, banner yang telat bisa menghapus hitungan buffer di tengah job.
        self._awaiting_banner = True
        self._banner_deadline = time.time() + 2.0
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()
        self.state = "Connecting"
        self.connected_changed.emit(True)
        self.log.emit("info", f"Terhubung ke {port} @ {baud}")
        time.sleep(0.15)
        try:
            self.ser.write(b"\x18")   # soft reset -> banner GRBL
        except Exception:
            pass
        return True

    def disconnect(self):
        was = self.connected
        self._running = False
        self._stop_req = True
        with self._cond:
            self._cond.notify_all()
        self._alive = False
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None
        self.state = "Disconnected"
        if was:
            self.connected_changed.emit(False)
            self.log.emit("info", "Terputus")

    # -- tulis -------------------------------------------------------------
    def _write(self, data: bytes):
        if not self.connected:
            return False
        try:
            self.ser.write(data)
            return True
        except Exception as e:
            self.log.emit("err", f"Gagal menulis serial: {e}")
            return False

    def realtime(self, byte: int):
        self._write(bytes([byte]))

    def send_line(self, line: str):
        """Kirim satu perintah manual (hanya saat tidak ada job berjalan)."""
        if not self.connected:
            self.log.emit("err", "Belum terhubung")
            return False
        if self._running:
            self.log.emit("err", "Job sedang berjalan")
            return False
        line = line.strip()
        if not line:
            return False
        self.log.emit("tx", line)
        return self._write((line + "\n").encode())

    def send_lines(self, lines):
        """Kirim beberapa baris singkat (mis. bingkai) lewat jalur streaming."""
        self.start_job(lines, quiet=True)

    # -- realtime & kontrol -------------------------------------------------
    def hold(self):
        self.realtime(0x21)
        self._paused = True
        self.job_state.emit("paused")

    def resume(self):
        self.realtime(0x7E)
        self._paused = False
        if self._running:
            self.job_state.emit("running")

    def stop(self):
        """Hentikan job: feed hold, buang antrian (soft reset), laser mati."""
        if not self.connected:
            return
        self._stop_req = True
        self._running = False
        self.realtime(0x21)
        time.sleep(0.12)
        self.realtime(0x18)
        with self._cond:
            self._pending.clear()
            self._pend_bytes = 0
            self._cond.notify_all()
        self.job_state.emit("stopped")
        self.log.emit("info", "Job dihentikan (reset GRBL). Posisi mungkin hilang bila sedang bergerak.")

    def jog(self, dx=0.0, dy=0.0, feed=3000.0):
        if not self.connected or self._running:
            return
        parts = ["$J=G21G91"]
        if dx:
            parts.append(f"X{dx:.3f}")
        if dy:
            parts.append(f"Y{dy:.3f}")
        parts.append(f"F{feed:.0f}")
        self._write(("".join(parts) + "\n").encode())

    def jog_cancel(self):
        self.realtime(0x85)

    def home(self):
        self.send_line("$H")

    def unlock(self):
        self.send_line("$X")

    def zero_here(self):
        self.send_line("G10 L20 P1 X0 Y0")

    def goto_zero(self):
        self.send_line("G90 G0 X0 Y0")

    def reset(self):
        self.realtime(0x18)

    def laser_test(self, pct, smax, cmd="M3"):
        s = int(round(pct / 100.0 * smax))
        self.send_line(f"{cmd} S{s}")

    def laser_off(self):
        self.send_line("M5")

    def request_settings(self):
        self.send_line("$$")

    # -- job -----------------------------------------------------------------
    def start_job(self, lines, quiet=False, prepared=False):
        if not self.connected:
            self.log.emit("err", "Belum terhubung")
            return False
        if self._running:
            self.log.emit("err", "Job sedang berjalan")
            return False
        if self.state.startswith("Alarm"):
            self.log.emit("err", "GRBL dalam kondisi ALARM. Lakukan Home atau Buka Kunci dulu.")
            return False
        if not prepared:      # `prepared` = sudah bersih (tanpa komentar/baris kosong), lewati satu pass besar
            lines = [l for l in lines if l and l.strip()]
        if not lines:
            return False
        with self._cond:
            self._lines = lines
            self._sent = 0
            self._acked = 0
            self._pending.clear()
            self._pend_bytes = 0
            self.error_count = 0
            self._stop_req = False
            self._paused = False
            self._running = True
        self._quiet = quiet
        self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self._tx_thread.start()
        self.job_state.emit("running")
        self.progress.emit(0, len(lines))
        return True

    def _tx_loop(self):
        total = len(self._lines)
        while self._running and self.connected:
            with self._cond:
                if self._sent >= total:
                    if self._acked >= total:
                        break
                    self._cond.wait(0.05)
                    continue
                line = self._lines[self._sent]
                need = len(line) + 1
                if self._pend_bytes + need > RX_BUFFER or self._paused and False:
                    self._cond.wait(0.02)
                    continue
                self._pending.append(need)
                self._pend_bytes += need
                self._sent += 1
            if not self._write((line + "\n").encode()):
                self._running = False
                self.job_state.emit("error")
                return
            if not self._quiet:
                self.log.emit("tx", line)
        if self._running and not self._stop_req:
            self._running = False
            self.job_state.emit("done")
            self.log.emit("info", f"Selesai ({total} baris, {self.error_count} error)")

    # -- baca ------------------------------------------------------------------
    def _rx_loop(self):
        buf = b""
        last_poll = 0.0
        while self._alive and self.ser is not None:
            try:
                chunk = self.ser.read(256)
            except Exception as e:
                if self._alive:
                    self.log.emit("err", f"Serial terputus: {e}")
                    self._running = False
                    self.job_state.emit("error")
                    self.ser = None
                    self.state = "Disconnected"
                    self.connected_changed.emit(False)
                break
            if chunk:
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode("ascii", "ignore").strip()
                    if line:
                        self._handle(line)
            now = time.time()
            if now - last_poll > 0.2 and self.connected:
                last_poll = now
                try:
                    self.ser.write(b"?")
                except Exception:
                    pass

    def _handle(self, line):
        if line.startswith("<"):
            self._parse_status(line)
            return
        if line == "ok" or line.startswith("error:"):
            err = line.startswith("error:")
            with self._cond:
                if self._pending:
                    self._pend_bytes -= self._pending.popleft()
                    self._acked += 1
                    total = len(self._lines)
                    acked = self._acked
                else:
                    total = acked = 0
                self._cond.notify_all()
            if err:
                self.error_count += 1
                try:
                    code = int(line.split(":")[1])
                except Exception:
                    code = 0
                if self._running and 0 < acked <= len(self._lines):
                    ln = self._lines[acked - 1]
                    self.log.emit("err", f"{line} ({ERRORS.get(code, '')}) pada baris {acked}: {ln}")
                else:
                    self.log.emit("err", f"{line} {ERRORS.get(code, '')}")
            elif not self._quiet_ok():
                self.log.emit("rx", line)
            if self._running and total:
                now = time.time()
                if now - self._last_prog > 0.1 or acked >= total:   # jangan banjiri UI
                    self._last_prog = now
                    self.progress.emit(acked, total)
            return
        if line.startswith("ALARM:"):
            try:
                code = int(line.split(":")[1])
            except Exception:
                code = 0
            msg = ALARMS.get(code, "")
            self.log.emit("err", f"{line} {msg}")
            self._running = False
            self.alarm.emit(f"{line} {msg}")
            self.job_state.emit("error")
            return
        if line.startswith("Grbl"):
            self.log.emit("info", line)
            self._awaiting_banner = False
            with self._cond:
                self._pending.clear()
                self._pend_bytes = 0
            return
        if line.startswith("$") and "=" in line:
            try:
                k, v = line.split("=", 1)
                self.settings[k] = v.split()[0]
            except Exception:
                pass
        self.log.emit("rx", line)

    def _quiet_ok(self):
        return True  # jangan banjiri log dengan 'ok'

    def _parse_status(self, line):
        if self._awaiting_banner:
            if time.time() < self._banner_deadline:
                return
            self._awaiting_banner = False   # perangkat tidak mengirim banner
        m = STATUS_RE.match(line)
        if not m:
            return
        state = m.group(1)
        fields = {}
        for part in (m.group(2) or "").split("|"):
            if ":" in part:
                k, v = part.split(":", 1)
                fields[k] = v
        try:
            if "WCO" in fields:
                self.wco = tuple(float(v) for v in fields["WCO"].split(","))[:3]
            if "MPos" in fields:
                self.mpos = tuple(float(v) for v in fields["MPos"].split(","))[:3]
                self.wpos = tuple(a - b for a, b in zip(self.mpos, self.wco))
            elif "WPos" in fields:
                self.wpos = tuple(float(v) for v in fields["WPos"].split(","))[:3]
                self.mpos = tuple(a + b for a, b in zip(self.wpos, self.wco))
        except ValueError:
            return
        self.state = state
        info = {"state": state, "mpos": self.mpos, "wpos": self.wpos}
        if "FS" in fields:
            try:
                f, s = fields["FS"].split(",")[:2]
                info["feed"], info["spindle"] = float(f), float(s)
            except ValueError:
                pass
        elif "F" in fields:
            try:
                info["feed"] = float(fields["F"])
            except ValueError:
                pass
        if "Bf" in fields:
            info["buf"] = fields["Bf"]
        if state.startswith("Hold") and self._running:
            self._paused = True
        self.status.emit(info)
