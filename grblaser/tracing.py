# SPDX-License-Identifier: MIT
"""Pelacakan kontur bitmap (marching squares) dan penyederhanaan garis (RDP)."""
from __future__ import annotations

import numpy as np


def _rdp_open(pts, eps):
    n = len(pts)
    if n < 3:
        return pts
    keep = np.zeros(n, bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = pts[i + 1:j]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = float(np.hypot(dx, dy))
        if L < 1e-12:
            dist = np.hypot(seg[:, 0] - a[0], seg[:, 1] - a[1])
        else:
            dist = np.abs(dx * (seg[:, 1] - a[1]) - dy * (seg[:, 0] - a[0])) / L
        k = int(np.argmax(dist))
        if dist[k] > eps:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return pts[keep]


def rdp(pts, eps, closed=False):
    """Ramer-Douglas-Peucker. Untuk poligon tertutup, `pts` tanpa titik penutup."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 4 or eps <= 0:
        return pts
    if not closed:
        return _rdp_open(pts, eps)
    d = np.hypot(pts[:, 0] - pts[0, 0], pts[:, 1] - pts[0, 1])
    k = int(np.argmax(d))
    if k == 0:
        return pts
    a = _rdp_open(pts[:k + 1], eps)
    b = _rdp_open(np.vstack((pts[k:], pts[:1])), eps)
    out = np.vstack((a[:-1], b[:-1]))
    return out if len(out) >= 3 else pts


_TABLE = {
    1: [("L", "B")], 2: [("B", "R")], 3: [("L", "R")], 4: [("T", "R")],
    5: [("T", "R"), ("L", "B")], 6: [("T", "B")], 7: [("L", "T")],
    8: [("L", "T")], 9: [("T", "B")], 10: [("L", "T"), ("B", "R")],
    11: [("T", "R")], 12: [("L", "R")], 13: [("B", "R")], 14: [("L", "B")],
}
_BIG = 1 << 20


def _mid(edge, ii, jj):
    if edge == "T":
        return 2 * jj + 1, 2 * ii
    if edge == "R":
        return 2 * jj + 2, 2 * ii + 1
    if edge == "B":
        return 2 * jj + 1, 2 * ii + 2
    return 2 * jj, 2 * ii + 1


def trace_mask(mask):
    """Lacak batas area True pada `mask` (H x W).

    Mengembalikan daftar kontur tertutup (array N x 2, kolom x,y dalam
    koordinat indeks piksel; pusat piksel (i, j) = (j, i)).
    """
    m = np.pad((np.asarray(mask) > 0).astype(np.int32), 1)
    idx = m[:-1, :-1] * 8 + m[:-1, 1:] * 4 + m[1:, 1:] * 2 + m[1:, :-1]
    ka, kb = [], []
    for case, pairs in _TABLE.items():
        ii, jj = np.nonzero(idx == case)
        if ii.size == 0:
            continue
        for e1, e2 in pairs:
            x1, y1 = _mid(e1, ii, jj)
            x2, y2 = _mid(e2, ii, jj)
            ka.append(x1.astype(np.int64) * _BIG + y1)
            kb.append(x2.astype(np.int64) * _BIG + y2)
    if not ka:
        return []
    kA = np.concatenate(ka).tolist()
    kB = np.concatenate(kb).tolist()
    n = len(kA)
    nbr = {}
    for i in range(n):
        nbr.setdefault(kA[i], []).append(i)
        nbr.setdefault(kB[i], []).append(i)
    visited = bytearray(n)
    loops = []
    for s in range(n):
        if visited[s]:
            continue
        visited[s] = 1
        start, cur = kA[s], kB[s]
        chain = [start, cur]
        while cur != start:
            for t in nbr[cur]:
                if not visited[t]:
                    visited[t] = 1
                    cur = kB[t] if kA[t] == cur else kA[t]
                    chain.append(cur)
                    break
            else:
                break
        arr = np.array(chain, dtype=np.int64)
        if arr[0] == arr[-1]:
            arr = arr[:-1]
        if len(arr) >= 3:
            x = (arr // _BIG) / 2.0 - 1.0
            y = (arr % _BIG) / 2.0 - 1.0
            loops.append(np.column_stack((x, y)))
    return loops
