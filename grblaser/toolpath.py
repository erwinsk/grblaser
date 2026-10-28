# SPDX-License-Identifier: MIT
"""Pembuat toolpath: garis (vektor), isian (fill) dan raster -> daftar operasi.

Operasi (tuple 4 elemen, koordinat ruang desain):
  ('R', x, y, 0)       gerak cepat (G0), laser mati
  ('F', x, y, S)       gerak potong/bakar (G1) dengan nilai S
  ('SF', feed, 0, 0)   atur kecepatan umpan (mm/menit)
  ('L', index, name, 0)  penanda awal layer
  ('A', 1|0, 0, 0)     air assist hidup/mati
  ('B', (kind, x, y, s), 0, 0)  blok besar (raster) berupa array numpy sejajar:
                       kind 0 = gerak cepat (R), 1 = gerak bakar (F); s = daya S
"""
from __future__ import annotations

import math

import numpy as np

from . import raster
from .model import PathShape, ImageShape, Poly
from .tracing import rdp

RAPID_GAP_MM = 2.0   # celah putih lebih panjang dari ini dilewati dengan G0


class Job:
    def __init__(self):
        self.ops = []
        self.bounds = None       # (x0, y0, x1, y1) area yang benar-benar dibakar
        self.time_s = 0.0
        self.feed_len = 0.0
        self.rapid_len = 0.0
        self.warnings = []
        self.layers_used = 0

    def empty(self):
        return not any(op[0] in ("F", "B") for op in self.ops)


def sval(dev, pct):
    return int(round(max(0.0, min(100.0, pct)) / 100.0 * dev.s_max))


# --------------------------------------------------------------------------
# Optimasi jalur garis
# --------------------------------------------------------------------------
def join_paths(polys, tol=0.02):
    """Gabungkan polyline terbuka yang ujungnya bertemu."""
    out = [p for p in polys if p.closed]
    opens = [p for p in polys if not p.closed and len(p.pts) >= 2]
    if not opens:
        return out
    grid = {}

    def cell(pt):
        return (int(math.floor(pt[0] / tol)), int(math.floor(pt[1] / tol)))

    for i, p in enumerate(opens):
        grid.setdefault(cell(p.pts[0]), []).append((i, 0))
        grid.setdefault(cell(p.pts[-1]), []).append((i, 1))
    used = [False] * len(opens)

    def find(pt):
        cx, cy = cell(pt)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for (i, e) in grid.get((cx + dx, cy + dy), ()):
                    if used[i]:
                        continue
                    q = opens[i].pts[0 if e == 0 else -1]
                    if abs(q[0] - pt[0]) <= tol and abs(q[1] - pt[1]) <= tol:
                        return i, e
        return None

    for i, p in enumerate(opens):
        if used[i]:
            continue
        used[i] = True
        chain = [p.pts]
        for side in (0, 1):
            while True:
                m = find(chain[-1][-1])
                if m is None:
                    break
                j, e = m
                used[j] = True
                pts = opens[j].pts
                if e == 1:
                    pts = pts[::-1]
                chain.append(pts[1:])
            if side == 0:
                chain = [np.vstack(chain)[::-1]]
        pts = np.vstack(chain)
        closed = len(pts) > 2 and abs(pts[0, 0] - pts[-1, 0]) <= tol and abs(pts[0, 1] - pts[-1, 1]) <= tol
        if closed:
            pts = pts[:-1]
        out.append(Poly(pts, closed))
    return out


def point_in_poly(pts, x, y):
    xs, ys = pts[:, 0], pts[:, 1]
    xs2, ys2 = np.roll(xs, -1), np.roll(ys, -1)
    cond = (ys > y) != (ys2 > y)
    dy = np.where(ys2 != ys, ys2 - ys, 1.0)
    xint = xs + (y - ys) * (xs2 - xs) / dy
    return bool(np.count_nonzero(cond & (x < xint)) % 2)


def _depths(polys):
    n = len(polys)
    depth = [0] * n
    closed = [i for i, p in enumerate(polys) if p.closed]
    if len(closed) > 1500 or len(closed) < 2:
        return depth
    bb = {i: (polys[i].pts[:, 0].min(), polys[i].pts[:, 1].min(),
              polys[i].pts[:, 0].max(), polys[i].pts[:, 1].max()) for i in closed}
    for i in closed:
        x, y = polys[i].pts[0]
        d = 0
        for j in closed:
            if i == j:
                continue
            b = bb[j]
            if b[0] <= x <= b[2] and b[1] <= y <= b[3] and point_in_poly(polys[j].pts, x, y):
                d += 1
        depth[i] = d
    return depth


def _nearest_order(polys, cur):
    n = len(polys)
    if n == 0:
        return []
    starts = np.array([p.pts[0] for p in polys])
    ends = np.array([p.pts[-1] for p in polys])
    is_open = np.array([not p.closed for p in polys])
    remaining = np.ones(n, bool)
    out = []
    cx, cy = cur
    for _ in range(n):
        d0 = np.hypot(starts[:, 0] - cx, starts[:, 1] - cy)
        d1 = np.hypot(ends[:, 0] - cx, ends[:, 1] - cy)
        d0 = np.where(remaining, d0, np.inf)
        d1 = np.where(remaining & is_open, d1, np.inf)
        i0, i1 = int(np.argmin(d0)), int(np.argmin(d1))
        if d0[i0] <= d1[i1]:
            i, rev = i0, False
        else:
            i, rev = i1, True
        remaining[i] = False
        p = polys[i]
        if rev:
            p = Poly(p.pts[::-1], False)
        elif p.closed and len(p.pts) > 3:
            k = int(np.argmin(np.hypot(p.pts[:, 0] - cx, p.pts[:, 1] - cy)))
            if k:
                p = Poly(np.roll(p.pts, -k, axis=0), True)
        out.append(p)
        cx, cy = (p.pts[0] if p.closed else p.pts[-1])
    return out


def order_paths(polys, inner_first=True):
    if len(polys) <= 1:
        return list(polys)
    depth = _depths(polys) if inner_first else [0] * len(polys)
    out = []
    cur = (0.0, 0.0)
    for d in sorted(set(depth), reverse=True):
        grp = [p for p, dd in zip(polys, depth) if dd == d]
        ordered = _nearest_order(grp, cur)
        out.extend(ordered)
        if ordered:
            last = ordered[-1]
            cur = tuple(last.pts[0] if last.closed else last.pts[-1])
    return out


# --------------------------------------------------------------------------
# Mode garis
# --------------------------------------------------------------------------
def line_ops(shapes, layer, dev):
    polys = []
    for sh in shapes:
        if isinstance(sh, PathShape):
            polys.extend(Poly(p.pts, p.closed) for p in sh.polys)
    if not polys:
        return []
    polys = join_paths(polys)
    polys = [Poly(rdp(p.pts, 0.005, p.closed) if len(p.pts) > 3 else p.pts, p.closed) for p in polys]
    polys = [p for p in polys if len(p.pts) >= 2]
    polys = order_paths(polys, layer.inner_first)
    s = sval(dev, layer.power)
    ops = []
    cur = None
    for p in polys:
        pts = p.pts
        first = pts[0]
        if cur is None or abs(cur[0] - first[0]) > 1e-4 or abs(cur[1] - first[1]) > 1e-4:
            ops.append(("R", float(first[0]), float(first[1]), 0))
        for q in pts[1:]:
            ops.append(("F", float(q[0]), float(q[1]), s))
        if p.closed:
            ops.append(("F", float(first[0]), float(first[1]), s))
            cur = first
        else:
            cur = pts[-1]
    return ops


# --------------------------------------------------------------------------
# Mode isi (fill)
# --------------------------------------------------------------------------
def fill_lines(poly_pts, interval, angle):
    """Garis pindai even-odd. Mengembalikan [(v, [(ua, ub), ...]), ...] dalam
    bingkai terputar (sumbu u searah garis pindai)."""
    th = math.radians(angle)
    c, s = math.cos(th), math.sin(th)
    X1, Y1, X2, Y2 = [], [], [], []
    for pts in poly_pts:
        if len(pts) < 3:
            continue
        x, y = pts[:, 0], pts[:, 1]
        xr = x * c + y * s
        yr = -x * s + y * c
        X1.append(xr)
        Y1.append(yr)
        X2.append(np.roll(xr, -1))
        Y2.append(np.roll(yr, -1))
    if not X1:
        return []
    X1, Y1, X2, Y2 = map(np.concatenate, (X1, Y1, X2, Y2))
    y0 = float(min(Y1.min(), Y2.min()))
    ymin = np.minimum(Y1, Y2)
    ymax = np.maximum(Y1, Y2)
    k0 = np.maximum(np.ceil((ymin - y0) / interval - 0.5).astype(np.int64), 0)
    k1 = np.ceil((ymax - y0) / interval - 0.5).astype(np.int64) - 1
    cnt = k1 - k0 + 1
    ok = (Y1 != Y2) & (cnt > 0)
    if not ok.any():
        return []
    X1, Y1, X2, Y2, k0, cnt = X1[ok], Y1[ok], X2[ok], Y2[ok], k0[ok], cnt[ok]
    total = int(cnt.sum())
    eidx = np.repeat(np.arange(len(cnt)), cnt)
    offs = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    k = k0[eidx] + offs
    yk = y0 + (k + 0.5) * interval
    x = X1[eidx] + (yk - Y1[eidx]) * (X2[eidx] - X1[eidx]) / (Y2[eidx] - Y1[eidx])
    order = np.lexsort((x, k))
    k, x = k[order], x[order]
    uk, start = np.unique(k, return_index=True)
    ends = np.append(start[1:], len(k))
    lines = []
    for kk, a, b in zip(uk, start, ends):
        xs = x[a:b]
        n = (len(xs) // 2) * 2
        segs = [(float(xs[i]), float(xs[i + 1])) for i in range(0, n, 2) if xs[i + 1] - xs[i] > 1e-6]
        if segs:
            lines.append((y0 + (float(kk) + 0.5) * interval, segs))
    return lines


def scan_ops(lines, angle, s, overscan, bidir):
    th = math.radians(angle)
    c, sn = math.cos(th), math.sin(th)

    def back(u, v):
        return float(u * c - v * sn), float(u * sn + v * c)

    ops = []
    flip = False
    for v, segs in lines:
        seq = [(b, a) for (a, b) in reversed(segs)] if flip else segs
        d = -1.0 if flip else 1.0
        first = seq[0][0]
        x, y = back(first - d * overscan, v)
        ops.append(("R", x, y, 0))
        if overscan > 0:
            x, y = back(first, v)
            ops.append(("F", x, y, 0))
        prev_end = first
        for i, (ua, ub) in enumerate(seq):
            if i > 0:
                x, y = back(ua, v)
                ops.append(("R" if abs(ua - prev_end) > RAPID_GAP_MM else "F", x, y, 0))
            x, y = back(ub, v)
            ops.append(("F", x, y, s))
            prev_end = ub
        if overscan > 0:
            x, y = back(prev_end + d * overscan, v)
            ops.append(("F", x, y, 0))
        if bidir:
            flip = not flip
    return ops


def fill_ops(shapes, layer, dev):
    s = sval(dev, layer.power)
    interval = max(layer.interval, 0.01)
    groups = []
    if layer.fill_group:
        pts = [p.pts for sh in shapes if isinstance(sh, PathShape) for p in sh.polys if p.closed]
        if pts:
            groups.append(pts)
    else:
        for sh in shapes:
            if isinstance(sh, PathShape):
                pts = [p.pts for p in sh.polys if p.closed]
                if pts:
                    groups.append(pts)
    angles = [layer.angle] + ([layer.angle + 90.0] if layer.crosshatch else [])
    ops = []
    for grp in groups:
        for ang in angles:
            ops.extend(scan_ops(fill_lines(grp, interval, ang), ang, s, layer.overscan, layer.bidir))
    return ops


# --------------------------------------------------------------------------
# Mode gambar (raster)
# --------------------------------------------------------------------------
def image_ops(shape, layer, dev):
    o = shape.opts
    if o.method == "trace":
        return []
    P = raster.prepare(shape.img, shape.x, shape.y, shape.w, shape.h, o)
    D = P.D
    pmax, pmin = layer.power, layer.min_power
    if o.method == "gray":
        pct = np.where(D > 0, pmin + D * (pmax - pmin), 0.0)
    else:
        pct = D * pmax
    S = np.rint(pct / 100.0 * dev.s_max).astype(np.int32)
    R, C = S.shape
    os_c = layer.overscan / P.px if P.px > 0 else 0.0
    gap_c = RAPID_GAP_MM / P.px if P.px > 0 else 1e9
    K, X, Y, V = [], [], [], []
    flip = False
    for r in range(R):
        row = S[r]
        nz = np.flatnonzero(row)
        if nz.size == 0:
            continue
        a0, b1 = int(nz[0]), int(nz[-1]) + 1
        seg = row[a0:b1]
        ch = np.flatnonzero(np.diff(seg)) + 1
        starts = np.concatenate(([0], ch)) + a0
        ends = np.concatenate((ch, [len(seg)])) + a0
        vals = row[starts]
        lengths = ends - starts
        if flip:
            targets = starts[::-1]
            lengths = lengths[::-1]
            vals = vals[::-1]
            first, d = float(b1), -1.0
        else:
            targets = ends
            first, d = float(a0), 1.0
        n = len(targets)
        head = 2 if os_c > 0 else 1
        cols = np.empty(head + n + (1 if os_c > 0 else 0))
        kind = np.ones(len(cols), np.uint8)
        sv = np.zeros(len(cols), np.int32)
        cols[0] = first - d * os_c
        kind[0] = 0
        if os_c > 0:
            cols[1] = first             # overscan masuk: F dengan S=0
        cols[head:head + n] = targets
        gap = (vals == 0) & (lengths > gap_c)
        kind[head:head + n] = np.where(gap, 0, 1)
        sv[head:head + n] = np.where(gap, 0, vals)
        if os_c > 0:
            cols[-1] = cols[-2] + d * os_c
        xs, ys = P.point(cols, r)
        K.append(kind)
        X.append(np.asarray(xs, dtype=float))
        Y.append(np.broadcast_to(np.asarray(ys, dtype=float), cols.shape))
        V.append(sv)
        if layer.bidir:
            flip = not flip
    if not K:
        return []
    return [("B", (np.concatenate(K), np.concatenate(X), np.concatenate(Y), np.concatenate(V)), 0, 0)]


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------
def build_job(project, only_ids=None):
    dev = project.device
    job = Job()
    for layer in project.layers:
        if not layer.output:
            continue
        shapes = [s for s in project.shapes
                  if s.layer == layer.index and (only_ids is None or s.id in only_ids)]
        if not shapes:
            continue
        if layer.mode == "line":
            lops = line_ops(shapes, layer, dev)
            if any(isinstance(s, ImageShape) for s in shapes):
                job.warnings.append(f"Layer '{layer.name}' (Garis) berisi gambar raster; gambar dilewati.")
        elif layer.mode == "fill":
            lops = fill_ops(shapes, layer, dev)
            if not lops and any(isinstance(s, PathShape) for s in shapes):
                job.warnings.append(f"Layer '{layer.name}' (Isi) hanya berisi garis terbuka; isian butuh bentuk tertutup. "
                                    "Ubah mode layer ke Garis atau tutup path-nya.")
            if any(isinstance(s, ImageShape) for s in shapes):
                job.warnings.append(f"Layer '{layer.name}' (Isi) berisi gambar raster; gambar dilewati.")
        else:
            lops = []
            for s in shapes:
                if isinstance(s, ImageShape):
                    lops.extend(image_ops(s, layer, dev))
            if any(isinstance(s, PathShape) for s in shapes):
                job.warnings.append(f"Layer '{layer.name}' (Gambar) berisi vektor; vektor dilewati.")
        if not lops:
            continue
        job.layers_used += 1
        job.ops.append(("L", layer.index, layer.name, 0))
        job.ops.append(("SF", float(layer.speed), 0, 0))
        if layer.air:
            job.ops.append(("A", 1, 0, 0))
        for _ in range(max(1, int(layer.passes))):
            job.ops.extend(lops)
        if layer.air:
            job.ops.append(("A", 0, 0, 0))
    compute_stats(job, dev)
    return job


def compute_stats(job, dev):
    x = y = None
    speed = 1000.0
    feed = rap = t = 0.0
    bxs = []                      # batas area yang benar-benar dibakar (S>0)
    axs = []                      # batas semua gerak bakar (cadangan)
    rr = max(dev.rapid_rate, 1.0)
    for tag, a, b, c in job.ops:
        if tag == "SF":
            speed = max(a, 1.0)
        elif tag == "R":
            if x is not None:
                d = math.hypot(a - x, b - y)
                rap += d
                t += d / rr * 60.0
            x, y = a, b
        elif tag == "F":
            if x is None:
                x, y = a, b
            d = math.hypot(a - x, b - y)
            feed += d
            t += d / speed * 60.0
            axs.append((a, b))
            if c > 0:
                bxs.append((x, y, a, b))
            x, y = a, b
        elif tag == "B":
            kind, bx, by, bs = a
            if x is None:
                x, y = float(bx[0]), float(by[0])
            px = np.concatenate(([x], bx[:-1]))
            py = np.concatenate(([y], by[:-1]))
            dist = np.hypot(bx - px, by - py)
            isr = kind == 0
            rd, fd = float(dist[isr].sum()), float(dist[~isr].sum())
            rap += rd
            feed += fd
            t += rd / rr * 60.0 + fd / speed * 60.0
            f = (~isr) & (bs > 0)
            if f.any():
                lo = np.minimum(px[f], bx[f]), np.minimum(py[f], by[f])
                hi = np.maximum(px[f], bx[f]), np.maximum(py[f], by[f])
                bxs.append((lo[0].min(), lo[1].min(), hi[0].max(), hi[1].max()))
            axs.append((float(bx.min()), float(by.min())))
            axs.append((float(bx.max()), float(by.max())))
            x, y = float(bx[-1]), float(by[-1])
    job.feed_len, job.rapid_len, job.time_s = feed, rap, t
    if bxs:
        arr = np.array([(min(a, c), min(b, d), max(a, c), max(b, d)) for a, b, c, d in bxs])
        job.bounds = (float(arr[:, 0].min()), float(arr[:, 1].min()), float(arr[:, 2].max()), float(arr[:, 3].max()))
    elif axs:
        arr = np.array(axs)
        job.bounds = (float(arr[:, 0].min()), float(arr[:, 1].min()), float(arr[:, 0].max()), float(arr[:, 1].max()))
