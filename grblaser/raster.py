# SPDX-License-Identifier: MIT
"""Pengolahan gambar raster -> peta kegelapan (darkness) untuk pembakaran
garis-per-garis, ala LaserGRBL: grayscale, pontilasi (dither), ambang, atau
vektorisasi kontur."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

from .model import ImageOptions, Poly
from .tracing import trace_mask, rdp

METHODS = [
    ("gray", "Grayscale (daya variabel, M4)"),
    ("threshold", "Ambang 1-bit"),
    ("floyd", "Dither Floyd-Steinberg"),
    ("atkinson", "Dither Atkinson (kontras tinggi)"),
    ("jarvis", "Dither Jarvis-Judice-Ninke (halus)"),
    ("stucki", "Dither Stucki (tajam)"),
    ("burkes", "Dither Burkes"),
    ("sierra", "Dither Sierra Lite (cepat)"),
    ("bayer4", "Dither terurut Bayer 4x4"),
    ("bayer", "Dither terurut Bayer 8x8"),
    ("halftone", "Halftone titik (koran)"),
    ("lines", "Halftone garis (engraving)"),
    ("trace", "Vektorisasi kontur (hasil berupa garis/isi)"),
]

DIFFUSION_METHODS = ("floyd", "atkinson", "jarvis", "stucki", "burkes", "sierra")

# kernel: (dx, dy, bobot), pembagi
_KERNELS = {
    "floyd": ([(1, 0, 7), (-1, 1, 3), (0, 1, 5), (1, 1, 1)], 16.0),
    "atkinson": ([(1, 0, 1), (2, 0, 1), (-1, 1, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)], 8.0),
    "jarvis": ([(1, 0, 7), (2, 0, 5), (-2, 1, 3), (-1, 1, 5), (0, 1, 7), (1, 1, 5), (2, 1, 3),
                (-2, 2, 1), (-1, 2, 3), (0, 2, 5), (1, 2, 3), (2, 2, 1)], 48.0),
    "stucki": ([(1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2),
                (-2, 2, 1), (-1, 2, 2), (0, 2, 4), (1, 2, 2), (2, 2, 1)], 42.0),
    "burkes": ([(1, 0, 8), (2, 0, 4), (-2, 1, 2), (-1, 1, 4), (0, 1, 8), (1, 1, 4), (2, 1, 2)], 32.0),
    "sierra": ([(1, 0, 2), (-1, 1, 1), (0, 1, 1)], 4.0),
}


def _bayer(n):
    m = np.array([[0]])
    while m.shape[0] < n:
        m = np.block([[4 * m, 4 * m + 2], [4 * m + 3, 4 * m + 1]])
    return (m + 0.5) / (m.size)


_BAYER8 = _bayer(8)
_BAYER4 = _bayer(4)


def diffuse(G, kind):
    """Error diffusion tervektorisasi (gelombang diagonal): semua piksel dengan
    t = x + k*y diproses serentak, sehingga jauh lebih cepat dari loop piksel Python.
    G: array 0..255 (255 = putih). Hasil: float32, 1 = bakar (gelap)."""
    kern, div = _KERNELS[kind]
    reach = max(abs(dx) for dx, dy, w in kern)
    k = reach + 1
    h, w = G.shape
    pad = 2
    stride = w + 2 * pad
    P = np.zeros((h + 3, stride), np.float32)
    P[:h, pad:pad + w] = G
    flat = P.reshape(-1)
    out = np.zeros((h, w), np.float32)
    offs = [(dy * stride + dx, wt / div) for dx, dy, wt in kern]
    for t in range(w + k * (h - 1)):
        y_lo = max(0, -((w - 1 - t) // k))
        y_hi = min(h - 1, t // k)
        if y_hi < y_lo:
            continue
        ys = np.arange(y_lo, y_hi + 1)
        xs = t - k * ys
        base = ys * stride + xs + pad
        old = flat[base]
        black = old < 128.0
        err = old - np.where(black, 0.0, 255.0).astype(np.float32)
        out[ys, xs] = black
        for off, wt in offs:
            flat[base + off] += err * wt
    return out


def _screen(G, cell_px, angle, lines):
    """Halftone klasik: ambang dari fungsi bintik (titik) atau gelombang (garis)."""
    h, w = G.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    th = math.radians(angle)
    ct, st = math.cos(th), math.sin(th)
    u = (xx * ct + yy * st) / cell_px
    v = (-xx * st + yy * ct) / cell_px
    if lines:
        thr = 0.5 + 0.5 * np.cos(2 * math.pi * v)
    else:
        thr = 0.5 + 0.25 * (np.cos(2 * math.pi * u) + np.cos(2 * math.pi * v))
    dark = 1.0 - G / 255.0
    return (dark > (1.0 - thr)).astype(np.float32)


def tone(G, o: ImageOptions):
    """Terapkan kecerahan/kontras/gamma/white-clip/invert pada array 0..255."""
    G = G.astype(np.float32)
    if o.brightness:
        G = G + o.brightness * 2.55
    if o.contrast:
        c = max(-254.0, min(254.0, o.contrast * 2.55))
        f = 259.0 * (c + 255.0) / (255.0 * (259.0 - c))
        G = f * (G - 128.0) + 128.0
    G = np.clip(G, 0, 255)
    if abs(o.gamma - 1.0) > 1e-6:
        G = 255.0 * np.power(G / 255.0, 1.0 / max(o.gamma, 0.05))
    if o.white_clip < 255:
        G = np.where(G >= o.white_clip, 255.0, G)
    if o.invert:
        G = 255.0 - G
    return G


def _grid(w_mm, h_mm, lpmm):
    pitch = 1.0 / max(lpmm, 0.05)
    cols = max(1, int(round(w_mm / pitch)))
    rows = max(1, int(round(h_mm / pitch)))
    return cols, rows


class Prepared:
    """Hasil pengolahan: D (R x C) kegelapan 0..1, geometri pemetaan ke desain."""

    def __init__(self, D, angle, px, py, cx, cy):
        self.D = D
        self.angle = angle
        self.px, self.py = px, py        # ukuran piksel (mm)
        self.cx, self.cy = cx, cy        # pusat gambar di ruang desain

    def point(self, c, r):
        """Titik desain untuk batas kolom `c` (float/array) pada baris `r`."""
        R, C = self.D.shape
        u = (np.asarray(c, dtype=float) - C / 2.0) * self.px
        v = (r + 0.5 - R / 2.0) * self.py
        th = math.radians(self.angle)
        ct, st = math.cos(th), math.sin(th)
        return self.cx + u * ct - v * st, self.cy + u * st + v * ct


def _filters(im, o: ImageOptions):
    """Filter pada resolusi keluaran (satu piksel = satu garis pindai)."""
    from PIL import ImageFilter
    if o.blur > 0:
        im = im.filter(ImageFilter.GaussianBlur(float(o.blur)))
    if o.sharpen > 0:
        im = im.filter(ImageFilter.UnsharpMask(radius=1.4, percent=int(o.sharpen), threshold=1))
    if o.autolevel:
        a = np.asarray(im, dtype=np.float32)
        lo, hi = np.percentile(a, 1.0), np.percentile(a, 99.0)
        if hi - lo > 8:
            a = np.clip((a - lo) * 255.0 / (hi - lo), 0, 255)
            im = Image.fromarray(a.astype(np.uint8), "L")
    return im


def prepare(img, x, y, w_mm, h_mm, o: ImageOptions, use_angle=True):
    cols, rows = _grid(w_mm, h_mm, o.lpmm)
    im = img.convert("L").resize((cols, rows), Image.LANCZOS)
    im = _filters(im, o)
    angle = float(o.angle) if use_angle else 0.0
    mask = None
    if abs(angle) > 1e-6:
        mask_im = Image.new("L", (cols, rows), 255).rotate(
            angle, expand=True, resample=Image.BILINEAR, fillcolor=0)
        im = im.rotate(angle, expand=True, resample=Image.BILINEAR, fillcolor=255)
        mask = np.asarray(mask_im) >= 128
    G = tone(np.asarray(im), o)
    m = o.method
    if m == "gray":
        L = max(2, int(o.gray_levels))
        D = (255.0 - G) / 255.0
        D = np.round(D * (L - 1)) / (L - 1)
    elif m in ("threshold", "trace"):
        D = (G < o.threshold).astype(np.float32)
    elif m == "floyd":
        b = Image.fromarray(G.astype(np.uint8), "L").convert("1")
        D = (np.asarray(b) == 0).astype(np.float32)
    elif m in DIFFUSION_METHODS:
        D = diffuse(G, m)
    elif m == "bayer":
        h, w = G.shape
        T = np.tile(_BAYER8 * 255.0, (h // 8 + 1, w // 8 + 1))[:h, :w]
        D = (G < T).astype(np.float32)
    elif m == "bayer4":
        h, w = G.shape
        T = np.tile(_BAYER4 * 255.0, (h // 4 + 1, w // 4 + 1))[:h, :w]
        D = (G < T).astype(np.float32)
    elif m in ("halftone", "lines"):
        cell_px = max(2.0, float(o.cell_mm) * float(o.lpmm))
        D = _screen(G, cell_px, float(o.cell_angle) - angle, m == "lines")
    else:
        D = (255.0 - G) / 255.0
    if mask is not None:
        D = D * mask
    D = D.astype(np.float32)
    if abs(angle) > 1e-6:
        p = (w_mm / cols + h_mm / rows) / 2.0
        px = py = p
    else:
        px, py = w_mm / cols, h_mm / rows
    return Prepared(D, angle, px, py, x + w_mm / 2.0, y + h_mm / 2.0)


def preview_gray(img, w_mm, h_mm, o: ImageOptions, max_px=900):
    """Array uint8 (255 = putih) untuk pratinjau; resolusi dibatasi."""
    o2 = ImageOptions.from_dict(o.to_dict())
    cols, rows = _grid(w_mm, h_mm, o2.lpmm)
    big = max(cols, rows)
    if big > max_px:
        o2.lpmm = o2.lpmm * max_px / big
    P = prepare(img, 0, 0, w_mm, h_mm, o2, use_angle=False)
    return (255.0 * (1.0 - P.D)).astype(np.uint8)


def trace_polys(img, x, y, w_mm, h_mm, o: ImageOptions, max_px=1400, eps_px=0.6):
    """Vektorisasi kontur: daftar Poly tertutup di ruang desain."""
    o2 = ImageOptions.from_dict(o.to_dict())
    cols, rows = _grid(w_mm, h_mm, o2.lpmm)
    big = max(cols, rows)
    if big > max_px:
        o2.lpmm = o2.lpmm * max_px / big
    P = prepare(img, x, y, w_mm, h_mm, o2, use_angle=False)
    loops = trace_mask(P.D > 0.5)
    out = []
    for lp in loops:
        if len(lp) < 4:
            continue
        if len(lp) >= 12:   # haluskan tangga piksel sebelum disederhanakan
            for _ in range(2):
                lp = (np.roll(lp, 1, axis=0) + 2.0 * lp + np.roll(lp, -1, axis=0)) / 4.0
        lp = rdp(lp, eps_px, closed=True)
        if len(lp) < 3:
            continue
        pts = np.column_stack((x + (lp[:, 0] + 0.5) * P.px, y + (lp[:, 1] + 0.5) * P.py))
        out.append(Poly(pts, True))
    return out
