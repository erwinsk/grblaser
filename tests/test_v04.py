# SPDX-License-Identifier: MIT
"""Uji v0.4: pemeriksaan sebelum jalan, simpan otomatis, mesin proyek, profil."""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
from PIL import Image
from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox

from grblaser import preflight
from grblaser.autosave import Autosave
from grblaser.model import Device, ImageShape, PathShape, Project, ImageOptions
from grblaser.profiles import Profiles
from grblaser import shapes as shp
from grblaser.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


def rect_shape(x, y, w, h, layer=0):
    return PathShape([shp.rounded_rect(x, y, x + w, y + h, 0)], layer, "rect")


class TestPreflight(unittest.TestCase):
    def setUp(self):
        self.pr = Project()
        self.dev = self.pr.device
        self.dev.bed_w = self.dev.bed_h = 400
        self.dev.s_max = 1000

    def texts(self, issues, level=None):
        return [i.text for i in issues if level is None or i.level == level]

    def test_empty_is_error(self):
        iss = preflight.check(self.pr, self.dev)
        self.assertTrue(preflight.has_errors(iss))

    def test_ok(self):
        self.pr.add(rect_shape(10, 10, 50, 50))
        self.assertEqual(preflight.check(self.pr, self.dev), [])

    def test_output_off_counts_as_empty(self):
        self.pr.add(rect_shape(10, 10, 50, 50))
        self.pr.layers[0].output = False
        self.assertTrue(preflight.has_errors(preflight.check(self.pr, self.dev)))

    def test_outside_bed(self):
        self.pr.add(rect_shape(380, 10, 50, 50))
        iss = preflight.check(self.pr, self.dev)
        self.assertEqual([i.level for i in iss], ["warn"])
        self.assertIn("luar area kerja", iss[0].text)
        # mode 'dari posisi laser' tidak memeriksa batas (posisi awal tidak diketahui)
        self.dev.start_from = "current"
        self.assertEqual(preflight.check(self.pr, self.dev), [])

    def test_only_ids_limits_check(self):
        a = self.pr.add(rect_shape(10, 10, 50, 50))
        b = self.pr.add(rect_shape(380, 10, 50, 50))
        self.assertEqual(preflight.check(self.pr, self.dev, None, {a.id}), [])
        self.assertEqual(len(preflight.check(self.pr, self.dev, None, {b.id})), 1)

    def test_power_and_speed(self):
        self.pr.add(rect_shape(10, 10, 50, 50))
        L = self.pr.layers[0]
        L.power = 150
        self.assertTrue(preflight.has_errors(preflight.check(self.pr, self.dev)))
        L.power, L.speed = 60, 0
        self.assertTrue(preflight.has_errors(preflight.check(self.pr, self.dev)))
        L.speed, L.power = 1000, 0
        iss = preflight.check(self.pr, self.dev)
        self.assertFalse(preflight.has_errors(iss))
        self.assertIn("daya 0%", iss[0].text)

    def test_grbl_mismatch(self):
        self.pr.add(rect_shape(10, 10, 50, 50))
        grbl = {"$30": "255", "$130": "410", "$131": "400", "$32": "0"}
        t = self.texts(preflight.check(self.pr, self.dev, grbl))
        self.assertEqual(len(t), 3)
        self.assertTrue(any("$30" in x for x in t))
        self.assertTrue(any("$130" in x for x in t))
        self.assertTrue(any("$32" in x for x in t))
        ok = {"$30": "1000", "$130": "400.000", "$131": "400.000", "$32": "1"}
        self.assertEqual(preflight.check(self.pr, self.dev, ok), [])


class TestAutosaveCore(unittest.TestCase):
    def test_roundtrip(self):
        d = tempfile.mkdtemp()
        a = Autosave(d, sync=True)
        pr = Project()
        self.assertFalse(a.write(pr))                 # proyek kosong tidak ditulis
        s1, s2 = rect_shape(1, 1, 10, 10), rect_shape(20, 20, 5, 5)
        s1.group = s2.group = 7
        pr.add(s1)
        pr.add(s2)
        img = Image.fromarray((np.random.rand(20, 30) * 255).astype(np.uint8), "L")
        pr.add(ImageShape(img, 5, 5, 30, 20, ImageOptions(), 1, "foto"))
        pr.path = os.path.join(d, "asli.glz")
        self.assertTrue(a.write(pr))
        self.assertTrue(a.exists())
        self.assertEqual(a.info()[0], pr.path)
        back = a.load()
        self.assertEqual(len(back.shapes), 3)
        self.assertEqual(back.shapes[0].group, back.shapes[1].group)
        self.assertIsNone(back.path)                 # berkas asli belum ada -> tidak menimpa apa pun
        self.assertEqual(pr.path, os.path.join(d, "asli.glz"))   # proyek aktif tidak berubah
        a.clear()
        self.assertFalse(a.exists())

    def test_threaded_write(self):
        d = tempfile.mkdtemp()
        a = Autosave(d)
        pr = Project()
        pr.add(rect_shape(1, 1, 10, 10))
        self.assertTrue(a.write(pr))
        a.wait()
        self.assertTrue(a.exists())


class TestProfilesRename(unittest.TestCase):
    def test_rename(self):
        st = QSettings(os.path.join(tempfile.mkdtemp(), "x.ini"), QSettings.IniFormat)
        p = Profiles(st)
        p.save(Device(name="A"))
        p.save(Device(name="B", bed_w=300))
        self.assertFalse(p.rename("A", "B"))        # nama bentrok
        self.assertFalse(p.rename("Z", "C"))        # tidak ada
        self.assertFalse(p.rename("A", "  "))
        self.assertTrue(p.rename("B", " Mesin B "))
        self.assertEqual(p.names(), ["A", "Mesin B"])
        self.assertEqual(p.get("Mesin B").name, "Mesin B")
        self.assertEqual(p.get("Mesin B").bed_w, 300)


class TestUIv04(unittest.TestCase):
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

    def test_autosave_and_recovery(self):
        w = self.w
        self.assertFalse(w.autosave_now())                 # tidak ada perubahan
        w.project.add(rect_shape(10, 10, 20, 20))
        w._mark()
        self.assertTrue(w.autosave_now())
        self.assertFalse(w.autosave_now())                 # tidak berubah sejak itu
        w.project.add(rect_shape(40, 10, 20, 20))
        w._mark()
        self.assertTrue(w.autosave_now())
        self.assertTrue(w.autosave.exists())
        # sesi baru menemukan salinan -> Ya
        w2 = MainWindow()
        w2.autosave = w.autosave
        w2.tasks.sync = True
        self.assertTrue(w2.check_recovery())
        self.assertEqual(len(w2.project.shapes), 2)
        self.assertTrue(w2.dirty)
        w2.dirty = False
        w2.close()
        # Tidak -> berkas dibuang
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
        w3 = MainWindow()
        w3.autosave = w.autosave
        self.assertFalse(w3.check_recovery())
        self.assertFalse(w.autosave.exists())
        w3.close()

    def test_save_clears_autosave(self):
        w = self.w
        w.project.add(rect_shape(10, 10, 20, 20))
        w._mark()
        w.autosave_now()
        self.assertTrue(w.autosave.exists())
        path = os.path.join(tempfile.mkdtemp(), "p.glz")
        w.project.path = path
        self.assertTrue(w.save_project())
        self.assertFalse(w.autosave.exists())

    def test_project_device_prompt(self):
        w = self.w
        other = Device(name="Mesin B", bed_w=300, bed_h=200, origin="top-left", s_max=255)
        pr = Project()
        pr.device = other
        # pilih "Pakai mesin proyek"
        QTimer.singleShot(50, lambda: [b for b in QApplication.activeModalWidget().buttons()
                                       if "proyek" in b.text()][0].click())
        self.assertEqual(w._resolve_project_device(pr), "project")
        self.assertEqual(pr.device.name, "Mesin B")
        self.assertIn("Mesin B", w.profiles.names())       # profil baru dari proyek
        w._set_project(pr)
        w.laser_panel.reload_profiles()
        self.assertEqual(w.laser_panel.profile.currentText(), "Mesin B")
        # proyek lain dengan mesin awal; pilih "Tetap mesin aktif"
        pr2 = Project()
        pr2.device = Device(name="GRBL")
        QTimer.singleShot(50, lambda: [b for b in QApplication.activeModalWidget().buttons()
                                       if "aktif" in b.text()][0].click())
        self.assertEqual(w._resolve_project_device(pr2), "active")
        self.assertIs(pr2.device, w.project.device)
        # mesin sama -> tanpa dialog
        pr3 = Project()
        pr3.device = Device.from_dict(w.project.device.to_dict())
        self.assertEqual(w._resolve_project_device(pr3), "same")

    def test_job_panel_persists_to_profile(self):
        w = self.w
        name = w.project.device.name
        self.assertEqual(w.profiles.get(name).start_from, "absolute")
        w.job.start.setCurrentIndex(1)
        self.assertEqual(w.profiles.get(name).start_from, "current")
        w.job.ret.setChecked(True)
        self.assertEqual(w.profiles.get(name).end_return, "origin")
        w.job.anchor_grp.button(4).click()
        self.assertEqual(w.profiles.get(name).anchor, 4)
        # memuat profil lain tidak menimpa profil lama dengan nilai baru
        w.profiles.save(Device(name="Mesin B", start_from="absolute", anchor=0))
        w.apply_profile("Mesin B")
        self.assertEqual(w.profiles.get(name).start_from, "current")
        self.assertEqual(w.profiles.get("Mesin B").start_from, "absolute")

    def test_start_dialog_default_no_on_warning(self):
        seen = []
        QMessageBox.question = staticmethod(lambda parent, title, msg, buttons=None, default=None: seen.append(default) or QMessageBox.No)
        self.assertFalse(self.w._confirm_start("x", True))
        self.assertFalse(self.w._confirm_start("x", False))
        self.assertEqual(seen, [QMessageBox.No, QMessageBox.Yes])

    def test_start_job_blocks_and_warns(self):
        import time as _t
        sys.path.insert(0, os.path.dirname(__file__))
        from fake_grbl import FakeGrbl
        w = self.w
        g = FakeGrbl(process_delay=0.0005)
        try:
            self.assertTrue(w.sender.connect(g.port, 115200))
            t0 = _t.time()
            while w.sender.state != "Idle" and _t.time() - t0 < 4:
                app.processEvents()
                _t.sleep(0.01)
            asked = []
            QMessageBox.question = staticmethod(lambda parent, title, msg, *a, **k: asked.append(msg) or QMessageBox.No)
            crit = []
            orig_crit = QMessageBox.critical
            QMessageBox.critical = staticmethod(lambda parent, title, msg, *a, **k: crit.append(msg))
            try:
                # 1) proyek kosong -> kesalahan, tidak bertanya
                w.start_job()
                self.assertEqual(asked, [])
                # 2) objek keluar bed -> peringatan di dialog; S maks mesin palsu = 1000 jadi tak ada peringatan S
                w.project.add(rect_shape(380, 10, 50, 50))
                w._mark()                                         # perubahan nyata selalu memanggil _mark (cache job)
                w.start_job()
            finally:
                QMessageBox.critical = orig_crit
            self.assertTrue(w.sender.settings.get("$30"))      # $$ dibaca otomatis
            self.assertEqual(len(asked), 1, (asked, crit, w.statusBar().currentMessage()))
            self.assertIn("luar area kerja", asked[0])
            self.assertFalse(w.sender._running)               # ditolak -> tidak mulai
        finally:
            w.sender.disconnect()
            g.close() if hasattr(g, "close") else None


if __name__ == "__main__":
    unittest.main()
