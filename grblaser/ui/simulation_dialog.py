# SPDX-License-Identifier: MIT
"""Dialog simulasi jalur laser: animasi urutan dan arah pembakaran (tanpa menyalakan mesin)."""
from __future__ import annotations

import numpy as np
from PyQt5.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QImage, QPainter, QPen
from PyQt5.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

from ..simulation import Simulator

SPEEDS = [("×1 (waktu nyata)", 1.0), ("×5", 5.0), ("×20", 20.0), ("×100", 100.0), ("×500", 500.0), ("×2000", 2000.0)]


def _fmt(t):
    t = int(t)
    return f"{t // 3600:d}:{t % 3600 // 60:02d}:{t % 60:02d}"


class _View(QWidget):
    def __init__(self, sim):
        super().__init__()
        self.sim = sim
        self.img = None
        self.setMinimumSize(480, 360)
        self.refresh()

    def refresh(self):
        a = np.ascontiguousarray(self.sim.image())
        self.img = QImage(a.data, a.shape[1], a.shape[0], a.shape[1] * 4, QImage.Format_RGBA8888).copy()
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(40, 40, 44))
        s = self.sim
        k = min(self.width() / s.bed_w, self.height() / s.bed_h) * 0.96
        ox, oy = (self.width() - s.bed_w * k) / 2, (self.height() - s.bed_h * k) / 2
        r = QRectF(ox, oy, s.bed_w * k, s.bed_h * k)
        p.fillRect(r, QColor(245, 240, 225))
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        if self.img is not None:
            p.drawImage(r, self.img)
        hx, hy = s.head()
        c = QPointF(ox + hx * k, oy + hy * k)
        p.setPen(QPen(QColor(0, 150, 255), 2))
        p.setBrush(QColor(255, 255, 0, 220) if s.burning() else Qt.NoBrush)
        p.drawEllipse(c, 5, 5)
        p.drawLine(QPointF(c.x() - 9, c.y()), QPointF(c.x() + 9, c.y()))
        p.drawLine(QPointF(c.x(), c.y() - 9), QPointF(c.x(), c.y() + 9))


class SimulationDialog(QDialog):
    def __init__(self, segs, bed_w, bed_h, s_max, time_s, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Simulasi jalur laser")
        self.sim = Simulator(segs, bed_w, bed_h, s_max, time_s)
        self.t = 0.0
        self.view = _View(self.sim)
        lay = QVBoxLayout(self)
        lay.addWidget(self.view, 1)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(self._seek)
        lay.addWidget(self.slider)
        row = QHBoxLayout()
        self.btn = QPushButton("⏸ Jeda")
        self.btn.clicked.connect(self.toggle)
        self.btn_re = QPushButton("⏮ Ulang")
        self.btn_re.clicked.connect(self.restart)
        self.speed = QComboBox()
        for n, v in SPEEDS:
            self.speed.addItem(n, v)
        self.speed.setCurrentIndex(3)
        self.lbl = QLabel("")
        row.addWidget(self.btn)
        row.addWidget(self.btn_re)
        row.addWidget(QLabel("Kecepatan:"))
        row.addWidget(self.speed)
        row.addStretch(1)
        row.addWidget(self.lbl)
        lay.addLayout(row)
        lay.addWidget(QLabel("Merah = bakar (makin pekat makin dalam), abu-ungu = gerak cepat, titik kuning = laser menyala. "
                             "Urutan ini yang akan dikerjakan mesin."))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.clock = QElapsedTimer()
        self.resize(820, 640)
        self.play()

    def play(self):
        if self.t >= self.sim.time_s:
            self.restart()
        self.clock.start()
        self.timer.start(33)
        self.btn.setText("⏸ Jeda")

    def pause(self):
        self.timer.stop()
        self.btn.setText("▶ Main")

    def toggle(self):
        self.pause() if self.timer.isActive() else self.play()

    def restart(self):
        self.t = 0.0
        self.sim.reset()
        self._show()
        if not self.timer.isActive():
            self.play()

    def _tick(self):
        dt = self.clock.restart() / 1000.0
        self.t = min(self.sim.time_s, self.t + dt * float(self.speed.currentData()))
        self._show()
        if self.t >= self.sim.time_s:
            self.pause()

    def _seek(self, v):
        self.t = self.sim.time_s * v / 1000.0
        self._show(move_slider=False)

    def _show(self, move_slider=True):
        self.sim.seek_time(self.t)
        self.view.refresh()
        if move_slider:
            self.slider.setValue(int(1000 * self.t / self.sim.time_s) if self.sim.time_s else 0)
        self.lbl.setText(f"{_fmt(self.t)} / {_fmt(self.sim.time_s)}")

    def done(self, r):
        self.timer.stop()
        super().done(r)
