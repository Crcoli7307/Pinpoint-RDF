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


class WaterfallWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(760, 420)
        self._rows = []
        self._scroll_offset = 0.0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(33)
        self._timer.timeout.connect(self._animate)
        self._timer.start()

    def add_spectrum(self, values):
        if not values:
            return
        self._rows.append([float(v) for v in values])
        self._rows = self._rows[-240:]
        self._scroll_offset = 0.0
        self.update()

    def _animate(self):
        if self._rows:
            row_height = max(2.0, self.height() / 120.0)
            self._scroll_offset += 0.22
            if self._scroll_offset >= row_height:
                self._rows.append(list(self._rows[-1]))
                self._rows = self._rows[-240:]
                self._scroll_offset = 0.0
            self.update()

    @staticmethod
    def _color(value, minimum, maximum):
        span = max(1e-6, maximum - minimum)
        t = max(0.0, min(1.0, (value - minimum) / span))
        if t < 0.33:
            frac = t / 0.33
            return QtGui.QColor(0, int(120 * frac), int(80 + 175 * frac))
        if t < 0.66:
            frac = (t - 0.33) / 0.33
            return QtGui.QColor(int(255 * frac), int(120 + 135 * frac), int(255 * (1.0 - frac)))
        frac = (t - 0.66) / 0.34
        return QtGui.QColor(255, int(255 * (1.0 - frac)), 0)

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#020617"))
        if not self._rows:
            painter.setPen(QtGui.QColor("#94a3b8"))
            painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter, "Waiting for live SDR samples...")
            return
        values = [value for row in self._rows for value in row]
        minimum, maximum = min(values), max(values)
        row_height = max(2.0, self.height() / 120.0)
        y = self.height() - row_height + self._scroll_offset
        for row in reversed(self._rows):
            if y < 0:
                break
            cell_width = self.width() / max(1, len(row))
            for column, value in enumerate(row):
                painter.fillRect(
                    QtCore.QRectF(column * cell_width, y, cell_width + 1.0, row_height + 1.0),
                    self._color(value, minimum, maximum),
                )
            y -= row_height


class LiveWaterfallDialog(QtWidgets.QDialog):
    def __init__(self, api: PinpointAPI, parent=None):
        super().__init__(parent)
        self._api = api
        self._latest = {}
        self.setWindowTitle("Live SDR Waterfall")
        self.setMinimumSize(860, 580)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.device_combo = QtWidgets.QComboBox()
        self.metrics = QtWidgets.QLabel("Waiting for telemetry...")
        self.canvas = WaterfallWidget()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("SDR"))
        top.addWidget(self.device_combo)
        top.addWidget(self.metrics, 1)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(close_btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        self.device_combo.currentIndexChanged.connect(self._render_latest)
        self._token = api.subscribe("telemetry", self._on_telemetry)
        initial = api.call("data.get_latest_telemetry").get("telemetry") or {}
        if initial:
            self._on_telemetry(initial)

    def _on_telemetry(self, payload):
        self._latest = dict(payload or {})
        states = self._latest.get("antenna_states") or []
        current = self.device_combo.currentData()
        labels = []
        for position, state in enumerate(states):
            idx = state.get("index", position)
            labels.append((idx, f"SDR {idx} — {state.get('serial') or state.get('name') or 'Unknown'}"))
        if labels != [(self.device_combo.itemData(i), self.device_combo.itemText(i)) for i in range(self.device_combo.count())]:
            self.device_combo.blockSignals(True)
            self.device_combo.clear()
            for idx, label in labels:
                self.device_combo.addItem(label, idx)
            match = self.device_combo.findData(current)
            self.device_combo.setCurrentIndex(max(0, match))
            self.device_combo.blockSignals(False)
        self._render_latest()

    def _render_latest(self):
        states = self._latest.get("antenna_states") or []
        selected = self.device_combo.currentData()
        state = next((item for item in states if item.get("index") == selected), None)
        if not state:
            return
        spectrum = state.get("spectrum_db") or []
        self.canvas.add_spectrum(spectrum)
        strength = state.get("strength")
        power = state.get("power_dbfs")
        snr = state.get("snr")
        self.metrics.setText(
            f"Strength {strength if strength is not None else '--'}  |  "
            f"Power {power:.1f} dBFS  |  SNR {snr:.1f} dB"
            if power is not None and snr is not None
            else "Waiting for spectrum metrics..."
        )

    def closeEvent(self, event):
        self._api.unsubscribe(self._token)
        self.canvas._timer.stop()
        super().closeEvent(event)


def _open_diagnostics(api: PinpointAPI) -> None:
    parent = api.call("ui.get_main_window").get("window")
    dlg = DiagnosticsDialog(api, parent=parent)
    dlg.exec()


def _open_waterfall(api: PinpointAPI) -> None:
    parent = api.call("ui.get_main_window").get("window")
    dlg = LiveWaterfallDialog(api, parent=parent)
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
            AddonAction(
                id="diagnostics_waterfall",
                label="Live SDR Waterfall...",
                handler=_open_waterfall,
            ),
        ],
    )
