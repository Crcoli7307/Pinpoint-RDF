"""
PINPOINT Software Project
pinpoint/app.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Bootstraps the Qt application and applies high-DPI settings.
Presents the startup dialog and creates the main window based on user choices.
---

https://nexus.crayton.dev/
"""
import multiprocessing
import os
import sys

from PyQt6 import QtCore, QtWidgets

from .core import APP_TITLE, _get_app_icon, _show_startup_splash
from .ui_components import GPSStartupDialog
from .main_window import MainWindow

# ---------------------------
# App bootstrap
# ---------------------------
def main():
    try:
        multiprocessing.freeze_support()
    except Exception:
        pass
    # High-DPI awareness (set before creating the app)
    try:
        if hasattr(QtCore.Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
            QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
        if hasattr(QtCore.Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
            QtCore.QCoreApplication.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except Exception:
        pass
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setWindowIcon(_get_app_icon())

    _show_startup_splash(app)

    startup = GPSStartupDialog()
    gps_port = None
    if startup.exec() == QtWidgets.QDialog.DialogCode.Accepted:
        playback_only = startup.playback_only()
        meshtastic_only = startup.meshtastic_only()
        if meshtastic_only:
            playback_only = False
        gps_port = startup.selected_port()
        if gps_port:
            os.environ["GPS_PORT"] = gps_port

        win = MainWindow(
            gps_port=gps_port,
            playback_only=playback_only,
            meshtastic_only=meshtastic_only,
        )
        if playback_only:
            ok = win.open_recording(required=True)
            if not ok:
                sys.exit(0)
        win.show()
        sys.exit(app.exec())
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()
