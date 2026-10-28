# SPDX-License-Identifier: MIT
"""Satu QApplication (widget) untuk semua tes, dibuat sebelum modul tes diimpor."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])
