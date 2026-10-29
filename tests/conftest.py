# SPDX-License-Identifier: MIT
"""Satu QApplication (widget) untuk semua tes, dibuat sebelum modul tes diimpor."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp(prefix="grblaser-xdg-")     # simpan otomatis tidak menyentuh HOME

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])
