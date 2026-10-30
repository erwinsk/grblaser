# SPDX-License-Identifier: MIT
"""Uji v0.6: urutan potong, lanjutkan job, uji bahan."""
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np

from grblaser import materialtest as mt
from grblaser import resume
from grblaser.model import Poly, Project
from grblaser.toolpath import optimize_order, travel_length, _nearest_order

G = ["G21", "G90", "M4", "G0 X0 Y0", "G1 X10 Y0 F1000 S500", "G1 X10 Y10", "M8", "G1 X0 Y10 S300",
     "G0 X5 Y5", "G1 X6 Y6", "M5"]


class TestOrder(unittest.TestCase):
    def test_optimize_keeps_paths_and_not_worse(self):
        rng = np.random.RandomState(1)
        polys = [Poly(np.array([rng.rand(2) * 200, rng.rand(2) * 200]), False) for _ in range(60)]
        base = travel_length(_nearest_order(polys, (0, 0)), (0, 0))
        out = optimize_order(polys, (0, 0))
        self.assertEqual(len(out), 60)
        self.assertLessEqual(travel_length(out, (0, 0)), base + 1e-6)
        key = lambda p: tuple(sorted(map(tuple, np.round(p.pts, 6))))
        self.assertEqual(sorted(map(key, polys)), sorted(map(key, out)))

    def test_small(self):
        self.assertEqual(optimize_order([], (0, 0)), [])
        p = [Poly(np.array([[0, 0], [1, 1]], float), False)]
        self.assertEqual(len(optimize_order(p, (0, 0))), 1)


class TestResume(unittest.TestCase):
    def test_plan_state(self):
        new, info = resume.plan_resume(G, 7)
        self.assertEqual(info["x"], 10)
        self.assertEqual(info["y"], 10)
        self.assertTrue(info["air"])
        self.assertIn("M8", new)
        self.assertIn("G0 X10 Y10", new)
        self.assertEqual(new[:3], ["G21", "G90", "M4"])
        self.assertEqual(new[info["prefix"]:][-1], "M5")
        first = new[info["prefix"]]
        self.assertTrue(first.startswith("G1"))
        self.assertIn("F1000", first)

    def test_plan_start_and_clamp(self):
        new, info = resume.plan_resume(G, 0)
        self.assertEqual(info["n"], 0)
        new, info = resume.plan_resume(G, 999)
        self.assertEqual(info["n"], len(G))

    def test_default_index(self):
        self.assertEqual(resume.default_resume_index(10, 100), 0)
        self.assertEqual(resume.default_resume_index(100, 100), 75)

    def test_lastjob(self):
        with tempfile.TemporaryDirectory() as d:
            lj = resume.LastJob(d)
            self.assertIsNone(lj.load())
            lj.save(G, {"name": "x"}, sync=True)
            lj.update(5, force=True)
            lines, meta = lj.load()
            self.assertEqual(lines, G)
            self.assertEqual(meta["acked"], 5)
            lj.update(11, done=True)
            self.assertIsNone(lj.load())
            lj.clear()
            self.assertIsNone(lj.load_meta())


class TestMaterialTest(unittest.TestCase):
    def test_build_remove(self):
        pr = Project()
        n0 = len(pr.layers)
        p = mt.TestParams(cols=3, rows=2, labels=False)
        self.assertIsNone(mt.validate(p, n0))
        ids = mt.build(pr, p, 5, 5)
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(pr.layers), n0 + 6)
        ns, nl = mt.remove_test(pr)
        self.assertEqual((ns, nl), (6, 6))
        self.assertEqual(len(pr.layers), n0)

    def test_validate_too_many(self):
        self.assertIsNotNone(mt.validate(mt.TestParams(cols=10, rows=10), 2))



# ---------------------------------------------------------------- simulasi, rotary, Marlin
import time

from PyQt5.QtCore import QCoreApplication

from grblaser.gcode import make_transform
from grblaser.model import Device
from grblaser.preflight import check
from grblaser.sender import GrblSender
from grblaser.simulation import Simulator


class TestSimulation(unittest.TestCase):
    def test_progress(self):
        segs = np.array([[0, 0, 10, 0, 0, 1], [10, 0, 10, 10, 500, 0], [10, 10, 0, 10, 500, 0]], float)
        s = Simulator(segs, 20, 20, 1000, time_s=6)
        self.assertEqual(s.index_at(100), 3)
        s.seek_time(100)
        self.assertGreater((s.image()[..., 3] > 0).sum(), 100)
        s.seek(1)                      # mundur: hanya gerak cepat
        im = s.image()
        self.assertTrue(((im[..., 0] == 230) & (im[..., 3] > 0)).sum() == 0)
        self.assertEqual(Simulator(np.zeros((0, 6)), 10, 10).index_at(5), 0)


class TestRotary(unittest.TestCase):
    def test_scale(self):
        d = Device()
        self.assertEqual(d.rotary_scale(), 1.0)
        d.rotary, d.object_diameter, d.mm_per_rotation = True, 50.0, 360.0
        self.assertAlmostEqual(d.rotary_scale(), 360 / (np.pi * 50), 6)
        d.rotary_mode, d.roller_diameter = "roller", 20.0
        self.assertAlmostEqual(d.rotary_scale(), 0.4)

    def test_transform(self):
        d = Device(origin="top-left", rotary=True, rotary_mode="roller", object_diameter=50, roller_diameter=25)
        T = make_transform(d, (0, 0, 10, 10))
        self.assertEqual(T(4.0, 10.0), (4.0, 5.0))
        d2 = Device(origin="top-left", rotary=True, rotary_mode="roller", object_diameter=50, roller_diameter=25,
                    start_from="current")
        T = make_transform(d2, (0, 0, 10, 10), current=(5, 20))
        self.assertEqual(T(0.0, 10.0), (5.0, 25.0))

    def test_preflight(self):
        pr = Project()
        mt.build(pr, mt.TestParams(cols=1, rows=1, labels=False, size=200), 0, 0)
        d = Device(rotary=True, object_diameter=20)
        txt = " ".join(i.text for i in check(pr, d))
        self.assertIn("ROTARY", txt)
        self.assertIn("keliling", txt)


class TestMarlin(unittest.TestCase):
    def test_job_and_jog(self):
        from fake_marlin import FakeMarlin
        fm = FakeMarlin()
        s = GrblSender()
        s.set_firmware("marlin")
        states = []
        s.job_state.connect(states.append)
        try:
            self.assertTrue(s.connect(fm.port, 115200))
            lines = ["G21", "G90", "G0 X5 Y6", "G1 X10 Y12 F1000 S100", "M5"]
            self.assertTrue(s.start_job(lines))
            t = time.time()
            while "done" not in states and time.time() - t < 5:
                QCoreApplication.processEvents()
                time.sleep(0.02)
            self.assertIn("done", states)
            self.assertEqual((fm.x, fm.y), (10.0, 12.0))
            s.jog_to(1, 2, 3000)
            time.sleep(0.4)
            self.assertEqual((fm.x, fm.y), (1.0, 2.0))
            t = time.time()
            while time.time() - t < 2.0 and s.wpos[0] != 1.0:
                QCoreApplication.processEvents()
                time.sleep(0.05)
            self.assertAlmostEqual(s.wpos[0], 1.0)
            self.assertFalse(any(l.startswith("$") or l.startswith("?") for l in fm.lines))
        finally:
            s.disconnect()
            fm.close()


# ---------------------------------------------------------------- UI
from PyQt5.QtCore import QSettings
from PyQt5.QtWidgets import QApplication, QDialog, QMessageBox

from fake_grbl import FakeGrbl
from grblaser.autosave import Autosave
from grblaser.ui import main_window as mw_mod
from grblaser.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])


class TestUIv06(unittest.TestCase):
    def setUp(self):
        QSettings("Kalingin", "Grblaser").clear()
        self.w = MainWindow()
        self.w.show()
        app.processEvents()
        self.w.tasks.sync = True
        self.w.autosave = Autosave(tempfile.mkdtemp(), sync=True)
        self.w.job_store = resume.LastJob(tempfile.mkdtemp())

    def tearDown(self):
        self.w.dirty = False
        self.w.close()

    def test_material_test_ui(self):
        w = self.w
        n0 = len(w.project.layers)
        orig = mw_mod.MaterialTestDialog.exec_
        mw_mod.MaterialTestDialog.exec_ = lambda self: QDialog.Accepted
        try:
            self.assertTrue(w.material_test_dialog())
        finally:
            mw_mod.MaterialTestDialog.exec_ = orig
        self.assertEqual(len(w.project.layers), n0 + 26)
        self.assertGreater(len(w.project.shapes), 24)
        w.remove_material_test()
        self.assertEqual(len(w.project.layers), n0)
        w.undo()
        self.assertEqual(len(w.project.layers), n0 + 26)

    def test_simulation_ui(self):
        from grblaser.model import PathShape, rect_poly
        from grblaser.ui import simulation_dialog as sd
        w = self.w
        w.project.shapes.append(PathShape([rect_poly(10, 10, 30, 30)], layer=w.project.layers[0].index))
        w._mark()
        seen = {}

        def fake_exec(self):
            seen["n"] = len(self.sim.segs)
            self.sim.seek_time(self.sim.time_s)
            seen["px"] = int((self.sim.image()[..., 3] > 0).sum())
            return 0
        orig = sd.SimulationDialog.exec_
        sd.SimulationDialog.exec_ = fake_exec
        try:
            w.show_simulation()
        finally:
            sd.SimulationDialog.exec_ = orig
        self.assertGreater(seen.get("n", 0), 0)
        self.assertGreater(seen.get("px", 0), 0)

    def test_resume_ui(self):
        w = self.w
        fg = FakeGrbl()
        try:
            w.sender.connect(fg.port, 115200)
            t = time.time()
            while not w.sender.connected and time.time() - t < 3:
                app.processEvents()
            time.sleep(0.5)
            app.processEvents()
            lines = [f"G1 X{i} Y{i} F1000 S100" for i in range(1, 60)]
            w._resume_job = None
            w._remember_job(["G21", "G90", "M4"] + lines, 0, 0)
            w.job_store.wait()
            w.job_store.update(40, force=True)
            self.assertIsNotNone(w._resumable())
            orig = mw_mod.ResumeDialog.exec_
            mw_mod.ResumeDialog.exec_ = lambda self: QDialog.Accepted
            try:
                self.assertTrue(w.resume_job())
            finally:
                mw_mod.ResumeDialog.exec_ = orig
            t = time.time()
            while w.sender._running and time.time() - t < 6:
                app.processEvents()
                time.sleep(0.02)
            self.assertFalse(w.sender._running)
            self.assertTrue(any("X59" in l or "X59.0" in l for l in fg.lines))
        finally:
            w.sender.disconnect()
            fg.close()


if __name__ == "__main__":
    unittest.main()
