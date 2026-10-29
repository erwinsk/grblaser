# SPDX-License-Identifier: MIT
"""Uji antarmuka (offscreen): impor, alat gambar, mouse, undo, job, ekspor, kirim lewat GRBL palsu."""
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from PIL import Image, ImageDraw
from PyQt5.QtCore import QEvent, QPointF, QSettings, Qt
from PyQt5.QtGui import QMouseEvent
from PyQt5.QtWidgets import QApplication, QMessageBox

from fake_grbl import FakeGrbl
from grblaser.gcode import clean_gcode, parse_gcode
from grblaser.model import ImageShape, PathShape
from grblaser.ui.canvas import BOT_R
from grblaser.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
app.setStyle("Fusion")

SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="60mm" viewBox="0 0 100 60">
<rect x="5" y="5" width="90" height="50" fill="none" stroke="#000000"/>
<circle cx="30" cy="30" r="15" fill="#ff0000" stroke="#ff0000"/>
<path d="M60 15 C 70 5, 85 5, 85 20 S 70 45, 60 40 Z" fill="none" stroke="#0000ff"/>
</svg>"""


def wait(cond, timeout=10.0):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _send(canvas, kind, pos, button, buttons, mods):
    ev = QMouseEvent(kind, QPointF(pos), canvas.mapToGlobal(pos) and QPointF(canvas.mapToGlobal(pos)),
                     button, buttons, mods)
    QApplication.sendEvent(canvas, ev)


def drag(canvas, a, b, mods=Qt.NoModifier):
    """Seret mouse (event dikirim langsung; QTest.mouseMove tidak andal di platform offscreen)."""
    pa, pb = canvas.w2s(*a).toPoint(), canvas.w2s(*b).toPoint()
    _send(canvas, QEvent.MouseButtonPress, pa, Qt.LeftButton, Qt.LeftButton, mods)
    steps = 6
    for i in range(1, steps + 1):
        q = canvas.w2s(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps).toPoint()
        _send(canvas, QEvent.MouseMove, q, Qt.NoButton, Qt.LeftButton, mods)
        app.processEvents()
    _send(canvas, QEvent.MouseButtonRelease, pb, Qt.LeftButton, Qt.NoButton, mods)
    app.processEvents()


class TestUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.svg = os.path.join(cls.tmp, "t.svg")
        open(cls.svg, "w").write(SVG)
        cls.png = os.path.join(cls.tmp, "f.png")
        im = Image.new("L", (200, 150), 230)
        d = ImageDraw.Draw(im)
        d.ellipse((50, 30, 150, 120), fill=40)
        im.save(cls.png)

    def setUp(self):
        QSettings("Kalingin", "Grblaser").clear()
        self.w = MainWindow()
        self.w.resize(1400, 850)
        self.w.show()
        app.processEvents()
        self.c = self.w.canvas
        self.c.zoom = 1.5                      # 1 px ~ 0,67 mm agar presisi tes cukup
        self.c.off = QPointF(10, 10)
        self.pr = self.w.project
        self.w.tasks.sync = True               # tugas latar dijalankan langsung agar tes deterministik
        self._orig_q = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    def run_job(self, gcode=False, comments=True, preview=False):
        out = []
        self.w._run_job("tes", out.append, gcode=gcode, comments=comments, preview=preview, need_t=True)
        return out[0] if out else None

    def tearDown(self):
        QMessageBox.question = self._orig_q
        self.w.dirty = False
        self.w.sender.disconnect()
        self.w.close()

    # ---------------------------------------------------------------- impor
    def test_import_svg_per_layer_by_color(self):
        self.w.import_files([self.svg])
        cols = {self.pr.layer(s.layer).color for s in self.pr.shapes}
        self.assertEqual(cols, {"#000000", "#ff0000", "#0000ff"})
        self.assertEqual(len({s.layer for s in self.pr.shapes}), 3)

    def test_import_image_goes_to_image_layer_and_fits_bed(self):
        self.w.import_files([self.png])
        s = self.pr.shapes[0]
        self.assertIsInstance(s, ImageShape)
        self.assertEqual(self.pr.layer(s.layer).mode, "image")
        dev = self.pr.device
        self.assertLessEqual(s.w, dev.bed_w)
        cx = s.x + s.w / 2
        self.assertAlmostEqual(cx, dev.bed_w / 2, 3)

    # ---------------------------------------------------------------- mouse
    def test_draw_move_scale_undo(self):
        self.w.set_tool("rect")
        drag(self.c, (50, 50), (80, 70))
        self.assertEqual(len(self.pr.shapes), 1)
        sh = self.pr.shapes[0]
        bb = sh.bbox()
        self.assertAlmostEqual(bb[0], 50, delta=1.0)
        self.assertAlmostEqual(bb[3], 70, delta=1.0)
        self.assertEqual(self.c.tool, "select")
        self.assertEqual(self.c.selection, {sh.id})
        # pindah: seret dari tepi atas (di luar handle)
        drag(self.c, (57, 50), (67, 55))
        bb = self.pr.shapes[0].bbox()
        self.assertAlmostEqual(bb[0], 60, delta=1.5)
        self.assertAlmostEqual(bb[1], 55, delta=1.5)
        # skala proporsional dari handle kanan-bawah
        bb = self.c.sel_bbox()
        ratio0 = (bb[2] - bb[0]) / (bb[3] - bb[1])
        w0 = bb[2] - bb[0]
        drag(self.c, (bb[2], bb[3]), (bb[2] + 30, bb[3] + 20))
        nb = self.pr.shapes[0].bbox()
        self.assertAlmostEqual((nb[2] - nb[0]) / (nb[3] - nb[1]), ratio0, 3)
        self.assertGreater(nb[2] - nb[0], w0 + 10)
        # undo 2x -> kembali ke posisi gambar awal
        self.w.undo()
        self.w.undo()
        bb = self.pr.shapes[0].bbox()
        self.assertAlmostEqual(bb[0], 50, delta=1.0)
        self.w.undo()
        self.assertEqual(len(self.pr.shapes), 0)
        self.w.redo()
        self.assertEqual(len(self.pr.shapes), 1)

    def test_ellipse_line_and_band_select(self):
        self.w.set_tool("ellipse")
        drag(self.c, (100, 100), (140, 130))
        self.w.set_tool("line")
        drag(self.c, (200, 200), (260, 230))
        self.assertEqual(len(self.pr.shapes), 2)
        self.c.set_selection(set())
        drag(self.c, (90, 90), (270, 240))       # kiri-atas -> kanan-bawah: harus melingkupi semua
        self.assertEqual(len(self.c.selection), 2)
        self.c.set_selection(set())
        drag(self.c, (130, 120), (110, 110))      # kanan->kiri, menyentuh elips saja
        self.assertEqual(len(self.c.selection), 1)

    def test_text_tool_and_trace(self):
        from grblaser.ui.dialogs import TextDialog
        d = TextDialog()
        d.edit.setPlainText("AB")
        d.size.setValue(20)
        sh = d.make_shape(10, 10)
        self.assertIsNotNone(sh)
        bb = sh.bbox()
        self.assertAlmostEqual(bb[0], 10, 3)
        self.assertGreater(bb[3] - bb[1], 8)
        self.assertTrue(any(p.closed for p in sh.polys))
        # trace
        self.w.import_files([self.png])
        self.c.select_all = None
        self.w.select_all()
        self.w.image_panel.method.setCurrentIndex(self.w.image_panel.method.findData("trace"))
        self.w.trace_selected_image()
        self.assertTrue(all(isinstance(s, PathShape) for s in self.pr.shapes))
        self.assertGreater(len(self.pr.shapes), 0)

    # ---------------------------------------------------------------- layer & job
    def test_layer_assignment_and_job(self):
        self.w.set_tool("rect")
        drag(self.c, (20, 20), (60, 50))
        L = self.pr.layers[0]
        L.mode = "fill"
        self.w.assign_selection_to_layer(L.index)
        L.interval = 0.2
        self.w.show_preview()
        job = self.w.last_job
        self.assertIsNotNone(job)
        self.assertIsNotNone(self.c.preview)
        self.assertGreater(job.feed_len, 30 * 25 / 0.2 * 0.9)
        # layer dimatikan -> tidak ada pekerjaan
        L.output = False
        self.w.push_undo()                   # menandai proyek berubah (membatalkan cache job)
        self.assertIsNone(self.run_job())

    def test_export_gcode_valid(self):
        self.w.import_files([self.svg, self.png])
        res = self.run_job(gcode=True)
        lines = res["lines"]
        dev = self.pr.device
        self.assertEqual(lines[1], "G21")
        self.assertTrue(any(l.startswith("M4 S0") for l in lines))
        self.assertEqual([l for l in lines if l][-2:-1] or ["M5"], ["M5"])
        segs = parse_gcode(lines)
        self.assertGreater(len(segs), 100)
        self.assertLessEqual(segs[:, 4].max(), dev.s_max)
        for k in (0, 2):
            self.assertGreaterEqual(segs[:, k].min(), -0.01)
            self.assertLessEqual(segs[:, k].max(), dev.bed_w + 0.01)
        for k in (1, 3):
            self.assertGreaterEqual(segs[:, k].min(), -0.01)
            self.assertLessEqual(segs[:, k].max(), dev.bed_h + 0.01)

    def test_project_roundtrip(self):
        self.w.import_files([self.svg, self.png])
        self.pr.layers[0].speed = 777
        path = os.path.join(self.tmp, "p.glz")
        self.w.project.path = None
        from grblaser.project import save_project, load_project
        save_project(self.pr, path)
        p2 = load_project(path)
        self.assertEqual(len(p2.shapes), len(self.pr.shapes))
        self.assertEqual(p2.layers[0].speed, 777)
        self.assertEqual(p2.layer(self.pr.shapes[-1].layer).mode, "image")

    # ---------------------------------------------------------------- kirim ke GRBL palsu
    def test_run_job_through_fake_grbl(self):
        g = FakeGrbl(process_delay=0.0005)
        try:
            self.w.import_files([self.svg])
            s = self.w.sender
            self.assertTrue(s.connect(g.port, 115200))
            self.assertTrue(wait(lambda: s.state == "Idle", 4))
            states = []
            s.job_state.connect(states.append)
            self.w.start_job()
            self.assertTrue(wait(lambda: "done" in states, 60), states)
            expected = len(clean_gcode(self.run_job(gcode=True, comments=False)["lines"]))
            self.assertEqual(len([l for l in g.lines if l.strip() != "$$"]), expected)
            self.assertFalse(g.overflow)
            self.assertEqual(s.error_count, 0)
            # bingkai
            states.clear()
            self.w.job.sel_only.setChecked(False)
            n0 = len(g.lines)
            self.w.frame_job()
            self.assertTrue(wait(lambda: "done" in states, 10))
            self.assertGreaterEqual(len(g.lines) - n0, 6)
        finally:
            g.close()

    def test_start_from_current_position(self):
        g = FakeGrbl(process_delay=0.0005)
        try:
            self.w.set_tool("rect")
            drag(self.c, (0, 0), (10, 10))
            self.w.job.start.setCurrentIndex(1)
            self.w.job.anchor_grp.button(0).click()      # jangkar kiri-atas
            s = self.w.sender
            s.connect(g.port, 115200)
            self.assertTrue(wait(lambda: s.state == "Idle", 4))
            g.x, g.y = 100.0, 200.0
            self.assertTrue(wait(lambda: abs(s.wpos[0] - 100.0) < 0.01, 3))
            lines = self.run_job(gcode=True)["lines"]
            segs = parse_gcode(lines)
            segs = segs[segs[:, 4] > 0]                                 # hanya segmen bakar (parser mulai dari 0,0)
            self.assertAlmostEqual(segs[:, [0, 2]].min(), 100.0, 2)    # pojok kiri-atas desain = posisi laser
            self.assertAlmostEqual(segs[:, [1, 3]].max(), 200.0, 2)
        finally:
            g.close()

    # ---------------------------------------------------------------- fitur baru
    def test_two_default_layers(self):
        self.assertEqual([(L.name, L.mode) for L in self.pr.layers], [("Garis", "line"), ("Gambar", "image")])

    def test_new_shape_tools(self):
        c = self.c
        self.w.set_tool("polygon")
        self.w.opt_sides.setValue(5)
        drag(c, (100, 100), (120, 100))                  # pusat (100,100), jari-jari 20, vertex pertama ke kanan
        sh = self.pr.shapes[-1]
        self.assertEqual(len(sh.polys[0].pts), 5)
        self.assertTrue(sh.polys[0].closed)
        self.assertAlmostEqual(max(sh.polys[0].pts[:, 0]), 120, delta=1.0)
        self.w.set_tool("star")
        self.w.opt_sides.setValue(7)
        self.w.opt_inner.setValue(40)
        drag(c, (200, 100), (200, 120))
        self.assertEqual(len(self.pr.shapes[-1].polys[0].pts), 14)
        self.w.set_tool("rect")
        self.w.opt_radius.setValue(5)
        drag(c, (50, 200), (90, 230))
        self.assertGreater(len(self.pr.shapes[-1].polys[0].pts), 8)       # sudut membulat
        bb = self.pr.shapes[-1].bbox()
        self.assertAlmostEqual(bb[2] - bb[0], 40, delta=1.5)
        # polyline: 3 klik lalu klik titik pertama = tertutup
        self.w.set_tool("polyline")
        for pt in ((150, 150), (190, 150), (170, 190)):
            p = c.w2s(*pt).toPoint()
            _send(c, QEvent.MouseButtonPress, p, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
            _send(c, QEvent.MouseButtonRelease, p, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        n_before = len(self.pr.shapes)
        p = c.w2s(150, 150).toPoint()
        _send(c, QEvent.MouseButtonPress, p, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        self.assertEqual(len(self.pr.shapes), n_before + 1)
        self.assertTrue(self.pr.shapes[-1].polys[0].closed)
        self.assertEqual(len(self.pr.shapes[-1].polys[0].pts), 3)

    def test_library_shapes(self):
        from grblaser import shapes as shp
        for name in shp.LIBRARY:
            self.w.add_library_shape(name, 30)
            bb = self.pr.shapes[-1].bbox()
            self.assertAlmostEqual(max(bb[2] - bb[0], bb[3] - bb[1]), 30, delta=0.5, msg=name)
        self.assertEqual(len(self.pr.shapes), len(shp.LIBRARY))

    def test_possize_bar(self):
        self.w.set_tool("rect")
        drag(self.c, (50, 50), (80, 70))
        bar = self.w.possize
        bb = self.c.sel_bbox()
        w0, h0 = bb[2] - bb[0], bb[3] - bb[1]
        # titik acuan bawaan = pojok terdekat asal mesin (kiri-bawah); Y = koordinat mesin
        self.assertAlmostEqual(bar.x.value(), bb[0], 2)
        self.assertAlmostEqual(bar.y.value(), 400 - bb[3], 2)
        # ubah lebar dengan rasio terkunci -> tinggi ikut
        bar.w.setValue(60.0)
        bb = self.c.sel_bbox()
        self.assertAlmostEqual(bb[2] - bb[0], 60.0, 3)
        self.assertAlmostEqual((bb[3] - bb[1]) / (bb[2] - bb[0]), h0 / w0, 3)
        # tanpa kunci: tinggi sendiri
        bar.lock.setChecked(False)
        bar.h.setValue(10.0)
        bb = self.c.sel_bbox()
        self.assertAlmostEqual(bb[3] - bb[1], 10.0, 3)
        self.assertAlmostEqual(bb[2] - bb[0], 60.0, 3)
        # posisi: X=100 Y=200 (mesin) untuk sudut kiri-bawah
        bar.x.setValue(100.0)
        bar.y.setValue(200.0)
        bb = self.c.sel_bbox()
        self.assertAlmostEqual(bb[0], 100.0, 3)
        self.assertAlmostEqual(400 - bb[3], 200.0, 3)
        # titik acuan tengah: skala menjaga titik tengah tetap
        bar.grp.button(4).click()
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        bar.w.setValue(30.0)
        bb2 = self.c.sel_bbox()
        self.assertAlmostEqual((bb2[0] + bb2[2]) / 2, cx, 3)
        self.assertAlmostEqual((bb2[1] + bb2[3]) / 2, cy, 3)
        # satuan inci
        bar.unit_box.setCurrentIndex(1)
        self.assertAlmostEqual(bar.w.value() * 25.4, 30.0, 2)
        # undo mengembalikan
        self.w.undo()
        self.assertAlmostEqual((self.c.sel_bbox()[2] - self.c.sel_bbox()[0]), 60.0, 3)

    def test_coordinates_in_sync(self):
        """Penggaris, kursor, bar posisi, properti & titik 0,0 harus sepakat di kedua jenis asal mesin."""
        for origin in ("bottom-left", "top-left"):
            self.pr.device.origin = origin
            self.w._refresh_all()
            dev = self.pr.device
            # objek persis di titik 0,0 mesin -> bar menampilkan X=0,Y=0
            self.w.set_tool("rect"); drag(self.c, (50, 50), (80, 70))
            self.w.corner_selection()
            bar = self.w.possize
            self.assertAlmostEqual(bar.x.value(), 0.0, 3, origin)
            self.assertAlmostEqual(bar.y.value(), 0.0, 3, origin)
            bb = self.c.sel_bbox()
            tx, ty = dev.from_user(0, 0)
            self.assertAlmostEqual(bb[0], tx, 3)
            self.assertTrue(abs(bb[1] - ty) < 1e-3 or abs(bb[3] - ty) < 1e-3)
            self.assertIn("0", self.w.props.pos_lbl.text())
            # konversi pergi-pulang untuk keempat pojok bed
            for X, Y in ((0, 0), (dev.bed_w, 0), (0, dev.bed_h), (dev.bed_w, dev.bed_h)):
                x, y = dev.from_user(X, Y)
                self.assertEqual(dev.to_user(x, y), (X, Y))
            self.assertEqual(dev.to_user(*dev.from_user(0, 0)), (0, 0))
            self.pr.shapes.clear()
            self.c.set_selection(set())

    def test_group_ungroup(self):
        for a, b in (((10, 10), (30, 30)), ((60, 40), (80, 60)), ((200, 70), (220, 90))):
            self.w.set_tool("rect"); drag(self.c, a, b)
        s1, s2, s3 = self.pr.shapes
        self.c.set_selection({s1.id, s2.id})
        self.w.group_selection()
        self.assertIsNotNone(s1.group)
        self.assertEqual(s1.group, s2.group)
        self.assertIsNone(s3.group)
        # memilih salah satu anggota memilih seluruh grup
        self.c.set_selection({s1.id})
        self.assertEqual(self.c.selection, {s1.id, s2.id})
        # undo membatalkan grup
        self.w.undo()
        self.assertTrue(all(s.group is None for s in self.pr.shapes))
        self.w.redo()
        self.assertIsNotNone(self.pr.shapes[0].group)
        # salin grup -> grup baru terpisah
        self.c.set_selection({self.pr.shapes[0].id})
        self.w.copy_selection(); self.w.paste()
        gs = {s.group for s in self.pr.shapes if s.group is not None}
        self.assertEqual(len(gs), 2)
        # simpan/muat mempertahankan grup
        from grblaser import project as prj
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "t.glz")
        prj.save_project(self.pr, path)
        pr2 = prj.load_project(path)
        self.assertEqual(len({s.group for s in pr2.shapes if s.group is not None}), 2)
        # pisahkan
        self.c.set_selection({self.pr.shapes[0].id})
        self.w.ungroup_selection()
        self.assertEqual(len({s.group for s in self.pr.shapes if s.group is not None}), 1)

    def test_profiles(self):
        w = self.w
        self.assertIn(w.project.device.name, w.profiles.names())
        d2 = w.project.device.__class__(name="Mesin B", bed_w=300, bed_h=200, origin="top-left", s_max=255)
        w.profiles.save(d2)
        w.laser_panel.reload_profiles()
        self.assertIn("Mesin B", [w.laser_panel.profile.itemText(i) for i in range(w.laser_panel.profile.count())])
        self.assertTrue(w.apply_profile("Mesin B"))
        self.assertEqual((w.project.device.bed_w, w.project.device.bed_h, w.project.device.origin), (300, 200, "top-left"))
        self.assertEqual(w.possize.grp.checkedId(), 0)      # acuan ikut asal mesin
        self.assertEqual(w.laser_panel.profile.currentText(), "Mesin B")
        w.undo()
        self.assertEqual(w.project.device.bed_w, 400)
        self.assertFalse(w.apply_profile("tidak-ada"))
        # hapus: selalu sisakan satu
        for n in list(w.profiles.names()):
            w.profiles.delete(n)
        self.assertEqual(len(w.profiles.names()), 1)

    def test_possize_image(self):
        self.w.import_files([self.png])
        bar = self.w.possize
        bar.lock.setChecked(True)
        bar.w.setValue(50.0)
        s = self.pr.shapes[0]
        self.assertAlmostEqual(s.w, 50.0, 3)
        self.assertAlmostEqual(s.h / s.w, 150 / 200, 3)

    def test_align_distribute_array(self):
        self.w.set_tool("rect"); drag(self.c, (10, 10), (30, 30))
        self.w.set_tool("rect"); drag(self.c, (60, 40), (80, 60))
        self.w.set_tool("rect"); drag(self.c, (200, 70), (220, 90))
        self.w.select_all()
        self.w.align_selection("top", "sel")
        tops = [round(s.bbox()[1], 1) for s in self.pr.shapes]
        self.assertEqual(len(set(tops)), 1)
        self.w.distribute_selection("h")
        bbs = sorted(s.bbox() for s in self.pr.shapes)
        g1, g2 = bbs[1][0] - bbs[0][2], bbs[2][0] - bbs[1][2]
        self.assertAlmostEqual(g1, g2, 3)
        self.w.align_selection("hcenter", "bed")
        bb = self.c.sel_bbox()
        self.assertAlmostEqual((bb[0] + bb[2]) / 2, 200.0, 3)
        # array grid
        self.c.set_selection({self.pr.shapes[0].id})
        orig = ArrayDialog_mod.ArrayDialog.exec_
        try:
            def fake(self_):
                self_.tabs.setCurrentIndex(0); self_.cols.setValue(3); self_.rows.setValue(2)
                self_.gx.setValue(50); self_.gy.setValue(40)
                return ArrayDialog_mod.QDialog.Accepted
            ArrayDialog_mod.ArrayDialog.exec_ = fake
            n0 = len(self.pr.shapes)
            self.w.array_dialog()
            self.assertEqual(len(self.pr.shapes), n0 + 5)
            # melingkar
            def fake2(self_):
                self_.tabs.setCurrentIndex(1); self_.n.setValue(6); self_.radius.setValue(30)
                return ArrayDialog_mod.QDialog.Accepted
            ArrayDialog_mod.ArrayDialog.exec_ = fake2
            self.c.set_selection({self.pr.shapes[0].id})
            n0 = len(self.pr.shapes)
            self.w.array_dialog()
            self.assertEqual(len(self.pr.shapes), n0 + 5)
        finally:
            ArrayDialog_mod.ArrayDialog.exec_ = orig

    def test_raster_methods_density_and_filters(self):
        from grblaser import raster
        from grblaser.model import ImageOptions
        im = Image.fromarray(np.tile(np.linspace(0, 255, 240, dtype=np.uint8), (160, 1)), "L")
        for key, _ in raster.METHODS:
            if key == "trace":
                continue
            o = ImageOptions(method=key, lpmm=4.0, cell_mm=1.0, sharpen=60, blur=0.6, autolevel=True)
            P = raster.prepare(im, 0, 0, 60, 40, o)
            self.assertEqual(P.D.shape, (160, 240), key)
            self.assertGreaterEqual(float(P.D.min()), 0.0)
            self.assertLessEqual(float(P.D.max()), 1.0)
            if key not in ("threshold",):
                # gradien putih->hitam: kerapatan rata-rata sekitar 50%
                self.assertAlmostEqual(float(P.D.mean()), 0.5, delta=0.12, msg=key)
        # crop menjaga isi di tempatnya
        from grblaser.model import ImageShape
        sh = ImageShape(im, 10, 20, 60, 40)
        self.assertTrue(sh.crop(0.25, 0.1, 0.25, 0.0))
        self.assertAlmostEqual(sh.x, 25.0, 3)
        self.assertAlmostEqual(sh.w, 30.0, 3)
        self.assertAlmostEqual(sh.y, 24.0, 3)
        self.assertAlmostEqual(sh.h, 36.0, 3)
        self.assertEqual(sh.img.size, (120, 144))

    def test_block_ops_match_gcode_geometry(self):
        self.w.import_files([self.png])
        res = self.run_job(gcode=True)
        job, lines = res["job"], res["lines"]
        segs = parse_gcode(lines)
        feed = segs[segs[:, 5] == 0]
        length = float(np.hypot(feed[:, 2] - feed[:, 0], feed[:, 3] - feed[:, 1]).sum())
        self.assertAlmostEqual(length, job.feed_len, delta=job.feed_len * 0.005)
        from grblaser.ui.preview import ops_to_segments
        seg2 = ops_to_segments(job.ops)
        f2 = seg2[seg2[:, 5] == 0]
        self.assertAlmostEqual(float(np.hypot(f2[:, 2] - f2[:, 0], f2[:, 3] - f2[:, 1]).sum()), job.feed_len, delta=1.0)

    def test_background_task_preview(self):
        self.w.tasks.sync = False
        self.w.import_files([self.png])
        self.w.show_preview()
        self.assertTrue(wait(lambda: self.c.preview is not None, 20))
        self.assertFalse(self.w.tasks.busy)

    def test_async_image_preview_cache(self):
        self.w.import_files([self.png])
        s = self.pr.shapes[0]
        self.assertIsNone(self.c._image_pixmap(s))                    # belum selesai dihitung
        self.assertTrue(wait(lambda: self.c._image_pixmap(s) is not None, 10))
        s.w *= 1.5                                                    # ukuran berubah: tetap ada gambar lama selagi menghitung
        self.assertIsNotNone(self.c._image_pixmap(s))

    def test_job_cache_reused_until_edit(self):
        self.w.import_files([self.svg])
        r1 = self.run_job()
        r2 = self.run_job()
        self.assertIs(r1["job"], r2["job"])
        self.w.push_undo()
        r3 = self.run_job()
        self.assertIsNot(r1["job"], r3["job"])

    def test_boolean_and_offset(self):
        import pytest
        pytest.importorskip("shapely")
        from shapely.geometry import Polygon
        from grblaser import shapes as shp
        from grblaser.model import ellipse_poly, rect_poly
        a = PathShape([rect_poly(0, 0, 20, 20)], self.pr.layers[0].index, "rect")
        b = PathShape([ellipse_poly(20, 10, 8, 8)], self.pr.layers[0].index, "ellipse")
        self.pr.add(a)
        self.pr.add(b)
        self.c.set_selection({a.id, b.id})
        self.w.boolean_selection("subtract")                      # A (bawah) dikurangi B
        self.assertEqual(len(self.pr.shapes), 1)
        area = sum(Polygon(p.pts).area for p in self.pr.shapes[0].polys)
        self.assertAlmostEqual(area, 400 - 100.2, delta=1.0)
        self.w.undo()
        self.assertEqual(len(self.pr.shapes), 2)
        self.c.set_selection({a.id, b.id})
        self.w.boolean_selection("union")
        self.assertAlmostEqual(Polygon(self.pr.shapes[0].polys[0].pts).area, 500.2, delta=1.0)
        # offset: persegi 20x20 +2 mm -> sisi 24 dengan sudut bulat
        polys = shp.offset([rect_poly(0, 0, 20, 20)], 2.0)
        self.assertAlmostEqual(Polygon(polys[0].pts).area, 24 * 24 - 4 * (4 - 3.14159265 * 4 / 4), delta=1.5)
        # offset ke dalam mengecilkan; terlalu dalam -> kosong
        self.assertAlmostEqual(Polygon(shp.offset([rect_poly(0, 0, 20, 20)], -2.0)[0].pts).area, 256, delta=0.5)
        self.assertEqual(shp.offset([rect_poly(0, 0, 20, 20)], -15.0), [])

    def test_job_lines_prepared_off_ui_thread(self):
        """Baris G-code untuk dikirim sudah bersih dari komentar/baris kosong (dibersihkan di thread latar)."""
        self.w.import_files([self.svg, self.png])
        res = self.run_job(gcode=True, comments=False)
        lines = res["lines"]
        self.assertTrue(lines)
        self.assertTrue(all(l.strip() and ";" not in l and "(" not in l for l in lines))
        self.assertEqual(len(lines), len(clean_gcode(lines)))


import grblaser.ui.dialogs as ArrayDialog_mod  # noqa: E402

if __name__ == "__main__":
    unittest.main()
