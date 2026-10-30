# SPDX-License-Identifier: MIT
"""Jendela utama Grblaser."""
from __future__ import annotations

import json
import math
import os
import time

from PyQt5.QtCore import QPointF, QSettings, Qt, QTimer
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QDialog, QDockWidget, QDoubleSpinBox,
                             QFileDialog, QLabel, QMainWindow, QMenu, QMessageBox, QScrollArea, QSpinBox,
                             QToolBar)

from .. import __version__, importers, model, raster, shapes as shp
from ..gcode import clean_gcode, emit_gcode, frame_lines, machine_bounds, make_transform
from PIL import Image
from ..model import Device, ImageShape, MAX_LAYERS, MODE_LABELS, PathShape, Project
from ..project import load_project, save_project
from ..sender import GrblSender
from ..toolpath import build_job
from .canvas import Canvas
from .. import preflight
from ..autosave import Autosave
from ..materials import Materials
from .. import resume as resume_mod
from .. import materialtest
from ..profiles import Profiles
from .dialogs import (ArrayDialog, DeviceDialog, MaterialTestDialog, MaterialsDialog, OffsetDialog, ResumeDialog,
                      TextDialog, text_polys)
from .panels import (ConsolePanel, ImagePanel, JobPanel, LaserPanel, LayerPanel, PosSizeBar,
                     PropertiesPanel)
from .tasks import TaskRunner
from .preview import gcode_to_design_segments, ops_to_segments, render_segments

RASTER_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
GCODE_EXT = {".gcode", ".nc", ".gc", ".ngc", ".tap", ".gco"}
IMPORT_FILTER = ("Didukung (*.svg *.dxf *.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;"
                 "Vektor (*.svg *.dxf);;Gambar (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp);;Semua (*)")


def fmt_time(sec):
    sec = int(round(sec))
    return "%d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("Kalingin", "Grblaser")
        self.project = Project()
        self._load_device_pref()
        self.profiles = Profiles(self.settings)
        self.profiles.ensure(self.project.device)
        self.materials = Materials(self.settings)
        self.sender = GrblSender(self)
        self.active_layer = 0
        self.undo_stack, self.redo_stack = [], []
        self._undo_key, self._undo_t = None, 0.0
        self.dirty = False
        self.clipboard = []
        self.rev = 0                 # naik setiap proyek berubah (untuk cache job)
        self._job_cache = None
        self.last_job = None
        self.tasks = TaskRunner(self)
        self._job_t0 = 0.0
        self._pending_job_summary = ""

        self.canvas = Canvas(self.project)
        self.setCentralWidget(self.canvas)
        self.console = ConsolePanel(self)
        self.layer_panel = LayerPanel(self)
        self.props = PropertiesPanel(self)
        self.image_panel = ImagePanel(self)
        self.laser_panel = LaserPanel(self)
        self.job = JobPanel(self)

        self._dock("Layer", self.layer_panel, Qt.RightDockWidgetArea)
        d2 = self._dock("Properti", self.props, Qt.RightDockWidgetArea)
        d3 = self._dock("Gambar", self.image_panel, Qt.RightDockWidgetArea)
        self.tabifyDockWidget(self.docks["Layer"], d2)
        self.tabifyDockWidget(d2, d3)
        self.docks["Layer"].raise_()
        self._dock("Laser", self.laser_panel, Qt.LeftDockWidgetArea)
        d5 = self._dock("Pekerjaan", self.job, Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(self.docks["Laser"], d5)
        self.docks["Laser"].raise_()
        self._dock("Konsol", self.console, Qt.BottomDockWidgetArea, scroll=False)
        for panel, wmin in ((self.layer_panel, 340), (self.props, 320), (self.image_panel, 340),
                            (self.laser_panel, 320), (self.job, 320)):
            panel.setMinimumWidth(wmin)

        self.possize = PosSizeBar(self)
        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        self.lbl_cursor = QLabel("X 0.0  Y 0.0")
        self.lbl_state = QLabel("Terputus")
        self.lbl_zoom = QLabel("")
        for w in (self.lbl_cursor, self.lbl_zoom, self.lbl_state):
            self.statusBar().addPermanentWidget(w)

        c = self.canvas
        self.set_tool('select')
        c.selectionChanged.connect(self._selection_changed)
        c.aboutToEdit.connect(lambda k: self.push_undo(k if k == "nudge" else None))
        c.geometryChanged.connect(lambda: self.geometry_edited())
        c.shapeCreated.connect(self._shape_created)
        c.textRequested.connect(self._text_requested)
        c.filesDropped.connect(self.import_files)
        c.contextRequested.connect(self._context_menu)
        c.doubleClicked.connect(self._canvas_double_click)
        c.cursorMoved.connect(self._cursor_moved)
        c.toolReset.connect(lambda: self.act_tools["select"].setChecked(True))
        c.deleteRequested.connect(self.delete_selection)

        s = self.sender
        s.log.connect(self.console.add)
        s.progress.connect(self._progress)
        s.job_state.connect(self._job_state)
        s.alarm.connect(lambda m: self.statusBar().showMessage("ALARM: " + m, 10000))
        s.status.connect(lambda info: self.lbl_state.setText(info.get("state", "")))
        s.connected_changed.connect(lambda on: self.lbl_state.setText("Terhubung" if on else "Terputus"))
        s.connected_changed.connect(lambda on: self._update_resume_action())

        self._zoom_timer = QTimer(self)
        self._zoom_timer.timeout.connect(lambda: self.lbl_zoom.setText("%d%%" % round(self.canvas.zoom / 3.78 * 100)
                                                                       if False else "%.2f px/mm" % self.canvas.zoom))
        self._zoom_timer.start(300)

        self.autosave = Autosave()
        self.job_store = resume_mod.LastJob(self.autosave.dir)
        self._resume_job = None        # job terakhir di memori: {'lines', 'n', 'prefix', 'acked'}
        self._autosaved_rev = -1
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.autosave_now)
        self._autosave_timer.start(45000)

        self._update_title()
        self._set_job_buttons("idle")
        self.resize(1400, 860)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        state = self.settings.value("state")
        if state:
            self.restoreState(state)
        else:
            QTimer.singleShot(0, self._default_dock_sizes)
        self.setAcceptDrops(False)
        self.console.add("info", f"Grblaser {__version__} siap. Seret file SVG/DXF/gambar ke kanvas.")

    def _default_dock_sizes(self):
        """Ukuran awal dock: kanvas dapat ruang terbanyak, konsol pendek (layar laptop 1366x768 pun muat)."""
        self.resizeDocks([self.docks["Laser"], self.docks["Layer"]], [335, 350], Qt.Horizontal)
        self.resizeDocks([self.docks["Konsol"]], [120], Qt.Vertical)

    # ------------------------------------------------------------------ dock
    def _dock(self, title, widget, area, scroll=True):
        d = QDockWidget(title, self)
        d.setObjectName("dock_" + title)
        if scroll:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QScrollArea.NoFrame)
            sa.setWidget(widget)
            d.setWidget(sa)
        else:
            d.setWidget(widget)
        self.addDockWidget(area, d)
        if not hasattr(self, "docks"):
            self.docks = {}
        self.docks[title] = d
        return d

    # ------------------------------------------------------------------ aksi
    def _act(self, text, fn, shortcut=None, tip=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        a.setCheckable(checkable)
        a.triggered.connect(lambda checked=False: fn())
        return a

    def _build_actions(self):
        A = self._act
        self.a_new = A("Baru", self.new_project, "Ctrl+N")
        self.a_open = A("Buka proyek…", self.open_project, "Ctrl+O")
        self.a_save = A("Simpan", self.save_project, "Ctrl+S")
        self.a_saveas = A("Simpan sebagai…", lambda: self.save_project(True), "Ctrl+Shift+S")
        self.a_import = A("Impor (SVG / DXF / Gambar)…", self.import_dialog, "Ctrl+I")
        self.a_export = A("Ekspor G-code…", self.export_gcode, "Ctrl+E")
        self.a_sendfile = A("Kirim file G-code…", self.open_gcode_file)
        self.a_quit = A("Keluar", self.close, "Ctrl+Q")
        self.a_undo = A("Urungkan", self.undo, "Ctrl+Z")
        self.a_redo = A("Ulangi", self.redo, "Ctrl+Y")
        self.a_copy = A("Salin", self.copy_selection, "Ctrl+C")
        self.a_paste = A("Tempel", self.paste, "Ctrl+V")
        self.a_dup = A("Duplikat", self.duplicate_selection, "Ctrl+D")
        self.a_del = A("Hapus", self.delete_selection)
        self.a_all = A("Pilih semua", self.select_all, "Ctrl+A")
        self.a_group = A("Grup", self.group_selection, "Ctrl+G", "Gabungkan objek terpilih menjadi satu grup (Ctrl+G)")
        self.a_ungroup = A("Pisahkan grup", self.ungroup_selection, "Ctrl+Shift+G", "Pecah grup terpilih (Ctrl+Shift+G / Ctrl+U)")
        self.a_edittext = A("Edit teks…", self.edit_text, "Ctrl+Return", "Ubah isi/font/ukuran teks terpilih (juga: klik ganda pada teks)")
        self.a_mtest = A("Uji bahan…", self.material_test_dialog, "", "Buat grid uji kecepatan × daya (satu layer per sel)")
        self.a_mtest_rm = A("Hapus objek & layer uji bahan", self.remove_material_test)
        self.a_ungroup.setShortcuts([QKeySequence("Ctrl+Shift+G"), QKeySequence("Ctrl+U")])
        self.a_rotcw = A("Putar 90° ↻", lambda: self.rotate_selection(90))
        self.a_rotccw = A("Putar 90° ↺", lambda: self.rotate_selection(-90))
        self.a_fliph = A("Cermin horizontal", lambda: self.flip_selection(True))
        self.a_flipv = A("Cermin vertikal", lambda: self.flip_selection(False))
        self.a_array = A("Array grid / melingkar…", self.array_dialog, "Ctrl+Shift+A")
        self.a_center = A("Letakkan di tengah bed", self.center_selection)
        self.a_corner = A("Pindah ke titik 0,0 mesin", self.corner_selection)
        self.a_fit = A("Zoom ke bed", self.canvas.fit, "F")
        self.a_fitsel = A("Zoom ke objek", self.canvas.fit_selection, "Shift+F")
        self.a_grid = A("Tampilkan grid", self._toggle_grid, None, checkable=True)
        self.a_grid.setChecked(True)
        self.a_hideprev = A("Sembunyikan pratinjau", self.invalidate_preview)
        self.a_prev = A("Pratinjau pekerjaan", self.show_preview, "F5")
        self.a_sim = A("▶ Simulasi jalur laser…", self.show_simulation, "Shift+F5", "Animasi urutan & arah pembakaran")
        self.a_frame = A("Bingkai", self.frame_job, "F6")
        self.a_start = A("▶ Mulai", self.start_job, "F7")
        self.a_pause = A("Jeda / Lanjut", self.pause_resume, "F8")
        self.a_stop = A("■ Henti", self.stop_job, "F9")
        self.a_resume = A("↻ Lanjutkan dari baris…", self.resume_job, "Ctrl+F7",
                          "Lanjutkan pekerjaan yang terhenti dari baris tertentu (juga setelah aplikasi ditutup)")
        self.a_dev = A("Pengaturan perangkat…", self.device_settings)
        self.a_about = A("Tentang", self.about)
        self.a_help = A("Pintasan & petunjuk", self.help)
        self.act_tools = {}
        grp = QActionGroup(self)
        for key, label, sc in (("select", "▸ Pilih", "S"), ("rect", "▭ Persegi", "R"), ("ellipse", "◯ Elips", "E"),
                               ("polygon", "⬡ Poligon", "G"), ("star", "★ Bintang", "B"),
                               ("line", "╱ Garis", "L"), ("polyline", "⌇ Polyline", "P"), ("text", "T Teks", "T")):
            a = QAction(label, self)
            a.setCheckable(True)
            a.setShortcut(QKeySequence(sc))
            a.setToolTip(f"{label[2:]} ({sc})")
            a.triggered.connect(lambda checked=False, k=key: self.set_tool(k))
            grp.addAction(a)
            self.act_tools[key] = a
        self.act_tools["select"].setChecked(True)

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&Berkas")
        for a in (self.a_new, self.a_open, self.a_save, self.a_saveas, None, self.a_import, self.a_export,
                  self.a_sendfile, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Edit")
        for a in (self.a_undo, self.a_redo, None, self.a_copy, self.a_paste, self.a_dup, self.a_del, self.a_all):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Susun")
        for a in (self.a_group, self.a_ungroup, self.a_edittext, None, self.a_mtest, self.a_mtest_rm, None,
                  self.a_rotcw, self.a_rotccw, self.a_fliph, self.a_flipv, None, self.a_center, self.a_corner,
                  None, self.a_array):
            m.addSeparator() if a is None else m.addAction(a)
        sub = m.addMenu("Rata")
        for label, mode in (("Kiri", "left"), ("Kanan", "right"), ("Atas", "top"), ("Bawah", "bottom"),
                            ("Tengah horizontal", "hcenter"), ("Tengah vertikal", "vcenter")):
            sub.addAction(self._act(label, lambda mode=mode: self.align_selection(mode, "sel")))
        sub = m.addMenu("Sebar")
        sub.addAction(self._act("Horizontal", lambda: self.distribute_selection("h")))
        sub.addAction(self._act("Vertikal", lambda: self.distribute_selection("v")))
        sub = m.addMenu("Boolean (butuh shapely)")
        for label, op in (("Gabung (union)", "union"), ("Kurangi (objek bawah − yang lain)", "subtract"),
                          ("Irisan (intersect)", "intersect")):
            sub.addAction(self._act(label, lambda op=op: self.boolean_selection(op)))
        m.addAction(self._act("Offset kontur…", self.offset_dialog))
        sub = m.addMenu("Bentuk siap pakai")
        for name in shp.LIBRARY:
            sub.addAction(self._act(name, lambda name=name: self.add_library_shape(name, 40.0)))
        m = mb.addMenu("&Tampilan")
        for a in (self.a_fit, self.a_fitsel, self.a_grid, self.a_hideprev):
            m.addAction(a)
        m.addSeparator()
        for d in self.docks.values():
            m.addAction(d.toggleViewAction())
        m = mb.addMenu("&Laser")
        for a in (self.a_prev, self.a_sim, self.a_frame, self.a_start, self.a_pause, self.a_stop, self.a_resume, None, self.a_dev):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("Ba&ntuan")
        m.addAction(self.a_help)
        m.addAction(self.a_about)

    def _build_toolbar(self):
        tb = QToolBar("Alat")
        tb.setObjectName("toolbar_main")
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        tb.setMovable(False)
        self.addToolBar(tb)
        # label singkat khusus toolbar (menu tetap memakai teks lengkap) agar Mulai/Henti tidak masuk overflow
        for a, t in ((self.a_import, "Impor"), (self.a_save, "Simpan"), (self.a_undo, "↶"), (self.a_redo, "↷"),
                     (self.a_fit, "⤢ Fit"), (self.a_prev, "Pratinjau"), (self.a_frame, "Bingkai"),
                     (self.a_start, "▶ Mulai"), (self.a_stop, "■ Henti")):
            a.setIconText(t)
        for a in (self.a_import, self.a_save):
            tb.addAction(a)
        tb.addSeparator()
        for a in self.act_tools.values():
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_undo, self.a_redo, self.a_fit):
            tb.addAction(a)
        tb.addSeparator()
        for a in (self.a_prev, self.a_frame, self.a_start, self.a_stop):
            tb.addAction(a)
        self.addToolBarBreak()
        tb2 = QToolBar("Posisi & ukuran")
        tb2.setObjectName("toolbar_possize")
        tb2.setMovable(False)
        tb2.addWidget(self.possize)
        self.addToolBar(tb2)
        # opsi alat (muncul sesuai alat aktif)
        self.opt_bar = QToolBar("Opsi alat")
        self.opt_bar.setObjectName("toolbar_opts")
        self.opt_bar.setMovable(False)
        self.opt_sides = QSpinBox()
        self.opt_sides.setRange(3, 100)
        self.opt_sides.setValue(6)
        self.opt_inner = QDoubleSpinBox()
        self.opt_inner.setRange(5, 98)
        self.opt_inner.setValue(50)
        self.opt_inner.setSuffix(" %")
        self.opt_radius = QDoubleSpinBox()
        self.opt_radius.setRange(0, 500)
        self.opt_radius.setDecimals(2)
        self.opt_radius.setSuffix(" mm")
        self.opt_actions = {}
        for key, label, w in (("sides", "Sisi / ujung ", self.opt_sides), ("inner", "  Jari-jari dalam ", self.opt_inner),
                              ("radius", "Radius sudut ", self.opt_radius)):
            la = self.opt_bar.addWidget(QLabel(label))
            wa = self.opt_bar.addWidget(w)
            self.opt_actions[key] = (la, wa)
        self.opt_sides.valueChanged.connect(lambda v: setattr(self.canvas, "opt_sides", int(v)))
        self.opt_inner.valueChanged.connect(lambda v: setattr(self.canvas, "opt_inner", v / 100.0))
        self.opt_radius.valueChanged.connect(lambda v: setattr(self.canvas, "opt_radius", float(v)))
        self.addToolBar(self.opt_bar)

    # ------------------------------------------------------------------ utilitas UI
    def set_tool(self, key):
        c = self.canvas
        if c._poly_pts:
            c._poly_pts, c._poly_cur = [], None
        c.tool = key
        need = {"polygon": ("sides",), "star": ("sides", "inner"), "rect": ("radius",)}.get(key, ())
        for k, (la, wa) in self.opt_actions.items():
            la.setVisible(k in need)
            wa.setVisible(k in need)
        self.opt_bar.setVisible(bool(need))
        c.update()
        self.canvas.setCursor(Qt.ArrowCursor if key == "select" else Qt.CrossCursor)
        self.act_tools[key].setChecked(True)
        hint = {"text": "Klik di kanvas untuk menaruh teks",
                "polyline": "Klik untuk menambah titik • klik titik pertama = tutup • Enter/klik dua kali = selesai • Esc = batal",
                "polygon": "Seret dari pusat; arah seretan = sudut (Shift = 15°)",
                "star": "Seret dari pusat; arah seretan = sudut (Shift = 15°)"}.get(key)
        if hint:
            self.statusBar().showMessage(hint, 8000)

    def _toggle_grid(self):
        self.canvas.show_grid = self.a_grid.isChecked()
        self.canvas.update()

    def _cursor_moved(self, x, y):
        dev = self.project.device
        ux, uy = dev.to_user(x, y)
        self.lbl_cursor.setText("X %.2f   Y %.2f mm" % (ux, uy))

    def _update_title(self):
        name = os.path.basename(self.project.path) if self.project.path else "Tanpa judul"
        mir = "".join(t for t, on in ((" [CERMIN X]", self.project.mirror_x), (" [CERMIN Y]", self.project.mirror_y)) if on)
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name}{mir} — Grblaser")

    def _mark(self):
        self.rev += 1
        if not self.dirty:
            self.dirty = True
            self._update_title()

    def _refresh_all(self):
        self.layer_panel.refresh()
        self.props.refresh()
        self.possize.reset_anchor()
        self.possize.refresh()
        self.image_panel.refresh()
        self.job.reload_from_device()
        self.canvas.update()

    def _selection_changed(self):
        self.props.refresh()
        self.possize.refresh()
        self.image_panel.refresh()

    def note(self, text, ms=5000):
        self.statusBar().showMessage(text, ms)

    # ------------------------------------------------------------------ undo
    def push_undo(self, key=None):
        now = time.time()
        if key and key == self._undo_key and now - self._undo_t < 1.2:
            self._undo_t = now
            self._mark()
            self.invalidate_preview()
            return
        self.undo_stack.append(self.project.snapshot())
        if len(self.undo_stack) > 80:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._undo_key, self._undo_t = key, now
        self._mark()
        self.invalidate_preview()

    def _restore(self, snap):
        self.project.restore(snap)
        ids = {s.id for s in self.project.shapes}
        self.canvas.selection &= ids
        if self.active_layer not in {L.index for L in self.project.layers}:
            self.active_layer = self.project.layers[0].index
        self._undo_key = None
        self.invalidate_preview()
        self._refresh_all()
        self._mark()

    def undo(self):
        if not self.undo_stack:
            return
        self.redo_stack.append(self.project.snapshot())
        self._restore(self.undo_stack.pop())

    def redo(self):
        if not self.redo_stack:
            return
        self.undo_stack.append(self.project.snapshot())
        self._restore(self.redo_stack.pop())

    # ------------------------------------------------------------------ perubahan umum
    def layers_changed(self):
        self.canvas.update()
        self.props.refresh()
        self.possize.refresh()
        self.invalidate_preview()
        self._mark()

    def geometry_edited(self, from_bar=False):
        self.canvas.update()
        self.invalidate_preview()
        self._mark()
        self.props.refresh()
        if not from_bar:
            self.possize.refresh()
        self.image_panel._update_info()

    def invalidate_preview(self):
        if self.canvas.preview is not None:
            self.canvas.set_preview(None)

    # ------------------------------------------------------------------ layer
    def add_layer(self):
        if len(self.project.layers) >= MAX_LAYERS:
            self.note("Jumlah layer maksimum tercapai")
            return
        self.push_undo()
        L = self.project.add_layer()
        self.active_layer = L.index
        self.layer_panel.refresh()
        self.layers_changed()

    def remove_current_layer(self):
        if len(self.project.layers) <= 1:
            return
        self.push_undo()
        self.project.remove_layer(self.active_layer)
        self.active_layer = self.project.layers[0].index
        self.layer_panel.refresh()
        self.layers_changed()

    def move_layer(self, d):
        ls = self.project.layers
        i = next((k for k, L in enumerate(ls) if L.index == self.active_layer), None)
        if i is None or not (0 <= i + d < len(ls)):
            return
        self.push_undo()
        ls[i], ls[i + d] = ls[i + d], ls[i]
        self.layer_panel.refresh()
        self._mark()

    def assign_selection_to_layer(self, idx):
        self.active_layer = idx
        sel = self.canvas.selected_shapes()
        L = self.project.layer(idx)
        if sel:
            self.push_undo()
            for s in sel:
                s.layer = idx
            if L.mode == "image" and any(isinstance(s, PathShape) for s in sel):
                self.console.add("err", f"Layer '{L.name}' bermode Gambar; objek vektor di dalamnya akan dilewati.")
            if L.mode != "image" and any(isinstance(s, ImageShape) for s in sel):
                self.console.add("err", f"Layer '{L.name}' bukan mode Gambar; gambar raster akan dilewati. "
                                         "Gunakan layer bermode Gambar.")
        self.layer_panel.refresh()
        self.layers_changed()

    def _layer_for_new_path(self):
        L = self.project.layer(self.active_layer)
        if L.mode != "image":
            return L.index
        for L in self.project.layers:
            if L.mode == "line":
                return L.index
        return self.project.layers[0].index

    def _layer_for_new_image(self):
        L = self.project.layer(self.active_layer)
        if L.mode == "image":
            return L.index
        L = self.project.layer_for_mode("image")
        if L is not None:
            return L.index
        L = self.project.add_layer()
        L.mode, L.name, L.speed, L.min_power = "image", "Gambar", 3000.0, 5.0
        return L.index

    def _layer_for_color(self, col):
        L = self.project.layer_for_color(col)
        if L is not None:
            return L.index
        L = self.project.add_layer()
        if L is None:
            return self.project.layers[0].index
        L.color = col
        L.name = f"Warna {col}"
        return L.index

    # ------------------------------------------------------------------ pembuatan bentuk
    def _shape_created(self, shape):
        self.push_undo()
        shape.layer = self._layer_for_new_path()
        self.project.add(shape)
        self.canvas.set_selection({shape.id})
        self.act_tools["select"].setChecked(True)
        self.set_tool("select")
        self.layer_panel.refresh()
        self.canvas.update()

    def _text_requested(self, x, y):
        dlg = TextDialog(self)
        if dlg.exec_() != dlg.Accepted:
            return
        sh = dlg.make_shape(x, y)
        if sh is None:
            self.note("Teks kosong / font tidak menghasilkan kontur")
            return
        self._shape_created(sh)

    # ------------------------------------------------------------------ edit seleksi
    def select_all(self):
        self.canvas.set_selection({s.id for s in self.project.shapes if self.project.layer(s.layer).visible})

    def delete_selection(self):
        if not self.canvas.selection:
            return
        self.push_undo()
        ids = set(self.canvas.selection)
        self.project.shapes = [s for s in self.project.shapes if s.id not in ids]
        self.canvas.set_selection(set())
        self.canvas.update()

    def copy_selection(self):
        self.clipboard = [s.clone() for s in self.canvas.selected_shapes()]
        self.note(f"{len(self.clipboard)} objek disalin")

    def material_test_dialog(self):
        dev = self.project.device
        dlg = MaterialTestDialog(len(self.project.layers), (dev.bed_w, dev.bed_h), self)
        if dlg.exec_() != dlg.Accepted:
            return False
        p = dlg.params()
        w, h, _l, _t = materialtest.extent(p)
        x0, y0 = max(0.0, (dev.bed_w - w) / 2), max(0.0, (dev.bed_h - h) / 2)
        self.push_undo()
        try:
            ids = materialtest.build(self.project, p, x0, y0,
                                     lambda text, size: text_polys({"text": text, "size": size}))
        except ValueError as e:
            self.undo_stack.pop()
            QMessageBox.warning(self, "Uji bahan", str(e))
            return False
        self.canvas.set_selection(set(ids))
        self.layer_panel.refresh()
        self.canvas.update()
        self.note(f"Uji bahan dibuat: {p.cols * p.rows} layer. Coba dari daya rendah; simpan hasil terbaik lewat 📚 Bahan…", 9000)
        return True

    def remove_material_test(self):
        self.push_undo()
        ns, nl = materialtest.remove_test(self.project)
        if not ns and not nl:
            self.undo_stack.pop()
            self.note("Tidak ada uji bahan di proyek ini.", 4000)
            return
        if all(L.index != self.active_layer for L in self.project.layers):
            self.active_layer = self.project.layers[0].index
        self.canvas.set_selection(set())
        self.layer_panel.refresh()
        self.canvas.update()
        self.note(f"Uji bahan dihapus ({ns} objek, {nl} layer).", 5000)

    def materials_dialog(self):
        layer = self.project.layer(self.active_layer)
        snap = self.project.snapshot()                      # untuk undo bila preset diterapkan
        dlg = MaterialsDialog(self.materials, self.project.device.name, layer, self)
        if dlg.exec_() == dlg.Accepted and dlg.applied:
            self.undo_stack.append(snap)
            self.redo_stack.clear()
            self._mark()
            self.layer_panel.refresh()
            self.invalidate_preview()
            self.note(f"Preset diterapkan ke layer \"{layer.name}\"", 4000)

    def _single_text(self):
        sel = self.canvas.selected_shapes()
        if len(sel) == 1 and isinstance(sel[0], PathShape) and sel[0].text_spec:
            return sel[0]
        return None

    def edit_text(self):
        sh = self._single_text()
        if sh is None:
            self.note("Pilih satu objek teks (dibuat dengan alat Teks) untuk diedit.", 4000)
            return False
        dlg = TextDialog(self, sh.text_spec["params"])
        if dlg.exec_() != dlg.Accepted:
            return False
        prm = dlg.params()
        polys = text_polys(prm)
        if not polys:
            self.note("Teks kosong / font tidak menghasilkan kontur", 4000)
            return False
        self.push_undo()
        sh.set_text_polys(polys, prm)
        self.geometry_edited()
        return True

    def _canvas_double_click(self, x, y):
        hit = self.canvas.hit_test(x, y)
        if hit is not None and isinstance(hit, PathShape) and hit.text_spec:
            self.canvas.set_selection({hit.id})
            self.edit_text()

    def group_selection(self):
        sel = self.canvas.selected_shapes()
        if len(sel) < 2:
            self.note("Pilih minimal 2 objek untuk dijadikan grup.", 4000)
            return
        if len({s.group for s in sel}) == 1 and sel[0].group is not None:
            self.note("Objek terpilih sudah berada dalam satu grup.", 4000)
            return
        self.push_undo()
        gid = next(model._ids)
        for s in sel:
            s.group = gid
        self.note(f"{len(sel)} objek digrup (Ctrl+Shift+G untuk memisahkan)", 4000)
        self.canvas.update()
        self.props.refresh()

    def ungroup_selection(self):
        sel = self.canvas.selected_shapes()
        groups = {s.group for s in sel if s.group is not None}
        if not groups:
            self.note("Tidak ada grup pada objek terpilih.", 4000)
            return
        self.push_undo()
        n = 0
        for s in self.project.shapes:
            if s.group in groups:
                s.group = None
                n += 1
        self.note(f"Grup dipisah ({n} objek).", 4000)
        self.canvas.update()
        self.props.refresh()

    def _paste_clones(self, shapes, dx, dy):
        new = []
        remap = {}
        self.push_undo()
        for s in shapes:
            c = self._clone_new(s, remap)
            c.translate(dx, dy)
            self.project.add(c)
            new.append(c.id)
        self.canvas.set_selection(set(new))
        self.canvas.update()

    def paste(self):
        if self.clipboard:
            self._paste_clones(self.clipboard, 5.0, 5.0)

    def duplicate_selection(self):
        sel = self.canvas.selected_shapes()
        if sel:
            self._paste_clones(sel, 5.0, 5.0)

    def rotate_selection(self, deg):
        sel = self.canvas.selected_shapes()
        bb = self.canvas.sel_bbox()
        if not sel:
            return
        if any(isinstance(s, ImageShape) for s in sel) and abs(deg / 90.0 - round(deg / 90.0)) > 1e-6:
            self.note("Gambar raster hanya bisa diputar kelipatan 90° (gunakan 'Sudut pindai' di panel Gambar).", 7000)
            return
        self.push_undo()
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        for s in sel:
            s.rotate(deg, cx, cy)
        self.geometry_edited()

    def flip_selection(self, horizontal):
        sel = self.canvas.selected_shapes()
        bb = self.canvas.sel_bbox()
        if not sel:
            return
        self.push_undo()
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        for s in sel:
            s.flip(horizontal, cx, cy)
        self.geometry_edited()

    def center_selection(self):
        bb = self.canvas.sel_bbox()
        if not bb:
            return
        dev = self.project.device
        self.push_undo()
        dx = dev.bed_w / 2 - (bb[0] + bb[2]) / 2
        dy = dev.bed_h / 2 - (bb[1] + bb[3]) / 2
        for s in self.canvas.selected_shapes():
            s.translate(dx, dy)
        self.geometry_edited()

    def corner_selection(self):
        bb = self.canvas.sel_bbox()
        if not bb:
            return
        dev = self.project.device
        ax = bb[0]                                              # pojok kotak pembatas terdekat titik 0,0 mesin
        ay = bb[3] if dev.origin == "bottom-left" else bb[1]
        tx, ty = dev.from_user(0.0, 0.0)
        self.push_undo()
        for s in self.canvas.selected_shapes():
            s.translate(tx - ax, ty - ay)
        self.geometry_edited()

    def _bbox_of(self, shapes):
        return self.project.bbox(shapes)

    def align_selection(self, mode, rel="sel"):
        sel = self.canvas.selected_shapes()
        if not sel:
            return
        dev = self.project.device
        group = self._bbox_of(sel)
        bed = (0.0, 0.0, dev.bed_w, dev.bed_h)
        per_object = rel == "sel" and len(sel) > 1
        ref = group if per_object else bed

        def delta(bb):
            dx = dy = 0.0
            if mode == "left":
                dx = ref[0] - bb[0]
            elif mode == "right":
                dx = ref[2] - bb[2]
            elif mode == "top":
                dy = ref[1] - bb[1]
            elif mode == "bottom":
                dy = ref[3] - bb[3]
            elif mode == "hcenter":
                dx = (ref[0] + ref[2]) / 2 - (bb[0] + bb[2]) / 2
            elif mode == "vcenter":
                dy = (ref[1] + ref[3]) / 2 - (bb[1] + bb[3]) / 2
            return dx, dy

        self.push_undo()
        if per_object:
            for s in sel:
                dx, dy = delta(s.bbox())
                s.translate(dx, dy)
        else:
            dx, dy = delta(group)
            for s in sel:
                s.translate(dx, dy)
        self.geometry_edited()

    def distribute_selection(self, axis):
        sel = self.canvas.selected_shapes()
        if len(sel) < 3:
            self.note("Sebar butuh minimal 3 objek terpilih", 4000)
            return
        i0, i1 = (0, 2) if axis == "h" else (1, 3)
        items = sorted(((s, s.bbox()) for s in sel), key=lambda t: (t[1][i0] + t[1][i1]) / 2)
        first, last = items[0][1], items[-1][1]
        span = last[i1] - first[i0]
        total = sum(bb[i1] - bb[i0] for _, bb in items)
        gap = (span - total) / (len(items) - 1)
        self.push_undo()
        pos = first[i0]
        for s, bb in items:
            d = pos - bb[i0]
            s.translate(d if axis == "h" else 0.0, d if axis == "v" else 0.0)
            pos += (bb[i1] - bb[i0]) + gap
        self.geometry_edited()

    def _clone_new(self, s, remap=None):
        """Salinan dengan id baru. Grup disalin sebagai grup baru per `remap` (satu kamus per salinan logis)."""
        c = s.clone()
        c.id = next(model._ids)
        c.group = remap.setdefault(s.group, next(model._ids)) if (remap is not None and s.group is not None) else None
        return c

    def _need_paths(self, minimum):
        sel = [s for s in self.canvas.selected_shapes() if isinstance(s, PathShape)]
        if len(sel) < minimum:
            self.note(f"Pilih minimal {minimum} objek vektor (bukan gambar raster).", 5000)
            return None
        if not shp.available():
            self.note("Fitur ini butuh pustaka 'shapely': pip install shapely", 8000)
            return None
        return sel

    def boolean_selection(self, op):
        sel = self._need_paths(2)
        if sel is None:
            return
        order = {s.id: i for i, s in enumerate(self.project.shapes)}
        sel.sort(key=lambda s: order[s.id])            # urutan tumpukan: objek paling bawah = A
        polys = shp.boolean(op, [[p for p in s.polys] for s in sel])
        if not polys:
            self.note("Hasil kosong (bentuk tidak saling beririsan / ada yang tidak tertutup).", 6000)
            return
        self.push_undo()
        first = sel[0]
        ids = {s.id for s in sel}
        self.project.shapes = [s for s in self.project.shapes if s.id not in ids]
        res = PathShape(polys, first.layer, "bool", first.name)
        self.project.add(res)
        self.canvas.set_selection({res.id})
        self.layer_panel.refresh()
        self.canvas.update()

    def offset_dialog(self):
        sel = self._need_paths(1)
        if sel is None:
            return
        dlg = OffsetDialog(self)
        if dlg.exec_() != QDialog.Accepted:
            return
        d = dlg.dist.value()
        new = []
        for s in sel:
            polys = shp.offset(s.polys, d)
            if polys:
                new.append(PathShape(polys, s.layer, "offset", s.name))
        if not new:
            self.note("Offset menghasilkan bentuk kosong (jarak terlalu besar ke dalam?).", 6000)
            return
        self.push_undo()
        if dlg.delete_orig.isChecked():
            ids = {s.id for s in sel}
            self.project.shapes = [s for s in self.project.shapes if s.id not in ids]
        for n in new:
            self.project.add(n)
        self.canvas.set_selection({n.id for n in new})
        self.canvas.update()

    def array_dialog(self):
        sel = self.canvas.selected_shapes()
        bb = self.canvas.sel_bbox()
        if not sel:
            self.note("Pilih objek yang akan disalin", 4000)
            return
        dlg = ArrayDialog(bb[2] - bb[0], bb[3] - bb[1], self)
        if dlg.exec_() != QDialog.Accepted:
            return
        dev = self.project.device
        up = -1.0 if dev.origin == "bottom-left" else 1.0     # baris bertambah searah sumbu Y mesin
        new = []
        self.push_undo()
        if dlg.tabs.currentIndex() == 0:
            cols, rows, gx, gy = dlg.cols.value(), dlg.rows.value(), dlg.gx.value(), dlg.gy.value()
            if cols * rows > 5000:
                self.note("Terlalu banyak salinan (maks. 5000)", 5000)
                return
            for r in range(rows):
                for c in range(cols):
                    if r == 0 and c == 0:
                        continue
                    remap = {}
                    for s in sel:
                        k = self._clone_new(s, remap)
                        k.translate(c * gx, up * r * gy)
                        new.append(k)
        else:
            n, rad, total = dlg.n.value(), dlg.radius.value(), dlg.total.value()
            cx0, cy0 = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
            ccx, ccy = cx0, cy0 - rad
            step = total / n if total >= 359.99 else total / max(n - 1, 1)
            for i in range(1, n):
                ang = step * i
                t = math.radians(ang)
                remap = {}
                for s in sel:
                    k = self._clone_new(s, remap)
                    if dlg.rot.isChecked() and not isinstance(k, ImageShape):
                        k.rotate(ang, ccx, ccy)
                    else:   # hanya pindahkan pusatnya
                        sb = k.bbox()
                        mx, my = (sb[0] + sb[2]) / 2 - ccx, (sb[1] + sb[3]) / 2 - ccy
                        nx = ccx + mx * math.cos(t) - my * math.sin(t)
                        ny = ccy + mx * math.sin(t) + my * math.cos(t)
                        k.translate(nx - (sb[0] + sb[2]) / 2, ny - (sb[1] + sb[3]) / 2)
                    new.append(k)
        for k in new:
            self.project.add(k)
        self.canvas.set_selection({k.id for k in new} | {s.id for s in sel})
        self.layer_panel.refresh()
        self.canvas.update()
        self.note(f"{len(new)} salinan dibuat")

    def add_library_shape(self, name, size):
        fn = shp.LIBRARY.get(name)
        if fn is None:
            return
        polys = fn(float(size))
        sh = PathShape(polys, kind=name.lower(), name=name)
        bb = sh.bbox()
        c = self.canvas
        wx, wy = c.s2w(QPointF(c.width() / 2, c.height() / 2))
        dev = self.project.device
        wx = min(max(wx, size / 2), dev.bed_w - size / 2) if dev.bed_w > size else dev.bed_w / 2
        wy = min(max(wy, size / 2), dev.bed_h - size / 2) if dev.bed_h > size else dev.bed_h / 2
        sh.translate(wx - (bb[0] + bb[2]) / 2, wy - (bb[1] + bb[3]) / 2)
        self._shape_created(sh)

    def trace_selected_image(self):
        imgs = [s for s in self.canvas.selected_shapes() if isinstance(s, ImageShape)]
        if not imgs:
            return
        data = [(s.id, s.clone(), model.ImageOptions.from_dict(s.opts.to_dict()), s.name) for s in imgs]

        def work():
            out = []
            for sid, sh, o, name in data:
                img, _mask, x, y, w, h = sh.rotated(o.lpmm)      # mendukung gambar yang diputar bebas
                out.append((sid, name, raster.trace_polys(img, x, y, w, h, o)))
            return out

        def done(results):
            self.push_undo()
            new_ids = set()
            line_layer = next((L.index for L in self.project.layers if L.mode == "line"), self.project.layers[0].index)
            for sid, name, polys in results:
                self.project.shapes = [x for x in self.project.shapes if x.id != sid]
                if polys:
                    ps = PathShape(polys, line_layer, "trace", name or "trace")
                    self.project.add(ps)
                    new_ids.add(ps.id)
            self.canvas.set_selection(new_ids)
            self.layer_panel.refresh()
            self.note("Gambar diubah menjadi vektor pada layer garis")

        self.tasks.run(work, "Melacak kontur gambar…", done)

    # ------------------------------------------------------------------ menu konteks
    def _context_menu(self, gpos, x, y):
        m = QMenu(self)
        has = bool(self.canvas.selection)
        for a in (self.a_copy, self.a_paste, self.a_dup, self.a_del):
            m.addAction(a)
        if has:
            m.addSeparator()
            m.addAction(self.a_group)
            m.addAction(self.a_ungroup)
            if self._single_text() is not None:
                m.addAction(self.a_edittext)
            m.addSeparator()
            sub = m.addMenu("Pindahkan ke layer")
            for L in self.project.layers:
                act = sub.addAction(f"C{L.index + 1:02d}  {L.name}  ({MODE_LABELS[L.mode]})")
                act.triggered.connect(lambda _=False, i=L.index: self.assign_selection_to_layer(i))
            m.addSeparator()
            for a in (self.a_rotcw, self.a_rotccw, self.a_fliph, self.a_flipv, self.a_center):
                m.addAction(a)
        m.addSeparator()
        ux, uy = self.project.device.to_user(x, y)
        ready = self.sender.connected and not self.sender._running
        mv = m.addAction(f"Pindahkan laser ke sini (X {ux:.1f}, Y {uy:.1f})")
        mv.setEnabled(ready)
        mv.triggered.connect(lambda: self._move_laser_to(x, y))
        st = m.addAction("Pindahkan laser ke sini & jadikan titik awal pekerjaan")
        st.setToolTip("Laser berjalan ke titik ini lalu mode 'posisi saat ini' diaktifkan: jangkar desain ditaruh di titik ini.")
        st.setEnabled(ready)
        st.triggered.connect(lambda: self._move_laser_to(x, y, make_start=True))
        z = m.addAction("Set nol mesin di posisi laser sekarang…")
        z.setToolTip("G10 L20 P1 X0 Y0: menjadikan posisi laser saat ini koordinat 0,0 (menggeser koordinat kerja).")
        z.setEnabled(ready)
        z.triggered.connect(self._zero_here)
        m.exec_(gpos)

    def _move_laser_to(self, x, y, make_start=False):
        """Jog laser ke titik desain (x, y) pada koordinat kerja yang sama dengan penggaris. Berlaku di semua mode awal."""
        dev = self.project.device
        if not self.sender.connected or self.sender._running:
            self.note("Hubungkan laser dan pastikan tidak ada pekerjaan berjalan.", 5000)
            return False
        X, Y = dev.to_user(x, y)
        if not (-0.001 <= X <= dev.bed_w and -0.001 <= Y <= dev.bed_h):
            self.note("Titik di luar area kerja", 4000)
            return False
        feed = self.laser_panel.feed.value() if hasattr(self.laser_panel, "feed") else dev.rapid_rate
        if not self.sender.jog_to(X, Y, feed):
            return False
        self.note(f"Laser menuju X {X:.2f}, Y {Y:.2f}", 4000)
        if make_start:
            dev.start_from = "current"
            self.job.reload_from_device()
            self.save_current_profile()
            self.note(f"Laser menuju X {X:.2f}, Y {Y:.2f}. Mode 'posisi saat ini' aktif: titik awal pekerjaan di sini.", 8000)
        return True

    def _zero_here(self):
        if QMessageBox.question(self, "Set nol mesin",
                                "Jadikan posisi laser SAAT INI sebagai koordinat 0,0 (G10 L20 P1 X0 Y0)?\n\n"
                                "Koordinat kerja bergeser: penggaris di aplikasi mengasumsikan 0,0 = asal mesin hasil homing. "
                                "Lanjutkan hanya bila Anda memang memakai titik nol buatan.",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes:
            self.sender.zero_here()

    # ------------------------------------------------------------------ impor
    def import_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Impor", self._last_dir(), IMPORT_FILTER)
        if paths:
            self.import_files(paths)

    def _last_dir(self):
        return self.settings.value("last_dir", os.path.expanduser("~"))

    def _remember_dir(self, path):
        self.settings.setValue("last_dir", os.path.dirname(path))

    def import_files(self, paths):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for p in paths:
                self._remember_dir(p)
                ext = os.path.splitext(p)[1].lower()
                try:
                    if ext == ".svg":
                        self._import_vector(importers.import_svg(p), p)
                    elif ext == ".dxf":
                        self._import_vector(importers.import_dxf(p), p)
                    elif ext in RASTER_EXT:
                        self._import_image(p)
                    elif ext == ".glz":
                        self.open_project(p)
                    elif ext in GCODE_EXT:
                        QApplication.restoreOverrideCursor()
                        self.open_gcode_file(p)
                        QApplication.setOverrideCursor(Qt.WaitCursor)
                    else:
                        self.console.add("err", f"Format tidak didukung: {os.path.basename(p)}")
                except ImportError as e:
                    self.console.add("err", f"Pustaka belum terpasang untuk {ext}: {e}")
                except Exception as e:  # noqa: BLE001
                    self.console.add("err", f"Gagal impor {os.path.basename(p)}: {e}")
        finally:
            QApplication.restoreOverrideCursor()
        self.layer_panel.refresh()
        self.canvas.update()

    def _import_vector(self, res, path):
        for w in res.warnings:
            self.console.add("err", w)
        if not res.shapes:
            return
        self.push_undo()
        ids = set()
        for sh in res.shapes:
            sh.layer = self._layer_for_color(getattr(sh, "src_color", "#000000"))
            self.project.add(sh)
            ids.add(sh.id)
        self.canvas.set_selection(ids)
        n = len({self.project.shape(i).layer for i in ids})
        self.console.add("info", f"{os.path.basename(path)}: {len(res.shapes)} grup bentuk di {n} layer (berdasarkan warna)")

    def _import_image(self, path):
        img, w, h = importers.load_raster(path)
        if max(img.size) > 3000:     # lebih dari cukup (0,1 mm x 300 mm); mempercepat semua proses
            k0 = 3000.0 / max(img.size)
            img = img.resize((max(1, int(img.width * k0)), max(1, int(img.height * k0))), Image.LANCZOS)
            self.console.add("info", f"Gambar besar diperkecil ke {img.width}×{img.height} px (ukuran fisik tetap)")
        dev = self.project.device
        k = min(1.0, dev.bed_w * 0.9 / w, dev.bed_h * 0.9 / h)
        w, h = w * k, h * k
        self.push_undo()
        L = self._layer_for_new_image()
        sh = ImageShape(img, (dev.bed_w - w) / 2, (dev.bed_h - h) / 2, w, h, layer=L, name=os.path.basename(path))
        self.project.add(sh)
        self.canvas.set_selection({sh.id})
        self.docks["Gambar"].raise_()
        self.console.add("info", f"{os.path.basename(path)}: {img.width}×{img.height} px → {w:.1f}×{h:.1f} mm")

    # ------------------------------------------------------------------ proyek
    def _confirm_discard(self):
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Perubahan belum disimpan", "Simpan perubahan proyek ini dulu?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        if r == QMessageBox.Discard:
            self.autosave.clear()
            return True
        return False

    # ------------------------------------------------------------------ simpan otomatis
    def autosave_now(self):
        """Salin proyek ke folder cadangan bila ada perubahan sejak penyimpanan otomatis terakhir."""
        if not self.dirty or self._autosaved_rev == self.rev or self.tasks.busy:
            return False
        if self.autosave.write(self.project):
            self._autosaved_rev = self.rev
            return True
        return False

    def check_recovery(self):
        """Tawarkan pemulihan bila sesi sebelumnya berakhir tanpa menyimpan (crash/mati listrik)."""
        inf = self.autosave.info()
        if not inf:
            return False
        orig, ts = inf
        when = time.strftime("%d %b %Y %H:%M", time.localtime(ts))
        nm = os.path.basename(orig) if orig else "proyek tanpa judul"
        r = QMessageBox.question(self, "Pulihkan pekerjaan?",
                                 f"Ditemukan salinan otomatis yang belum tersimpan ({nm}, {when}).\n"
                                 "Pulihkan sekarang?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if r != QMessageBox.Yes:
            self.autosave.clear()
            return False
        try:
            pr = self.autosave.load()
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Gagal memulihkan", str(e))
            self.autosave.clear()
            return False
        self._set_project(pr)
        self.dirty = True
        self._update_title()
        self.note("Pekerjaan dipulihkan dari salinan otomatis. Simpan untuk mempertahankannya.", 8000)
        return True

    def _set_project(self, pr):
        self.project = pr
        self.canvas.project = pr
        self.canvas.selection = set()
        self.canvas.set_preview(None)
        self.active_layer = pr.layers[0].index
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.dirty = False
        self.canvas.fit()
        self._refresh_all()
        self._update_title()

    def new_project(self):
        if not self._confirm_discard():
            return
        pr = Project()
        self._apply_device_pref(pr)
        self._set_project(pr)

    def open_project(self, path=None):
        if not path:
            if not self._confirm_discard():
                return
            path, _ = QFileDialog.getOpenFileName(self, "Buka proyek", self._last_dir(), "Proyek Grblaser (*.glz)")
        if not path:
            return
        try:
            pr = load_project(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Gagal membuka", str(e))
            return
        self._remember_dir(path)
        self._resolve_project_device(pr)
        self._set_project(pr)
        self.laser_panel.reload_profiles()
        self.console.add("info", f"Proyek dibuka: {path}")

    def _resolve_project_device(self, pr):
        """Proyek menyimpan mesin pembuatnya. Bila berbeda dari mesin aktif, tanyakan mesin mana yang dipakai.

        Koordinat desain bergantung pada area kerja & asal mesin, jadi pilihannya eksplisit:
        pakai profil proyek (diambil dari profil tersimpan bila ada) atau tetap pakai mesin aktif."""
        cur, fd = self.project.device, pr.device
        if fd.same_machine(cur):
            pr.device = cur
            return "same"
        box = QMessageBox(QMessageBox.Question, "Mesin proyek berbeda",
                          f"Proyek ini dibuat untuk mesin \"{fd.name}\" ({fd.bed_w:g}×{fd.bed_h:g} mm, "
                          f"asal {'kiri-bawah' if fd.origin == 'bottom-left' else 'kiri-atas'}).\n"
                          f"Mesin aktif: \"{cur.name}\" ({cur.bed_w:g}×{cur.bed_h:g} mm).\n\n"
                          "Pakai mesin dari proyek, atau tetap pakai mesin aktif?", QMessageBox.NoButton, self)
        b_proj = box.addButton("Pakai mesin proyek", QMessageBox.AcceptRole)
        box.addButton("Tetap mesin aktif", QMessageBox.RejectRole)
        box.setDefaultButton(b_proj)
        box.exec_()
        if box.clickedButton() is b_proj:
            saved = self.profiles.get(fd.name)
            if saved is not None and saved.same_machine(fd):
                pr.device = saved          # profil tersimpan memuat port/titik awal terbaru
            else:
                self.profiles.save(fd)     # profil baru dari proyek
            self.settings.setValue("device", json.dumps(pr.device.to_dict()))
            return "project"
        pr.device = cur
        return "active"

    def save_project(self, save_as=False):
        path = self.project.path
        if save_as or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Simpan proyek", self._last_dir(), "Proyek Grblaser (*.glz)")
            if not path:
                return False
            if not path.lower().endswith(".glz"):
                path += ".glz"
        try:
            save_project(self.project, path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "Gagal menyimpan", str(e))
            return False
        self._remember_dir(path)
        self.dirty = False
        self.autosave.clear()
        self._update_title()
        self.note("Tersimpan: " + path)
        return True

    # ------------------------------------------------------------------ perangkat
    def _load_device_pref(self):
        try:
            d = json.loads(self.settings.value("device", "{}"))
            self.project.device = Device.from_dict(d)
        except Exception:
            pass

    def _apply_device_pref(self, pr):
        try:
            pr.device = Device.from_dict(json.loads(self.settings.value("device", "{}")))
        except Exception:
            pass

    def device_settings(self):
        dlg = DeviceDialog(self.project.device, self.sender, self, profiles=self.profiles)
        ok = dlg.exec_() == dlg.Accepted
        # profil yang disimpan/dihapus di dialog tetap berlaku walau dialog dibatalkan
        if not ok:
            self.laser_panel.reload_profiles()
            return
        self.push_undo()
        old = (self.project.device.bed_w, self.project.device.bed_h, self.project.device.origin)
        dlg.apply(self.project.device)
        dev = self.project.device
        self.settings.setValue("device", json.dumps(dev.to_dict()))
        self.profiles.save(dev)
        self.laser_panel.reload_profiles()
        if old != (dev.bed_w, dev.bed_h, dev.origin):
            self.canvas.fit()
        self._refresh_all()

    def apply_profile(self, name):
        """Pindah ke profil mesin lain (manual). Ditolak saat job berjalan."""
        new = self.profiles.get(name)
        if new is None:
            return False
        if self.sender._running:
            self.note("Tidak bisa ganti profil saat job berjalan.", 5000)
            self.laser_panel.reload_profiles()
            return False
        if self.sender.connected and new.last_port == "":
            new.last_port = self.project.device.last_port
        self.push_undo()
        self.project.device = new
        self.settings.setValue("device", json.dumps(new.to_dict()))
        self.canvas.fit()
        self._refresh_all()
        self.laser_panel.reload_profiles()
        self.note(f"Profil perangkat: {new.name} ({new.bed_w:g}×{new.bed_h:g} mm)", 5000)
        return True

    def save_current_profile(self):
        self.profiles.save(self.project.device)
        self.settings.setValue("device", json.dumps(self.project.device.to_dict()))

    # ------------------------------------------------------------------ pekerjaan
    def _only_ids(self):
        if self.job.sel_only.isChecked() and self.canvas.selection:
            return set(self.canvas.selection)
        return None

    def _run_job(self, title, on_done, gcode=False, comments=True, preview=False, need_t=False, sim=False):
        """Bangun job (+G-code, +pratinjau) di thread latar. Hasil job dipakai ulang bila proyek tidak berubah."""
        dev = self.project.device
        cur = None
        if (need_t or gcode) and dev.start_from == "current":
            if not self.sender.connected:
                QMessageBox.warning(self, "Belum terhubung",
                                    "Mode 'dari posisi laser saat ini' butuh koneksi ke GRBL untuk membaca posisi.")
                return False
            cur = (self.sender.wpos[0], self.sender.wpos[1])
        only = self._only_ids()
        key = (self.rev, tuple(sorted(only)) if only else None)
        cached = self._job_cache[1] if (self._job_cache and self._job_cache[0] == key) else None
        project, version = self.project, __version__
        mirror = (project.mirror_x, project.mirror_y)

        def work():
            job = cached if cached is not None else build_job(project, only)
            out = {"job": job, "key": key, "fresh": cached is None}
            if job.empty():
                return out
            if need_t or gcode:
                T = make_transform(dev, job.bounds, current=cur, mirror=mirror)
                out["T"] = T
                if gcode:
                    lines = emit_gcode(job, dev, T, comments=comments, title="Grblaser " + version)
                    # pembersihan jutaan baris dilakukan di thread latar, bukan di thread UI
                    out["lines"] = lines if comments else clean_gcode(lines)
            if preview or sim:
                segs = ops_to_segments(job.ops)
                if (mirror[0] or mirror[1]) and len(segs):          # pratinjau mengikuti cermin
                    segs = segs.copy()
                    b = job.bounds
                    if mirror[0]:
                        segs[:, [0, 2]] = (b[0] + b[2]) - segs[:, [0, 2]]
                    if mirror[1]:
                        segs[:, [1, 3]] = (b[1] + b[3]) - segs[:, [1, 3]]
                if sim:
                    out["segs"] = segs
                else:
                    out["img"], _ = render_segments(segs, dev.bed_w, dev.bed_h, dev.s_max)
            return out

        def done(res):
            job = res["job"]
            self._job_cache = (res["key"], job)
            self.last_job = job
            if res["fresh"]:
                for w in job.warnings:
                    self.console.add("err", w)
            if job.empty():
                self.note("Tidak ada yang akan dikerjakan: periksa layer (Output) dan mode layer.", 7000)
                return
            on_done(res)

        return self.tasks.run(work, title, done)

    def show_simulation(self):
        self._run_job("Menyiapkan simulasi…", self._simulation_ready, sim=True)

    def _simulation_ready(self, res):
        from .simulation_dialog import SimulationDialog
        dev = self.project.device
        dlg = SimulationDialog(res["segs"], dev.bed_w, dev.bed_h, dev.s_max, res["job"].time_s, self)
        self._sim_dialog = dlg
        dlg.exec_()

    def show_preview(self):
        self._run_job("Membuat pratinjau…", self._preview_ready, preview=True)

    def _preview_ready(self, res):
        job = res["job"]
        self.canvas.set_preview(res["img"])
        b = job.bounds
        txt = (f"{job.layers_used} layer • bakar {job.feed_len / 1000:.2f} m • gerak cepat {job.rapid_len / 1000:.2f} m\n"
               f"Estimasi waktu: {fmt_time(job.time_s)}")
        if b:
            txt += f"\nUkuran: {b[2] - b[0]:.1f} × {b[3] - b[1]:.1f} mm"
        self.job.summary.setText(txt)

    def export_gcode(self):
        def ready(res):
            lines, job = res["lines"], res["job"]
            path, _ = QFileDialog.getSaveFileName(self, "Ekspor G-code", self._last_dir(), "G-code (*.gcode *.nc)")
            if not path:
                return
            if not os.path.splitext(path)[1]:
                path += ".gcode"
            with open(path, "w") as f:
                f.write("\n".join(lines) + "\n")
            self._remember_dir(path)
            self.console.add("info", f"G-code disimpan: {path} ({len(lines)} baris, estimasi {fmt_time(job.time_s)})")
            for i in preflight.check(self.project, self.project.device, None, self._only_ids()):
                self.console.add("err" if i.level == "error" else "info", str(i))
            self.note(f"G-code disimpan ({len(lines)} baris)")

        self._run_job("Membuat G-code…", ready, gcode=True, comments=True)

    def set_mirror(self, mx, my):
        if (mx, my) == (self.project.mirror_x, self.project.mirror_y):
            return
        self.project.mirror_x, self.project.mirror_y = bool(mx), bool(my)
        self._mark()
        self.invalidate_preview()
        self._update_title()

    def frame_job(self):
        if not self._need_ready():
            return
        dev = self.project.device
        if dev.frame_laser and dev.frame_power > 0:
            ok = QMessageBox.question(
                self, "Bingkai dengan laser menyala",
                f"Laser akan MENYALA {dev.frame_power:g}% selama bingkai (penunjuk posisi).\n"
                "Daya rendah tetap bisa menghitamkan/membakar bahan peka. Pakai kacamata pelindung dan pastikan bahan sesuai.\n\n"
                "Lanjutkan?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ok != QMessageBox.Yes:
                return

        def ready(res):
            job = res["job"]
            bounds = job.bounds or self.project.bbox()
            lines = frame_lines(self.project.device, res["T"], bounds)
            self._job_kind = "frame"
            self.sender.start_job(lines, quiet=True)

        self._run_job("Menghitung bingkai…", ready, need_t=True)

    def _confirm_start(self, msg, has_warn):
        """Dialog mulai pekerjaan; bila ada peringatan, tombol bawaan = Tidak."""
        return QMessageBox.question(self, "Mulai pekerjaan", msg, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No if has_warn else QMessageBox.Yes) == QMessageBox.Yes

    def _ensure_grbl_settings(self, wait_ms=1000):
        """Baca $$ sekali (jika belum) agar pemeriksaan bisa membandingkan $30/$130/$131/$32."""
        s = self.sender
        if not s.connected or s._running or s.settings:
            return
        s.request_settings()
        t0 = time.time()
        while "$30" not in s.settings and (time.time() - t0) * 1000 < wait_ms:
            QApplication.processEvents()
            time.sleep(0.02)

    def _resumable(self):
        """(lines, acked, keterangan) untuk job yang bisa dilanjutkan: di memori, atau dari disk (sesi sebelumnya)."""
        rj = self._resume_job
        if rj is not None:
            return rj["lines"], rj["acked"], "Job terakhir (sesi ini)"
        got = self.job_store.load()
        if got:
            lines, meta = got
            when = time.strftime("%d %b %Y %H:%M", time.localtime(meta.get("time", 0)))
            return lines, int(meta.get("acked", 0)), f"Job tersimpan dari sesi sebelumnya: {meta.get('project', '?')} ({when})"
        return None

    def _update_resume_action(self):
        on = self.sender.connected and not self.sender._running
        if hasattr(self, "a_resume"):
            self.a_resume.setEnabled(on)
        if hasattr(self, "job") and hasattr(self.job, "btn_resume"):
            self.job.btn_resume.setEnabled(on)

    def resume_job(self):
        """Lanjutkan job yang terhenti dari baris pilihan (default: mundur BACKOFF baris dari titik berhenti)."""
        if not self._need_ready():
            return False
        src = self._resumable()
        if src is None:
            self.note("Tidak ada pekerjaan yang bisa dilanjutkan.", 5000)
            return False
        lines, acked, desc = src
        dlg = ResumeDialog(lines, resume_mod.default_resume_index(acked, len(lines)),
                           f"{desc}\nTerakhir di-OK-kan GRBL: baris {acked} dari {len(lines)}.", self)
        if dlg.exec_() != dlg.Accepted:
            return False
        n = dlg.index()
        new, info = resume_mod.plan_resume(lines, n, self.project.device.air_on)
        if not new:
            return False
        self._job_kind = "job"
        self._job_t0 = time.time()
        if not self.sender.start_job(new, quiet=len(new) > 300, prepared=True):
            self.note("Gagal memulai (cek alarm / kunci).", 6000)
            return False
        self._resume_job = {"lines": lines, "n": info["n"], "prefix": info["prefix"], "acked": info["n"]}
        self.note(f"Melanjutkan dari baris {n + 1} dari {len(lines)}", 6000)
        return True

    def _need_ready(self):
        s = self.sender
        if self.tasks.busy:
            return False
        if not s.connected:
            self.note("Hubungkan ke laser dulu (panel Laser).", 6000)
            return False
        if s._running:
            self.note("Pekerjaan sedang berjalan.", 4000)
            return False
        return True

    def start_job(self):
        if not self._need_ready():
            return
        self._ensure_grbl_settings()

        def ready(res):
            job, T = res["job"], res["T"]
            self._preview_ready(res)
            lines = res["lines"]
            dev = self.project.device
            issues = preflight.check(self.project, dev, self.sender.settings, self._only_ids())
            if preflight.has_errors(issues):
                QMessageBox.critical(self, "Tidak bisa memulai",
                                     "Perbaiki dulu:\n\n" + preflight.format_issues(issues))
                return
            msg = (f"Layer dikerjakan: {job.layers_used}\nEstimasi waktu: {fmt_time(job.time_s)}\n"
                   f"Baris G-code: {len(lines)}\n")
            if issues:
                msg += "\nPeriksa dulu:\n" + preflight.format_issues(issues) + "\n"
            msg += "\nPastikan kacamata pelindung terpasang & bahan siap. Mulai?"
            if not self._confirm_start(msg, bool(issues)):
                return
            self._job_kind = "job"
            self._job_t0 = time.time()
            if not self.sender.start_job(lines, quiet=len(lines) > 300, prepared=True):
                self.note("Gagal memulai (cek alarm / kunci).", 6000)
            else:
                self._remember_job(lines, 0, 0)

        self._run_job("Menyiapkan pekerjaan…", ready, gcode=True, comments=False, preview=True, need_t=True)

    def open_gcode_file(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Kirim file G-code", self._last_dir(),
                                                  "G-code (*.gcode *.nc *.gc *.ngc *.tap *.txt);;Semua (*)")
        if not path:
            return
        self._remember_dir(path)
        with open(path, "r", errors="ignore") as f:
            raw = f.read().splitlines()
        dev = self.project.device
        name = os.path.basename(path)

        def work():
            clean = clean_gcode(raw)
            segs = gcode_to_design_segments(clean, dev)
            img, _ = render_segments(segs, dev.bed_w, dev.bed_h, dev.s_max)
            return clean, img

        def done(res):
            clean, img = res
            self.canvas.set_preview(img)
            self.console.add("info", f"{name}: {len(clean)} baris perintah")
            if not self.sender.connected:
                self.note("G-code dimuat untuk pratinjau. Hubungkan laser untuk mengirimnya.", 7000)
                return
            if QMessageBox.question(self, "Kirim G-code",
                                    f"Kirim {len(clean)} baris dari {name} ke laser?") == QMessageBox.Yes:
                if self._need_ready():
                    self._job_kind = "job"
                    self._job_t0 = time.time()
                    if self.sender.start_job(clean, quiet=len(clean) > 300, prepared=True):
                        self._remember_job(clean, 0, 0)

        self.tasks.run(work, "Membaca G-code…", done)

    def pause_resume(self):
        s = self.sender
        if not s._running:
            return
        if s._paused:
            s.resume()
        else:
            s.hold()

    def stop_job(self):
        if self.sender.connected:
            self.sender.stop()

    def _remember_job(self, lines, n, prefix):
        """Simpan job yang sedang berjalan (memori + disk) agar bisa dilanjutkan bila berhenti."""
        self._resume_job = {"lines": lines, "n": n, "prefix": prefix, "acked": n}
        info = {"project": os.path.basename(self.project.path) if self.project.path else "Tanpa judul",
                "machine": self.project.device.name}
        self.job_store.save(lines, info)

    def _progress(self, done, total):
        rj = self._resume_job
        if rj is not None and getattr(self, "_job_kind", "") == "job":
            rj["acked"] = rj["n"] + max(0, done - rj["prefix"])         # indeks pada daftar baris asli
            self.job_store.update(rj["acked"])
        pct = int(100 * done / max(total, 1))
        self.job.bar.setValue(pct)
        if getattr(self, "_job_kind", "") == "job" and done > 5:
            el = time.time() - self._job_t0
            eta = el * (total - done) / max(done, 1)
            self.job.bar.setFormat(f"{pct}%  •  sisa ≈ {fmt_time(eta)}")

    def _job_state(self, st):
        self._set_job_buttons(st)
        kind = getattr(self, "_job_kind", "")
        if st == "done" and kind == "job":
            self.job_store.update(0, done=True, force=True)      # selesai normal: tidak perlu dilanjutkan
            self._resume_job = None
        if st in ("stopped", "error") and kind == "job" and self._resume_job is not None:
            self.job_store.update(self._resume_job["acked"], force=True)
        self._update_resume_action()
        if st == "done":
            self.job.bar.setValue(100)
            if kind == "job":
                QApplication.beep()
                self.note("Pekerjaan selesai.", 10000)
                self.job.bar.setFormat("Selesai • %d:%02d" % divmod(int(time.time() - self._job_t0), 60))
        elif st in ("stopped", "error"):
            self.note("Pekerjaan dihentikan" if st == "stopped" else "Pekerjaan berhenti karena error/alarm", 10000)
            self.job.bar.setFormat("%p%")
        elif st == "running":
            self.job.bar.setFormat("%p%")

    def _set_job_buttons(self, st):
        running = st in ("running", "paused")
        self.job.btn_start.setEnabled(not running)
        self.job.btn_frame.setEnabled(not running)
        self.job.btn_pause.setEnabled(running)
        self.job.btn_pause.setText("Lanjut" if st == "paused" else "Jeda")
        self.job.btn_stop.setEnabled(running or self.sender.connected)

    def laser_moved(self, m, w):
        dev = self.project.device
        x, y = dev.from_user(w[0], w[1])
        if dev.start_from == "absolute":
            self.canvas.set_laser((x, y))
        else:
            self.canvas.set_laser(None)

    # ------------------------------------------------------------------ bantuan
    def about(self):
        QMessageBox.about(self, "Grblaser",
                          f"<b>Grblaser {__version__}</b><br>Perangkat lunak laser GRBL untuk Linux.<br>"
                          "Layer per warna ala LightBurn; pengolahan gambar garis-demi-garis & dither ala LaserGRBL.")

    def help(self):
        QMessageBox.information(self, "Pintasan", (
            "<b>Kanvas</b><br>Roda mouse: zoom • Tengah/Spasi+seret: geser • Klik dua kali area kosong: zoom bed<br>"
            "Seret objek: pindah (Ctrl = satu sumbu) • Sudut: skala proporsional (Shift = bebas) • Bulatan atas: putar (Shift = 15°)<br>"
            "Panah: geser 1 mm (Shift 10, Ctrl 0,1) • Seret kotak kanan→kiri: pilih yang tersentuh<br><br>"
            "<b>Alat</b>: S pilih, R persegi, E elips, L garis, T teks<br>"
            "<b>Pekerjaan</b>: F5 pratinjau, F6 bingkai, F7 mulai, F8 jeda/lanjut, F9 henti<br>"
            "Ctrl+I impor • Ctrl+E ekspor G-code • Ctrl+D duplikat • F zoom bed"))

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.autosave.clear()          # keluar normal: tidak ada yang perlu dipulihkan
        self.save_current_profile()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("state", self.saveState())
        self.tasks.shutdown()
        self.sender.disconnect()
        e.accept()
