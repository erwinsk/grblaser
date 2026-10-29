# SPDX-License-Identifier: MIT
"""Pemeriksaan sebelum pekerjaan dijalankan: cari masalah yang membuat job gagal atau berbahaya.

`check()` murni (tanpa Qt) sehingga mudah diuji. Hasilnya daftar `Issue`:
 - level "error": pekerjaan tidak boleh dimulai;
 - level "warn" : boleh dilanjutkan setelah pengguna menyetujui.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import ImageShape

EPS = 0.01          # toleransi mm saat membandingkan batas area kerja


@dataclass
class Issue:
    level: str      # "error" | "warn"
    text: str

    def __str__(self):
        return ("✖ " if self.level == "error" else "⚠ ") + self.text


def _num(settings, key):
    try:
        return float(settings[key])
    except (KeyError, TypeError, ValueError):
        return None


def design_bounds(project, only_ids=None):
    """Kotak pembatas (ruang desain) semua objek pada layer yang ikut dikerjakan."""
    bbs = []
    for s in project.shapes:
        if only_ids is not None and s.id not in only_ids:
            continue
        L = project.layer(s.layer)
        if L.output:
            bbs.append(s.bbox())
    if not bbs:
        return None
    return (min(b[0] for b in bbs), min(b[1] for b in bbs), max(b[2] for b in bbs), max(b[3] for b in bbs))


def check(project, dev, grbl=None, only_ids=None):
    """`grbl` = kamus pengaturan dari mesin (`sender.settings`, mis. {"$30": "1000"}); boleh kosong."""
    out = []
    used = {}
    for s in project.shapes:
        if only_ids is not None and s.id not in only_ids:
            continue
        used.setdefault(s.layer, []).append(s)

    active = [(project.layer(i), shs) for i, shs in used.items() if project.layer(i).output]
    if not active:
        out.append(Issue("error", "Tidak ada objek pada layer yang ikut dikerjakan (kolom Output). Tidak ada yang akan dibakar."))
        return out

    for L, shs in sorted(active, key=lambda t: t[0].index):
        nm = f"Layer \"{L.name or L.index + 1}\""
        if L.speed <= 0:
            out.append(Issue("error", f"{nm}: kecepatan {L.speed:g} mm/mnt tidak valid."))
        if L.power > 100.0 + 1e-6 or L.power < 0 or L.min_power < 0 or L.min_power > 100.0 + 1e-6:
            out.append(Issue("error", f"{nm}: daya di luar 0–100% (maks {L.power:g}%, min {L.min_power:g}%)."))
        elif L.min_power > L.power + 1e-6:
            out.append(Issue("warn", f"{nm}: daya minimum ({L.min_power:g}%) lebih besar dari maksimum ({L.power:g}%)."))
        if L.power <= 0 and not any(isinstance(s, ImageShape) for s in shs):
            out.append(Issue("warn", f"{nm}: daya 0% — laser tidak akan menyala (hanya bergerak)."))
        if L.mode == "fill" and 0 < L.interval < 0.02:
            out.append(Issue("warn", f"{nm}: jarak garis isian {L.interval:g} mm sangat rapat; G-code bisa sangat besar dan lambat."))

    if dev.start_from == "absolute":
        bb = design_bounds(project, only_ids)
        if bb is not None and (bb[0] < -EPS or bb[1] < -EPS or bb[2] > dev.bed_w + EPS or bb[3] > dev.bed_h + EPS):
            out.append(Issue("warn",
                             f"Sebagian pekerjaan berada di luar area kerja {dev.bed_w:g}×{dev.bed_h:g} mm "
                             f"(objek: {bb[0]:.1f},{bb[1]:.1f} → {bb[2]:.1f},{bb[3]:.1f} mm di ruang desain). "
                             "GRBL bisa ALARM (soft limit) atau menabrak ujung rel."))

    if grbl:
        s30 = _num(grbl, "$30")
        if s30 is not None and int(s30) != int(dev.s_max):
            out.append(Issue("warn", f"S maksimum di aplikasi ({dev.s_max}) berbeda dari $30 di mesin ({int(s30)}): "
                                     "daya sebenarnya akan meleset. Samakan di Pengaturan perangkat."))
        for key, mine, label in (("$130", dev.bed_w, "lebar"), ("$131", dev.bed_h, "tinggi")):
            v = _num(grbl, key)
            if v is not None and abs(v - mine) > 0.5:
                out.append(Issue("warn", f"Area kerja {label} di aplikasi ({mine:g} mm) berbeda dari {key} di mesin ({v:g} mm)."))
        s32 = _num(grbl, "$32")
        if s32 is not None and int(s32) != 1 and dev.laser_cmd == "M4":
            out.append(Issue("warn", "Mode laser ($32) di mesin MATI: M4 tidak akan memodulasi daya. Set $32=1."))
    return out


def has_errors(issues):
    return any(i.level == "error" for i in issues)


def format_issues(issues):
    return "\n".join(f"{i}" for i in issues)
