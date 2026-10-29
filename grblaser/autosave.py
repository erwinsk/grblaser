# SPDX-License-Identifier: MIT
"""Simpan otomatis & pemulihan setelah crash.

Salinan proyek ditulis berkala ke `$XDG_DATA_HOME/grblaser/` (bawaan `~/.local/share/grblaser/`) di thread latar,
dari *snapshot* proyek sehingga UI tidak membeku dan berkas asli pengguna tidak disentuh.
"""
from __future__ import annotations

import json
import os
import threading
import time

from .model import Device, Layer, Project
from .project import load_project, save_project


def default_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "grblaser")


def snapshot(project):
    """Salinan terpisah (bentuk di-clone) yang aman dibaca thread lain."""
    pr = Project()
    pr.device = Device.from_dict(project.device.to_dict())
    pr.layers = [Layer.from_dict(l.to_dict()) for l in project.layers]
    pr.shapes = [s.clone() for s in project.shapes]
    pr.path = None
    return pr


class Autosave:
    def __init__(self, directory=None, sync=False):
        self.dir = directory or default_dir()
        self.sync = sync                  # tes: tulis langsung tanpa thread
        self._lock = threading.Lock()
        self._thread = None
        self.file = os.path.join(self.dir, "autosave.glz")
        self.meta = os.path.join(self.dir, "autosave.json")

    # -- tulis ---------------------------------------------------------------
    def write(self, project):
        """Mulai penulisan; kembali False bila penulisan sebelumnya belum selesai."""
        if not project.shapes:
            return False
        if self._thread is not None and self._thread.is_alive():
            return False
        snap = snapshot(project)
        orig = project.path
        if self.sync:
            self._write(snap, orig)
            return True
        self._thread = threading.Thread(target=self._write, args=(snap, orig), daemon=True)
        self._thread.start()
        return True

    def _write(self, snap, orig):
        try:
            os.makedirs(self.dir, exist_ok=True)
            with self._lock:
                tmp = self.file + ".tmp"
                save_project(snap, tmp)
                os.replace(tmp, self.file)
                with open(self.meta, "w", encoding="utf-8") as f:
                    json.dump({"path": orig, "time": time.time()}, f)
        except Exception:  # noqa: BLE001 - simpan otomatis tidak boleh mengganggu pengguna
            pass

    def wait(self, timeout=10.0):
        t = self._thread
        if t is not None:
            t.join(timeout)

    # -- baca / hapus --------------------------------------------------------
    def exists(self):
        return os.path.isfile(self.file) and os.path.isfile(self.meta)

    def info(self):
        """(path asli atau None, waktu epoch) atau None."""
        if not self.exists():
            return None
        try:
            with open(self.meta, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("path"), float(d.get("time", 0))
        except Exception:  # noqa: BLE001
            return None

    def load(self):
        pr = load_project(self.file)
        inf = self.info()
        pr.path = inf[0] if inf and inf[0] and os.path.isfile(inf[0]) else None
        return pr

    def clear(self):
        self.wait(5.0)
        for p in (self.file, self.meta, self.file + ".tmp"):
            try:
                os.remove(p)
            except OSError:
                pass
