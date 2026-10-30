# SPDX-License-Identifier: MIT
"""Generator uji bahan: grid kotak dengan kecepatan berbeda per baris dan daya berbeda per kolom.

Tiap sel menjadi layer sendiri (nama berawalan `Uji `) sehingga parameternya mudah dibaca dan hasil terbaik bisa langsung
disimpan ke pustaka bahan (pilih layer sel terbaik → tombol Bahan…). Modul ini murni (tanpa Qt): teks label dibuat
lewat `label_fn(text, size_mm)` yang diberikan pemanggil.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import Layer, LAYER_COLORS, MAX_LAYERS, PathShape, Poly

PREFIX = "Uji "
TAG = "uji"


@dataclass
class TestParams:
    cols: int = 5                 # jumlah nilai daya (kolom)
    rows: int = 5                 # jumlah nilai kecepatan (baris)
    p_min: float = 10.0           # % daya
    p_max: float = 50.0
    v_min: float = 1000.0         # mm/menit
    v_max: float = 3000.0
    size: float = 10.0            # sisi kotak mm
    gap: float = 2.0
    mode: str = "fill"            # fill (ukir) | line (potong)
    interval: float = 0.1         # mm, jarak garis isian
    passes: int = 1
    labels: bool = True
    label_power: float = 15.0     # % daya untuk teks label
    label_speed: float = 3000.0
    label_size: float = 3.0       # mm


def _linspace(a, b, n):
    return [float(a)] if n <= 1 else [float(v) for v in np.linspace(a, b, n)]


def values(p: TestParams):
    pw = [round(v, 1) for v in _linspace(p.p_min, p.p_max, p.cols)]
    sp = [round(v) for v in _linspace(p.v_min, p.v_max, p.rows)]
    return pw, sp


def validate(p: TestParams, existing_layers=0):
    n = p.cols * p.rows + (1 if p.labels else 0)
    if p.cols < 1 or p.rows < 1:
        return "Jumlah baris/kolom minimal 1."
    if existing_layers + n > MAX_LAYERS:
        return (f"Butuh {n} layer baru, tetapi layer yang ada {existing_layers} dan batasnya {MAX_LAYERS}. "
                "Kurangi baris/kolom atau hapus layer.")
    if not (0 <= p.p_min <= 100 and 0 <= p.p_max <= 100):
        return "Daya harus 0–100%."
    if p.v_min <= 0 or p.v_max <= 0:
        return "Kecepatan harus > 0."
    if p.size <= 0 or p.gap < 0:
        return "Ukuran kotak harus > 0."
    return None


def extent(p: TestParams):
    """(lebar, tinggi) total grid termasuk margin label."""
    left = (p.label_size * 5 if p.labels else 0.0)       # ruang teks kecepatan di kiri
    top = (p.label_size * 1.8 if p.labels else 0.0)       # ruang teks daya di atas
    w = left + p.cols * p.size + (p.cols - 1) * p.gap
    h = top + p.rows * p.size + (p.rows - 1) * p.gap
    return w, h, left, top


def build(project, p: TestParams, x0, y0, label_fn=None):
    """Tambahkan layer & bentuk uji ke `project` (grid berpojok kiri-atas di x0, y0). Mengembalikan daftar id bentuk."""
    err = validate(p, len(project.layers))
    if err:
        raise ValueError(err)
    pw, sp = values(p)
    _w, _h, left, top = extent(p)
    ids = []
    pitch = p.size + p.gap
    ci = 0
    if p.labels:
        lab = project.add_layer()
        lab.name, lab.mode, lab.speed, lab.power, lab.passes = PREFIX + "label", "line", p.label_speed, p.label_power, 1
        lab.name = PREFIX + "label"
    for r, v in enumerate(sp):
        for c, pcent in enumerate(pw):
            L = project.add_layer()
            L.name = f"{PREFIX}{v:g}mm/m {pcent:g}%"
            L.color = LAYER_COLORS[ci % len(LAYER_COLORS)]
            L.mode, L.speed, L.power, L.min_power = p.mode, float(v), float(pcent), 0.0
            L.passes, L.interval = int(p.passes), float(p.interval)
            ci += 1
            x = x0 + left + c * pitch
            y = y0 + top + r * pitch
            poly = Poly(np.array([[x, y], [x + p.size, y], [x + p.size, y + p.size], [x, y + p.size]], float), True)
            sh = PathShape([poly], L.index, "rect", f"{TAG}-{v:g}-{pcent:g}")
            project.add(sh)
            ids.append(sh.id)
    if p.labels and label_fn is not None:
        def put(text, x, y):
            polys = label_fn(text, p.label_size)
            if not polys:
                return
            sh = PathShape(polys, lab.index, "text", f"{TAG}-label")
            bb = sh.bbox()
            sh.translate(x - bb[0], y - bb[1])
            project.add(sh)
            ids.append(sh.id)
        for c, pcent in enumerate(pw):
            put(f"{pcent:g}%", x0 + left + c * pitch, y0)
        for r, v in enumerate(sp):
            put(f"{v:g}", x0, y0 + top + r * pitch + p.size / 2 - p.label_size * 0.35)
    return ids


def remove_test(project):
    """Hapus semua bentuk & layer uji bahan. Mengembalikan (jumlah bentuk, jumlah layer)."""
    test_layers = [L.index for L in project.layers if L.name.startswith(PREFIX)]
    n_sh = len(project.shapes)
    project.shapes = [s for s in project.shapes if s.layer not in test_layers and not s.name.startswith(TAG + "-")]
    removed_layers = 0
    for idx in test_layers:
        if len(project.layers) > 1 and project.remove_layer(idx):
            removed_layers += 1
    return n_sh - len(project.shapes), removed_layers
