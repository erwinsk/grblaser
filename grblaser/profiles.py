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

    def ensure(self, dev):
        """Pastikan ada minimal satu profil (dari perangkat aktif) saat pertama kali dipakai."""
        if not self._load():
            self.save(dev)
