# SPDX-License-Identifier: MIT
"""Pustaka bahan: preset parameter layer (mode, kecepatan, daya, pass, ...) per mesin, disimpan di QSettings (JSON).

Tidak ada nilai bawaan: kecepatan/daya yang aman bergantung pada daya laser, fokus, dan bahan Anda. Isi pustaka dari
hasil uji Anda sendiri (lihat juga generator uji bahan), lalu bagikan lewat ekspor/impor berkas JSON.
"""
from __future__ import annotations

import json

from .model import Layer

# bidang Layer yang disimpan (tanpa index, name, color, output, visible)
FIELDS = ("mode", "speed", "power", "min_power", "passes", "air", "interval", "angle", "bidir",
          "overscan", "crosshatch", "fill_group", "inner_first")


def layer_params(layer):
    d = layer.to_dict()
    return {k: d[k] for k in FIELDS}


def apply_to_layer(layer, params):
    """Terapkan preset ke layer (hanya bidang dikenal & bertipe benar). Mengembalikan daftar bidang yang diubah."""
    base = Layer().to_dict()
    changed = []
    for k in FIELDS:
        if k not in params:
            continue
        v = params[k]
        try:
            v = type(base[k])(v)
        except (TypeError, ValueError):
            continue
        if k == "mode" and v not in ("line", "fill", "image"):
            continue
        if getattr(layer, k) != v:
            setattr(layer, k, v)
            changed.append(k)
    layer.power = max(0.0, min(100.0, layer.power))
    layer.min_power = max(0.0, min(100.0, layer.min_power))
    layer.speed = max(1.0, layer.speed)
    layer.passes = max(1, layer.passes)
    return changed


class Materials:
    KEY = "materials"

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

    def names(self, machine):
        return sorted(self._load().get(machine, {}), key=str.lower)

    def get(self, machine, name):
        return self._load().get(machine, {}).get(name)

    def save(self, machine, name, layer, note=""):
        name = (name or "").strip()
        if not name:
            return False
        d = self._load()
        d.setdefault(machine, {})[name] = {"params": layer_params(layer), "note": note}
        self._store(d)
        return True

    def delete(self, machine, name):
        d = self._load()
        if name in d.get(machine, {}):
            del d[machine][name]
            self._store(d)
            return True
        return False

    def export_json(self, machine):
        return json.dumps({"format": "grblaser-materials", "version": 1, "machine": machine,
                           "materials": self._load().get(machine, {})}, indent=2, ensure_ascii=False)

    def import_json(self, machine, text, overwrite=False):
        """Impor ke mesin `machine`. Mengembalikan jumlah preset yang ditambahkan (nama bentrok dilewati kecuali overwrite)."""
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("format") != "grblaser-materials":
            raise ValueError("Bukan berkas pustaka bahan Grblaser.")
        d = self._load()
        cur = d.setdefault(machine, {})
        n = 0
        for name, ent in (data.get("materials") or {}).items():
            if not isinstance(ent, dict) or not isinstance(ent.get("params"), dict):
                continue
            if name in cur and not overwrite:
                continue
            cur[str(name)] = {"params": {k: v for k, v in ent["params"].items() if k in FIELDS},
                              "note": str(ent.get("note", ""))}
            n += 1
        self._store(d)
        return n
