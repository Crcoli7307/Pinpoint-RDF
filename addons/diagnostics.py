"""
PINPOINT Software Project
addons/diagnostics.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Implements the Diagnostics add-on dialog with system, settings, and device checks.
Wires the menu action used to open the dialog.
---

https://nexus.crayton.dev/
"""
from __future__ import annotations

import os
import platform
import sys
import time
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from pinpoint.plugin_api import AddonAction, AddonPlugin, PinpointAPI


class DiagnosticsDialog(QtWidgets.QDialog):
    def __init__(self, api: PinpointAPI, parent=None):
        super().__init__(parent)
        self._api = api
        self.setWindowTitle("Diagnostics")
        self.setMinimumSize(720, 520)
        self.setModal(True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.summary_label = QtWidgets.QLabel("Telemetry: --")
        self.summary_label.setObjectName("diagSummary")
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)

        self.run_btn = QtWidgets.QPushButton("Run Diagnostics")
        self.copy_btn = QtWidgets.QPushButton("Copy Report")
        self.log_btn = QtWidgets.QPushButton("Open Log")
        self.close_btn = QtWidgets.QPushButton("Close")

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.copy_btn)
        btn_row.addWidget(self.log_btn)
        btn_row.addStretch(1)
        btn_row.addWidget(self.close_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addWidget(self.output, 1)
        layout.addLayout(btn_row)

        self._telemetry_count = 0
        self._telemetry_last_ts: Optional[float] = None
        self._telemetry_token = self._api.subscribe("telemetry", self._on_telemetry)

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(1000)
        self._refresh_timer.timeout.connect(self._refresh_summary)
        self._refresh_timer.start()

        self.run_btn.clicked.connect(self._run_checks)
        self.copy_btn.clicked.connect(self._copy_report)
        self.log_btn.clicked.connect(self._open_log)
        self.close_btn.clicked.connect(self.close)

        self._run_checks()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            if self._telemetry_token is not None:
                self._api.unsubscribe(self._telemetry_token)
        finally:
            self._refresh_timer.stop()
            super().closeEvent(event)

    def _on_telemetry(self, _payload: dict) -> None:
        self._telemetry_count += 1
        self._telemetry_last_ts = time.time()

    def _refresh_summary(self) -> None:
        if self._telemetry_last_ts:
            ago = max(0.0, time.time() - self._telemetry_last_ts)
            last = f"{ago:.1f}s ago"
        else:
            last = "--"
        self.summary_label.setText(f"Telemetry events: {self._telemetry_count} | Last: {last}")

    def _copy_report(self) -> None:
        QtWidgets.QApplication.clipboard().setText(self.output.toPlainText())
        self._api.call(
            "ui.show_message",
            {"title": "Diagnostics", "message": "Report copied to clipboard.", "level": "info"},
        )

    def _open_log(self) -> None:
        log_path = self._api.call("core.get_resource_path", {"parts": ["main.log"]}).get("path")
        if not log_path or not os.path.exists(log_path):
            self._api.call(
                "ui.show_message",
                {"title": "Log Not Found", "message": "main.log was not found.", "level": "warning"},
            )
            return
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(log_path))

    def _run_checks(self) -> None:
        lines = []
        title = self._api.call("core.get_title").get("title") or "Pinpoint"
        version = self._api.call("core.get_version").get("version") or "unknown"
        lines.append(f"{title} {version}")
        lines.append(f"Python: {platform.python_version()} ({sys.executable})")
        lines.append(f"Platform: {platform.platform()}")

        settings = self._api.call("core.get_settings").get("settings") or {}
        if settings:
            lines.append("Settings:")
            for key in sorted(settings.keys()):
                lines.append(f"  {key}: {settings.get(key)}")

        history_points = self._api.call("data.get_history_points").get("points") or []
        report_available = bool(self._api.call("data.report_available").get("available"))
        lines.append(f"History points: {len(history_points)}")
        lines.append(f"Report available: {report_available}")

        addons_dir = self._api.get_context("addons_dir") or "--"
        lines.append(f"Add-ons dir: {addons_dir}")
        if addons_dir and os.path.isdir(addons_dir):
            addons = [f for f in os.listdir(addons_dir) if not f.startswith(('.', '_'))]
            lines.append(f"Add-ons entries: {len(addons)}")

        try:
            import funcs

            devices = funcs.list_sdr_devices()
            lines.append(f"SDR devices: {len(devices)}")
            for dev in devices:
                name = dev.get("name") or "Unknown"
                serial = dev.get("serial") or "--"
                lines.append(f"  SDR {dev.get('index')}: {name} (serial={serial})")
        except Exception as exc:
            lines.append(f"SDR devices: error ({exc})")

        try:
            import funcs

            ports = funcs.list_serial_ports()
            lines.append(f"GPS/Serial ports: {len(ports)}")
            for p in ports:
                desc = p.get("description") or ""
                lines.append(f"  {p.get('device')}: {desc}")
        except Exception as exc:
            lines.append(f"GPS/Serial ports: error ({exc})")

        cal_path = self._api.call("core.get_resource_path", {"parts": ["calibration_profiles.json"]}).get("path")
        lines.append(f"Calibration file: {'found' if cal_path and os.path.exists(cal_path) else 'missing'}")

        log_path = self._api.call("core.get_resource_path", {"parts": ["main.log"]}).get("path")
        lines.append(f"Log file: {'found' if log_path and os.path.exists(log_path) else 'missing'}")

        lines.append("")
        lines.append(f"Telemetry events (this session): {self._telemetry_count}")

        self.output.setPlainText("\n".join(lines))


def _open_diagnostics(api: PinpointAPI) -> None:
    parent = api.call("ui.get_main_window").get("window")
    dlg = DiagnosticsDialog(api, parent=parent)
    dlg.exec()


def plugin_entry(api: PinpointAPI) -> AddonPlugin:
    return AddonPlugin(
        id="diagnostics",
        name="Diagnostics",
        version="1.0.0",
        description="Troubleshooting checks and system information.",
        menu=[
            AddonAction(
                id="diagnostics_open",
                label="Open Diagnostics...",
                handler=_open_diagnostics,
            ),
        ],
    )
