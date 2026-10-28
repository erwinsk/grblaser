# SPDX-License-Identifier: MIT
"""Pembuat bentuk parametrik (semua mengembalikan daftar Poly; satuan mm, Y ke bawah)."""
from __future__ import annotations

import math

import numpy as np

from .model import Poly, ellipse_poly, rect_poly


def rounded_rect(x0, y0, x1, y1, r):
    """Persegi dengan sudut membulat (r dibatasi setengah sisi terpendek)."""
    w, h = x1 - x0, y1 - y0
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    if r < 1e-6:
        return rect_poly(x0, y0, x1, y1)
    n = max(6, int(math.ceil(math.pi / 2 / math.acos(max(0.0, 1 - 0.02 / max(r, 0.05))))))
    n = min(n, 90)
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(n + 1):
            a = math.radians(a0 + 90.0 * k / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return Poly(pts, True)


def regular_polygon(cx, cy, radius, sides, rot_deg=-90.0):
    sides = max(3, int(sides))
    a = np.radians(rot_deg) + np.arange(sides) * 2 * math.pi / sides
    return Poly(np.column_stack((cx + radius * np.cos(a), cy + radius * np.sin(a))), True)


def star(cx, cy, radius, points, inner_ratio=0.5, rot_deg=-90.0):
    points = max(3, int(points))
    n = points * 2
    a = np.radians(rot_deg) + np.arange(n) * math.pi / points
    r = np.where(np.arange(n) % 2 == 0, radius, radius * max(0.05, min(inner_ratio, 0.98)))
    return Poly(np.column_stack((cx + r * np.cos(a), cy + r * np.sin(a))), True)


# --------------------------------------------------------------------------
# Bentuk siap pakai: semua digambar di kotak 0..size, lalu dipakai apa adanya
# --------------------------------------------------------------------------
def heart(size):
    t = np.linspace(0, 2 * math.pi, 180, endpoint=False)
    x = 16 * np.sin(t) ** 3
    y = -(13 * np.cos(t) - 5 * np.cos(2 * t) - 2 * np.cos(3 * t) - np.cos(4 * t))
    pts = np.column_stack((x, y))
    pts -= pts.min(axis=0)
    pts *= size / pts.max()
    return [Poly(pts, True)]


def gear(size, teeth=12, depth=0.16):
    r_out = size / 2.0
    r_in = r_out * (1 - depth)
    pts = []
    for i in range(teeth):
        a0 = 2 * math.pi * i / teeth
        step = 2 * math.pi / teeth
        for frac, r in ((0.0, r_in), (0.18, r_in), (0.28, r_out), (0.62, r_out), (0.72, r_in)):
            a = a0 + frac * step
            pts.append((r_out + r * math.cos(a), r_out + r * math.sin(a)))
    hole = ellipse_poly(r_out, r_out, size * 0.14, size * 0.14)
    return [Poly(pts, True), hole]


def ring(size, ratio=0.6):
    r = size / 2.0
    return [ellipse_poly(r, r, r, r), ellipse_poly(r, r, r * ratio, r * ratio)]


def plus(size, thick=0.34):
    a = size * (1 - thick) / 2
    b = size - a
    pts = [(a, 0), (b, 0), (b, a), (size, a), (size, b), (b, b), (b, size), (a, size), (a, b), (0, b), (0, a), (a, a)]
    return [Poly(pts, True)]


def arrow(size):
    w, h = size, size * 0.6
    pts = [(0, h * 0.3), (w * 0.6, h * 0.3), (w * 0.6, 0), (w, h / 2), (w * 0.6, h), (w * 0.6, h * 0.7), (0, h * 0.7)]
    return [Poly(pts, True)]


def crescent(size, offset=0.45, inner=0.8):
    """Bulan sabit = lingkaran A dikurangi lingkaran B (dihitung dari titik potong)."""
    R, rb, d = 1.0, inner, offset
    x0 = (d * d + R * R - rb * rb) / (2 * d)
    y0 = math.sqrt(max(R * R - x0 * x0, 1e-9))
    alpha = math.atan2(y0, x0)
    beta = math.atan2(y0, x0 - d)
    ta = np.linspace(alpha, 2 * math.pi - alpha, 120)
    tb = np.linspace(2 * math.pi - beta, beta, 120)
    arc_a = np.column_stack((R * np.cos(ta), R * np.sin(ta)))
    arc_b = np.column_stack((d + rb * np.cos(tb), rb * np.sin(tb)))
    pts = np.vstack((arc_a, arc_b[1:-1]))
    pts -= pts.min(axis=0)
    pts *= size / pts.max()
    return [Poly(pts, True)]


LIBRARY = {
    "Hati": heart,
    "Roda gigi": gear,
    "Cincin": ring,
    "Plus": plus,
    "Panah": arrow,
    "Bulan sabit": crescent,
}


# --------------------------------------------------------------------------
# Operasi boolean & offset (butuh pustaka opsional `shapely`)
# --------------------------------------------------------------------------
class ShapelyMissing(RuntimeError):
    pass


def _sh():
    try:
        import shapely  # noqa: F401
        from shapely import geometry, ops, validation
    except ImportError as e:   # pragma: no cover
        raise ShapelyMissing("Fitur ini butuh pustaka 'shapely' (pip install shapely)") from e
    return geometry, ops, validation


def available():
    try:
        _sh()
        return True
    except ShapelyMissing:
        return False


def to_geom(polys):
    """Poly tertutup -> geometri shapely (aturan even-odd, jadi lubang ikut benar). None bila tak ada."""
    geometry, _ops, validation = _sh()
    geom = None
    for p in polys:
        if not p.closed or len(p.pts) < 3:
            continue
        g = geometry.Polygon(p.pts)
        if not g.is_valid:
            g = validation.make_valid(g)
        geom = g if geom is None else geom.symmetric_difference(g)
    return geom


def from_geom(geom):
    """Geometri shapely -> daftar Poly tertutup (batas luar + lubang)."""
    out = []
    if geom is None or geom.is_empty:
        return out
    parts = getattr(geom, "geoms", [geom])
    for g in parts:
        if g.geom_type == "Polygon":
            for ring in [g.exterior] + list(g.interiors):
                pts = np.asarray(ring.coords)[:-1]
                if len(pts) >= 3:
                    out.append(Poly(pts, True))
        elif g.geom_type in ("MultiPolygon", "GeometryCollection"):
            out.extend(from_geom(g))
    return out


def boolean(op, groups):
    """op: 'union' | 'subtract' | 'intersect'. groups = [daftar Poly per objek]; objek pertama (paling bawah) = A."""
    _g, ops, _v = _sh()
    geoms = [to_geom(g) for g in groups]
    geoms = [g for g in geoms if g is not None]
    if len(geoms) < 2:
        return []
    if op == "union":
        res = ops.unary_union(geoms)
    elif op == "intersect":
        res = geoms[0]
        for g in geoms[1:]:
            res = res.intersection(g)
    elif op == "subtract":
        res = geoms[0].difference(ops.unary_union(geoms[1:]))
    else:
        raise ValueError(op)
    return from_geom(res)


def offset(polys, dist, arc_tol=0.02):
    """Offset kontur sejauh `dist` mm (positif = keluar). Garis terbuka menjadi kontur di sekelilingnya."""
    geometry, ops, _v = _sh()
    res = []
    closed = to_geom(polys)
    if closed is not None:
        res.append(closed.buffer(dist, quad_segs=16))
    if dist > 0:
        for p in polys:
            if not p.closed and len(p.pts) >= 2:
                res.append(geometry.LineString(p.pts).buffer(dist, quad_segs=16))
    if not res:
        return []
    return from_geom(ops.unary_union(res))
