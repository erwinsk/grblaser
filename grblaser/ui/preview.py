# SPDX-License-Identifier: MIT
"""Pratinjau hasil G-code: ubah operasi toolpath / G-code menjadi gambar RGBA
(satu gambar untuk seluruh bed) yang digambar di atas kanvas.

Garis bakar dirender sebagai *kerapatan energi* (panjang bakar x daya per luas),
bukan garis 1 piksel, sehingga hasil raster 0,1 mm (jauh lebih rapat dari piksel
layar) tetap terbaca sebagai abu-abu/dither, sementara garis potong tetap jelas."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw
from PyQt5.QtGui import QImage

from ..gcode import parse_gcode

DOSE_REF = 8.0        # mm bakar penuh per mm^2 yang dianggap "hitam penuh" (jarak 0,125 mm)
BURN_RGB = (205, 45, 20)


def ops_to_segments(ops):
    """Operasi toolpath -> array Nx6 (x0, y0, x1, y1, S, rapid) di ruang desain."""
    parts, cur = [], []
    x = y = None

    def flush():
        if cur:
            parts.append(np.array(cur, dtype=float).reshape(-1, 6))
            cur.clear()

    for tag, a, b, c in ops:
        if tag == "R":
            if x is not None and (a != x or b != y):
                cur.append((x, y, a, b, 0.0, 1))
            x, y = a, b
        elif tag == "F":
            if x is None:
                x, y = a, b
                continue
            if a != x or b != y:
                cur.append((x, y, a, b, float(c), 0))
            x, y = a, b
        elif tag == "B":
            flush()
            kind, bx, by, bs = a
            if x is None:
                x, y = float(bx[0]), float(by[0])
            px = np.concatenate(([x], bx[:-1]))
            py = np.concatenate(([y], by[:-1]))
            mv = (bx != px) | (by != py)
            blk = np.column_stack((px, py, bx, by, np.where(kind == 1, bs, 0).astype(float),
                                   (kind == 0).astype(float)))[mv]
            parts.append(blk)
            x, y = float(bx[-1]), float(by[-1])
    flush()
    return np.vstack(parts) if parts else np.zeros((0, 6))


def gcode_to_design_segments(lines, dev):
    """G-code (koordinat mesin, mode absolut) -> segmen di ruang desain."""
    segs = parse_gcode(lines)
    if len(segs) and dev.origin == "bottom-left":
        segs = segs.copy()
        segs[:, 1] = dev.bed_h - segs[:, 1]
        segs[:, 3] = dev.bed_h - segs[:, 3]
    return segs


def _energy_map(burn, scale, W, H, s_max):
    """Akumulasi (daya relatif x panjang mm) per piksel dengan sampling sepanjang segmen."""
    energy = np.zeros(W * H, dtype=np.float64)
    step_px = 0.6
    i = 0
    n_seg = len(burn)
    while i < n_seg:
        # ambil potongan segmen hingga ~3 juta sampel
        L_all = np.hypot(burn[i:i + 200000, 2] - burn[i:i + 200000, 0],
                         burn[i:i + 200000, 3] - burn[i:i + 200000, 1]) * scale
        n_all = np.maximum(1, np.ceil(L_all / step_px)).astype(np.int64)
        cum = np.cumsum(n_all)
        take = int(np.searchsorted(cum, 3_000_000, side="right"))
        take = max(1, min(take, len(n_all)))
        ch = burn[i:i + take]
        L = L_all[:take] / scale                      # mm
        n = n_all[:take]
        total = int(n.sum())
        idx = np.repeat(np.arange(take), n)
        start = np.cumsum(n) - n
        t = (np.arange(total) - start[idx] + 0.5) / n[idx]
        px = (ch[idx, 0] + t * (ch[idx, 2] - ch[idx, 0])) * scale
        py = (ch[idx, 1] + t * (ch[idx, 3] - ch[idx, 1])) * scale
        ix = np.clip(px.astype(np.int64), 0, W - 1)
        iy = np.clip(py.astype(np.int64), 0, H - 1)
        w = (np.clip(ch[idx, 4] / max(s_max, 1), 0, 1)) * (L[idx] / n[idx])
        energy += np.bincount(iy * W + ix, weights=w, minlength=W * H)
        i += take
    return energy.reshape(H, W)


def render_segments(segs, bed_w, bed_h, s_max=1000, max_px=2400, px_per_mm=6.0):
    """Kembalikan (QImage, skala px/mm). Latar transparan."""
    scale = min(px_per_mm, max_px / max(bed_w, bed_h, 1.0))
    W, H = max(1, int(bed_w * scale)), max(1, int(bed_h * scale))
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(base)
    arr = None
    if len(segs):
        rapid = segs[segs[:, 5] > 0]
        for x0, y0, x1, y1, _s, _r in rapid[:200000]:
            d.line((x0 * scale, y0 * scale, x1 * scale, y1 * scale), fill=(90, 130, 200, 110), width=1)
        feed = segs[segs[:, 5] == 0]
        zero = feed[feed[:, 4] <= 0]
        for x0, y0, x1, y1, _s, _r in zero[:100000]:
            d.line((x0 * scale, y0 * scale, x1 * scale, y1 * scale), fill=(150, 150, 150, 70), width=1)
        burn = feed[feed[:, 4] > 0]
        arr = np.asarray(base).copy()
        if len(burn):
            dose = _energy_map(burn, scale, W, H, s_max) * scale * scale   # mm bakar / mm^2
            dark = np.clip(dose / DOSE_REF, 0.0, 1.0)
            # garis tipis (potong) tetap terlihat jelas walau kerapatan rendah
            dark = np.where(dose > 0, np.maximum(dark, 0.55 * np.minimum(1.0, dose / (0.5 * scale))), 0.0)
            a_new = (dark * 245).astype(np.float32)
            a_old = arr[:, :, 3].astype(np.float32)
            a_out = a_new + a_old * (1 - a_new / 255.0)
            for k, c in enumerate(BURN_RGB):
                old = arr[:, :, k].astype(np.float32)
                arr[:, :, k] = np.where(a_new > 0, (c * a_new + old * a_old * (1 - a_new / 255.0)) / np.maximum(a_out, 1), old)
            arr[:, :, 3] = a_out.astype(np.uint8)
    if arr is None:
        arr = np.asarray(base).copy()
    arr = np.ascontiguousarray(arr)
    q = QImage(arr.data, W, H, W * 4, QImage.Format_RGBA8888).copy()
    return q, scale
