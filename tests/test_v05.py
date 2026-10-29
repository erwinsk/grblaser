# SPDX-License-Identifier: MIT
"""Uji v0.5: teks editable, pustaka bahan, jog klik, cermin & bingkai laser, rotasi gambar dari UI."""
import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
from PIL import Image
from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox

from fake_grbl import FakeGrbl
from grblaser.autosave import Autosave
from grblaser.materials import Materials, apply_to_layer, layer_params
from grblaser.model import ImageShape, Layer, Project
from grblaser.project import load_project, save_project
from grblaser.ui.dialogs import TextDialog, text_polys
from grblaser.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def wait(cond, timeout=6.0):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


class TestMaterialsCore(unittest.TestCase):
    def setUp(self):
        self.st = QSettings(os.path.join(tempfile.mkdtemp(), "m.ini"), QSettings.IniFormat)
        self.m = Materials(self.st)

    def test_save_apply_delete(self):
        L = Layer(name="x", speed=1234, power=42, passes=3, mode="fill", interval=0.2, color="#123456")
        self.assertFalse(self.m.save("A", "  ", L))
        self.assertTrue(self.m.save("A", "Kayu 3mm", L, "fokus 0"))
        self.assertEqual(self.m.names("A"), ["Kayu 3mm"])
        self.assertEqual(self.m.names("B"), [])                     # per mesin
        tgt = Layer(name="target", color="#abcdef", output=False)
        ch = apply_to_layer(tgt, self.m.get("A", "Kayu 3mm")["params"])
        self.assertIn("speed", ch)
        self.assertEqual((tgt.speed, tgt.power, tgt.passes, tgt.mode), (1234, 42, 3, "fill"))
        self.assertEqual((tgt.name, tgt.color, tgt.output), ("target", "#abcdef", False))   # identitas layer tak berubah
        self.assertTrue(self.m.delete("A", "Kayu 3mm"))
        self.assertFalse(self.m.delete("A", "Kayu 3mm"))

    def test_apply_sanitizes(self):
        L = Layer()
        apply_to_layer(L, {"power": 500, "speed": -5, "passes": 0, "mode": "bogus", "min_power": "abc", "evil": 1})
        self.assertEqual((L.power, L.passes, L.mode), (100.0, 1, "line"))
        self.assertGreaterEqual(L.speed, 1.0)

    def test_export_import(self):
        L = Layer(speed=900, power=30)
        self.m.save("A", "Akrilik", L, "catatan")
        txt = self.m.export_json("A")
        self.assertEqual(self.m.import_json("B", txt), 1)
        self.assertEqual(self.m.get("B", "Akrilik")["note"], "catatan")
        self.assertEqual(self.m.import_json("B", txt), 0)           # bentrok dilewati
        self.assertEqual(self.m.import_json("B", txt, overwrite=True), 1)
        with self.assertRaises(ValueError):
            self.m.import_json("B", '{"format": "lain"}')


class TestUIv05(unittest.TestCase):
    def setUp(self):
        QSettings("Kalingin", "Grblaser").clear()
        self.w = MainWindow()
        self.w.show()
        app.processEvents()
        self.w.tasks.sync = True
        self.w.autosave = Autosave(tempfile.mkdtemp(), sync=True)
        self._q = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)

    def tearDown(self):
        QMessageBox.question = self._q
        self.w.dirty = False
        self.w.close()

    # ---- teks editable
    def test_text_edit_keeps_transform(self):
        w = self.w
        d = TextDialog()
        d.edit.setPlainText("Halo")
        d.size.setValue(10)
        sh = d.make_shape(20, 30)
        w._shape_created(sh)
        bb0 = sh.bbox()
        self.assertAlmostEqual(bb0[0], 20, 6)
        self.assertAlmostEqual(bb0[1], 30, 6)
        sh.scale(2, 2, 20, 30)                       # pengguna memperbesar dua kali
        w.canvas.set_selection({sh.id})
        self.assertIs(w._single_text(), sh)
        prm = dict(sh.text_spec["params"])
        prm["text"] = "Halo dunia"
        sh.set_text_polys(text_polys(prm), prm)
        bb = sh.bbox()
        self.assertAlmostEqual(bb[0], 20, 6)         # posisi kiri-atas tetap
        self.assertAlmostEqual(bb[1], 30, 6)
        self.assertGreater(bb[2] - bb[0], (bb0[2] - bb0[0]) * 2)    # lebih lebar & skala 2x tetap berlaku
        self.assertEqual(sh.name, "Halo dunia")

    def test_text_edit_dialog_and_undo_and_save(self):
        w = self.w
        d = TextDialog()
        d.edit.setPlainText("A")
        sh = d.make_shape(10, 10)
        w._shape_created(sh)
        w.canvas.set_selection({sh.id})
        w2w = None
        # edit lewat dialog: ganti isi tanpa membuka UI sungguhan
        import grblaser.ui.main_window as mw
        orig = mw.TextDialog

        class FakeDlg:
            Accepted = 1

            def __init__(self, parent, params):
                self._p = dict(params, text="B\nC")

            def exec_(self):
                return 1

            def params(self):
                return self._p
        mw.TextDialog = FakeDlg
        try:
            self.assertTrue(w.edit_text())
        finally:
            mw.TextDialog = orig
        self.assertEqual(sh.text_spec["params"]["text"], "B\nC")
        h_after = sh.bbox()[3] - sh.bbox()[1]
        w.undo()
        self.assertEqual(w.project.shapes[0].text_spec["params"]["text"], "A")
        w.redo()
        # simpan & muat mempertahankan kemampuan edit
        path = os.path.join(tempfile.mkdtemp(), "t.glz")
        save_project(w.project, path)
        back = load_project(path).shapes[0]
        self.assertEqual(back.text_spec["params"]["text"], "B\nC")
        self.assertAlmostEqual(back.bbox()[3] - back.bbox()[1], h_after, 3)
        self.assertGreater(h_after, 0)

    def test_double_click_on_non_text_does_nothing(self):
        from grblaser import shapes as shp
        from grblaser.model import PathShape
        w = self.w
        sh = PathShape([shp.rounded_rect(10, 10, 50, 50, 0)], 0, "rect")
        w._shape_created(sh)
        w._canvas_double_click(30, 10)                # tidak membuka dialog / tidak error
        self.assertIsNone(w._single_text())

    # ---- cermin & bingkai laser dari panel
    def test_mirror_ui_title_preflight_and_persistence(self):
        from grblaser import preflight, shapes as shp
        from grblaser.model import PathShape
        w = self.w
        w.project.add(PathShape([shp.rounded_rect(10, 10, 60, 40, 0)], 0, "rect"))
        w._mark()
        w.job.mir_x.setChecked(True)
        self.assertTrue(w.project.mirror_x)
        self.assertIn("[CERMIN X]", w.windowTitle())
        self.assertTrue(any("DICERMINKAN" in i.text for i in preflight.check(w.project, w.project.device)))
        # G-code tercermin pada sumbu X: kiri <-> kanan dalam kotak pekerjaan
        job = w._run_job("t", lambda r: setattr(self, "res", r), gcode=True, comments=False, need_t=True)
        x_mirror = [l for l in self.res["lines"] if l.startswith(("G1", "G0")) and "X" in l]
        w.job.mir_x.setChecked(False)
        self.assertNotIn("[CERMIN", w.windowTitle())
        w._run_job("t", lambda r: setattr(self, "res", r), gcode=True, comments=False, need_t=True)
        x_plain = [l for l in self.res["lines"] if l.startswith(("G1", "G0")) and "X" in l]
        self.assertNotEqual(x_mirror, x_plain)
        # simpan/muat
        w.job.mir_y.setChecked(True)
        path = os.path.join(tempfile.mkdtemp(), "m.glz")
        save_project(w.project, path)
        back = load_project(path)
        self.assertEqual((back.mirror_x, back.mirror_y), (False, True))

    def test_frame_laser_checkbox_persists_to_profile(self):
        w = self.w
        name = w.project.device.name
        self.assertFalse(w.project.device.frame_laser)
        w.job.frame_pow.setValue(2.0)
        w.job.frame_laser.setChecked(True)
        dev = w.project.device
        self.assertTrue(dev.frame_laser)
        self.assertEqual(dev.frame_power, 2.0)
        self.assertTrue(w.profiles.get(name).frame_laser)
        self.assertTrue(w.job.frame_pow.isEnabled())
        w.job.frame_laser.setChecked(False)
        self.assertFalse(w.job.frame_pow.isEnabled())
        self.assertFalse(w.profiles.get(name).frame_laser)

    def test_frame_with_laser_asks_confirmation_and_sends_power(self):
        from grblaser import shapes as shp
        from grblaser.model import PathShape
        w = self.w
        g = FakeGrbl(process_delay=0.0005)
        try:
            self.assertTrue(w.sender.connect(g.port, 115200))
            self.assertTrue(wait(lambda: w.sender.state == "Idle", 4))
            w.project.add(PathShape([shp.rounded_rect(10, 10, 60, 40, 0)], 0, "rect"))
            w._mark()
            w.job.frame_pow.setValue(1.5)
            w.job.frame_laser.setChecked(True)
            asked = []
            QMessageBox.question = staticmethod(lambda parent, title, msg, *a, **k: asked.append(msg) or QMessageBox.No)
            w.frame_job()
            self.assertEqual(len(asked), 1)
            self.assertIn("MENYALA", asked[0])
            self.assertFalse(w.sender._running)
            QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
            w.frame_job()
            self.assertTrue(wait(lambda: any(l.startswith("M4 S15") for l in g.lines), 5), g.lines)
            self.assertTrue(wait(lambda: any(l == "M5" for l in g.lines), 5))
        finally:
            w.sender.disconnect()
            g.close()

    # ---- jog klik di kanvas
    def test_move_laser_to_uses_jog_and_can_set_start(self):
        w = self.w
        g = FakeGrbl(process_delay=0.0005)
        try:
            self.assertTrue(w.sender.connect(g.port, 115200))
            self.assertTrue(wait(lambda: w.sender.state == "Idle", 4))
            dev = w.project.device
            self.assertFalse(w._move_laser_to(500, 10))               # di luar bed
            self.assertTrue(w._move_laser_to(100, 50))                # desain (100,50) -> mesin (100, 350)
            self.assertTrue(wait(lambda: any(l.startswith("$J=G21G90X100.000Y350.000") for l in g.lines), 4), g.lines)
            self.assertEqual(dev.start_from, "absolute")
            self.assertTrue(w._move_laser_to(20, 20, make_start=True))
            self.assertEqual(dev.start_from, "current")
            self.assertEqual(w.job.start.currentData(), "current")
            w.sender.disconnect()
            self.assertFalse(w._move_laser_to(100, 50))               # tidak terhubung
        finally:
            w.sender.disconnect()
            g.close()

    # ---- rotasi gambar dari panel
    def test_image_rotation_from_panel(self):
        w = self.w
        arr = (np.random.rand(30, 40) * 255).astype(np.uint8)
        im = ImageShape(Image.fromarray(arr, "L"), 20, 20, 40, 30)
        w.project.add(im)
        w.canvas.set_selection({im.id})
        w.image_panel.refresh()
        w.image_panel.rot.setValue(30.0)
        self.assertAlmostEqual(im.angle, 30.0)
        bb = im.bbox()
        self.assertGreater(bb[2] - bb[0], 40)
        w.image_panel.rot.setValue(90.0)                              # kelipatan 90 -> putar piksel
        self.assertAlmostEqual(im.angle, 0.0)
        self.assertAlmostEqual(im.w, 30)
        w.undo()
        w.undo()
        self.assertAlmostEqual(w.project.shapes[0].w, 40)

    def test_rotate_selection_action_rotates_image_freely(self):
        w = self.w
        im = ImageShape(Image.new("L", (20, 10), 128), 10, 10, 20, 10)
        w.project.add(im)
        w.canvas.set_selection({im.id})
        cx, cy = im.center()
        im.rotate(20, cx, cy)
        self.assertTrue(w.canvas._can_rotate())
        self.assertIs(w.canvas.hit_test(cx, cy), im)
        # titik di sudut AABB yang berada di luar persegi miring tidak kena
        bb = im.bbox()
        self.assertIsNone(w.canvas.hit_test(bb[0] + 0.01, bb[1] + 0.01))


if __name__ == "__main__":
    unittest.main()
