# SPDX-License-Identifier: MIT
"""Dialog: pengaturan perangkat (mesin) dan alat teks."""
from __future__ import annotations

import numpy as np
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontDatabase, QPainterPath
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                             QFontComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QFileDialog, QInputDialog, QListWidget, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout)

from ..model import Device, Poly, PathShape


def _dspin(lo, hi, val, dec=1, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    return s


class DeviceDialog(QDialog):
    def __init__(self, dev: Device, sender=None, parent=None, profiles=None):
        super().__init__(parent)
        self.setWindowTitle("Pengaturan perangkat")
        self.sender = sender
        self.profiles = profiles
        self._extra = {"start_from": dev.start_from, "anchor": dev.anchor,
                       "end_return": dev.end_return, "last_port": dev.last_port}
        f = QFormLayout()
        self.name = QLineEdit(dev.name)
        self.w = _dspin(10, 5000, dev.bed_w, 1, " mm")
        self.h = _dspin(10, 5000, dev.bed_h, 1, " mm")
        self.smax = QSpinBox()
        self.smax.setRange(1, 100000)
        self.smax.setValue(dev.s_max)
        self.cmd = QComboBox()
        self.cmd.addItems(["M4", "M3"])
        self.cmd.setCurrentText(dev.laser_cmd)
        self.cmd.setToolTip("M4 = mode laser dinamis ($32=1, daya mengikuti kecepatan; disarankan).\n"
                            "M3 = daya konstan.")
        self.origin = QComboBox()
        self.origin.addItem("Kiri-bawah (umum pada GRBL)", "bottom-left")
        self.origin.addItem("Kiri-atas", "top-left")
        self.origin.setCurrentIndex(0 if dev.origin == "bottom-left" else 1)
        self.rapid = _dspin(100, 100000, dev.rapid_rate, 0, " mm/mnt")
        self.fspeed = _dspin(100, 100000, dev.frame_speed, 0, " mm/mnt")
        self.fpower = _dspin(0, 100, dev.frame_power, 1, " %")
        self.fpower.setToolTip("Daya laser saat membingkai bila 'Laser menyala saat bingkai' dicentang. Beberapa persen (mis. 1-2%) cukup.")
        self.flaser = QCheckBox("Laser menyala saat bingkai (penunjuk posisi)")
        self.flaser.setChecked(dev.frame_laser)
        self.air_on = QLineEdit(dev.air_on)
        self.air_off = QLineEdit(dev.air_off)
        self.baud = QComboBox()
        for b in (9600, 57600, 115200, 230400, 250000, 460800, 921600):
            self.baud.addItem(str(b), b)
        self.baud.setCurrentText(str(dev.baud))
        f.addRow("Nama", self.name)
        f.addRow("Lebar area kerja ($130)", self.w)
        f.addRow("Tinggi area kerja ($131)", self.h)
        f.addRow("Daya maksimum S ($30)", self.smax)
        f.addRow("Perintah laser", self.cmd)
        f.addRow("Asal mesin (0,0)", self.origin)
        f.addRow("Kecepatan gerak cepat (estimasi)", self.rapid)
        f.addRow("Kecepatan bingkai", self.fspeed)
        f.addRow(self.flaser)
        f.addRow("Daya bingkai", self.fpower)
        f.addRow("Air assist hidup", self.air_on)
        f.addRow("Air assist mati", self.air_off)
        f.addRow("Baud rate", self.baud)
        lay = QVBoxLayout(self)
        if profiles is not None:
            pr = QHBoxLayout()
            self.prof = QComboBox()
            self.prof.addItems(profiles.names())
            self.prof.setCurrentText(dev.name)
            self.prof.setToolTip("Muat pengaturan dari profil tersimpan ke form ini.")
            bl = QPushButton("Muat")
            bl.clicked.connect(self._load_profile)
            bs = QPushButton("Simpan profil")
            bs.setToolTip("Simpan isi form sebagai profil bernama sesuai kolom Nama (nama baru = profil baru).")
            bs.clicked.connect(self._save_profile)
            bd = QPushButton("Hapus")
            bd.clicked.connect(self._delete_profile)
            br = QPushButton("Ganti nama")
            br.clicked.connect(self._rename_profile)
            pr.addWidget(QLabel("Profil"))
            pr.addWidget(self.prof, 1)
            for b in (bl, bs, br, bd):
                pr.addWidget(b)
            lay.addLayout(pr)
        lay.addLayout(f)
        if sender is not None:
            b = QPushButton("Baca dari GRBL ($30, $130, $131, $32)")
            b.setEnabled(sender.connected)
            b.clicked.connect(self._read)
            lay.addWidget(b)
            self.info = QLabel("")
            self.info.setWordWrap(True)
            lay.addWidget(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _load_into(self, d: Device):
        self.name.setText(d.name)
        self.w.setValue(d.bed_w)
        self.h.setValue(d.bed_h)
        self.smax.setValue(d.s_max)
        self.cmd.setCurrentText(d.laser_cmd)
        self.origin.setCurrentIndex(0 if d.origin == "bottom-left" else 1)
        self.rapid.setValue(d.rapid_rate)
        self.fspeed.setValue(d.frame_speed)
        self.fpower.setValue(d.frame_power)
        self.flaser.setChecked(d.frame_laser)
        self.air_on.setText(d.air_on)
        self.air_off.setText(d.air_off)
        self.baud.setCurrentText(str(d.baud))
        self._extra = {"start_from": d.start_from, "anchor": d.anchor,
                       "end_return": d.end_return, "last_port": d.last_port}

    def _load_profile(self):
        d = self.profiles.get(self.prof.currentText())
        if d:
            self._load_into(d)

    def _save_profile(self):
        d = Device()
        self.apply(d)
        self.profiles.save(d)
        self.prof.clear()
        self.prof.addItems(self.profiles.names())
        self.prof.setCurrentText(d.name)

    def _rename_profile(self):
        old = self.prof.currentText()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Ganti nama profil", "Nama baru:", text=old)
        if not ok:
            return
        if self.profiles.rename(old, new):
            if self.name.text() == old:
                self.name.setText(new.strip())
            self.prof.clear()
            self.prof.addItems(self.profiles.names())
            self.prof.setCurrentText(new.strip())

    def _delete_profile(self):
        if self.profiles.delete(self.prof.currentText()):
            self.prof.clear()
            self.prof.addItems(self.profiles.names())

    def _read(self):
        s = self.sender
        s.settings.clear()
        s.request_settings()
        from PyQt5.QtCore import QTimer, QEventLoop
        loop = QEventLoop()
        QTimer.singleShot(900, loop.quit)
        loop.exec_()
        st = s.settings
        got = []
        try:
            if "$30" in st:
                self.smax.setValue(int(float(st["$30"])))
                got.append(f"$30={st['$30']}")
            if "$130" in st:
                self.w.setValue(float(st["$130"]))
                got.append(f"$130={st['$130']}")
            if "$131" in st:
                self.h.setValue(float(st["$131"]))
                got.append(f"$131={st['$131']}")
            if "$32" in st:
                laser = st["$32"] in ("1", "1.000")
                self.cmd.setCurrentText("M4")
                got.append(f"$32={st['$32']}" + ("" if laser else "  (mode laser MATI! set $32=1)"))
        except ValueError:
            pass
        self.info.setText("Terbaca: " + ", ".join(got) if got else "Tidak ada respons. Pastikan GRBL idle.")

    def apply(self, dev: Device):
        dev.name = self.name.text() or "GRBL"
        dev.bed_w, dev.bed_h = self.w.value(), self.h.value()
        dev.s_max = self.smax.value()
        dev.laser_cmd = self.cmd.currentText()
        dev.origin = self.origin.currentData()
        dev.rapid_rate = self.rapid.value()
        dev.frame_speed = self.fspeed.value()
        dev.frame_power = self.fpower.value()
        dev.frame_laser = self.flaser.isChecked()
        dev.air_on = self.air_on.text() or "M8"
        dev.air_off = self.air_off.text() or "M9"
        dev.baud = int(self.baud.currentData())
        for k, v in self._extra.items():
            setattr(dev, k, v)


def text_polys(params):
    """Kontur teks dari `params` (text, font, size, bold, italic, spacing); pojok kiri-atas kotak pembatas di (0,0)."""
    em = 200.0
    font = QFont(params.get("font") or QFont().family())
    font.setPixelSize(int(em))
    font.setBold(bool(params.get("bold")))
    font.setItalic(bool(params.get("italic")))
    font.setLetterSpacing(QFont.PercentageSpacing, float(params.get("spacing", 100)))
    from PyQt5.QtGui import QFontMetricsF
    fm = QFontMetricsF(font)
    path = QPainterPath()
    for i, line in enumerate(str(params.get("text", "")).split("\n")):
        if line:
            path.addText(0, i * fm.lineSpacing(), font, line)
    k = float(params.get("size", 12)) / em
    polys = []
    for sub in path.toSubpathPolygons():
        pts = np.array([(p.x(), p.y()) for p in sub], dtype=float)
        if len(pts) < 2:
            continue
        closed = len(pts) > 2 and np.allclose(pts[0], pts[-1])
        if closed:
            pts = pts[:-1]
        polys.append(Poly(pts * k, closed))
    if not polys:
        return []
    allp = np.vstack([p.pts for p in polys])
    mn = allp.min(axis=0)
    for p in polys:
        p.pts = p.pts - mn
    return polys


class TextDialog(QDialog):
    """Buat / edit teks sebagai path (kontur huruf) - siap dipotong atau diisi."""

    def __init__(self, parent=None, params=None):
        super().__init__(parent)
        self.setWindowTitle("Edit teks" if params else "Teks")
        params = params or {}
        self.edit = QPlainTextEdit(params.get("text", "Teks"))
        self.edit.setMinimumHeight(70)
        self.font = QFontComboBox()
        if params.get("font"):
            self.font.setCurrentFont(QFont(params["font"]))
        self.size = _dspin(1, 1000, float(params.get("size", 12)), 1, " mm")
        self.size.setToolTip("Ukuran em huruf (tinggi huruf besar kira-kira 70%).")
        self.bold = QCheckBox("Tebal")
        self.bold.setChecked(bool(params.get("bold")))
        self.italic = QCheckBox("Miring")
        self.italic.setChecked(bool(params.get("italic")))
        self.spacing = _dspin(0, 200, float(params.get("spacing", 100)), 0, " %")
        f = QFormLayout()
        f.addRow("Isi", self.edit)
        f.addRow("Font", self.font)
        f.addRow("Ukuran", self.size)
        f.addRow("Jarak huruf", self.spacing)
        h = QHBoxLayout()
        h.addWidget(self.bold)
        h.addWidget(self.italic)
        f.addRow("Gaya", h)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(bb)

    def params(self):
        return {"text": self.edit.toPlainText(), "font": self.font.currentFont().family(), "size": self.size.value(),
                "bold": self.bold.isChecked(), "italic": self.italic.isChecked(), "spacing": self.spacing.value()}

    def make_shape(self, x, y):
        prm = self.params()
        polys = text_polys(prm)
        if not polys:
            return None
        sh = PathShape(polys, kind="text", name=prm["text"].split("\n")[0][:24])
        sh.text_spec = {"params": prm, "m": [1, 0, 0, 1, 0, 0]}
        sh.translate(x, y)
        return sh


class ArrayDialog(QDialog):
    """Salin berulang: grid (baris x kolom) atau melingkar."""

    def __init__(self, bbox_w, bbox_h, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Array / salin berulang")
        from PyQt5.QtWidgets import QTabWidget, QWidget
        self.tabs = QTabWidget()
        # --- grid
        g = QWidget()
        f = QFormLayout(g)
        self.cols = QSpinBox()
        self.cols.setRange(1, 200)
        self.cols.setValue(3)
        self.rows = QSpinBox()
        self.rows.setRange(1, 200)
        self.rows.setValue(3)
        self.gx = _dspin(-5000, 5000, bbox_w + 5, 3, " mm")
        self.gy = _dspin(-5000, 5000, bbox_h + 5, 3, " mm")
        f.addRow("Kolom", self.cols)
        f.addRow("Baris", self.rows)
        f.addRow("Jarak X (pusat ke pusat)", self.gx)
        f.addRow("Jarak Y (pusat ke pusat)", self.gy)
        self.tabs.addTab(g, "Grid")
        # --- melingkar
        c = QWidget()
        f2 = QFormLayout(c)
        self.n = QSpinBox()
        self.n.setRange(2, 720)
        self.n.setValue(8)
        self.radius = _dspin(0.1, 5000, max(bbox_w, bbox_h) * 1.2 + 5, 3, " mm")
        self.radius.setToolTip("Jarak dari pusat objek ke pusat lingkaran (pusat berada di atas objek).")
        self.total = _dspin(1, 360, 360, 1, " °")
        self.rot = QCheckBox("Putar salinan mengikuti lingkaran")
        self.rot.setChecked(True)
        f2.addRow("Jumlah (termasuk asli)", self.n)
        f2.addRow("Radius", self.radius)
        f2.addRow("Sudut total", self.total)
        f2.addRow(self.rot)
        self.tabs.addTab(c, "Melingkar")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addWidget(self.tabs)
        lay.addWidget(bb)


class MaterialsDialog(QDialog):
    """Pustaka bahan untuk mesin aktif: terapkan ke layer aktif, simpan dari layer aktif, hapus, ekspor/impor."""

    def __init__(self, materials, machine, layer, parent=None):
        super().__init__(parent)
        from .. import materials as mat
        self.mat = mat
        self.materials, self.machine, self.layer = materials, machine, layer
        self.applied = False
        self.setWindowTitle(f"Pustaka bahan — {machine}")
        self.resize(460, 380)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Layer aktif: <b>{layer.name or layer.index + 1}</b>. Preset berlaku per mesin "
                             "(tidak ada nilai bawaan: ukur sendiri untuk laser & bahan Anda)."))
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._show)
        lay.addWidget(self.list, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        r1 = QHBoxLayout()
        self.b_apply = QPushButton("Terapkan ke layer")
        self.b_save = QPushButton("Simpan dari layer…")
        self.b_del = QPushButton("Hapus")
        for b in (self.b_apply, self.b_save, self.b_del):
            r1.addWidget(b)
        lay.addLayout(r1)
        r2 = QHBoxLayout()
        self.b_exp = QPushButton("Ekspor…")
        self.b_imp = QPushButton("Impor…")
        r2.addWidget(self.b_exp)
        r2.addWidget(self.b_imp)
        r2.addStretch(1)
        close = QPushButton("Tutup")
        close.clicked.connect(self.accept)
        r2.addWidget(close)
        lay.addLayout(r2)
        self.b_apply.clicked.connect(self._apply)
        self.b_save.clicked.connect(self._save)
        self.b_del.clicked.connect(self._delete)
        self.b_exp.clicked.connect(self._export)
        self.b_imp.clicked.connect(self._import)
        self.list.itemDoubleClicked.connect(lambda _i: self._apply())
        self._fill()

    def _fill(self, select=None):
        self.list.clear()
        names = self.materials.names(self.machine)
        self.list.addItems(names)
        if names:
            self.list.setCurrentRow(names.index(select) if select in names else 0)
        self._show(self.list.currentItem().text() if self.list.currentItem() else "")

    def _show(self, name):
        ent = self.materials.get(self.machine, name) if name else None
        has = ent is not None
        self.b_apply.setEnabled(has)
        self.b_del.setEnabled(has)
        self.b_exp.setEnabled(bool(self.materials.names(self.machine)))
        if not has:
            self.info.setText("Belum ada preset. Atur layer lalu klik 'Simpan dari layer…'." if not self.list.count() else "")
            return
        p = ent["params"]
        txt = (f"Mode {p.get('mode')}, {p.get('speed'):g} mm/mnt, daya {p.get('power'):g}% "
               f"(min {p.get('min_power', 0):g}%), {p.get('passes')} pass")
        if ent.get("note"):
            txt += f"\nCatatan: {ent['note']}"
        self.info.setText(txt)

    def _cur(self):
        it = self.list.currentItem()
        return it.text() if it else None

    def _apply(self):
        name = self._cur()
        ent = self.materials.get(self.machine, name) if name else None
        if not ent:
            return
        self.mat.apply_to_layer(self.layer, ent["params"])
        self.applied = True
        self.accept()

    def _save(self):
        name, ok = QInputDialog.getText(self, "Simpan preset", "Nama preset (mis. Kayu lapis 3 mm – potong):",
                                        text=self._cur() or "")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in self.materials.names(self.machine):
            if QMessageBox.question(self, "Timpa preset?", f"Preset \"{name}\" sudah ada. Timpa?",
                                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        note, ok = QInputDialog.getText(self, "Catatan (opsional)", "Catatan (fokus, jarak, bahan, dll.):")
        self.materials.save(self.machine, name, self.layer, note if ok else "")
        self._fill(name)

    def _delete(self):
        name = self._cur()
        if name and QMessageBox.question(self, "Hapus preset", f"Hapus \"{name}\"?",
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.materials.delete(self.machine, name)
            self._fill()

    def _export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Ekspor pustaka bahan", "bahan.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.materials.export_json(self.machine))

    def _import(self):
        path, _ = QFileDialog.getOpenFileName(self, "Impor pustaka bahan", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                n = self.materials.import_json(self.machine, f.read())
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Gagal impor", str(e))
            return
        self._fill()
        QMessageBox.information(self, "Impor", f"{n} preset ditambahkan (nama yang sudah ada dilewati).")


class OffsetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Offset kontur")
        self.dist = _dspin(-200, 200, 1.0, 2, " mm")
        self.dist.setSingleStep(0.1)
        self.dist.setToolTip("Positif = membesar / keluar, negatif = mengecil / ke dalam.\n"
                             "Garis terbuka (hanya jarak positif) menjadi kontur tertutup di sekelilingnya.")
        self.delete_orig = QCheckBox("Hapus objek asli")
        f = QFormLayout()
        f.addRow("Jarak", self.dist)
        f.addRow(self.delete_orig)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.addLayout(f)
        lay.addWidget(bb)
