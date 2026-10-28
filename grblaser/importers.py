# SPDX-License-Identifier: MIT
"""Impor SVG, DXF dan gambar raster."""
from __future__ import annotations

import math

import numpy as np
from PIL import Image

from .model import PathShape, Poly

PX_TO_MM = 25.4 / 96.0


class ImportResult:
    def __init__(self):
        self.shapes = []      # PathShape; atribut src_color diisi
        self.warnings = []


def _hex(c):
    try:
        if c is None or c.value is None:
            return None
        return "#%02x%02x%02x" % (int(c.red), int(c.green), int(c.blue))
    except Exception:
        return None


def import_svg(path):
    from svgelements import (SVG, Path as SvgPath, Shape as SvgShape, Move, Close,
                             Line, SVGText, Image as SvgImage)
    res = ImportResult()
    svg = SVG.parse(path, reify=True, ppi=96.0)
    warned_text = warned_img = False
    for e in svg.elements():
        if isinstance(e, SVGText):
            if not warned_text:
                res.warnings.append("Teks SVG dilewati. Ubah teks menjadi path (Inkscape: Path > Object to Path) "
                                    "atau gunakan alat Teks di aplikasi ini.")
                warned_text = True
            continue
        if isinstance(e, SvgImage):
            if not warned_img:
                res.warnings.append("Gambar tertanam di SVG dilewati; impor gambar lewat menu Impor > Gambar.")
                warned_img = True
            continue
        if not isinstance(e, SvgShape):
            continue
        vals = getattr(e, "values", {}) or {}
        if str(vals.get("display", "")).strip() == "none" or str(vals.get("visibility", "")).strip() == "hidden":
            continue
        try:
            p = abs(SvgPath(e))
        except Exception:
            continue
        polys = []
        cur = []
        start = None

        def flush(closed):
            nonlocal cur
            if len(cur) >= 2:
                pts = np.array(cur, dtype=float)
                if not closed and len(pts) > 2 and np.hypot(*(pts[0] - pts[-1])) < 1e-3:
                    closed = True
                if closed and len(pts) > 1 and np.hypot(*(pts[0] - pts[-1])) < 1e-6:
                    pts = pts[:-1]
                if len(pts) >= 2:
                    polys.append(Poly(pts * PX_TO_MM, closed))
            cur = []

        for seg in p:
            if isinstance(seg, Move):
                flush(False)
                cur = [(float(seg.end.x), float(seg.end.y))]
                start = cur[0]
            elif isinstance(seg, Close):
                flush(True)
                cur = [start] if start else []
            elif isinstance(seg, Line):
                if not cur and seg.start is not None:
                    cur = [(float(seg.start.x), float(seg.start.y))]
                    start = start or cur[0]
                cur.append((float(seg.end.x), float(seg.end.y)))
            else:
                try:
                    L = float(seg.length(error=1e-2))
                except Exception:
                    L = 50.0
                n = int(max(6, min(400, L / 1.0)))
                if not cur and seg.start is not None:
                    cur = [(float(seg.start.x), float(seg.start.y))]
                    start = start or cur[0]
                for t in np.linspace(0, 1, n + 1)[1:]:
                    q = seg.point(float(t))
                    cur.append((float(q.x), float(q.y)))
        flush(False)
        polys = [pl for pl in polys if len(pl.pts) >= 2]
        if not polys:
            continue
        col = _hex(getattr(e, "stroke", None)) or _hex(getattr(e, "fill", None)) or "#000000"
        sh = PathShape(polys, kind="svg", name=str(vals.get("id", "") or ""))
        sh.src_color = col
        res.shapes.append(sh)
    if not res.shapes:
        res.warnings.append("Tidak ada bentuk vektor yang bisa dibaca dari file SVG.")
    return res


_DXF_UNITS = {0: 1.0, 1: 25.4, 2: 304.8, 3: 1609344.0, 4: 1.0, 5: 10.0, 6: 1000.0,
              7: 1e6, 8: 0.0000254, 9: 0.0254, 10: 914.4, 13: 0.001, 14: 100.0}


def import_dxf(path):
    import ezdxf
    from ezdxf import path as ezpath
    from ezdxf import colors as ezcolors
    res = ImportResult()
    doc = ezdxf.readfile(path)
    k = _DXF_UNITS.get(int(doc.units or 0), 1.0)
    if int(doc.units or 0) == 0:
        res.warnings.append("DXF tanpa satuan; diasumsikan milimeter.")
    msp = doc.modelspace()
    groups = {}
    skipped = set()

    def color_of(ent):
        try:
            if ent.dxf.hasattr("true_color"):
                r, g, b = ezcolors.int2rgb(ent.dxf.true_color)
                return "#%02x%02x%02x" % (r, g, b)
            aci = ent.dxf.color
            if aci in (0, 256):
                aci = doc.layers.get(ent.dxf.layer).dxf.color
            if aci in (7, 0, 256):
                return "#000000"
            r, g, b = ezcolors.aci2rgb(abs(int(aci)))
            return "#%02x%02x%02x" % (r, g, b)
        except Exception:
            return "#000000"

    def handle(ent, depth=0):
        t = ent.dxftype()
        if t == "INSERT" and depth < 6:
            try:
                for ve in ent.virtual_entities():
                    handle(ve, depth + 1)
            except Exception:
                pass
            return
        if t not in ("LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE"):
            skipped.add(t)
            return
        try:
            pth = ezpath.make_path(ent)
            subs = pth.sub_paths()
        except Exception:
            skipped.add(t)
            return
        col = color_of(ent)
        for sub in subs:
            pts = np.array([(v.x, v.y) for v in sub.flattening(0.02)], dtype=float)
            if len(pts) < 2:
                continue
            closed = bool(sub.is_closed)
            if closed and np.hypot(*(pts[0] - pts[-1])) < 1e-6:
                pts = pts[:-1]
            groups.setdefault(col, []).append(Poly(pts * k * np.array([1.0, -1.0]), closed))

    for ent in msp:
        handle(ent)
    allp = [p.pts for lst in groups.values() for p in lst]
    if not allp:
        res.warnings.append("Tidak ada entitas yang didukung di DXF (LINE, POLYLINE, CIRCLE, ARC, ELLIPSE, SPLINE).")
        return res
    allp = np.vstack(allp)
    mn = allp.min(axis=0)
    for col, lst in groups.items():
        for p in lst:
            p.pts = p.pts - mn
        sh = PathShape(lst, kind="dxf", name="DXF")
        sh.src_color = col
        res.shapes.append(sh)
    if skipped:
        res.warnings.append("Entitas DXF dilewati: " + ", ".join(sorted(skipped)))
    return res


def load_raster(path):
    """Kembalikan (gambar 'L', lebar_mm, tinggi_mm). Alpha dilebur ke putih."""
    im = Image.open(path)
    im.load()
    dpi = im.info.get("dpi", (96, 96))
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    g = im.convert("L")
    try:
        dx = float(dpi[0]) if float(dpi[0]) > 1 else 96.0
        dy = float(dpi[1]) if float(dpi[1]) > 1 else 96.0
    except Exception:
        dx = dy = 96.0
    return g, g.width * 25.4 / dx, g.height * 25.4 / dy
