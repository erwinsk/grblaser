# SPDX-License-Identifier: MIT
"""Jalankan: python3 -m grblaser [file ...]"""
import sys


def main():
    from PyQt5.QtWidgets import QApplication
    from .ui.main_window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName("Grblaser")
    app.setOrganizationName("Kalingin")
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    if len(sys.argv) > 1:
        w.import_files(sys.argv[1:])
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
