# SPDX-License-Identifier: MIT
"""Kanvas desain: bed, grid, bentuk per layer, seleksi, pindah/skala/putar,
alat gambar (persegi, elips, garis, teks) dan hamparan pratinjau G-code."""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PyQt5.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt5.QtGui import (QBrush, QColor, QCursor, QFont, QImage, QPainter, QPainterPath,
                         QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import QWidget

from .. import raster, shapes as shp
from ..model import ImageOptions, PathShape, ImageShape, Poly, ellipse_poly, rect_poly
from ..toolpath import point_in_poly

HANDLE_PX = 8
TOP_L, TOP, TOP_R, RIGHT, BOT_R, BOT, BOT_L, LEFT, ROT = range(9)
CURSORS = {TOP_L: Qt.SizeFDiagCursor, BOT_R: Qt.SizeFDiagCursor,
           TOP_R: Qt.SizeBDiagCursor, BOT_L: Qt.SizeBDiagCursor,
           TOP: Qt.SizeVerCursor, BOT: Qt.SizeVerCursor,
           LEFT: Qt.SizeHorCursor, RIGHT: Qt.SizeHorCursor, ROT: Qt.CrossCursor}


def _dist_to_poly(pts, closed, x, y):
    p = np.vstack((pts, pts[:1])) if closed else pts
    if len(p) < 2:
        return float(np.hypot(p[0, 0] - x, p[0, 1] - y))
    a, b = p[:-1], p[1:]
    d = b - a
    l2 = (d ** 2).sum(axis=1)
    l2[l2 == 0] = 1e-12
    t = np.clip(((x - a[:, 0]) * d[:, 0] + (y - a[:, 1]) * d[:, 1]) / l2, 0, 1)
    return float(np.hypot(a[:, 0] + t * d[:, 0] - x, a[:, 1] + t * d[:, 1] - y).min())


class Canvas(QWidget):
    selectionChanged = pyqtSignal()
    aboutToEdit = pyqtSignal(str)          # kunci undo
    geometryChanged = pyqtSignal()         # bentuk berubah akibat mouse/keyboard
    shapeCreated = pyqtSignal(object)      # PathShape baru dari alat gambar
    textRequested = pyqtSignal(float, float)
    filesDropped = pyqtSignal(list)
    contextRequested = pyqtSignal(object, float, float)
    doubleClicked = pyqtSignal(float, float)
    cursorMoved = pyqtSignal(float, float)
    toolReset = pyqtSignal()
    deleteRequested = pyqtSignal()
    imageComputed = pyqtSignal(int, object, object)   # (id bentuk, kunci, array pratinjau)

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.project = project
        self.selection = set()
        self.tool = "select"
        self.zoom = 2.0
        self.off = QPointF(40, 40)
        self._fitted = False
        self._user_view = False     # True setelah zoom/geser manual: jangan fit ulang saat jendela berubah
        self.show_grid = True
        self.preview = None            # QImage hamparan G-code
        self.laser = None              # (x, y) desain atau None
        self._pcache = {}
        self._img_cache = {}
        self._mode = None
        self._press = None
        self._orig = {}
        self._moved = False
        self._band = None
        self._drawing = None
        self._draw_shift = False
        self._poly_pts = []            # titik alat polyline
        self._poly_cur = None
        self.opt_sides = 6             # opsi alat poligon/bintang
        self.opt_inner = 0.5           # rasio jari-jari dalam bintang
        self.opt_radius = 0.0          # radius sudut persegi (mm)
        self._pv_pool = ThreadPoolExecutor(max_workers=1)
        self._pv_inflight = {}
        self._pv_wanted = {}
        self._img_stale = {}
        self.imageComputed.connect(self._image_computed)
        self._space = False
        self._hover_handle = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.setMinimumSize(320, 240)

    # ------------------------------------------------------------ koordinat
    def w2s(self, x, y):
        return QPointF(self.off.x() + x * self.zoom, self.off.y() + y * self.zoom)

    def s2w(self, p):
        return (p.x() - self.off.x()) / self.zoom, (p.y() - self.off.y()) / self.zoom

    def fit(self):
        dev = self.project.device
        w, h = max(self.width(), 100), max(self.height(), 100)
        m = 36
        self.zoom = max(0.05, min((w - 2 * m) / dev.bed_w, (h - 2 * m) / dev.bed_h))
        self.off = QPointF((w - dev.bed_w * self.zoom) / 2, (h - dev.bed_h * self.zoom) / 2)
        self._fitted = True
        self._user_view = False
        self.update()

    def fit_selection(self):
        shapes = self.selected_shapes() or self.project.shapes
        bb = self.project.bbox(shapes)
        if not bb:
            return self.fit()
        w, h = max(self.width(), 100), max(self.height(), 100)
        bw, bh = max(bb[2] - bb[0], 1.0), max(bb[3] - bb[1], 1.0)
        self.zoom = max(0.05, min((w - 80) / bw, (h - 80) / bh))
        self.off = QPointF(w / 2 - (bb[0] + bb[2]) / 2 * self.zoom, h / 2 - (bb[1] + bb[3]) / 2 * self.zoom)
        self._fitted = True
        self._user_view = True
        self.update()

    def resizeEvent(self, e):
        if not self._fitted or not self._user_view:
            self.fit()
        super().resizeEvent(e)

    # ------------------------------------------------------------ seleksi
    def selected_shapes(self):
        return [s for s in self.project.shapes if s.id in self.selection]

    def group_ids(self, ids):
        """Perluas himpunan id sehingga mencakup semua anggota grup yang tersentuh."""
        ids = set(ids)
        groups = {s.group for s in self.project.shapes if s.id in ids and s.group is not None}
        if groups:
            ids |= {s.id for s in self.project.shapes if s.group in groups}
        return ids

    def set_selection(self, ids):
        ids = self.group_ids(ids)
        if ids != self.selection:
            self.selection = ids
            self.selectionChanged.emit()
        self.update()

    def sel_bbox(self):
        sh = self.selected_shapes()
        return self.project.bbox(sh) if sh else None

    def _layer_visible(self, s):
        return self.project.layer(s.layer).visible

    def hit_test(self, x, y):
        tol = 6.0 / self.zoom
        for s in reversed(self.project.shapes):
            if not self._layer_visible(s):
                continue
            b = s.bbox()
            if not (b[0] - tol <= x <= b[2] + tol and b[1] - tol <= y <= b[3] + tol):
                continue
            if isinstance(s, ImageShape):
                if abs(s.angle) > 1e-9:       # uji titik pada persegi yang diputar
                    cx, cy = s.center()
                    th = math.radians(-s.angle)
                    dx, dy = x - cx, y - cy
                    u = dx * math.cos(th) - dy * math.sin(th)
                    v = dx * math.sin(th) + dy * math.cos(th)
                    if abs(u) <= s.w / 2 and abs(v) <= s.h / 2:
                        return s
                    continue
                if b[0] <= x <= b[2] and b[1] <= y <= b[3]:
                    return s
                continue
            fill = self.project.layer(s.layer).mode == "fill"
            for p in s.polys:
                if _dist_to_poly(p.pts, p.closed, x, y) <= tol:
                    return s
                if fill and p.closed and len(p.pts) >= 3 and point_in_poly(p.pts, x, y):
                    return s
        return None

    # ------------------------------------------------------------ pratinjau
    def set_preview(self, img):
        self.preview = img
        self.update()

    def set_laser(self, pos):
        self.laser = pos
        self.update()

    # ------------------------------------------------------------ gambar
    def _qpoly(self, pts):
        ent = self._pcache.get(id(pts))
        if ent is not None and ent[0] is pts:
            return ent[1]
        poly = QPolygonF([QPointF(float(a), float(b)) for a, b in pts])
        if len(self._pcache) > 4000:
            self._pcache.clear()
        self._pcache[id(pts)] = (pts, poly)
        return poly

    def _shape_path(self, s):
        key = ("p",) + tuple(id(p.pts) for p in s.polys)
        ent = self._pcache.get(key)
        if ent is not None and all(a is b.pts for a, b in zip(ent[0], s.polys)):
            return ent[1]
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        for p in s.polys:
            if p.closed:
                path.addPolygon(self._qpoly(p.pts))
                path.closeSubpath()
            else:
                path.addPolygon(self._qpoly(p.pts))
        self._pcache[key] = ([p.pts for p in s.polys], path)
        return path

    def _image_pixmap(self, s):
        """Pratinjau dither dihitung di thread latar; selama menunggu, pakai hasil lama (diregangkan)."""
        key = (id(s.img), round(s.w, 3), round(s.h, 3), s.opts.key())
        pm = self._img_cache.get((s.id, key))
        if pm is not None:
            return pm
        if self._pv_inflight.get(s.id) != key:
            self._pv_wanted[s.id] = (key, s.img, s.w, s.h, ImageOptions.from_dict(s.opts.to_dict()))
            if s.id not in self._pv_inflight:
                self._kick_preview(s.id)
        return self._img_stale.get(s.id)

    def _kick_preview(self, sid):
        key, img, w, h, opts = self._pv_wanted.pop(sid)
        self._pv_inflight[sid] = key

        def work():
            try:
                arr = raster.preview_gray(img, w, h, opts, max_px=700)
            except Exception:
                arr = np.full((8, 8), 200, np.uint8)
            self.imageComputed.emit(sid, key, np.ascontiguousarray(arr))

        try:
            self._pv_pool.submit(work)
        except RuntimeError:
            self._pv_inflight.pop(sid, None)

    def _image_computed(self, sid, key, arr):
        self._pv_inflight.pop(sid, None)
        qi = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1], QImage.Format_Grayscale8).copy()
        pm = QPixmap.fromImage(qi)
        if len(self._img_cache) > 24:
            self._img_cache.clear()
        self._img_cache[(sid, key)] = pm
        self._img_stale[sid] = pm
        if sid in self._pv_wanted:
            self._kick_preview(sid)
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#2d3036"))
        dev = self.project.device
        z = self.zoom
        bed = QRectF(self.off.x(), self.off.y(), dev.bed_w * z, dev.bed_h * z)
        p.fillRect(bed, QColor("#fbfbfb"))
        if self.show_grid:
            self._draw_grid(p, bed, dev)
        p.setPen(QPen(QColor("#5d6470"), 1.5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(bed)
        self._draw_origin(p, dev)

        p.save()
        p.translate(self.off)
        p.scale(z, z)
        if self.preview is not None:
            p.setOpacity(0.28)   # desain dipudarkan, hasil G-code di atasnya
        for s in self.project.shapes:
            L = self.project.layer(s.layer)
            if not L.visible:
                continue
            self._draw_shape(p, s, L, s.id in self.selection)
        if self.preview is not None:
            p.setOpacity(1.0)
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.drawImage(QRectF(0, 0, dev.bed_w, dev.bed_h), self.preview)
        p.restore()

        self._draw_selection(p)
        self._draw_overlays(p)
        p.end()

    def _draw_grid(self, p, bed, dev):
        z = self.zoom
        step = 1.0
        for st in (1, 5, 10, 25, 50, 100, 250):
            step = st
            if st * z >= 9:
                break
        major = step * (5 if step in (1, 5, 25) else 2 if step in (10, 50, 250) else 5)
        clip = bed.intersected(QRectF(self.rect()))
        p.save()
        p.setClipRect(clip)
        x = 0.0
        while x <= dev.bed_w + 1e-6:
            sx = self.off.x() + x * z
            is_major = abs(x / major - round(x / major)) < 1e-6
            p.setPen(QPen(QColor("#c9ccd4" if is_major else "#e6e8ec"), 1))
            p.drawLine(QPointF(sx, bed.top()), QPointF(sx, bed.bottom()))
            x += step
        y = 0.0
        while y <= dev.bed_h + 1e-6:
            sy = self.off.y() + y * z
            is_major = abs(y / major - round(y / major)) < 1e-6
            p.setPen(QPen(QColor("#c9ccd4" if is_major else "#e6e8ec"), 1))
            p.drawLine(QPointF(bed.left(), sy), QPointF(bed.right(), sy))
            y += step
        p.restore()
        # label penggaris
        f = QFont(self.font())
        f.setPointSizeF(7.5)
        p.setFont(f)
        p.setPen(QColor("#9aa1ad"))
        x = 0.0
        while x <= dev.bed_w + 1e-6:
            if abs(x / major - round(x / major)) < 1e-6:
                p.drawText(QPointF(self.off.x() + x * z + 2, self.off.y() - 4), "%g" % x)
            x += step
        y = 0.0
        while y <= dev.bed_h + 1e-6:
            if abs(y / major - round(y / major)) < 1e-6:
                p.drawText(QPointF(self.off.x() - 30, self.off.y() + y * z + 4), "%g" % dev.to_user(0, y)[1])
            y += step

    def _draw_origin(self, p, dev):
        pt = self.w2s(0, dev.bed_h if dev.origin == "bottom-left" else 0)
        p.setPen(QPen(QColor("#d23c3c"), 2))
        d = 14 if dev.origin == "bottom-left" else -14
        p.drawLine(pt, pt + QPointF(14, 0))
        p.setPen(QPen(QColor("#2c9a3c"), 2))
        p.drawLine(pt, pt + QPointF(0, -d))
        f = QFont(self.font())
        f.setPointSizeF(8)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor("#d23c3c"))
        p.drawText(pt + QPointF(4, 14 if dev.origin != "bottom-left" else -5), "0,0")

    def _draw_shape(self, p, s, L, selected):
        col = QColor(L.color)
        if isinstance(s, ImageShape):
            r = QRectF(s.x, s.y, s.w, s.h)
            rotated = abs(s.angle) > 1e-9
            if rotated:
                p.save()
                p.translate(r.center())
                p.rotate(s.angle)             # searah jarum jam pada sumbu Y ke bawah
                r = QRectF(-s.w / 2, -s.h / 2, s.w, s.h)
            if L.mode == "image":
                pm = self._image_pixmap(s)
                if pm is not None:
                    p.drawPixmap(r, pm, QRectF(pm.rect()))
                else:
                    p.fillRect(r, QColor(215, 215, 215, 150))
            else:
                p.fillRect(r, QColor(200, 200, 200, 120))
            pen = QPen(col, 1.2)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
            if rotated:
                p.restore()
            return
        path = self._shape_path(s)
        pen = QPen(col, 2.2 if selected else 1.4)
        pen.setCosmetic(True)
        if not L.output:
            pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        if L.mode == "fill":
            c = QColor(col)
            c.setAlpha(90)
            p.setBrush(QBrush(c))
        else:
            p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _handles(self, bb):
        a, b = self.w2s(bb[0], bb[1]), self.w2s(bb[2], bb[3])
        cx, cy = (a.x() + b.x()) / 2, (a.y() + b.y()) / 2
        pts = {TOP_L: QPointF(a.x(), a.y()), TOP: QPointF(cx, a.y()), TOP_R: QPointF(b.x(), a.y()),
               RIGHT: QPointF(b.x(), cy), BOT_R: QPointF(b.x(), b.y()), BOT: QPointF(cx, b.y()),
               BOT_L: QPointF(a.x(), b.y()), LEFT: QPointF(a.x(), cy)}
        if self._can_rotate():
            pts[ROT] = QPointF(cx, a.y() - 24)
        return pts

    def _can_rotate(self):
        return True

    def _draw_selection(self, p):
        bb = self.sel_bbox()
        if not bb:
            return
        a, b = self.w2s(bb[0], bb[1]), self.w2s(bb[2], bb[3])
        pen = QPen(QColor("#1f7ae0"), 1, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(a, b))
        hs = self._handles(bb)
        p.setPen(QPen(QColor("#1f7ae0"), 1.2))
        p.setBrush(QColor("#ffffff"))
        for k, pt in hs.items():
            if k == ROT:
                p.drawLine(QPointF(pt.x(), a.y()), pt)
                p.drawEllipse(pt, 5, 5)
            else:
                p.drawRect(QRectF(pt.x() - HANDLE_PX / 2, pt.y() - HANDLE_PX / 2, HANDLE_PX, HANDLE_PX))

    def _draw_overlays(self, p):
        if self._band is not None:
            a, b = self._band
            r = QRectF(self.w2s(*a), self.w2s(*b)).normalized()
            crossing = b[0] < a[0]
            c = QColor("#2ca44a" if crossing else "#1f7ae0")
            p.setPen(QPen(c, 1, Qt.DashLine))
            c.setAlpha(40)
            p.setBrush(c)
            p.drawRect(r)
        if self._drawing is not None:
            kind, a, b = self._drawing
            poly = self._make_poly(kind, a, b, self._draw_shift)
            if poly is not None:
                p.setPen(QPen(QColor("#e07a1f"), 1.5, Qt.DashLine))
                p.setBrush(Qt.NoBrush)
                qp = QPolygonF([self.w2s(float(x), float(y)) for x, y in poly.pts])
                p.drawPolygon(qp) if poly.closed else p.drawPolyline(qp)
        if self._poly_pts:
            pts = [self.w2s(*q) for q in self._poly_pts]
            if self._poly_cur is not None:
                pts.append(self.w2s(*self._poly_cur))
            p.setPen(QPen(QColor("#e07a1f"), 1.5, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(QPolygonF(pts))
            p.setBrush(QColor("#ffffff"))
            for i, q in enumerate(pts[:len(self._poly_pts)]):
                p.drawEllipse(q, 4.5 if i == 0 else 3, 4.5 if i == 0 else 3)
        if self.laser is not None:
            pt = self.w2s(*self.laser)
            p.setPen(QPen(QColor("#e03030"), 1.6))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(pt, 7, 7)
            p.drawLine(pt - QPointF(12, 0), pt + QPointF(12, 0))
            p.drawLine(pt - QPointF(0, 12), pt + QPointF(0, 12))

    # ------------------------------------------------------------ mouse
    def _handle_at(self, pos):
        bb = self.sel_bbox()
        if not bb:
            return None
        for k, pt in self._handles(bb).items():
            if abs(pt.x() - pos.x()) <= HANDLE_PX and abs(pt.y() - pos.y()) <= HANDLE_PX:
                return k
        return None

    def _begin_edit(self, key):
        self.aboutToEdit.emit(key)
        self._orig = {s.id: s.clone() for s in self.selected_shapes()}

    def _apply(self, fn):
        sh = self.project.shapes
        for i, s in enumerate(sh):
            o = self._orig.get(s.id)
            if o is not None:
                c = o.clone()
                fn(c)
                sh[i] = c

    def mousePressEvent(self, e):
        self.setFocus()
        pos = e.pos()
        x, y = self.s2w(pos)
        if e.button() == Qt.MiddleButton or (e.button() == Qt.LeftButton and self._space):
            self._mode = "pan"
            self._user_view = True
            self._press = (pos, QPointF(self.off))
            self.setCursor(Qt.ClosedHandCursor)
            return
        if e.button() != Qt.LeftButton:
            return
        if self.tool == "text":
            self.textRequested.emit(x, y)
            return
        if self.tool == "polyline":
            if len(self._poly_pts) >= 3:
                f = self.w2s(*self._poly_pts[0])
                if abs(f.x() - pos.x()) <= 9 and abs(f.y() - pos.y()) <= 9:
                    self._finish_polyline(True)
                    return
            if not self._poly_pts or math.hypot(x - self._poly_pts[-1][0], y - self._poly_pts[-1][1]) > 0.05:
                self._poly_pts.append((x, y))
            self._poly_cur = (x, y)
            self.update()
            return
        if self.tool in ("rect", "ellipse", "line", "polygon", "star"):
            self._mode = "draw"
            self._drawing = (self.tool, (x, y), (x, y))
            self._draw_shift = bool(e.modifiers() & Qt.ShiftModifier)
            return
        h = self._handle_at(pos)
        bb = self.sel_bbox()
        if h is not None and bb:
            self._press = (pos, (x, y), bb, h)
            self._moved = False
            self._mode = "rotate" if h == ROT else "scale"
            return
        hit = self.hit_test(x, y)
        shift = bool(e.modifiers() & Qt.ShiftModifier)
        if hit is not None:
            if shift:
                ids = set(self.selection)
                members = self.group_ids({hit.id})          # satu grup = satu kesatuan
                if hit.id in ids:
                    ids -= members
                else:
                    ids |= members
                self.set_selection(ids)
            elif hit.id not in self.selection:
                self.set_selection({hit.id})
            if hit.id in self.selection:
                self._mode = "move"
                self._press = (pos, (x, y))
                self._moved = False
        else:
            if not shift:
                self.set_selection(set())
            self._mode = "band"
            self._band = ((x, y), (x, y))

    def mouseMoveEvent(self, e):
        pos = e.pos()
        x, y = self.s2w(pos)
        self.cursorMoved.emit(x, y)
        if self._mode == "pan":
            p0, o0 = self._press
            self.off = o0 + (pos - p0)
            self.update()
            return
        if self._mode == "draw":
            kind, a, _ = self._drawing
            self._drawing = (kind, a, (x, y))
            self._draw_shift = bool(e.modifiers() & Qt.ShiftModifier)
            self.update()
            return
        if self.tool == "polyline" and self._poly_pts:
            self._poly_cur = (x, y)
            self.update()
            return
        if self._mode == "band":
            self._band = (self._band[0], (x, y))
            self.update()
            return
        if self._mode == "move":
            p0, w0 = self._press
            if not self._moved and (abs(pos.x() - p0.x()) + abs(pos.y() - p0.y())) < 3:
                return
            if not self._moved:
                self._moved = True
                self._begin_edit("move")
            dx, dy = x - w0[0], y - w0[1]
            if e.modifiers() & Qt.ControlModifier:
                if abs(dx) > abs(dy):
                    dy = 0
                else:
                    dx = 0
            self._apply(lambda c: c.translate(dx, dy))
            self.geometryChanged.emit()
            self.update()
            return
        if self._mode == "scale":
            self._do_scale(pos, x, y, e)
            return
        if self._mode == "rotate":
            self._do_rotate(x, y, e)
            return
        # hover
        h = self._handle_at(pos)
        if h is not None:
            self.setCursor(CURSORS[h])
        elif self.tool != "select":
            self.setCursor(Qt.CrossCursor)
        elif self._space:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setCursor(Qt.SizeAllCursor if self.hit_test(x, y) is not None else Qt.ArrowCursor)

    def _do_scale(self, pos, x, y, e):
        p0, w0, bb, h = self._press
        if not self._moved:
            if abs(pos.x() - p0.x()) + abs(pos.y() - p0.y()) < 3:
                return
            self._moved = True
            self._begin_edit("scale")
        x0, y0, x1, y1 = bb
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        ax = {TOP_L: x1, TOP_R: x0, BOT_R: x0, BOT_L: x1, TOP: cx, BOT: cx, LEFT: x1, RIGHT: x0}[h]
        ay = {TOP_L: y1, TOP_R: y1, BOT_R: y0, BOT_L: y0, TOP: y1, BOT: y0, LEFT: cy, RIGHT: cy}[h]
        hx = {TOP_L: x0, TOP_R: x1, BOT_R: x1, BOT_L: x0, TOP: cx, BOT: cx, LEFT: x0, RIGHT: x1}[h]
        hy = {TOP_L: y0, TOP_R: y0, BOT_R: y1, BOT_L: y1, TOP: y0, BOT: y1, LEFT: cy, RIGHT: cy}[h]
        sx = (x - ax) / (hx - ax) if abs(hx - ax) > 1e-9 else 1.0
        sy = (y - ay) / (hy - ay) if abs(hy - ay) > 1e-9 else 1.0
        if h in (TOP, BOT):
            sx = 1.0
        elif h in (LEFT, RIGHT):
            sy = 1.0
        elif not (e.modifiers() & Qt.ShiftModifier):
            vx, vy = hx - ax, hy - ay
            den = vx * vx + vy * vy
            s = ((x - ax) * vx + (y - ay) * vy) / den if den > 1e-12 else 1.0
            sx = sy = s
        sx, sy = max(sx, 0.01), max(sy, 0.01)
        self._apply(lambda c: c.scale(sx, sy, ax, ay))
        self.geometryChanged.emit()
        self.update()

    def _do_rotate(self, x, y, e):
        p0, w0, bb, h = self._press
        if not self._moved:
            self._moved = True
            self._begin_edit("rotate")
        cx, cy = (bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2
        a0 = math.atan2(w0[1] - cy, w0[0] - cx)
        a1 = math.atan2(y - cy, x - cx)
        deg = math.degrees(a1 - a0)
        if e.modifiers() & Qt.ShiftModifier:
            deg = round(deg / 15.0) * 15.0
        self._apply(lambda c: c.rotate(deg, cx, cy))
        self.geometryChanged.emit()
        self.update()

    def mouseReleaseEvent(self, e):
        mode = self._mode
        self._mode = None
        if mode == "pan":
            self.setCursor(Qt.ArrowCursor)
        elif mode == "draw" and self._drawing is not None:
            kind, a, b = self._drawing
            self._drawing = None
            self._finish_draw(kind, a, b, self._draw_shift)
        elif mode == "band" and self._band is not None:
            a, b = self._band
            self._band = None
            self._finish_band(a, b, bool(e.modifiers() & Qt.ShiftModifier))
        elif mode in ("move", "scale", "rotate") and self._moved:
            self._orig = {}
            self.geometryChanged.emit()
        self._press = None
        self.update()

    def _make_poly(self, kind, a, b, shift=False):
        """Bentuk dari alat gambar: a = titik tekan, b = titik sekarang. None bila terlalu kecil."""
        if kind == "line":
            if math.hypot(b[0] - a[0], b[1] - a[1]) < 0.2:
                return None
            if shift:   # kunci ke kelipatan 15 derajat
                ang = round(math.atan2(b[1] - a[1], b[0] - a[0]) / (math.pi / 12)) * (math.pi / 12)
                d = math.hypot(b[0] - a[0], b[1] - a[1])
                b = (a[0] + d * math.cos(ang), a[1] + d * math.sin(ang))
            return Poly([a, b], False)
        if kind in ("polygon", "star"):
            r = math.hypot(b[0] - a[0], b[1] - a[1])
            if r < 0.3:
                return None
            rot = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            if shift:
                rot = round(rot / 15.0) * 15.0
            if kind == "polygon":
                return shp.regular_polygon(a[0], a[1], r, self.opt_sides, rot)
            return shp.star(a[0], a[1], r, self.opt_sides, self.opt_inner, rot)
        if shift:
            d = max(abs(b[0] - a[0]), abs(b[1] - a[1]))
            b = (a[0] + math.copysign(d, b[0] - a[0] or 1), a[1] + math.copysign(d, b[1] - a[1] or 1))
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        if x1 - x0 < 0.2 or y1 - y0 < 0.2:
            return None
        if kind == "rect":
            return shp.rounded_rect(x0, y0, x1, y1, self.opt_radius)
        return ellipse_poly((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2)

    def _finish_draw(self, kind, a, b, shift=False):
        poly = self._make_poly(kind, a, b, shift)
        if poly is not None:
            self.shapeCreated.emit(PathShape([poly], kind=kind))

    def _finish_polyline(self, closed):
        pts = self._poly_pts
        self._poly_pts, self._poly_cur = [], None
        if len(pts) >= (3 if closed else 2):
            self.shapeCreated.emit(PathShape([Poly(pts, closed)], kind="polyline"))
        self.update()

    def _finish_band(self, a, b, shift):
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        if (x1 - x0) * self.zoom < 3 and (y1 - y0) * self.zoom < 3:
            return
        crossing = b[0] < a[0]
        ids = set(self.selection) if shift else set()
        for s in self.project.shapes:
            if not self._layer_visible(s):
                continue
            bb = s.bbox()
            inside = bb[0] >= x0 and bb[2] <= x1 and bb[1] >= y0 and bb[3] <= y1
            touch = not (bb[2] < x0 or bb[0] > x1 or bb[3] < y0 or bb[1] > y1)
            if inside or (crossing and touch):
                ids.add(s.id)
        self.set_selection(ids)

    def mouseDoubleClickEvent(self, e):
        if self.tool == "polyline" and self._poly_pts:
            self._finish_polyline(False)
            return
        if e.button() == Qt.LeftButton and self.tool == "select" and self.hit_test(*self.s2w(e.pos())) is None:
            self.fit()

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        mx, my = self.s2w(e.pos())
        self.zoom = max(0.05, min(80.0, self.zoom * f))
        self.off = QPointF(e.pos().x() - mx * self.zoom, e.pos().y() - my * self.zoom)
        self._fitted = True
        self._user_view = True
        self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self.tool == "select":
            x, y = self.s2w(e.pos())
            self.doubleClicked.emit(x, y)
            return
        super().mouseDoubleClickEvent(e)

    def contextMenuEvent(self, e):
        if self.tool == "polyline" and self._poly_pts:
            self._finish_polyline(False)
            return
        x, y = self.s2w(e.pos())
        hit = self.hit_test(x, y)
        if hit is not None and hit.id not in self.selection:
            self.set_selection({hit.id})
        self.contextRequested.emit(e.globalPos(), x, y)

    # ------------------------------------------------------------ keyboard
    def keyPressEvent(self, e):
        k = e.key()
        if k == Qt.Key_Space and not e.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.OpenHandCursor)
            return
        if k in (Qt.Key_Return, Qt.Key_Enter) and self._poly_pts:
            self._finish_polyline(False)
            return
        if k == Qt.Key_Escape:
            self._drawing = None
            self._band = None
            if self._poly_pts:
                self._poly_pts, self._poly_cur = [], None
                self.update()
                return
            if self.tool != "select":
                self.toolReset.emit()
            else:
                self.set_selection(set())
            return
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.deleteRequested.emit()
            return
        if k in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down) and self.selection:
            step = 10.0 if e.modifiers() & Qt.ShiftModifier else 0.1 if e.modifiers() & Qt.ControlModifier else 1.0
            dx = {Qt.Key_Left: -step, Qt.Key_Right: step}.get(k, 0.0)
            dy = {Qt.Key_Up: -step, Qt.Key_Down: step}.get(k, 0.0)
            self.aboutToEdit.emit("nudge")
            for s in self.selected_shapes():
                s.translate(dx, dy)
            self.geometryChanged.emit()
            self.update()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() == Qt.Key_Space and not e.isAutoRepeat():
            self._space = False
            self.setCursor(Qt.ArrowCursor)
            return
        super().keyReleaseEvent(e)

    # ------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
