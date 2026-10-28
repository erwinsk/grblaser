# SPDX-License-Identifier: MIT
"""Uji pengirim GRBL dengan GRBL palsu (pty). Butuh PyQt5 (QObject/sinyal)."""
import os
import sys
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from PyQt5.QtCore import QCoreApplication

from fake_grbl import FakeGrbl
from grblaser.sender import GrblSender

app = QCoreApplication.instance() or QCoreApplication([])


def wait(cond, timeout=10.0):
    t = time.time()
    while time.time() - t < timeout:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


class TestSender(unittest.TestCase):
    def setUp(self):
        self.g = FakeGrbl(process_delay=0.003)
        self.s = GrblSender()
        self.states = []
        self.s.job_state.connect(self.states.append)
        self.logs = []
        self.s.log.connect(lambda d, t: self.logs.append((d, t)))
        self.assertTrue(self.s.connect(self.g.port, 115200))
        wait(lambda: self.s.state == "Idle", 3)

    def tearDown(self):
        self.s.disconnect()
        self.g.close()

    def test_streaming_no_overflow(self):
        lines = [f"G1 X{i * 0.137:.3f} Y{i * 0.291:.3f} S{i % 1000} F3000" for i in range(400)]
        self.assertTrue(self.s.start_job(lines))
        self.assertTrue(wait(lambda: "done" in self.states, 20), self.states)
        self.assertEqual(self.g.lines, lines)
        self.assertFalse(self.g.overflow, f"buffer RX melebihi 127 (maks {self.g.max_rx})")
        self.assertGreater(self.g.max_rx, 40)   # benar-benar memakai buffer (bukan satu-satu)

    def test_status(self):
        self.assertTrue(wait(lambda: self.s.state == "Idle", 2))
        self.s.send_line("G0 X12.5 Y7")
        self.assertTrue(wait(lambda: abs(self.s.mpos[0] - 12.5) < 1e-6, 3))
        self.assertAlmostEqual(self.s.mpos[1], 7.0)

    def test_stop(self):
        slow = FakeGrbl(process_delay=0.05)
        s = GrblSender()
        states = []
        s.job_state.connect(states.append)
        s.connect(slow.port)
        wait(lambda: s.state == "Idle", 3)
        s.start_job([f"G1 X{i} Y0 F100" for i in range(200)])
        time.sleep(0.5)
        s.stop()
        self.assertTrue(wait(lambda: "stopped" in states, 3))
        self.assertTrue(wait(lambda: s.state == "Idle", 3))
        self.assertLess(len(slow.lines), 100)
        s.disconnect()
        slow.close()

    def test_progress_is_throttled(self):
        got = []
        self.s.progress.connect(lambda a, b: got.append((a, b)))
        lines = [f"G1 X{i % 50} Y{i % 7} F500" for i in range(1500)]
        self.s.start_job(lines, quiet=True)
        self.assertTrue(wait(lambda: "done" in self.states, 30))
        self.assertLess(len(got), 400)            # tidak satu sinyal per baris
        self.assertEqual(got[-1][0], len(lines))   # tetapi tetap berakhir di 100%

    def test_error_reported(self):
        self.g.error_on = "X5"
        lines = ["G0 X1", "G0 X5", "G0 X9"]
        self.s.start_job(lines)
        self.assertTrue(wait(lambda: "done" in self.states, 5))
        self.assertEqual(self.s.error_count, 1)
        self.assertTrue(any("error:20" in t for d, t in self.logs))

    def test_alarm_blocks_start(self):
        self.g.state = "Alarm"
        self.assertTrue(wait(lambda: self.s.state == "Alarm", 2))
        self.assertFalse(self.s.start_job(["G0 X1"]))

    def test_jog_and_pause(self):
        self.s.hold()
        self.assertTrue(wait(lambda: self.g.state.startswith("Hold"), 2))
        self.s.resume()
        self.assertTrue(wait(lambda: self.g.state == "Idle", 2))


if __name__ == "__main__":
    unittest.main()
