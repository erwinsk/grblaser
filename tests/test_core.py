# SPDX-License-Identifier: MIT
"""Uji inti (tanpa GUI): impor, toolpath, G-code, raster, proyek.
Jalankan:  python3 -m unittest discover -s tests -v
"""
import math
import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from grblaser import gcode, importers, raster, toolpath
from grblaser.model import (LAYER_COLORS, ImageOptions, ImageShape, Layer, PathShape, Poly,
                            Project as _Project, ellipse_poly, rect_poly)
from grblaser.project import load_project, save_project
from grblaser.tracing import rdp, trace_mask

def legacy_layers():
    """Delapan layer yang dulu jadi bawaan (indeks 2 = Isi, 4 = Gambar) agar tes lama tetap berlaku."""
    specs = [("Garis 1", "line"), ("Garis 2", "line"), ("Isi 1", "fill"), ("Isi 2", "fill"),
             ("Gambar 1", "image"), ("Gambar 2", "image"), ("Garis 3", "line"), ("Garis 4", "line")]
    out = []
    for i, (name, mode) in enumerate(specs):
        out.append(Layer(index=i, name=name, color=LAYER_COLORS[i], mode=mode,
                         speed=3000 if mode != "line" else 1000, power=30 if mode == "fill" else 60,
                         min_power=5.0 if mode == "image" else 0.0))
    return out


class Project(_Project):
    def __init__(self):
        super().__init__()
        self.layers = legacy_layers()


SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="80mm" viewBox="0 0 100 80">
<rect x="10" y="10" width="50" height="30" fill="none" stroke="#ff0000"/>
<circle cx="70" cy="50" r="10" fill="none" stroke="#0000ff"/>
<path d="M 5 70 C 20 60, 40 80, 55 70" fill="none" stroke="#ff0000"/>
<text x="1" y="5">abaikan</text>
</svg>"""


def burned_length(segs):
    cut = segs[(segs[:, 5] == 0) & (segs[:, 4] > 0)]
    return float(np.hypot(cut[:, 2] - cut[:, 0], cut[:, 3] - cut[:, 1]).sum())


class TestImport(unittest.TestCase):
    def test_svg(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.svg")
            with open(p, "w") as f:
                f.write(SVG)
            res = importers.import_svg(p)
        self.assertEqual(len(res.shapes), 3)
        self.assertTrue(any("Teks" in w for w in res.warnings))
        pr = Project()
        for s in res.shapes:
            pr.add(s)
        x0, y0, x1, y1 = pr.bbox()
        self.assertAlmostEqual(x0, 5, delta=0.05)
        self.assertAlmostEqual(x1, 80, delta=0.05)
        self.assertAlmostEqual(y0, 10, delta=0.05)
        # kurva Bezier (kontrol y=80) turun hingga y ~ 72,89 (lebih rendah dari lingkaran, 60)
        self.assertAlmostEqual(y1, 72.89, delta=0.1)
        cols = {s.src_color for s in res.shapes}
        self.assertEqual(cols, {"#ff0000", "#0000ff"})

    def test_dxf(self):
        import ezdxf
        doc = ezdxf.new(setup=True)
        doc.units = 4
        msp = doc.modelspace()
        msp.add_line((0, 0), (50, 0))
        msp.add_line((50, 0), (50, 20))
        msp.add_circle((25, 10), 5)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.dxf")
            doc.saveas(p)
            res = importers.import_dxf(p)
        pr = Project()
        for s in res.shapes:
            pr.add(s)
        x0, y0, x1, y1 = pr.bbox()
        self.assertAlmostEqual(x0, 0, delta=1e-6)
        self.assertAlmostEqual(y0, 0, delta=1e-6)
        self.assertAlmostEqual(x1, 50, delta=1e-3)
        self.assertAlmostEqual(y1, 20, delta=1e-3)


class TestLine(unittest.TestCase):
    def test_join_and_inner_first(self):
        a = Poly([(0, 0), (10, 0)], False)
        b = Poly([(10, 0), (10, 10)], False)
        c = Poly([(10, 10), (0, 0)], False)
        j = toolpath.join_paths([a, b, c])
        self.assertEqual(len(j), 1)
        self.assertTrue(j[0].closed)
        outer = rect_poly(0, 0, 100, 100)
        inner = rect_poly(40, 40, 60, 60)
        order = toolpath.order_paths([outer, inner], True)
        # bentuk dalam (lebar 20) harus dipotong lebih dulu
        self.assertAlmostEqual(order[0].pts[:, 0].max() - order[0].pts[:, 0].min(), 20)

    def test_gcode_line(self):
        pr = Project()
        pr.add(PathShape([rect_poly(10, 10, 60, 40)], layer=0))
        L = pr.layer(0)
        L.power, L.speed, L.passes = 80, 1200, 2
        job = toolpath.build_job(pr)
        self.assertEqual(job.layers_used, 1)
        T = gcode.make_transform(pr.device, job.bounds)
        lines = gcode.emit_gcode(job, pr.device, T)
        txt = "\n".join(lines)
        self.assertIn("M4 S0", txt)
        self.assertIn("S800", txt)
        self.assertIn("F1200", txt)
        segs = gcode.parse_gcode(lines)
        # 2 pass * keliling 160
        self.assertAlmostEqual(burned_length(segs), 320, delta=0.01)
        # Y dibalik: bed 400 -> y desain 10..40 => Y mesin 360..390
        cut = segs[(segs[:, 5] == 0) & (segs[:, 4] > 0)]
        ys = np.concatenate((cut[:, 1], cut[:, 3]))
        self.assertAlmostEqual(ys.min(), 360, delta=1e-6)
        self.assertAlmostEqual(ys.max(), 390, delta=1e-6)
        self.assertEqual(lines[-1], "; fim")
        # baris terkirim tidak boleh > 60 karakter wajar
        self.assertTrue(all(len(x) < 80 for x in lines))

    def test_current_position(self):
        pr = Project()
        pr.add(PathShape([rect_poly(10, 10, 60, 40)], layer=0))
        job = toolpath.build_job(pr)
        T = gcode.make_transform(pr.device, job.bounds, "current", 0, (100.0, 200.0))
        # jangkar kiri-atas desain (10,10) -> posisi (100,200)
        self.assertEqual(T(10, 10), (100.0, 200.0))
        self.assertEqual(T(60, 40), (150.0, 170.0))


class TestFill(unittest.TestCase):
    def test_rect_fill(self):
        pr = Project()
        pr.add(PathShape([rect_poly(10, 10, 30, 20)], layer=2))
        L = pr.layer(2)
        L.interval, L.angle, L.bidir, L.power = 0.5, 0, True, 40
        job = toolpath.build_job(pr)
        lines = gcode.emit_gcode(job, pr.device, gcode.make_transform(pr.device, job.bounds))
        segs = gcode.parse_gcode(lines)
        self.assertAlmostEqual(burned_length(segs), 20 * 10 / 0.5, delta=0.5)
        # zig-zag: arah garis berselang-seling
        cut = segs[(segs[:, 5] == 0) & (segs[:, 4] > 0)]
        dirs = np.sign(cut[:, 2] - cut[:, 0])
        self.assertTrue((dirs[:-1] != dirs[1:]).all())

    def test_hole_even_odd(self):
        pr = Project()
        pr.add(PathShape([rect_poly(0, 0, 40, 40), rect_poly(10, 10, 30, 30)], layer=2))
        L = pr.layer(2)
        L.interval = 0.5
        job = toolpath.build_job(pr)
        segs = gcode.parse_gcode(gcode.emit_gcode(job, pr.device, gcode.make_transform(pr.device, job.bounds)))
        self.assertAlmostEqual(burned_length(segs), (1600 - 400) / 0.5, delta=2)

    def test_angle_and_crosshatch(self):
        pr = Project()
        pr.add(PathShape([rect_poly(0, 0, 40, 40)], layer=2))
        L = pr.layer(2)
        L.interval, L.angle, L.crosshatch = 1.0, 45, True
        job = toolpath.build_job(pr)
        segs = gcode.parse_gcode(gcode.emit_gcode(job, pr.device, gcode.make_transform(pr.device, job.bounds)))
        self.assertAlmostEqual(burned_length(segs), 2 * 1600 / 1.0, delta=40)

    def test_overscan_s0(self):
        pr = Project()
        pr.add(PathShape([rect_poly(10, 10, 30, 20)], layer=2))
        L = pr.layer(2)
        L.interval, L.overscan = 1.0, 3.0
        job = toolpath.build_job(pr)
        self.assertEqual(job.bounds, (10, 10.5, 30, 19.5))


class TestRaster(unittest.TestCase):
    def _grad(self):
        im = Image.new("L", (200, 100))
        d = ImageDraw.Draw(im)
        for x in range(200):
            d.line([(x, 0), (x, 99)], fill=int(255 * x / 199))
        return im

    def test_gray(self):
        pr = Project()
        o = ImageOptions(method="gray", lpmm=5)
        pr.add(ImageShape(self._grad(), 10, 10, 40, 20, o, layer=4))
        L = pr.layer(4)
        L.speed, L.power, L.min_power, L.overscan = 3000, 70, 10, 2
        job = toolpath.build_job(pr)
        lines = gcode.emit_gcode(job, pr.device, gcode.make_transform(pr.device, job.bounds))
        smax = max(int(s[1:]) for ln in lines for s in ln.split() if s.startswith("S") and s[1:].isdigit())
        self.assertLessEqual(smax, 700)
        self.assertGreaterEqual(smax, 690)
        # 20mm / 0.2mm = 100 garis
        ys = {round(float(w[1:]), 3) for ln in lines for w in ln.split() if w.startswith("Y")}
        self.assertEqual(len(ys), 100 + 0 * len(ys))

    def test_methods_run(self):
        for m in ("threshold", "floyd", "atkinson", "bayer"):
            pr = Project()
            pr.add(ImageShape(self._grad(), 0, 0, 20, 10, ImageOptions(method=m, lpmm=5), layer=4))
            job = toolpath.build_job(pr)
            self.assertFalse(job.empty(), m)
            segs = gcode.parse_gcode(gcode.emit_gcode(job, pr.device, gcode.make_transform(pr.device, job.bounds)))
            self.assertTrue(set(np.unique(segs[:, 4])) <= {0.0, 600.0}, m)

    def test_angle_image(self):
        pr = Project()
        pr.add(ImageShape(Image.new("L", (100, 100), 0), 0, 0, 20, 20,
                          ImageOptions(method="threshold", lpmm=5, angle=45), layer=4))
        job = toolpath.build_job(pr)
        x0, y0, x1, y1 = job.bounds
        self.assertGreaterEqual(x0, -0.5)
        self.assertLessEqual(x1, 20.5)
        segs = gcode.parse_gcode(gcode.emit_gcode(job, pr.device, gcode.make_transform(pr.device, job.bounds)))
        area_len = burned_length(segs) * (1 / 5)   # panjang * jarak garis
        self.assertAlmostEqual(area_len, 400, delta=40)

    def test_trace(self):
        im = Image.new("L", (300, 300), 255)
        ImageDraw.Draw(im).ellipse([50, 50, 250, 250], fill=0)
        polys = raster.trace_polys(im, 0, 0, 30, 30, ImageOptions(method="trace", lpmm=10))
        self.assertEqual(len(polys), 1)
        pts = polys[0].pts
        circ = float(np.hypot(*(np.roll(pts, -1, axis=0) - pts).T).sum())
        self.assertAlmostEqual(circ, math.pi * 20, delta=1.0)

    def test_marching_squares_hole(self):
        m = np.zeros((20, 20), bool)
        m[3:17, 3:17] = True
        m[8:12, 8:12] = False
        self.assertEqual(len(trace_mask(m)), 2)

    def test_rdp(self):
        t = np.linspace(0, 2 * math.pi, 400, endpoint=False)
        pts = np.column_stack((10 * np.cos(t), 10 * np.sin(t)))
        r = rdp(pts, 0.01, True)
        self.assertLess(len(r), 400)
        self.assertGreater(len(r), 30)


class TestProject(unittest.TestCase):
    def test_roundtrip_and_undo(self):
        pr = Project()
        pr.add(PathShape([ellipse_poly(30, 30, 10, 5)], layer=1, kind="ellipse"))
        pr.add(ImageShape(Image.new("L", (10, 10), 77), 5, 5, 10, 10, ImageOptions(method="floyd"), layer=4))
        snap = pr.snapshot()
        pr.shapes[0].translate(100, 0)
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "p.glz")
            save_project(pr, f)
            pr2 = load_project(f)
        self.assertEqual(len(pr2.shapes), 2)
        self.assertAlmostEqual(pr2.shapes[0].bbox()[0], 120, delta=1e-3)
        self.assertEqual(pr2.shapes[1].opts.method, "floyd")
        pr.restore(snap)
        self.assertAlmostEqual(pr.shapes[0].bbox()[0], 20, delta=1e-6)

    def test_transforms(self):
        s = PathShape([rect_poly(0, 0, 10, 10)])
        s.scale(2, 3, 0, 0)
        self.assertEqual(s.bbox(), (0, 0, 20, 30))
        s.rotate(90, 0, 0)  # searah jarum jam di tampilan Y-bawah
        b = s.bbox()
        self.assertAlmostEqual(b[0], -30, delta=1e-9)
        self.assertAlmostEqual(b[2], 0, delta=1e-9)
        im = ImageShape(Image.new("L", (20, 10), 0), 0, 0, 20, 10)
        self.assertTrue(im.rotate(90, 10, 5))
        self.assertAlmostEqual(im.w, 10)
        self.assertAlmostEqual(im.h, 20)
        self.assertFalse(im.rotate(33, 0, 0))


if __name__ == "__main__":
    unittest.main()
