# SPDX-License-Identifier: MIT
"""Mesin simulasi jalur laser (tanpa Qt): segmen -> gambar bertahap menurut waktu/progres.

Segmen: array Nx6 (x0, y0, x1, y1, S, rapid) di ruang desain (lihat ui.preview.ops_to_segments).
Waktu simulasi dihitung proporsional panjang (gerak cepat dianggap RAPID_FACTOR kali lebih cepat) lalu
diskalakan ke estimasi waktu job, sehingga "×1" mendekati kecepatan mesin sebenarnya."""
from __future__ import annotations

import numpy as np

RAPID_FACTOR = 8.0
BURN_RGB = (230, 60, 20)
RAPID_RGB = (120, 120, 160)
DOSE_REF = 8.0


class Simulator:
    def __init__(self, segs, bed_w, bed_h, s_max=1000, time_s=0.0, px_per_mm=None, max_px=1400):
        self.segs = np.asarray(segs, dtype=float).reshape(-1, 6)
        self.bed_w, self.bed_h, self.s_max = float(bed_w), float(bed_h), max(float(s_max), 1.0)
        self.scale = px_per_mm or min(6.0, max_px / max(self.bed_w, self.bed_h, 1.0))
        self.W = max(2, int(round(self.bed_w * self.scale)))
        self.H = max(2, int(round(self.bed_h * self.scale)))
        s = self.segs
        self.length = np.hypot(s[:, 2] - s[:, 0], s[:, 3] - s[:, 1]) if len(s) else np.zeros(0)
        w = self.length / np.where(s[:, 5] > 0, RAPID_FACTOR, 1.0) if len(s) else np.zeros(0)
        self.cum = np.cumsum(w)
        wt = float(self.cum[-1]) if len(self.cum) else 0.0
        self.total_w = wt
        self.time_s = float(time_s) if time_s and time_s > 0 else wt / 16.0        # cadangan: ~1000 mm/mnt
        self.energy = np.zeros((self.H, self.W), np.float32)
        self.rapid = np.zeros((self.H, self.W), np.uint8)
        self.n = 0                                           # jumlah segmen yang sudah digambar

    # -- posisi/waktu
    def reset(self):
        self.energy[:] = 0
        self.rapid[:] = 0
        self.n = 0

    def index_at(self, t):
        """Jumlah segmen yang selesai pada waktu simulasi t (detik)."""
        if not len(self.cum) or self.time_s <= 0:
            return 0
        f = min(max(t / self.time_s, 0.0), 1.0)
        return int(np.searchsorted(self.cum, f * self.total_w, side="right"))

    def time_at(self, n):
        if not len(self.cum) or n <= 0:
            return 0.0
        return self.time_s * float(self.cum[min(n, len(self.cum)) - 1]) / self.total_w

    def head(self):
        """Posisi kepala laser (mm) setelah n segmen."""
        if not len(self.segs):
            return (0.0, 0.0)
        if self.n <= 0:
            return (float(self.segs[0, 0]), float(self.segs[0, 1]))
        s = self.segs[min(self.n, len(self.segs)) - 1]
        return (float(s[2]), float(s[3]))

    def burning(self):
        return self.n > 0 and self.segs[min(self.n, len(self.segs)) - 1, 5] == 0 and \
            self.segs[min(self.n, len(self.segs)) - 1, 4] > 0

    # -- penggambaran
    def seek(self, n):
        n = max(0, min(int(n), len(self.segs)))
        if n < self.n:
            self.reset()
        self._stamp(self.n, n)
        self.n = n

    def _stamp(self, a, b):
        step_px = 0.6
        i = a
        while i < b:
            j = min(b, i + 100000)
            ch = self.segs[i:j]
            L_px = self.length[i:j] * self.scale
            cnt = np.maximum(1, np.ceil(L_px / step_px)).astype(np.int64)
            cum = np.cumsum(cnt)
            take = max(1, min(len(cnt), int(np.searchsorted(cum, 2_000_000, side="right"))))
            ch, cnt, L_mm = ch[:take], cnt[:take], self.length[i:i + take]
            tot = int(cnt.sum())
            idx = np.repeat(np.arange(take), cnt)
            start = np.cumsum(cnt) - cnt
            t = (np.arange(tot) - start[idx] + 0.5) / cnt[idx]
            px = (ch[idx, 0] + t * (ch[idx, 2] - ch[idx, 0])) * self.scale
            py = (ch[idx, 1] + t * (ch[idx, 3] - ch[idx, 1])) * self.scale
            ix = np.clip(px.astype(np.int64), 0, self.W - 1)
            iy = np.clip(py.astype(np.int64), 0, self.H - 1)
            lin = iy * self.W + ix
            rap = ch[idx, 5] > 0
            if rap.any():
                self.rapid.reshape(-1)[lin[rap]] = 1
            bm = ~rap
            if bm.any():
                wgt = np.clip(ch[idx, 4] / self.s_max, 0, 1) * (L_mm[idx] / cnt[idx])
                self.energy += np.bincount(lin[bm], weights=wgt[bm], minlength=self.W * self.H) \
                    .reshape(self.H, self.W).astype(np.float32)
            i += take

    def seek_time(self, t):
        self.seek(self.index_at(t))

    def image(self):
        """RGBA uint8 (H, W, 4): bakar (merah, transparansi ~ dosis) di atas jejak gerak cepat."""
        out = np.zeros((self.H, self.W, 4), np.uint8)
        r = self.rapid > 0
        out[r] = (*RAPID_RGB, 110)
        a = np.clip(self.energy * self.scale ** 2 / DOSE_REF, 0, 1)
        m = a > 0.02
        out[m, 0], out[m, 1], out[m, 2] = BURN_RGB
        out[m, 3] = np.maximum(out[m, 3], (60 + 195 * a[m]).astype(np.uint8))
        return out
