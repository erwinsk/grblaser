# SPDX-License-Identifier: MIT
"""Model data aplikasi: layer, perangkat, bentuk (shape) dan proyek.

Ruang desain: satuan mm, titik (0,0) di kiri-atas bed, sumbu Y ke BAWAH
(sama dengan tampilan kanvas). Konversi ke koordinat mesin dilakukan hanya
saat G-code dibuat (lihat gcode.py).
"""
from __future__ import annotations

import copy
import itertools
import math
from dataclasses import dataclass, asdict, fields

import numpy as np
from PIL import Image, ImageOps

LAYER_COLORS = [
    "#000000", "#0000ff", "#ff0000", "#00b000", "#d0b000", "#ff8000",
    "#00c8c8", "#ff00ff", "#808080", "#000080", "#a00000", "#006400",
    "#8c8c00", "#c06000", "#0080c0", "#800080", "#4040ff", "#ff4080",
    "#40c040", "#c0c040", "#ffa060", "#60e0e0", "#ff80ff", "#a0a0a0",
    "#6060c0", "#c06060", "#60a060", "#a08040", "#40a0a0", "#a040a0",
]
MAX_LAYERS = 30

MODES = ("line", "fill", "image")
MODE_LABELS = {"line": "Garis", "fill": "Isi", "image": "Gambar"}

_ids = itertools.count(1)


# --------------------------------------------------------------------------
# Layer & perangkat
# --------------------------------------------------------------------------
@dataclass
class Layer:
    index: int = 0
    name: str = ""
    color: str = "#000000"
    mode: str = "line"          # line | fill | image
    speed: float = 1000.0       # mm/menit
    power: float = 60.0         # % dari S-max (daya maksimum)
    min_power: float = 0.0      # % (dipakai mode gambar grayscale)
    passes: int = 1
    output: bool = True         # ikut dikerjakan laser
    visible: bool = True        # tampil di kanvas
    air: bool = False           # air assist (M8/M9)
    interval: float = 0.1       # mm, jarak antar garis isian
    angle: float = 0.0          # derajat, arah isian
    bidir: bool = True          # dua arah (zig-zag)
    overscan: float = 0.0       # mm
    crosshatch: bool = False    # isian silang (tambah 90 derajat)
    fill_group: bool = True     # isi semua bentuk sekaligus (even-odd)
    inner_first: bool = True    # potong bentuk dalam lebih dulu

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        names = {f.name for f in fields(Layer)}
        return Layer(**{k: v for k, v in d.items() if k in names})


@dataclass
class Device:
    name: str = "GRBL"
    bed_w: float = 400.0
    bed_h: float = 400.0
    s_max: int = 1000            # nilai $30
    laser_cmd: str = "M4"        # M4 (dinamis, disarankan) atau M3
    origin: str = "bottom-left"  # asal mesin: bottom-left | top-left
    rapid_rate: float = 3000.0   # mm/menit, hanya untuk estimasi waktu
    start_from: str = "absolute"  # absolute | current
    anchor: int = 0              # 0..8 baris-demi-baris (0=kiri-atas, 4=tengah, 8=kanan-bawah)
    end_return: str = "none"     # none | origin
    frame_speed: float = 3000.0
    frame_power: float = 0.0     # % daya saat bingkai (dipakai bila frame_laser aktif)
    frame_laser: bool = False    # laser menyala redup saat bingkai (hanya untuk melihat posisi)
    air_on: str = "M8"
    air_off: str = "M9"
    baud: int = 115200
    last_port: str = ""
    firmware: str = "grbl"       # grbl | grbl-hal | marlin (eksperimental)
    # Rotary (sumbu Y menggerakkan pemutar): Y desain = keliling benda yang diskalakan ke gerak mesin
    rotary: bool = False
    rotary_mode: str = "chuck"   # chuck (jepit/rahang) | roller (rol penyangga)
    object_diameter: float = 50.0    # mm, diameter benda di titik bakar
    roller_diameter: float = 20.0    # mm, diameter rol (mode roller)
    mm_per_rotation: float = 360.0   # gerak Y mesin untuk satu putaran penuh (mode chuck; 360 bila $101 dalam derajat)

    def rotary_scale(self):
        """Faktor pengali Y desain -> Y mesin (1.0 bila rotary mati)."""
        if not self.rotary or self.object_diameter <= 0:
            return 1.0
        if self.rotary_mode == "roller":
            return self.roller_diameter / self.object_diameter
        return self.mm_per_rotation / (3.141592653589793 * self.object_diameter)

    # --- koordinat pengguna (yang tampil di penggaris, kursor, toolbar & properti) ---
    # Ruang desain: (0,0) kiri-atas, Y ke bawah. Koordinat pengguna = koordinat mesin kerja:
    # (0,0) di titik asal mesin, Y ke atas bila asal kiri-bawah. Fungsi ini satu-satunya tempat konversinya.
    def to_user(self, x, y):
        return (x, self.bed_h - y) if self.origin == "bottom-left" else (x, y)

    def from_user(self, X, Y):
        return (X, self.bed_h - Y) if self.origin == "bottom-left" else (X, Y)

    def same_machine(self, other):
        """Mesin yang sama untuk keperluan koordinat: nama, area kerja, asal, dan S maks."""
        return (self.name == other.name and abs(self.bed_w - other.bed_w) < 1e-6
                and abs(self.bed_h - other.bed_h) < 1e-6 and self.origin == other.origin
                and self.s_max == other.s_max)

    def origin_anchor(self):
        """Indeks 0..8 (kolom + 3*baris) titik kotak pembatas yang paling dekat dengan asal mesin."""
        return 6 if self.origin == "bottom-left" else 0

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        names = {f.name for f in fields(Device)}
        dev = Device(**{k: v for k, v in d.items() if k in names})
        if "frame_laser" not in d and dev.frame_power > 0:      # berkas/profil lama: daya > 0 berarti laser menyala
            dev.frame_laser = True
        return dev


@dataclass
class ImageOptions:
    method: str = "gray"     # lihat raster.METHODS
    lpmm: float = 10.0       # garis per mm (kualitas); 10 = 0,1 mm
    angle: float = 0.0       # arah garis pindai (derajat)
    brightness: float = 0.0  # -100..100
    contrast: float = 0.0    # -100..100
    gamma: float = 1.0
    threshold: int = 128     # 0..255 (threshold & trace)
    white_clip: int = 255    # piksel >= nilai ini dianggap putih
    invert: bool = False
    gray_levels: int = 64    # kuantisasi tingkat abu-abu (mode gray)
    sharpen: float = 0.0     # 0..300 (%), unsharp mask pada resolusi keluaran
    blur: float = 0.0        # 0..5 piksel keluaran (haluskan noise sebelum dither)
    autolevel: bool = False  # regangkan histogram (1%..99%)
    cell_mm: float = 1.0     # ukuran sel halftone / jarak garis screen (mm)
    cell_angle: float = 45.0  # sudut screen halftone (derajat)

    def to_dict(self):
        return asdict(self)

    @staticmethod
    def from_dict(d):
        names = {f.name for f in fields(ImageOptions)}
        return ImageOptions(**{k: v for k, v in d.items() if k in names})

    def key(self):
        return tuple(sorted(asdict(self).items()))


# --------------------------------------------------------------------------
# Geometri
# --------------------------------------------------------------------------
class Poly:
    __slots__ = ("pts", "closed")

    def __init__(self, pts, closed=False):
        self.pts = np.asarray(pts, dtype=float).reshape(-1, 2)
        self.closed = bool(closed)

    def copy(self):
        return Poly(self.pts.copy(), self.closed)


def rect_poly(x0, y0, x1, y1):
    return Poly([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True)


def ellipse_poly(cx, cy, rx, ry, tol=0.02):
    r = max(rx, ry, 1e-6)
    n = int(math.ceil(math.pi / math.acos(max(0.0, 1.0 - tol / r)))) if r > tol else 16
    n = max(24, min(n, 1440))
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return Poly(np.column_stack((cx + rx * np.cos(t), cy + ry * np.sin(t))), True)


class Shape:
    type = "shape"

    def __init__(self, layer=0, name=""):
        self.id = next(_ids)
        self.layer = layer
        self.name = name
        self.group = None      # id grup (bilangan bulat) atau None


class PathShape(Shape):
    type = "path"

    def __init__(self, polys, layer=0, kind="path", name=""):
        super().__init__(layer, name)
        self.polys = [p for p in polys if len(p.pts) >= 2]
        self.kind = kind
        # Teks yang masih bisa diedit: {"params": {...}, "m": [a,b,c,d,e,f]}; m = transformasi dari kontur hasil
        # pembuatan (pojok kiri-atas di 0,0) ke posisi sekarang, diperbarui oleh setiap translate/skala/putar.
        self.text_spec = None

    def bbox(self):
        allp = np.vstack([p.pts for p in self.polys])
        return (float(allp[:, 0].min()), float(allp[:, 1].min()),
                float(allp[:, 0].max()), float(allp[:, 1].max()))

    def apply(self, a, b, c, d, e, f):
        if self.text_spec is not None:
            a1, b1, c1, d1, e1, f1 = self.text_spec["m"]
            self.text_spec["m"] = [a * a1 + c * b1, b * a1 + d * b1, a * c1 + c * d1, b * c1 + d * d1,
                                   a * e1 + c * f1 + e, b * e1 + d * f1 + f]
        for p in self.polys:
            x = p.pts[:, 0]
            y = p.pts[:, 1]
            p.pts = np.column_stack((a * x + c * y + e, b * x + d * y + f))

    def translate(self, dx, dy):
        self.apply(1, 0, 0, 1, dx, dy)

    def scale(self, sx, sy, cx, cy):
        self.apply(sx, 0, 0, sy, cx - sx * cx, cy - sy * cy)

    def rotate(self, deg, cx, cy):
        """Putar `deg` derajat searah jarum jam (di tampilan, Y ke bawah)."""
        t = math.radians(deg)
        c, s = math.cos(t), math.sin(t)
        self.apply(c, s, -s, c, cx - c * cx + s * cy, cy - s * cx - c * cy)
        return True

    def flip(self, horizontal, cx, cy):
        if horizontal:
            self.scale(-1, 1, cx, cy)
        else:
            self.scale(1, -1, cx, cy)

    def clone(self):
        c = PathShape([p.copy() for p in self.polys], self.layer, self.kind, self.name)
        c.id = self.id
        c.group = self.group
        c.text_spec = copy.deepcopy(self.text_spec)
        return c

    def set_text_polys(self, polys, params):
        """Ganti isi teks (kontur baru dibuat di titik asal), pertahankan transformasi pengguna."""
        a, b, c, d, e, f = self.text_spec["m"]
        new = []
        for p in polys:
            q = p.copy()
            x, y = q.pts[:, 0], q.pts[:, 1]
            q.pts = np.column_stack((a * x + c * y + e, b * x + d * y + f))
            new.append(q)
        self.polys = new
        self.text_spec["params"] = dict(params)
        self.name = (params.get("text", "").split("\n")[0])[:24] or self.name

    def point_count(self):
        return int(sum(len(p.pts) for p in self.polys))


class ImageShape(Shape):
    """Gambar raster. (x, y, w, h) = persegi *sebelum* rotasi; `angle` (derajat, searah jarum jam) memutarnya
    mengelilingi pusat persegi itu. Tanpa rotasi bebas (angle == 0) rotasi 90° dilakukan lossless pada piksel."""
    type = "image"

    def __init__(self, img, x, y, w, h, opts=None, layer=0, name=""):
        super().__init__(layer, name)
        self.img = img.convert("L")
        self.x, self.y, self.w, self.h = float(x), float(y), float(w), float(h)
        self.opts = opts or ImageOptions()
        self.angle = 0.0

    def center(self):
        return self.x + self.w / 2.0, self.y + self.h / 2.0

    def corners(self):
        """Empat sudut (ruang desain) setelah rotasi, berurutan kiri-atas, kanan-atas, kanan-bawah, kiri-bawah."""
        cx, cy = self.center()
        th = math.radians(self.angle)
        c, s = math.cos(th), math.sin(th)
        hw, hh = self.w / 2.0, self.h / 2.0
        return [(cx + dx * c - dy * s, cy + dx * s + dy * c)
                for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]

    def bbox(self):
        if abs(self.angle) < 1e-9:
            return (self.x, self.y, self.x + self.w, self.y + self.h)
        pts = self.corners()
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))

    def translate(self, dx, dy):
        self.x += dx
        self.y += dy

    def scale(self, sx, sy, cx, cy):
        if abs(self.angle) > 1e-9:
            # gambar yang diputar hanya bisa diskalakan proporsional (persegi miring tetap persegi)
            k = math.sqrt(abs(sx * sy)) or 1e-9
            ccx, ccy = self.center()
            nx, ny = cx + (ccx - cx) * sx, cy + (ccy - cy) * sy
            self.w *= k
            self.h *= k
            self.x, self.y = nx - self.w / 2, ny - self.h / 2
            if sx < 0:
                self.img = ImageOps.mirror(self.img)
                self.angle = -self.angle % 360.0
            if sy < 0:
                self.img = ImageOps.flip(self.img)
                self.angle = -self.angle % 360.0
            return
        x0 = cx + (self.x - cx) * sx
        x1 = cx + (self.x + self.w - cx) * sx
        y0 = cy + (self.y - cy) * sy
        y1 = cy + (self.y + self.h - cy) * sy
        if x1 < x0:
            self.img = ImageOps.mirror(self.img)
        if y1 < y0:
            self.img = ImageOps.flip(self.img)
        self.x, self.y = min(x0, x1), min(y0, y1)
        self.w, self.h = abs(x1 - x0), abs(y1 - y0)

    def rotate(self, deg, cx, cy):
        """Putar searah jarum jam `deg` derajat mengelilingi (cx, cy). Sudut bebas diperbolehkan."""
        ccx, ccy = self.center()
        th = math.radians(deg)
        mx, my = ccx - cx, ccy - cy
        nx = cx + mx * math.cos(th) - my * math.sin(th)
        ny = cy + mx * math.sin(th) + my * math.cos(th)
        ang = (self.angle + deg) % 360.0
        q = ang / 90.0
        if abs(q - round(q)) * 90.0 < 1e-6:          # kelipatan 90°: putar piksel (tanpa interpolasi)
            k = int(round(q)) % 4
            if k:
                self.img = self.img.rotate(-90 * k, expand=True)
                if k % 2 == 1:
                    self.w, self.h = self.h, self.w
            self.angle = 0.0
        else:
            self.angle = ang
        self.x, self.y = nx - self.w / 2, ny - self.h / 2
        return True

    def set_angle(self, deg):
        """Atur sudut absolut (−180..180) mengelilingi pusat gambar."""
        cur = self.angle if self.angle <= 180 else self.angle - 360
        cx, cy = self.center()
        self.rotate(deg - cur, cx, cy)

    def flip(self, horizontal, cx, cy):
        if horizontal:
            self.scale(-1, 1, cx, cy)
        else:
            self.scale(1, -1, cx, cy)

    def crop(self, l, t, r, b):
        """Potong gambar (pecahan 0..1 dari tiap sisi); isi gambar tetap di tempatnya. Tidak untuk gambar yang diputar."""
        if abs(self.angle) > 1e-9:
            return False
        W, H = self.img.size
        x0, y0 = int(round(W * l)), int(round(H * t))
        x1, y1 = W - int(round(W * r)), H - int(round(H * b))
        if x1 - x0 < 2 or y1 - y0 < 2:
            return False
        fw, fh = (x1 - x0) / W, (y1 - y0) / H
        self.x += self.w * x0 / W
        self.y += self.h * y0 / H
        self.w *= fw
        self.h *= fh
        self.img = self.img.crop((x0, y0, x1, y1))
        return True

    def rotated(self, lpmm=10.0, max_side_px=3200):
        """(img, mask, x, y, w, h) untuk pengolahan raster/trace.

        Tanpa rotasi bebas: gambar asli. Dengan rotasi: gambar dirotasi pada resolusi kerja (cukup untuk `lpmm`
        garis/mm) dengan latar putih; `mask` (L) menandai piksel yang berasal dari gambar (255) vs sudut kosong (0)."""
        if abs(self.angle) < 1e-9:
            return self.img, None, self.x, self.y, self.w, self.h
        src_k = max(self.img.width / self.w, self.img.height / self.h)
        k = min(src_k, 2.0 * max(lpmm, 1.0), max_side_px / max(self.w, self.h))
        W, H = max(2, int(round(self.w * k))), max(2, int(round(self.h * k)))
        base = self.img.resize((W, H), Image.LANCZOS)
        rot = base.rotate(-self.angle, expand=True, resample=Image.BICUBIC, fillcolor=255)
        mask = Image.new("L", (W, H), 255).rotate(-self.angle, expand=True, resample=Image.BILINEAR, fillcolor=0)
        w2, h2 = rot.width / k, rot.height / k
        cx, cy = self.center()
        return rot, mask, cx - w2 / 2.0, cy - h2 / 2.0, w2, h2

    def clone(self):
        c = ImageShape(self.img, self.x, self.y, self.w, self.h,
                       ImageOptions.from_dict(self.opts.to_dict()), self.layer, self.name)
        c.id = self.id
        c.group = self.group
        c.angle = self.angle
        return c


# --------------------------------------------------------------------------
# Proyek
# --------------------------------------------------------------------------
def default_layers():
    """Dua layer bawaan: Garis (potong/gores vektor) dan Gambar (raster)."""
    garis = Layer(index=0, name="Garis", color=LAYER_COLORS[0], mode="line", speed=1000, power=60)
    gambar = Layer(index=1, name="Gambar", color="#d0b000", mode="image", speed=3000, power=60, min_power=5.0)
    return [garis, gambar]


class Project:
    def __init__(self):
        self.device = Device()
        self.layers = default_layers()
        self.shapes = []
        self.path = None
        self.mirror_x = False       # cermin pekerjaan (horizontal / vertikal) saat membuat G-code
        self.mirror_y = False

    # -- layer ------------------------------------------------------------
    def layer(self, index):
        for L in self.layers:
            if L.index == index:
                return L
        return self.layers[0]

    def add_layer(self):
        if len(self.layers) >= MAX_LAYERS:
            return None
        used = {L.index for L in self.layers}
        idx = next(i for i in itertools.count() if i not in used)
        L = Layer(index=idx, name=f"Layer {idx + 1}", color=LAYER_COLORS[idx % len(LAYER_COLORS)])
        self.layers.append(L)
        return L

    def remove_layer(self, index):
        if len(self.layers) <= 1:
            return False
        rest = [L for L in self.layers if L.index != index]
        target = rest[0].index
        for s in self.shapes:
            if s.layer == index:
                s.layer = target
        self.layers = rest
        return True

    def layer_for_mode(self, mode):
        for L in self.layers:
            if L.mode == mode:
                return L
        return None

    def layer_for_color(self, hex_color):
        for L in self.layers:   # layer Gambar tidak dipakai untuk vektor
            if L.mode != "image" and L.color.lower() == hex_color.lower():
                return L
        return None

    # -- bentuk -----------------------------------------------------------
    def shape(self, sid):
        for s in self.shapes:
            if s.id == sid:
                return s
        return None

    def add(self, shape):
        self.shapes.append(shape)
        return shape

    def bbox(self, shapes=None):
        shapes = self.shapes if shapes is None else shapes
        if not shapes:
            return None
        bbs = [s.bbox() for s in shapes]
        return (min(b[0] for b in bbs), min(b[1] for b in bbs),
                max(b[2] for b in bbs), max(b[3] for b in bbs))

    # -- undo -------------------------------------------------------------
    def snapshot(self):
        return ([l.to_dict() for l in self.layers],
                [s.clone() for s in self.shapes],
                self.device.to_dict())

    def restore(self, snap):
        layers, shapes, dev = snap
        self.layers = [Layer.from_dict(copy.deepcopy(d)) for d in layers]
        self.shapes = [s.clone() for s in shapes]
        self.device = Device.from_dict(dict(dev))
