# SPDX-License-Identifier: MIT
"""Menjalankan pekerjaan berat (bangun toolpath, G-code, pratinjau, trace) di thread latar
supaya antarmuka tidak membeku; dialog progres bisa dibatalkan."""
from __future__ import annotations

import traceback

from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import QApplication, QProgressDialog


class _Runner(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception:  # noqa: BLE001
            self.failed.emit(traceback.format_exc())


class TaskRunner:
    """Satu tugas aktif pada satu waktu. `sync=True` menjalankan langsung (untuk tes)."""

    def __init__(self, window):
        self.win = window
        self.sync = False
        self._active = None
        self._dlg = None

    @property
    def busy(self):
        return self._active is not None

    def run(self, fn, title, on_done, on_error=None):
        if self.busy:
            return False
        if self.sync:
            try:
                res = fn()
            except Exception:  # noqa: BLE001
                tb = traceback.format_exc()
                (on_error or self._default_error)(tb)
                return True
            on_done(res)
            return True
        r = _Runner(fn, self.win)
        dlg = QProgressDialog(title, "Batal", 0, 0, self.win)
        dlg.setWindowTitle("Grblaser")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(350)          # tugas singkat tidak memunculkan dialog
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        self._active, self._dlg = r, dlg
        cancelled = {"v": False}
        dlg.canceled.connect(lambda: cancelled.__setitem__("v", True))

        def finish():
            self._active = None
            dlg.close()
            dlg.deleteLater()
            self._dlg = None
            r.deleteLater()

        def ok(res):
            was_cancelled = cancelled["v"]      # baca sebelum close(): close() ikut memancarkan canceled()
            dlg.canceled.disconnect()
            finish()
            if not was_cancelled:
                on_done(res)

        def bad(tb):
            dlg.canceled.disconnect()
            finish()
            (on_error or self._default_error)(tb)

        r.done.connect(ok)
        r.failed.connect(bad)
        r.start()
        return True

    def shutdown(self, timeout_ms=5000):
        """Dipanggil saat jendela ditutup: tunggu thread latar selesai agar tidak dihancurkan saat berjalan."""
        r = self._active
        if r is not None:
            r.wait(timeout_ms)

    def _default_error(self, tb):
        self.win.console.add("err", "Tugas gagal:\n" + tb)
        self.win.note("Terjadi kesalahan; lihat konsol.", 8000)
