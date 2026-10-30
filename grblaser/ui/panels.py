# SPDX-License-Identifier: MIT
"""Panel-panel samping: layer, properti objek, opsi gambar, laser/jog, pekerjaan, konsol."""
from __future__ import annotations

import html

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QColorDialog, QComboBox,
                             QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QProgressBar,
                             QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QToolButton,
                             QVBoxLayout, QWidget)

from .. import raster
from ..model import ImageShape, MODES, MODE_LABELS, MAX_LAYERS
from ..sender import list_ports


def dsb(lo, hi, step=1.0, dec=2, suffix=""):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setDecimals(dec)
    s.setKeyboardTracking(False)
    if suffix:
        s.setSuffix(suffix)
    return s


def isb(lo, hi, step=1):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    s.setKeyboardTracking(False)
    return s


def contrast_text(hex_color):
    c = QColor(hex_color)
    return "#000000" if (c.red() * 299 + c.green() * 587 + c.blue() * 114) / 1000 > 140 else "#ffffff"


# =============================================================================
class LayerPanel(QWidget):
    COLS = ["Layer", "Nama", "Mode", "Kec.", "Daya%", "Pass", "Out", "Lihat"]

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        self.palette_row = QHBoxLayout()
        self.palette_row.setSpacing(2)
        lay.addWidget(QLabel("Klik warna untuk memindahkan objek terpilih ke layer:"))
        lay.addLayout(self.palette_row)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c, wd in enumerate((46, 60, 86, 52, 56, 42, 36, 44)):
            self.table.setColumnWidth(c, wd)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setMinimumHeight(190)
        self.table.itemChanged.connect(self._item_changed)
        self.table.itemSelectionChanged.connect(self._row_selected)
        lay.addWidget(self.table, 1)

        btns = QHBoxLayout()
        for text, fn, tip in (("+", self.win.add_layer, "Tambah layer"),
                              ("−", self.win.remove_current_layer, "Hapus layer (objeknya dipindah ke layer pertama)"),
                              ("▲", lambda: self.win.move_layer(-1), "Naikkan urutan pengerjaan"),
                              ("▼", lambda: self.win.move_layer(1), "Turunkan urutan pengerjaan")):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            btns.addWidget(b)
        bm = QToolButton()
        bm.setText("📚 Bahan…")
        bm.setToolTip("Pustaka bahan: simpan/terapkan kecepatan, daya, dan pass untuk bahan tertentu")
        bm.clicked.connect(self.win.materials_dialog)
        btns.addWidget(bm)
        btns.addStretch(1)
        lay.addLayout(btns)

        # ---- detail
        self.g_gen = QGroupBox("Pengaturan layer")
        f = QFormLayout(self.g_gen)
        self.w_name = QLineEdit()
        self.w_color = QPushButton()
        self.w_mode = QComboBox()
        for m in MODES:
            self.w_mode.addItem(MODE_LABELS[m], m)
        self.w_speed = dsb(1, 60000, 50, 0, " mm/mnt")
        self.w_power = dsb(0, 100, 1, 1, " %")
        self.w_min = dsb(0, 100, 1, 1, " %")
        self.w_passes = isb(1, 99)
        self.w_output = QCheckBox("Ikut dikerjakan (Output)")
        self.w_visible = QCheckBox("Tampil di kanvas")
        self.w_air = QCheckBox("Air assist")
        f.addRow("Nama", self.w_name)
        f.addRow("Warna", self.w_color)
        f.addRow("Mode", self.w_mode)
        f.addRow("Kecepatan", self.w_speed)
        f.addRow("Daya maks", self.w_power)
        self.row_min = (QLabel("Daya min"), self.w_min)
        f.addRow(*self.row_min)
        f.addRow("Jumlah pass", self.w_passes)
        f.addRow(self.w_output)
        f.addRow(self.w_visible)
        f.addRow(self.w_air)
        lay.addWidget(self.g_gen)

        self.g_line = QGroupBox("Garis")
        f2 = QFormLayout(self.g_line)
        self.w_inner = QCheckBox("Potong bagian dalam lebih dulu")
        f2.addRow(self.w_inner)
        lay.addWidget(self.g_line)

        self.g_fill = QGroupBox("Isi")
        f3 = QFormLayout(self.g_fill)
        self.w_interval = dsb(0.01, 5, 0.01, 3, " mm")
        self.w_angle = dsb(-360, 360, 5, 1, " °")
        self.w_cross = QCheckBox("Isi silang (crosshatch)")
        self.w_group = QCheckBox("Isi semua bentuk sebagai satu grup")
        f3.addRow("Jarak garis", self.w_interval)
        f3.addRow("Sudut", self.w_angle)
        f3.addRow(self.w_cross)
        f3.addRow(self.w_group)
        lay.addWidget(self.g_fill)

        self.g_scan = QGroupBox("Pemindaian")
        f4 = QFormLayout(self.g_scan)
        self.w_bidir = QCheckBox("Dua arah (bolak-balik)")
        self.w_over = dsb(0, 50, 0.5, 2, " mm")
        self.w_over.setToolTip("Laser bergerak lebih jauh di ujung baris agar akselerasi tidak merusak hasil.")
        f4.addRow(self.w_bidir)
        f4.addRow("Overscan", self.w_over)
        lay.addWidget(self.g_scan)

        # sambungkan editor
        for w, attr in ((self.w_speed, "speed"), (self.w_power, "power"), (self.w_min, "min_power"),
                        (self.w_interval, "interval"), (self.w_angle, "angle"), (self.w_over, "overscan")):
            w.valueChanged.connect(lambda v, a=attr: self._set(a, float(v)))
        self.w_passes.valueChanged.connect(lambda v: self._set("passes", int(v)))
        for w, attr in ((self.w_output, "output"), (self.w_visible, "visible"), (self.w_air, "air"),
                        (self.w_inner, "inner_first"), (self.w_cross, "crosshatch"),
                        (self.w_group, "fill_group"), (self.w_bidir, "bidir")):
            w.toggled.connect(lambda v, a=attr: self._set(a, bool(v)))
        self.w_name.textEdited.connect(lambda t: self._set("name", t))
        self.w_mode.currentIndexChanged.connect(lambda i: self._set("mode", self.w_mode.currentData()))
        self.w_color.clicked.connect(self._pick_color)
        self.refresh()

    # ------------------------------------------------------------------
    def current(self):
        return self.win.project.layer(self.win.active_layer)

    def _row_layer_index(self, row):
        it = self.table.item(row, 0)
        return it.data(Qt.UserRole) if it else None

    def refresh(self):
        self._loading = True
        pr = self.win.project
        # palet
        while self.palette_row.count():
            it = self.palette_row.takeAt(0)
            wdg = it.widget()
            if wdg is not None:
                wdg.hide()
                wdg.setParent(None)
                wdg.deleteLater()
        for L in pr.layers:
            b = QToolButton()
            b.setFixedSize(22, 22)
            b.setText("%02d" % (L.index + 1))
            b.setStyleSheet(f"background:{L.color};color:{contrast_text(L.color)};font-size:8px;border:1px solid #666;")
            b.setToolTip(f"{L.name} ({MODE_LABELS[L.mode]})")
            b.clicked.connect(lambda _=False, i=L.index: self.win.assign_selection_to_layer(i))
            self.palette_row.addWidget(b)
        self.palette_row.addStretch(1)
        # tabel
        self.table.setRowCount(len(pr.layers))
        for r, L in enumerate(pr.layers):
            it = QTableWidgetItem("C%02d" % (L.index + 1))
            it.setData(Qt.UserRole, L.index)
            it.setBackground(QColor(L.color))
            it.setForeground(QColor(contrast_text(L.color)))
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(r, 0, it)
            self.table.setItem(r, 1, QTableWidgetItem(L.name))
            cb = QComboBox()
            for m in MODES:
                cb.addItem(MODE_LABELS[m], m)
            cb.setCurrentIndex(MODES.index(L.mode))
            cb.currentIndexChanged.connect(lambda i, idx=L.index, c=cb: self._mode_from_table(idx, c.currentData()))
            self.table.setCellWidget(r, 2, cb)
            for c, val in ((3, "%g" % L.speed), (4, "%g" % L.power), (5, "%d" % L.passes)):
                self.table.setItem(r, c, QTableWidgetItem(val))
            for c, val in ((6, L.output), (7, L.visible)):
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Checked if val else Qt.Unchecked)
                self.table.setItem(r, c, chk)
        rows = [i for i, L in enumerate(pr.layers) if L.index == self.win.active_layer]
        if rows:
            self.table.selectRow(rows[0])
        self._load_detail()
        self._loading = False

    def _load_detail(self):
        L = self.current()
        self._loading = True
        self.w_name.setText(L.name)
        self.w_color.setText(L.color)
        self.w_color.setStyleSheet(f"background:{L.color};color:{contrast_text(L.color)};")
        self.w_mode.setCurrentIndex(MODES.index(L.mode))
        self.w_speed.setValue(L.speed)
        self.w_power.setValue(L.power)
        self.w_min.setValue(L.min_power)
        self.w_passes.setValue(L.passes)
        self.w_output.setChecked(L.output)
        self.w_visible.setChecked(L.visible)
        self.w_air.setChecked(L.air)
        self.w_inner.setChecked(L.inner_first)
        self.w_interval.setValue(L.interval)
        self.w_angle.setValue(L.angle)
        self.w_cross.setChecked(L.crosshatch)
        self.w_group.setChecked(L.fill_group)
        self.w_bidir.setChecked(L.bidir)
        self.w_over.setValue(L.overscan)
        self._vis()
        self._loading = False

    def _vis(self):
        m = self.current().mode
        self.g_line.setVisible(m == "line")
        self.g_fill.setVisible(m == "fill")
        self.g_scan.setVisible(m in ("fill", "image"))
        for w in self.row_min:
            w.setVisible(m == "image")

    def _row_selected(self):
        if self._loading:
            return
        rows = self.table.selectionModel().selectedRows()
        if rows:
            idx = self._row_layer_index(rows[0].row())
            if idx is not None:
                self.win.active_layer = idx
                self._load_detail()

    # ------------------------------------------------------------------
    def _set(self, attr, value):
        if self._loading:
            return
        L = self.current()
        self.win.push_undo("layer-" + attr + str(L.index))
        setattr(L, attr, value)
        if attr in ("mode", "name", "color", "output", "visible"):
            self.refresh()
        else:
            self._sync_row(L)
        self.win.layers_changed()

    def _sync_row(self, L):
        for r in range(self.table.rowCount()):
            if self._row_layer_index(r) == L.index:
                self._loading = True
                self.table.item(r, 3).setText("%g" % L.speed)
                self.table.item(r, 4).setText("%g" % L.power)
                self.table.item(r, 5).setText("%d" % L.passes)
                self._loading = False

    def _mode_from_table(self, idx, mode):
        if self._loading:
            return
        L = self.win.project.layer(idx)
        self.win.push_undo("layer-mode" + str(idx))
        L.mode = mode
        self.win.active_layer = idx
        self.refresh()
        self.win.layers_changed()

    def _pick_color(self):
        L = self.current()
        c = QColorDialog.getColor(QColor(L.color), self, "Warna layer")
        if c.isValid():
            self._set("color", c.name())

    def _item_changed(self, it):
        if self._loading:
            return
        r, c = it.row(), it.column()
        idx = self._row_layer_index(r)
        if idx is None:
            return
        L = self.win.project.layer(idx)
        self.win.push_undo(f"table{idx}-{c}")
        try:
            if c == 1:
                L.name = it.text()
            elif c == 3:
                L.speed = max(1.0, float(it.text()))
            elif c == 4:
                L.power = max(0.0, min(100.0, float(it.text())))
            elif c == 5:
                L.passes = max(1, int(float(it.text())))
            elif c == 6:
                L.output = it.checkState() == Qt.Checked
            elif c == 7:
                L.visible = it.checkState() == Qt.Checked
        except ValueError:
            pass
        self.win.active_layer = idx
        QTimer.singleShot(0, lambda: (self.refresh(), self.win.layers_changed()))


# =============================================================================
class PosSizeBar(QWidget):
    """Bar numerik selalu terlihat: posisi & ukuran objek terpilih, dengan titik acuan, satuan, kunci rasio.
    Sumbu Y mengikuti koordinat mesin (sama dengan penggaris & kursor)."""
    UNITS = (("mm", 1.0, 3), ("inci", 25.4, 4))

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._loading = False
        self.unit = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(6)
        g = QGridLayout()
        g.setSpacing(1)
        self.grp = QButtonGroup(self)
        for i in range(9):
            b = QToolButton()
            b.setCheckable(True)
            b.setFixedSize(13, 13)
            b.setToolTip("Titik acuan posisi & skala (bawaan: pojok terdekat titik 0,0 mesin)")
            self.grp.addButton(b, i)
            g.addWidget(b, i // 3, i % 3)
        self.grp.button(win.project.device.origin_anchor()).setChecked(True)
        self.grp.buttonClicked[int].connect(lambda i: self.refresh())
        lay.addLayout(g)
        self.x = dsb(-99999, 99999, 1, 3, " mm")
        self.y = dsb(-99999, 99999, 1, 3, " mm")
        self.w = dsb(0, 99999, 1, 3, " mm")
        self.h = dsb(0, 99999, 1, 3, " mm")
        for lbl, wd in (("X", self.x), ("Y", self.y), ("Lebar", self.w), ("Tinggi", self.h)):
            lay.addWidget(QLabel(lbl))
            wd.setMinimumWidth(104)
            lay.addWidget(wd)
        self.lock = QCheckBox("Kunci rasio")
        self.lock.setChecked(True)
        lay.addWidget(self.lock)
        self.unit_box = QComboBox()
        for n, _, _ in self.UNITS:
            self.unit_box.addItem(n)
        self.unit_box.currentIndexChanged.connect(self._unit_changed)
        lay.addWidget(self.unit_box)
        lay.addStretch(1)
        self.x.valueChanged.connect(self._pos)
        self.y.valueChanged.connect(self._pos)
        self.w.valueChanged.connect(lambda v: self._size("w"))
        self.h.valueChanged.connect(lambda v: self._size("h"))
        self.refresh()

    # ---- konversi
    def _k(self):
        return self.UNITS[self.unit][1]

    def _anchor(self, bb):
        i = self.grp.checkedId()
        col, row = i % 3, i // 3
        return bb[0] + (bb[2] - bb[0]) * col / 2.0, bb[1] + (bb[3] - bb[1]) * row / 2.0

    def _flip_y(self, y):
        """Y desain <-> Y pengguna (fungsi yang sama dengan penggaris & kursor; berlaku dua arah)."""
        return self.win.project.device.to_user(0.0, y)[1]

    def reset_anchor(self):
        """Titik acuan kembali ke pojok yang paling dekat dengan asal mesin (dipanggil saat perangkat berganti)."""
        b = self.grp.button(self.win.project.device.origin_anchor())
        if b:
            b.setChecked(True)
        self.refresh()

    ANCHOR_NAMES = ("kiri-atas", "tengah-atas", "kanan-atas", "kiri-tengah", "tengah", "kanan-tengah",
                    "kiri-bawah", "tengah-bawah", "kanan-bawah")

    def describe(self):
        """Teks pembacaan posisi/ukuran yang persis sama dengan angka di bar (untuk panel Properti)."""
        if not self.x.isEnabled():
            return ""
        n, _, dec = self.UNITS[self.unit]
        f = "%." + str(dec) + "f"
        return (f"Posisi ({self.ANCHOR_NAMES[self.grp.checkedId()]}): X " + f % self.x.value() + "  Y " + f % self.y.value()
                + f" {n}\nUkuran: " + f % self.w.value() + " × " + f % self.h.value() + f" {n}")
    def _unit_changed(self, i):
        self.unit = i
        name, _, dec = self.UNITS[i]
        for w in (self.x, self.y, self.w, self.h):
            w.setDecimals(dec)
            w.setSuffix(" " + name)
        self.refresh()

    def refresh(self):
        self._loading = True
        bb = self.win.canvas.sel_bbox()
        for w in (self.x, self.y, self.w, self.h, self.lock):
            w.setEnabled(bb is not None)
        if bb is None:
            for w in (self.x, self.y, self.w, self.h):
                w.setValue(0)
        else:
            k = self._k()
            ax, ay = self._anchor(bb)
            self.x.setValue(ax / k)
            self.y.setValue(self._flip_y(ay) / k)
            self.w.setValue((bb[2] - bb[0]) / k)
            self.h.setValue((bb[3] - bb[1]) / k)
        self._loading = False
        props = getattr(self.win, "props", None)
        if props is not None:
            props.show_pos(self.describe())

    # ---- edit
    def _pos(self):
        if self._loading:
            return
        bb = self.win.canvas.sel_bbox()
        if not bb:
            return
        k = self._k()
        ax, ay = self._anchor(bb)
        dx = self.x.value() * k - ax
        dy = self._flip_y(self.y.value() * k) - ay
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return
        self.win.push_undo("bar-pos")
        for s in self.win.canvas.selected_shapes():
            s.translate(dx, dy)
        self.win.geometry_edited(from_bar=True)
        self.refresh()

    def _size(self, which):
        if self._loading:
            return
        bb = self.win.canvas.sel_bbox()
        if not bb:
            return
        k = self._k()
        ow, oh = bb[2] - bb[0], bb[3] - bb[1]
        sx = self.w.value() * k / ow if ow > 1e-6 else 1.0
        sy = self.h.value() * k / oh if oh > 1e-6 else 1.0
        if self.lock.isChecked():
            s = sx if which == "w" else sy
            sx = sy = s
        if sx <= 0 or sy <= 0:
            self.refresh()
            return
        ax, ay = self._anchor(bb)
        self.win.push_undo("bar-size")
        for sh in self.win.canvas.selected_shapes():
            sh.scale(sx, sy, ax, ay)
        self.win.geometry_edited(from_bar=True)
        self.refresh()


# =============================================================================
class PropertiesPanel(QWidget):
    """Info objek, rotasi/cermin, perataan, array, dan bentuk siap pakai."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.info = QLabel("Tidak ada objek terpilih")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.pos_lbl = QLabel("")
        self.pos_lbl.setWordWrap(True)
        self.pos_lbl.setStyleSheet("color:#555;")
        self.pos_lbl.setToolTip("Sama dengan bar Posisi & Ukuran di atas kanvas dan penggaris/kursor pada bed "
                                "(koordinat mesin; 0,0 = titik asal mesin).")
        lay.addWidget(self.pos_lbl)

        gb = QGroupBox("Putar & cermin")
        v = QVBoxLayout(gb)
        r = QHBoxLayout()
        self.angle = dsb(-360, 360, 5, 1, " °")
        b = QPushButton("Putar")
        b.clicked.connect(lambda: self.win.rotate_selection(self.angle.value()))
        r.addWidget(self.angle)
        r.addWidget(b)
        v.addLayout(r)
        v.addLayout(self._grid((
            ("↻ 90°", lambda: self.win.rotate_selection(90), "Putar 90° searah jarum jam"),
            ("↺ 90°", lambda: self.win.rotate_selection(-90), "Putar 90° berlawanan"),
            ("⇋ Cermin H", lambda: self.win.flip_selection(True), "Cermin horizontal"),
            ("⇅ Cermin V", lambda: self.win.flip_selection(False), "Cermin vertikal"))))
        lay.addWidget(gb)

        gb = QGroupBox("Rata & sebar")
        v = QVBoxLayout(gb)
        self.rel = QComboBox()
        self.rel.addItem("Relatif ke seleksi", "sel")
        self.rel.addItem("Relatif ke bed", "bed")
        self.rel.setToolTip("Dengan satu objek, perataan selalu terhadap bed.")
        v.addWidget(self.rel)
        v.addLayout(self._grid((
            ("⇤ Kiri", lambda: self.win.align_selection("left", self.rel.currentData()), ""),
            ("⇥ Kanan", lambda: self.win.align_selection("right", self.rel.currentData()), ""),
            ("⇡ Atas", lambda: self.win.align_selection("top", self.rel.currentData()), ""),
            ("⇣ Bawah", lambda: self.win.align_selection("bottom", self.rel.currentData()), ""),
            ("↔ Tengah H", lambda: self.win.align_selection("hcenter", self.rel.currentData()), ""),
            ("↕ Tengah V", lambda: self.win.align_selection("vcenter", self.rel.currentData()), ""),
            ("⋯ Sebar H", lambda: self.win.distribute_selection("h"), "Jarak sama antar objek (min. 3)"),
            ("⋮ Sebar V", lambda: self.win.distribute_selection("v"), "Jarak sama antar objek (min. 3)"),
            ("Tengah bed", self.win.center_selection, "Letakkan di tengah area kerja"),
            ("Ke titik 0,0", self.win.corner_selection, "Pindah ke titik 0,0 mesin (pojok bed yang sama dengan penanda '0,0' merah-hijau)"))))
        lay.addWidget(gb)

        gb = QGroupBox("Salin berulang")
        v = QVBoxLayout(gb)
        ba = QPushButton("Array grid / melingkar…")
        ba.clicked.connect(self.win.array_dialog)
        v.addWidget(ba)
        lay.addWidget(gb)

        gb = QGroupBox("Bentuk siap pakai")
        h = QHBoxLayout(gb)
        self.lib = QComboBox()
        from .. import shapes as _shp
        self.lib.addItems(list(_shp.LIBRARY))
        self.lib_size = dsb(1, 1000, 5, 1, " mm")
        self.lib_size.setValue(40)
        bl = QPushButton("Tambah")
        bl.clicked.connect(lambda: self.win.add_library_shape(self.lib.currentText(), self.lib_size.value()))
        h.addWidget(self.lib, 1)
        h.addWidget(self.lib_size)
        h.addWidget(bl)
        lay.addWidget(gb)
        lay.addStretch(1)

    @staticmethod
    def _grid(items):
        g = QGridLayout()
        for i, (t, fn, tip) in enumerate(items):
            bt = QPushButton(t)
            if tip:
                bt.setToolTip(tip)
            bt.clicked.connect(lambda _=False, fn=fn: fn())
            g.addWidget(bt, i // 2, i % 2)
        return g

    def show_pos(self, text):
        self.pos_lbl.setText(text)

    def refresh(self):
        sh = self.win.canvas.selected_shapes()
        if not sh:
            self.info.setText("Tidak ada objek terpilih")
            self.pos_lbl.setText("")
            return
        layers = sorted({self.win.project.layer(s.layer).name for s in sh})
        pts = sum(s.point_count() for s in sh if hasattr(s, "point_count"))
        self.info.setText(f"{len(sh)} objek • layer: {', '.join(layers)}" + (f" • {pts} titik" if pts else ""))


# =============================================================================
class ImagePanel(QWidget):
    PRESETS = {
        "Foto (Jarvis, halus)": dict(method="jarvis", lpmm=10.0, contrast=10.0, brightness=0.0, gamma=1.0, sharpen=40.0, blur=0.0, autolevel=True),
        "Foto (Floyd)": dict(method="floyd", lpmm=10.0, contrast=10.0, brightness=0.0, gamma=1.0, sharpen=0.0, blur=0.0, autolevel=False),
        "Logo / teks": dict(method="threshold", lpmm=10.0, contrast=0.0, brightness=0.0, gamma=1.0, threshold=128, sharpen=0.0, blur=0.0, autolevel=False),
        "Grayscale (daya variabel)": dict(method="gray", lpmm=10.0, contrast=0.0, brightness=0.0, gamma=1.0, gray_levels=64, sharpen=0.0, blur=0.0, autolevel=True),
        "Sketsa (Atkinson)": dict(method="atkinson", lpmm=8.0, contrast=15.0, brightness=0.0, gamma=1.0, sharpen=0.0, blur=0.0, autolevel=False),
        "Koran (halftone titik)": dict(method="halftone", lpmm=10.0, contrast=10.0, cell_mm=0.8, cell_angle=45.0, sharpen=0.0, blur=0.0, autolevel=True),
        "Engraving (halftone garis)": dict(method="lines", lpmm=10.0, contrast=10.0, cell_mm=0.6, cell_angle=0.0, sharpen=0.0, blur=0.0, autolevel=True),
    }

    def __init__(self, win):
        super().__init__()
        self.win = win
        self._loading = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.info = QLabel("Pilih gambar di kanvas untuk mengubah pengolahannya.")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.form_w = QWidget()
        f = QFormLayout(self.form_w)
        self.form = f
        self.preset = QComboBox()
        self.preset.addItem("— Preset —")
        self.preset.addItems(list(self.PRESETS))
        self.method = QComboBox()
        for key, label in raster.METHODS:
            self.method.addItem(label, key)
        self.lpmm = dsb(0.5, 60, 1, 2, " garis/mm")
        self.angle = dsb(-180, 180, 5, 1, " °")
        self.bright = dsb(-100, 100, 5, 0)
        self.contrast = dsb(-100, 100, 5, 0)
        self.gamma = dsb(0.1, 5, 0.1, 2)
        self.sharpen = dsb(0, 300, 10, 0, " %")
        self.blur = dsb(0, 5, 0.2, 1, " px")
        self.thr = isb(0, 255)
        self.white = isb(0, 255)
        self.levels = isb(2, 256)
        self.cell = dsb(0.2, 10, 0.1, 2, " mm")
        self.cell_ang = dsb(-90, 90, 5, 0, " °")
        self.autolevel = QCheckBox("Auto-level (regangkan kontras otomatis)")
        self.invert = QCheckBox("Negatif (invert)")
        self.rows = {}

        def add(key, label, widget):
            f.addRow(label, widget)
            self.rows[key] = (f.labelForField(widget), widget)

        add("preset", "Preset", self.preset)
        add("method", "Metode", self.method)
        add("lpmm", "Resolusi", self.lpmm)
        add("angle", "Sudut pindai", self.angle)
        add("bright", "Kecerahan", self.bright)
        add("contrast", "Kontras", self.contrast)
        add("gamma", "Gamma", self.gamma)
        add("sharpen", "Pertajam", self.sharpen)
        add("blur", "Haluskan", self.blur)
        add("thr", "Ambang", self.thr)
        add("white", "Putih ≥", self.white)
        add("levels", "Level abu-abu", self.levels)
        add("cell", "Ukuran sel", self.cell)
        add("cell_ang", "Sudut screen", self.cell_ang)
        f.addRow(self.autolevel)
        f.addRow(self.invert)
        lay.addWidget(self.form_w)

        qd = QHBoxLayout()
        qd.addWidget(QLabel("Arah pindai:"))
        for t, ang in (("↔ 0°", 0), ("◢ 45°", 45), ("↕ 90°", 90)):
            b = QPushButton(t)
            b.clicked.connect(lambda _=False, a=ang: self.angle.setValue(a))
            qd.addWidget(b)
        lay.addLayout(qd)

        rr = QHBoxLayout()
        rr.addWidget(QLabel("Putar gambar:"))
        self.rot = dsb(-180, 180, 5, 1, " °")
        self.rot.setToolTip("Putar gambar searah jarum jam mengelilingi pusatnya (bebas, tidak harus 90°).\n"
                            "Di kanvas: seret bulatan di atas kotak pilihan (Shift = kelipatan 15°).")
        rr.addWidget(self.rot, 1)
        b0 = QPushButton("0°")
        b0.clicked.connect(lambda: self.rot.setValue(0))
        rr.addWidget(b0)
        lay.addLayout(rr)

        gb = QGroupBox("Potong (crop) & ukuran")
        g = QGridLayout(gb)
        self.crop = {}
        for i, (key, lbl) in enumerate((("l", "Kiri"), ("t", "Atas"), ("r", "Kanan"), ("b", "Bawah"))):
            sp = dsb(0, 90, 1, 1, " %")
            self.crop[key] = sp
            g.addWidget(QLabel(lbl), i // 2, (i % 2) * 2)
            g.addWidget(sp, i // 2, (i % 2) * 2 + 1)
        bc = QPushButton("Potong")
        bc.clicked.connect(self._apply_crop)
        br = QPushButton("Rasio asli")
        br.setToolTip("Kembalikan perbandingan lebar:tinggi sesuai piksel gambar (lebar tetap)")
        br.clicked.connect(self._restore_ratio)
        g.addWidget(bc, 2, 0, 1, 2)
        g.addWidget(br, 2, 2, 1, 2)
        lay.addWidget(gb)

        self.btn_trace = QPushButton("Ubah ke vektor (trace) → layer garis")
        self.btn_trace.setToolTip("Melacak kontur gambar menjadi path vektor memakai ambang saat ini.")
        self.btn_trace.clicked.connect(self.win.trace_selected_image)
        lay.addWidget(self.btn_trace)
        lay.addStretch(1)

        for w, a in ((self.lpmm, "lpmm"), (self.angle, "angle"), (self.bright, "brightness"),
                     (self.contrast, "contrast"), (self.gamma, "gamma"), (self.sharpen, "sharpen"),
                     (self.blur, "blur"), (self.cell, "cell_mm"), (self.cell_ang, "cell_angle")):
            w.valueChanged.connect(lambda v, a=a: self._set(a, float(v)))
        for w, a in ((self.thr, "threshold"), (self.white, "white_clip"), (self.levels, "gray_levels")):
            w.valueChanged.connect(lambda v, a=a: self._set(a, int(v)))
        self.invert.toggled.connect(lambda v: self._set("invert", bool(v)))
        self.autolevel.toggled.connect(lambda v: self._set("autolevel", bool(v)))
        self.method.currentIndexChanged.connect(lambda i: self._set("method", self.method.currentData()))
        self.preset.activated.connect(self._preset)
        self.rot.valueChanged.connect(self._rot_changed)
        self.refresh()

    def images(self):
        return [s for s in self.win.canvas.selected_shapes() if isinstance(s, ImageShape)]

    def _vis(self, method):
        show = {k: True for k in self.rows}
        show["thr"] = method in ("threshold", "trace")
        show["levels"] = method == "gray"
        show["cell"] = show["cell_ang"] = method in ("halftone", "lines")
        for k, (lbl, w) in self.rows.items():
            if lbl is not None:
                lbl.setVisible(show[k])
            w.setVisible(show[k])

    def refresh(self):
        ims = self.images()
        self.form_w.setEnabled(bool(ims))
        self.btn_trace.setEnabled(bool(ims))
        if not ims:
            self.info.setText("Pilih gambar di kanvas untuk mengubah pengolahannya.")
            self._vis("gray")
            return
        o = ims[0].opts
        self._loading = True
        self.method.setCurrentIndex(max(0, self.method.findData(o.method)))
        self.lpmm.setValue(o.lpmm)
        self.angle.setValue(o.angle)
        self.bright.setValue(o.brightness)
        self.contrast.setValue(o.contrast)
        self.gamma.setValue(o.gamma)
        self.sharpen.setValue(o.sharpen)
        self.blur.setValue(o.blur)
        self.thr.setValue(o.threshold)
        self.white.setValue(o.white_clip)
        self.levels.setValue(o.gray_levels)
        self.cell.setValue(o.cell_mm)
        self.cell_ang.setValue(o.cell_angle)
        self.autolevel.setChecked(o.autolevel)
        self.invert.setChecked(o.invert)
        a = ims[0].angle
        self.rot.setValue(a if a <= 180 else a - 360)
        self.preset.setCurrentIndex(0)
        self._vis(o.method)
        self._loading = False
        self._update_info()

    def _update_info(self):
        ims = self.images()
        if not ims:
            return
        s = ims[0]
        o = s.opts
        v = s.angle if s.angle <= 180 else s.angle - 360
        if abs(self.rot.value() - v) > 0.06 and not self._loading:      # sinkron saat diputar dari kanvas
            self._loading = True
            self.rot.setValue(v)
            self._loading = False
        cols, rows = raster._grid(s.w, s.h, o.lpmm)
        speed = max(self.win.project.layer(s.layer).speed, 1.0)
        est = rows * s.w / speed          # menit, kasar (tanpa lompat area putih / overscan)
        self.info.setText(f"Sumber {s.img.width}×{s.img.height} px → {s.w:.1f}×{s.h:.1f} mm\n"
                          f"{rows} baris • jarak {1 / o.lpmm:.3f} mm (≈{o.lpmm * 25.4:.0f} DPI)\n"
                          f"Perkiraan kasar ≤ {est:.0f} menit pada {speed:g} mm/mnt")

    def _set(self, attr, val):
        if self._loading:
            return
        self.win.push_undo("img-" + attr)
        for s in self.images():
            setattr(s.opts, attr, val)
        if attr == "method":
            self._vis(val)
        self.win.canvas.update()
        self._update_info()

    def _preset(self, i):
        if i <= 0:
            return
        d = self.PRESETS[self.preset.currentText()]
        self.win.push_undo("img-preset")
        for s in self.images():
            for k, v in d.items():
                setattr(s.opts, k, v)
        self.refresh()
        self.win.canvas.update()

    def _rot_changed(self, v):
        if self._loading:
            return
        ims = self.images()
        if not ims:
            return
        self.win.push_undo("img-rot")
        for s in ims:
            s.set_angle(float(v))
        self.win.geometry_edited()

    def _apply_crop(self):
        ims = self.images()
        if not ims:
            return
        c = {k: sp.value() / 100.0 for k, sp in self.crop.items()}
        if c["l"] + c["r"] >= 0.95 or c["t"] + c["b"] >= 0.95:
            self.win.note("Potongan terlalu besar", 4000)
            return
        if any(abs(s.angle) > 1e-9 for s in ims):
            self.win.note("Potong tidak tersedia untuk gambar yang diputar bebas. Setel putaran ke 0° dulu.", 5000)
            return
        self.win.push_undo()
        for s in ims:
            s.crop(c["l"], c["t"], c["r"], c["b"])
        for sp in self.crop.values():
            sp.setValue(0)
        self.win.geometry_edited()

    def _restore_ratio(self):
        ims = self.images()
        if not ims:
            return
        self.win.push_undo()
        for s in ims:
            s.h = s.w * s.img.height / s.img.width
        self.win.geometry_edited()


# =============================================================================
class LaserPanel(QWidget):
    def __init__(self, win):
        super().__init__()
        self.win = win
        s = win.sender
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)

        prow = QHBoxLayout()
        self.profile = QComboBox()
        self.profile.setToolTip("Profil perangkat (mesin). Ubah/simpan lewat tombol ⚙.")
        gear = QToolButton()
        gear.setText("⚙")
        gear.setToolTip("Pengaturan perangkat & profil")
        gear.clicked.connect(win.device_settings)
        prow.addWidget(QLabel("Mesin"))
        prow.addWidget(self.profile, 1)
        prow.addWidget(gear)
        lay.addLayout(prow)
        self.profile.activated.connect(self._profile_picked)

        row = QHBoxLayout()
        self.port = QComboBox()
        self.port.setEditable(True)
        self.port.setMinimumWidth(120)
        self.baud = QComboBox()
        for b in (9600, 57600, 115200, 230400, 250000, 460800, 921600):
            self.baud.addItem(str(b), b)
        self.baud.setCurrentText(str(win.project.device.baud))
        rf = QToolButton()
        rf.setText("⟳")
        rf.setToolTip("Segarkan daftar port")
        rf.clicked.connect(self.refresh_ports)
        self.btn_conn = QPushButton("Hubungkan")
        self.btn_conn.clicked.connect(self.toggle_connect)
        row.addWidget(self.port, 1)
        row.addWidget(rf)
        row.addWidget(self.baud)
        lay.addLayout(row)
        lay.addWidget(self.btn_conn)

        self.state = QLabel("Terputus")
        f = QFont()
        f.setBold(True)
        f.setPointSize(13)
        self.state.setFont(f)
        self.state.setAlignment(Qt.AlignCenter)
        self.dro = QLabel("X 0.000   Y 0.000")
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(12)
        self.dro.setFont(mono)
        self.dro.setAlignment(Qt.AlignCenter)
        self.mpos = QLabel("")
        self.mpos.setAlignment(Qt.AlignCenter)
        self.mpos.setStyleSheet("color:#888;")
        lay.addWidget(self.state)
        lay.addWidget(self.dro)
        lay.addWidget(self.mpos)

        # jog
        g = QGridLayout()
        arrows = {(0, 0): ("↖", -1, 1), (0, 1): ("↑", 0, 1), (0, 2): ("↗", 1, 1),
                  (1, 0): ("←", -1, 0), (1, 2): ("→", 1, 0),
                  (2, 0): ("↙", -1, -1), (2, 1): ("↓", 0, -1), (2, 2): ("↘", 1, -1)}
        for (r, c), (t, dx, dy) in arrows.items():
            b = QPushButton(t)
            b.setMinimumSize(40, 36)
            b.clicked.connect(lambda _=False, dx=dx, dy=dy: self.jog(dx, dy))
            g.addWidget(b, r, c)
        z = QPushButton("⌂ 0,0")
        z.setToolTip("Pindah ke titik nol kerja (G0 X0 Y0)")
        z.clicked.connect(s.goto_zero)
        g.addWidget(z, 1, 1)
        lay.addLayout(g)
        r2 = QHBoxLayout()
        self.step = QComboBox()
        self.step.setEditable(True)
        for v in ("0.1", "1", "5", "10", "50", "100"):
            self.step.addItem(v)
        self.step.setCurrentText("10")
        self.feed = dsb(10, 30000, 100, 0, " mm/mnt")
        self.feed.setValue(3000)
        r2.addWidget(QLabel("Langkah"))
        r2.addWidget(self.step)
        r2.addWidget(self.feed)
        lay.addLayout(r2)

        grid = QGridLayout()
        items = (("Home ($H)", s.home, "Homing (perlu limit switch)"),
                 ("Buka kunci ($X)", s.unlock, "Matikan status ALARM"),
                 ("Set nol di sini", s.zero_here, "G10 L20 P1 X0 Y0"),
                 ("Reset (Ctrl-X)", s.reset, "Soft reset"),
                 ("Jeda (!)", s.hold, "Feed hold"),
                 ("Lanjut (~)", s.resume, "Cycle start"))
        for i, (t, fn, tip) in enumerate(items):
            b = QPushButton(t)
            b.setToolTip(tip)
            b.clicked.connect(lambda _=False, fn=fn: fn())
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)

        # tes laser
        gb = QGroupBox("Tes laser")
        tl = QHBoxLayout(gb)
        self.test_en = QCheckBox("Izinkan")
        self.test_pw = dsb(0.1, 100, 1, 1, " %")
        self.test_pw.setValue(1.0)
        self.test_btn = QPushButton("Tahan = nyala")
        self.test_btn.pressed.connect(self._test_on)
        self.test_btn.released.connect(self._test_off)
        tl.addWidget(self.test_en)
        tl.addWidget(self.test_pw)
        tl.addWidget(self.test_btn, 1)
        lay.addWidget(gb)

        # override
        gb2 = QGroupBox("Override saat berjalan")
        ol = QGridLayout(gb2)
        for i, (name, codes) in enumerate((("Kecepatan", (0x92, 0x90, 0x91)), ("Daya", (0x9B, 0x99, 0x9A)))):
            ol.addWidget(QLabel(name), i, 0)
            for j, (t, code) in enumerate(zip(("−10%", "100%", "+10%"), codes)):
                b = QPushButton(t)
                b.clicked.connect(lambda _=False, c=code: s.realtime(c))
                ol.addWidget(b, i, j + 1)
        lay.addWidget(gb2)
        lay.addStretch(1)

        s.connected_changed.connect(self._conn_changed)
        s.status.connect(self._status)
        self.refresh_ports()
        self.reload_profiles()

    def reload_profiles(self):
        """Isi ulang daftar profil; pilih yang sedang aktif; sinkronkan baud & port."""
        dev = self.win.project.device
        self.profile.blockSignals(True)
        self.profile.clear()
        names = self.win.profiles.names()
        self.profile.addItems(names)
        if dev.name in names:
            self.profile.setCurrentText(dev.name)
        else:
            self.profile.addItem(dev.name + " *")
            self.profile.setCurrentIndex(self.profile.count() - 1)
        self.profile.blockSignals(False)
        self.baud.setCurrentText(str(dev.baud))
        if not self.win.sender.connected:
            ports = [self.port.itemText(i) for i in range(self.port.count())]
            if dev.last_port in ports:
                self.port.setCurrentText(dev.last_port)

    def _profile_picked(self, _i):
        name = self.profile.currentText()
        if name and name != self.win.project.device.name:
            self.win.apply_profile(name)

    def refresh_ports(self):
        cur = self.port.currentText()
        self.port.clear()
        ports = list_ports()
        self.port.addItems(ports)
        last = self.win.project.device.last_port
        if cur:
            self.port.setCurrentText(cur)
        elif last in ports:
            self.port.setCurrentText(last)

    def toggle_connect(self):
        s = self.win.sender
        if s.connected:
            s.disconnect()
        else:
            dev = self.win.project.device
            dev.baud = int(self.baud.currentText() or 115200)
            dev.last_port = self.port.currentText().strip()
            if dev.last_port:
                s.set_firmware(dev.firmware)
                s.connect(dev.last_port, dev.baud)
                self.win.save_current_profile()
            else:
                self.win.console.add("err", "Pilih port serial dulu.")

    def _conn_changed(self, on):
        self.btn_conn.setText("Putuskan" if on else "Hubungkan")
        if not on:
            self.state.setText("Terputus")
            self.state.setStyleSheet("")
            self.win.canvas.set_laser(None)

    def _status(self, info):
        st = info.get("state", "")
        self.state.setText(st)
        color = {"Idle": "#2c9a3c", "Run": "#1f7ae0", "Jog": "#1f7ae0", "Hold": "#d09000",
                 "Alarm": "#d23c3c", "Home": "#1f7ae0", "Check": "#888"}.get(st.split(":")[0], "#d09000")
        self.state.setStyleSheet(f"color:{color};")
        w, m = info["wpos"], info["mpos"]
        self.dro.setText(f"X {w[0]:9.3f}   Y {w[1]:9.3f}")
        self.mpos.setText(f"mesin: X {m[0]:.3f}  Y {m[1]:.3f}"
                          + (f"   F{info['feed']:.0f} S{info['spindle']:.0f}" if "feed" in info and "spindle" in info else ""))
        self.win.laser_moved(m, w)

    def jog(self, dx, dy):
        try:
            st = float(self.step.currentText())
        except ValueError:
            return
        self.win.sender.jog(dx * st, dy * st, self.feed.value())

    def _test_on(self):
        if not self.test_en.isChecked():
            self.win.console.add("err", "Centang 'Izinkan' dulu untuk tes laser (pakai kacamata pelindung!).")
            return
        s = self.win.sender
        s.laser_test(self.test_pw.value(), self.win.project.device.s_max, "M3")

    def _test_off(self):
        if self.test_en.isChecked():
            self.win.sender.laser_off()


# =============================================================================
class JobPanel(QWidget):
    ANCHOR_TIPS = ["kiri-atas", "tengah-atas", "kanan-atas", "kiri-tengah", "tengah", "kanan-tengah",
                   "kiri-bawah", "tengah-bawah", "kanan-bawah"]

    def __init__(self, win):
        super().__init__()
        self.win = win
        dev = win.project.device
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        gb = QGroupBox("Titik awal pekerjaan")
        v = QVBoxLayout(gb)
        self.start = QComboBox()
        self.start.addItem("Koordinat absolut (0,0 = pojok bed)", "absolute")
        self.start.addItem("Dari posisi laser saat ini", "current")
        self.start.setCurrentIndex(0 if dev.start_from == "absolute" else 1)
        self.start.currentIndexChanged.connect(self._start_changed)
        v.addWidget(self.start)
        h = QHBoxLayout()
        self.anchor_grp = QButtonGroup(self)
        ag = QGridLayout()
        for i in range(9):
            b = QToolButton()
            b.setCheckable(True)
            b.setFixedSize(26, 26)
            b.setToolTip("Jangkar: " + self.ANCHOR_TIPS[i])
            self.anchor_grp.addButton(b, i)
            ag.addWidget(b, i // 3, i % 3)
        self.anchor_grp.button(dev.anchor).setChecked(True)
        self.anchor_grp.buttonClicked[int].connect(self._anchor_changed)
        h.addLayout(ag)
        self.anchor_lbl = QLabel("Titik pada desain yang diletakkan\ndi posisi laser (mode 'posisi saat ini').")
        self.anchor_lbl.setWordWrap(True)
        h.addWidget(self.anchor_lbl, 1)
        v.addLayout(h)
        lay.addWidget(gb)

        mrow = QHBoxLayout()
        self.mir_x = QCheckBox("Cermin X")
        self.mir_x.setToolTip("Cerminkan pekerjaan secara horizontal (kiri↔kanan) di G-code, mis. mengukir dari sisi belakang kaca/akrilik.\n"
                              "Kotak pekerjaan tetap di tempatnya; hanya isinya yang dibalik.")
        self.mir_y = QCheckBox("Cermin Y")
        self.mir_y.setToolTip("Cerminkan pekerjaan secara vertikal (atas↔bawah) di G-code.")
        self.mir_x.toggled.connect(self._mirror_changed)
        self.mir_y.toggled.connect(self._mirror_changed)
        mrow.addWidget(self.mir_x)
        mrow.addWidget(self.mir_y)
        mrow.addStretch(1)
        lay.addLayout(mrow)
        self.sel_only = QCheckBox("Hanya objek terpilih")
        self.ret = QCheckBox("Kembali ke titik awal setelah selesai")
        self.ret.setChecked(dev.end_return == "origin")
        self.ret.toggled.connect(self._ret_changed)
        lay.addWidget(self.sel_only)
        lay.addWidget(self.ret)

        fr = QHBoxLayout()
        self.frame_laser = QCheckBox("Bingkai: laser nyala")
        self.frame_laser.setToolTip("Hanya untuk melihat posisi: laser menyala redup mengelilingi batas pekerjaan.\n"
                                    "Gunakan daya serendah mungkin (mis. 0,5–2%). Aplikasi akan meminta konfirmasi setiap kali.")
        self.frame_pow = dsb(0.1, 20, 0.5, 1, " %")
        self.frame_pow.setToolTip("Daya laser saat bingkai, persen dari S maks (maks 20% di sini).")
        self.frame_laser.setChecked(dev.frame_laser)
        self.frame_pow.setValue(min(20.0, dev.frame_power if dev.frame_power > 0 else 1.0))
        self.frame_pow.setEnabled(dev.frame_laser)
        self.frame_laser.toggled.connect(self._frame_changed)
        self.frame_pow.valueChanged.connect(self._frame_changed)
        fr.addWidget(self.frame_laser)
        fr.addWidget(self.frame_pow)
        lay.addLayout(fr)
        b1 = QHBoxLayout()
        self.btn_prev = QPushButton("Pratinjau")
        self.btn_prev.clicked.connect(self.win.show_preview)
        self.btn_frame = QPushButton("Bingkai")
        self.btn_frame.setToolTip("Gerakkan laser mengelilingi batas pekerjaan (laser mati, atau menyala redup bila opsi di atas dicentang)")
        self.btn_frame.clicked.connect(self.win.frame_job)
        b1.addWidget(self.btn_prev)
        b1.addWidget(self.btn_frame)
        lay.addLayout(b1)
        b2 = QHBoxLayout()
        self.btn_save = QPushButton("Simpan G-code…")
        self.btn_save.clicked.connect(self.win.export_gcode)
        self.btn_open = QPushButton("Kirim file G-code…")
        self.btn_open.setToolTip("Muat file .gcode/.nc, pratinjau, lalu kirim langsung ke laser (seperti LaserGRBL)")
        self.btn_open.clicked.connect(self.win.open_gcode_file)
        b2.addWidget(self.btn_save)
        b2.addWidget(self.btn_open)
        lay.addLayout(b2)
        self.btn_start = QPushButton("▶  Mulai")
        self.btn_start.setStyleSheet("font-weight:bold;padding:8px;")
        self.btn_start.clicked.connect(self.win.start_job)
        lay.addWidget(self.btn_start)
        b3 = QHBoxLayout()
        self.btn_pause = QPushButton("Jeda")
        self.btn_pause.clicked.connect(self.win.pause_resume)
        self.btn_stop = QPushButton("■ Henti")
        self.btn_stop.setStyleSheet("color:#c02020;font-weight:bold;")
        self.btn_stop.clicked.connect(self.win.stop_job)
        b3.addWidget(self.btn_pause)
        b3.addWidget(self.btn_stop)
        lay.addLayout(b3)
        self.btn_resume = QPushButton("↻ Lanjutkan dari baris…")
        self.btn_resume.setToolTip("Lanjutkan pekerjaan yang terhenti (alarm / daya putus), juga setelah aplikasi ditutup (Ctrl+F7)")
        self.btn_resume.clicked.connect(self.win.resume_job)
        lay.addWidget(self.btn_resume)
        self.bar = QProgressBar()
        self.bar.setValue(0)
        lay.addWidget(self.bar)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        lay.addStretch(1)
        self._start_changed()

    def _persist(self):
        """Simpan perubahan titik awal/jangkar/kembali ke profil aktif (bukan saat memuat dari perangkat)."""
        if not getattr(self, "_loading", False) and hasattr(self.win, "profiles"):
            self.win.save_current_profile()

    def _mirror_changed(self, *_):
        if not getattr(self, "_loading", False):
            self.win.set_mirror(self.mir_x.isChecked(), self.mir_y.isChecked())

    def _frame_changed(self, *_):
        if getattr(self, "_loading", False):
            return
        dev = self.win.project.device
        dev.frame_laser = self.frame_laser.isChecked()
        dev.frame_power = float(self.frame_pow.value())
        self.frame_pow.setEnabled(dev.frame_laser)
        self._persist()

    def _ret_changed(self, v):
        self.win.project.device.end_return = "origin" if v else "none"
        self._persist()

    def _start_changed(self, *_):
        dev = self.win.project.device
        dev.start_from = self.start.currentData()
        self._persist()
        on = dev.start_from == "current"
        for b in self.anchor_grp.buttons():
            b.setEnabled(on)
        self.anchor_lbl.setEnabled(on)

    def _anchor_changed(self, i):
        self.win.project.device.anchor = int(i)
        self._persist()

    def reload_from_device(self):
        dev = self.win.project.device
        self._loading = True
        try:
            self.start.setCurrentIndex(0 if dev.start_from == "absolute" else 1)
            b = self.anchor_grp.button(dev.anchor)
            if b:
                b.setChecked(True)
            self.ret.setChecked(dev.end_return == "origin")
            self.frame_laser.setChecked(dev.frame_laser)
            self.frame_pow.setValue(min(20.0, dev.frame_power if dev.frame_power > 0 else 1.0))
            self.frame_pow.setEnabled(dev.frame_laser)
            pr = self.win.project
            self.mir_x.setChecked(pr.mirror_x)
            self.mir_y.setChecked(pr.mirror_y)
            self._start_changed()
        finally:
            self._loading = False


# =============================================================================
class ConsolePanel(QWidget):
    COL = {"tx": "#1f6fd0", "rx": "#6b7280", "err": "#d23c3c", "info": "#2c9a3c"}

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.hist = []
        self.hpos = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(4000)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        self.view.setFont(mono)
        lay.addWidget(self.view, 1)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Perintah GRBL / G-code, Enter untuk kirim (mis. $$, $I, G0 X10)")
        self.edit.returnPressed.connect(self._send)
        self.edit.installEventFilter(self)
        clr = QPushButton("Bersihkan")
        clr.clicked.connect(self.view.clear)
        row.addWidget(self.edit, 1)
        row.addWidget(clr)
        lay.addLayout(row)

    def eventFilter(self, obj, ev):
        from PyQt5.QtCore import QEvent
        if obj is self.edit and ev.type() == QEvent.KeyPress and self.hist:
            if ev.key() == Qt.Key_Up:
                self.hpos = max(0, self.hpos - 1)
                self.edit.setText(self.hist[self.hpos])
                return True
            if ev.key() == Qt.Key_Down:
                self.hpos = min(len(self.hist), self.hpos + 1)
                self.edit.setText(self.hist[self.hpos] if self.hpos < len(self.hist) else "")
                return True
        return super().eventFilter(obj, ev)

    def _send(self):
        t = self.edit.text().strip()
        if not t:
            return
        self.hist.append(t)
        self.hpos = len(self.hist)
        self.edit.clear()
        self.win.sender.send_line(t)

    def add(self, kind, text):
        col = self.COL.get(kind, "#000")
        prefix = {"tx": "› ", "rx": "‹ ", "err": "! ", "info": "• "}.get(kind, "")
        self.view.appendHtml(f'<span style="color:{col}">{html.escape(prefix + text)}</span>')
