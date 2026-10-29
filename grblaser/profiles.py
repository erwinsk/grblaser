# SPDX-License-Identifier: MIT
"""Profil perangkat (mesin laser): disimpan manual oleh pengguna di QSettings (JSON).

Satu profil = satu `Device` lengkap (area kerja, S maks, perintah laser, asal mesin, port/baud terakhir,
titik awal pekerjaan, dll). Kuncinya adalah `Device.name`."""
from __future__ import annotations

import json

from .model import Device


class Profiles:
    KEY = "profiles"

    def __init__(self, settings):
        self.s = settings

    def _load(self):
        try:
            d = json.loads(self.s.value(self.KEY, "{}"))
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _store(self, d):
        self.s.setValue(self.KEY, json.dumps(d))

    def names(self):
        return list(self._load())

    def get(self, name):
        d = self._load().get(name)
        return Device.from_dict(d) if d else None

    def save(self, dev):
        """Simpan/perbarui profil bernama `dev.name`."""
        d = self._load()
        d[dev.name] = dev.to_dict()
        self._store(d)

    def delete(self, name):
        d = self._load()
        if name in d and len(d) > 1:       # selalu sisakan minimal satu profil
            del d[name]
            self._store(d)
            return True
        return False

    def rename(self, old, new):
        """Ganti nama profil. Gagal bila nama baru kosong/sudah dipakai atau profil lama tidak ada."""
        new = (new or "").strip()
        d = self._load()
        if not new or old not in d or (new in d and new != old):
            return False
        if new != old:
            d = {(new if k == old else k): v for k, v in d.items()}      # urutan tetap
            d[new]["name"] = new
            self._store(d)
        return True

    def ensure(self, dev):
        """Pastikan ada minimal satu profil (dari perangkat aktif) saat pertama kali dipakai."""
        if not self._load():
            self.save(dev)
