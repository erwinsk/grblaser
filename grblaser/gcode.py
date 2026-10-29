# SPDX-License-Identifier: MIT
"""Konversi operasi toolpath -> G-code GRBL, plus utilitas (parse, bingkai)."""
from __future__ import annotations

import re

import numpy as np


def fmt(v):
    s = ("%.3f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def fmt_array(arr):
    """Format array float -> daftar string; nilai unik diformat sekali saja."""
    arr = np.round(np.asarray(arr, dtype=float), 3) + 0.0
    uniq, inv = np.unique(arr, return_inverse=True)
    strs = [fmt(v) for v in uniq]
    return [strs[i] for i in inv.tolist()]


def anchor_point(bounds, anchor):
    x0, y0, x1, y1 = bounds
    col, row = anchor % 3, anchor // 3
    return x0 + (x1 - x0) * col / 2.0, y0 + (y1 - y0) * row / 2.0


def make_transform(dev, bounds, start_from=None, anchor=None, current=None, mirror=(False, False)):
    """Kembalikan fungsi T(x, y) -> (X, Y) mesin.

    start_from='absolute' : desain (0,0) kiri-atas bed == pojok bed pada koordinat kerja.
    start_from='current'  : titik jangkar desain diletakkan di posisi laser saat ini.
    mirror=(mx, my)       : cerminkan pekerjaan terhadap pusat kotak pembatasnya (kotaknya tetap di tempat), mis. untuk
                            mengukir dari sisi belakang kaca/akrilik. Berlaku sebelum penempatan di atas.
    """
    start_from = start_from or dev.start_from
    anchor = dev.anchor if anchor is None else anchor
    sign = -1.0 if dev.origin == "bottom-left" else 1.0
    if start_from == "current" and bounds is not None and current is not None:
        ax, ay = anchor_point(bounds, anchor)
        wx, wy = current

        def T(x, y):
            return (wx + (x - ax), wy + sign * (y - ay))
        T.origin = (wx, wy)
    else:
        bed_h = dev.bed_h
        if dev.origin == "bottom-left":
            def T(x, y):
                return (x, bed_h - y)
        else:
            def T(x, y):
                return (x, y)
        T.origin = (0.0, 0.0)
    mx, my = mirror
    if (mx or my) and bounds is not None:
        sx, sy = bounds[0] + bounds[2], bounds[1] + bounds[3]
        T0 = T

        def T(x, y):                       # noqa: F811 - bekerja juga untuk array numpy
            return T0(sx - x if mx else x, sy - y if my else y)
        T.origin = T0.origin
    return T


def machine_bounds(T, bounds):
    x0, y0, x1, y1 = bounds
    pts = [T(x0, y0), T(x1, y0), T(x1, y1), T(x0, y1)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def emit_gcode(job, dev, T, comments=True, title="Grblaser"):
    out = []
    if comments:
        out.append(f"; {title}")
    out += ["G21", "G90", "G17", "G94", f"{dev.laser_cmd} S0"]
    st = {"lx": None, "ly": None, "cs": 0, "cf": None, "pf": None, "g": None}
    append = out.append

    def emit_block(kind, bx, by, bs):
        """Blok raster: koordinat diubah vektor, lalu string disusun per baris."""
        X, Y = T(bx, by)
        xs, ys = fmt_array(X), fmt_array(Y)
        kinds, svals = kind.tolist(), bs.tolist()
        lx, ly, cs, cf, pf, g = st["lx"], st["ly"], st["cs"], st["cf"], st["pf"], st["g"]
        for k, sx, sy, sv in zip(kinds, xs, ys, svals):
            if k == 0:
                if sx == lx and sy == ly:
                    continue
                if g != 0:
                    g = 0
                    line = "G0 "
                else:
                    line = ""
                if sx != lx:
                    line += "X" + sx
                    if sy != ly:
                        line += " Y" + sy
                else:
                    line += "Y" + sy
                append(line)
                lx, ly = sx, sy
            else:
                mx, my = sx != lx, sy != ly
                if not mx and not my and sv == cs:
                    continue
                parts = []
                if g != 1:
                    g = 1
                    parts.append("G1")
                if mx:
                    parts.append("X" + sx)
                if my:
                    parts.append("Y" + sy)
                if sv != cs:
                    parts.append("S%d" % sv)
                    cs = sv
                if pf is not None and pf != cf:
                    parts.append("F" + fmt(pf))
                    cf = pf
                append(" ".join(parts))
                lx, ly = sx, sy
        st.update(lx=lx, ly=ly, cs=cs, cf=cf, g=g)

    for tag, a, b, c in job.ops:
        if tag == "SF":
            st["pf"] = a
        elif tag == "L":
            if comments:
                append(f"; layer {a + 1}: {b}")
        elif tag == "A":
            append(dev.air_on if a else dev.air_off)
        elif tag == "B":
            emit_block(*a)
        elif tag == "R":
            X, Y = T(a, b)
            sx, sy = fmt(X), fmt(Y)
            if sx == st["lx"] and sy == st["ly"]:
                continue
            parts = []
            if st["g"] != 0:
                parts.append("G0")
                st["g"] = 0
            if sx != st["lx"]:
                parts.append("X" + sx)
            if sy != st["ly"]:
                parts.append("Y" + sy)
            append(" ".join(parts))
            st["lx"], st["ly"] = sx, sy
        elif tag == "F":
            X, Y = T(a, b)
            sx, sy = fmt(X), fmt(Y)
            sv = int(c)
            moved = (sx != st["lx"]) or (sy != st["ly"])
            if not moved and sv == st["cs"]:
                continue
            parts = []
            if st["g"] != 1:
                parts.append("G1")
                st["g"] = 1
            if moved:
                if sx != st["lx"]:
                    parts.append("X" + sx)
                if sy != st["ly"]:
                    parts.append("Y" + sy)
            if sv != st["cs"]:
                parts.append("S%d" % sv)
                st["cs"] = sv
            if st["pf"] is not None and st["pf"] != st["cf"]:
                parts.append("F" + fmt(st["pf"]))
                st["cf"] = st["pf"]
            append(" ".join(parts))
            st["lx"], st["ly"] = sx, sy
    append("M5")
    if dev.end_return == "origin":
        ox, oy = T.origin
        append(f"G0 X{fmt(ox)} Y{fmt(oy)}")
    if comments:
        append("; fim")
    return out


def frame_lines(dev, T, bounds):
    x0, y0, x1, y1 = bounds
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    pts = [T(*c) for c in corners]
    lines = ["G21", "G90"]
    lines.append(f"G0 X{fmt(pts[0][0])} Y{fmt(pts[0][1])}")
    if dev.frame_laser and dev.frame_power > 0:
        s = int(round(dev.frame_power / 100.0 * dev.s_max))
        lines.append(f"{dev.laser_cmd} S{s}")
        first = True
        for p in pts[1:]:
            lines.append(f"G1 X{fmt(p[0])} Y{fmt(p[1])}" + (f" F{fmt(dev.frame_speed)}" if first else ""))
            first = False
        lines.append("M5")
    else:
        for p in pts[1:]:
            lines.append(f"G0 X{fmt(p[0])} Y{fmt(p[1])}")
    return lines


_COMMENT = re.compile(r"\(.*?\)|;.*$")
_WORD = re.compile(r"([A-Za-z])\s*(-?\d*\.?\d+)")


def clean_gcode(lines):
    """Buang komentar & baris kosong (siap dikirim ke GRBL)."""
    out = []
    for ln in lines:
        ln = _COMMENT.sub("", ln).strip()
        if ln:
            out.append(ln)
    return out


def parse_gcode(lines):
    """Parser sederhana (G0/G1, G90/G91, X, Y, S) -> array Nx6:
    x0, y0, x1, y1, S, rapid(1/0). Dipakai untuk pratinjau & pengujian."""
    x = y = 0.0
    s = 0.0
    absolute = True
    g = 0
    segs = []
    for line in clean_gcode(lines):
        nx, ny = x, y
        for letter, val in _WORD.findall(line):
            L = letter.upper()
            v = float(val)
            if L == "G":
                gi = int(v)
                if gi in (0, 1):
                    g = gi
                elif gi == 90:
                    absolute = True
                elif gi == 91:
                    absolute = False
            elif L == "X":
                nx = v if absolute else x + v
            elif L == "Y":
                ny = v if absolute else y + v
            elif L == "S":
                s = v
        if (nx, ny) != (x, y):
            segs.append((x, y, nx, ny, s, 1 if g == 0 else 0))
        x, y = nx, ny
    return np.array(segs, dtype=float).reshape(-1, 6)
