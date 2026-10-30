# SPDX-License-Identifier: MIT
"""Lanjutkan pekerjaan dari baris tertentu (setelah alarm, jeda lama, atau daya putus).

Bagian murni (`plan_resume`) membangun G-code lanjutan: header aman, air assist, gerak cepat dengan laser mati ke titik
terakhir, lalu sisa baris dengan kondisi modal (G0/G1, F, S) dipulihkan pada baris gerak pertama. `LastJob` menyimpan
job terakhir + kemajuan ke disk supaya masih bisa dilanjutkan setelah aplikasi/komputer mati.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

_WORD = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")
_COMMENT = re.compile(r"\(.*?\)|;.*$")
BACKOFF = 25         # baris dimundurkan dari titik berhenti: antrean planner GRBL sudah "ok" tetapi belum dijalankan


def _words(line):
    return [(c.upper(), float(v)) for c, v in _WORD.findall(_COMMENT.sub("", line))]


def _fmt(v):
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def scan_state(lines, n):
    """Keadaan modal setelah mengeksekusi lines[:n]: posisi, G0/G1, F, S, air assist, daya aktif."""
    st = {"x": None, "y": None, "g": None, "f": None, "s": 0.0, "air": False, "motion": 0}
    for ln in lines[:n]:
        w = _words(ln)
        moved = False
        for c, v in w:
            if c == "G" and v in (0.0, 1.0):
                st["g"] = int(v)
            elif c == "X":
                st["x"] = v
                moved = True
            elif c == "Y":
                st["y"] = v
                moved = True
            elif c == "F":
                st["f"] = v
            elif c == "S":
                st["s"] = v
            elif c == "M":
                if v in (7.0, 8.0):
                    st["air"] = True
                elif v == 9.0:
                    st["air"] = False
        if moved:
            st["motion"] += 1
    return st


def _is_motion(line):
    return any(c in ("X", "Y") for c, _ in _words(line))


def header_lines(lines):
    """Baris sebelum gerakan pertama (G21, G90, mode laser, ...), tanpa gerakan."""
    out = []
    for ln in lines:
        if _is_motion(ln):
            break
        if ln.strip():
            out.append(ln)
    return out


def plan_resume(lines, n, air_on="M8"):
    """Bangun G-code lanjutan mulai dari indeks `n` (0-based) pada `lines`.

    Mengembalikan (baris_baru, info). `info`: x, y, f, s, g, air, line (teks baris pertama yang dikerjakan), prefix (jumlah
    baris awalan sebelum sisa job), n (indeks dipakai).
    """
    n = max(0, min(int(n), len(lines)))
    st = scan_state(lines, n)
    hdr = header_lines(lines)
    out = list(hdr)
    laser_mode = next((l for l in hdr if re.match(r"\s*M[34]\b", l, re.I)), None)
    if st["air"]:
        out.append(air_on)
    if st["x"] is not None or st["y"] is not None:      # ke titik terakhir, laser mati (G0)
        parts = ["G0"]
        if st["x"] is not None:
            parts.append("X" + _fmt(st["x"]))
        if st["y"] is not None:
            parts.append("Y" + _fmt(st["y"]))
        out.append(" ".join(parts))
    prefix = len(out)
    rest = list(lines[n:])
    # pulihkan keadaan modal pada baris gerak pertama sisa job
    for k, ln in enumerate(rest):
        if not _is_motion(ln) and not any(c == "G" and v in (0.0, 1.0) for c, v in _words(ln)):
            continue
        w = _words(ln)
        has_g = any(c == "G" and v in (0.0, 1.0) for c, v in w)
        gnow = next((int(v) for c, v in w if c == "G" and v in (0.0, 1.0)), st["g"])
        add = []
        if not has_g and st["g"] is not None:
            add.append(f"G{st['g']}")
        if gnow == 1:
            if not any(c == "S" for c, _ in w) and st["s"]:
                add.append("S" + _fmt(st["s"]))
            if not any(c == "F" for c, _ in w) and st["f"] is not None:
                add.append("F" + _fmt(st["f"]))
        if add:
            body = ln.strip()
            m = re.match(r"(G[01])\b\s*(.*)", body, re.I)
            rest[k] = (" ".join([m.group(1).upper()] + add + ([m.group(2)] if m.group(2) else []))
                       if m else " ".join(add + [body]))
        break
    info = {"x": st["x"], "y": st["y"], "f": st["f"], "s": st["s"], "g": st["g"], "air": st["air"],
            "line": lines[n] if n < len(lines) else "", "prefix": prefix, "n": n, "laser_mode": laser_mode}
    return out + rest, info


def default_resume_index(acked, total, backoff=BACKOFF):
    """Indeks awal yang disarankan: mundur `backoff` baris dari yang sudah di-"ok"-kan."""
    return max(0, min(int(acked) - backoff, max(0, total - 1)))


class LastJob:
    """Job terakhir + kemajuan di disk (untuk lanjut setelah aplikasi/komputer mati)."""

    def __init__(self, directory):
        self.dir = directory
        self.gfile = os.path.join(directory, "last_job.gcode")
        self.meta = os.path.join(directory, "last_job.json")
        self._thread = None
        self._t_upd = 0.0

    def save(self, lines, info=None, sync=False):
        data = {"time": time.time(), "acked": 0, "total": len(lines), "done": False, **(info or {})}

        def work():
            try:
                os.makedirs(self.dir, exist_ok=True)
                tmp = self.gfile + ".tmp"
                with open(tmp, "w") as f:
                    f.write("\n".join(lines) + "\n")
                os.replace(tmp, self.gfile)
                self._write_meta(data)
            except OSError:
                pass
        if sync:
            work()
        else:
            self._thread = threading.Thread(target=work, daemon=True)
            self._thread.start()

    def _write_meta(self, data):
        try:
            tmp = self.meta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.meta)
        except OSError:
            pass

    def wait(self, t=10.0):
        if self._thread is not None:
            self._thread.join(t)

    def update(self, acked, done=False, force=False):
        """Perbarui kemajuan (dibatasi 1 kali / 2 detik agar tidak membebani disk)."""
        now = time.time()
        if not force and not done and now - self._t_upd < 2.0:
            return
        self._t_upd = now
        d = self.load_meta()
        if d is None:
            return
        d["acked"], d["done"] = int(acked), bool(done)
        self._write_meta(d)

    def load_meta(self):
        try:
            with open(self.meta, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def load(self):
        """(lines, meta) bila ada job tak selesai yang bisa dilanjutkan, selain itu None."""
        self.wait()
        d = self.load_meta()
        if not d or d.get("done") or not os.path.isfile(self.gfile):
            return None
        try:
            with open(self.gfile, "r") as f:
                lines = f.read().splitlines()
        except OSError:
            return None
        return lines, d

    def clear(self):
        self.wait()
        for p in (self.gfile, self.meta, self.gfile + ".tmp", self.meta + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass
