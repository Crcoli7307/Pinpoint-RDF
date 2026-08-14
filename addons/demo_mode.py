"""
PINPOINT Software Project
addons/demo_mode.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Provides a demo add-on that simulates GPS and SDR telemetry for the UI.
Includes a settings dialog to tune demo path, signal, and timing parameters.
---

https://crayton.dev/
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from PyQt6 import QtCore, QtGui, QtWidgets

from pinpoint.plugin_api import AddonAction, AddonPlugin, PinpointAPI

DEFAULT_CONFIG: Dict[str, Any] = {
    "center_lat": 37.7749,
    "center_lon": -122.4194,
    "radius_m": 250.0,
    "speed_mps": 6.0,
    "target_bearing_deg": 45.0,
    "target_distance_m": 180.0,
    "antenna_count": 4,
    "update_interval_s": 0.0,
    "satellite_count": 9,
    "signal_noise": 0.08,
    "scenario_period_s": 120.0,
    "fault_simulation": True,
    "antenna_beamwidth_deg": 70.0,
    "antenna_front_back_db": 18.0,
}


def _config_path(api: PinpointAPI) -> str:
    addons_dir = api.get_context("addons_dir") if api else None
    if addons_dir and os.path.isdir(addons_dir):
        return os.path.join(addons_dir, "demo_settings.json")
    return os.path.join(os.path.dirname(__file__), "demo_settings.json")


def load_config(api: PinpointAPI) -> Dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    path = _config_path(api)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key, value in data.items():
                if key in cfg:
                    cfg[key] = value
    except Exception:
        pass
    return cfg


def save_config(api: PinpointAPI, cfg: Dict[str, Any]) -> bool:
    path = _config_path(api)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception:
        return False


def _get_main_window(api: PinpointAPI):
    if api is None:
        return None
    win = api.get_context("main_window")
    if win:
        return win
    return api.call("ui.get_main_window").get("window")


class DemoSettingsDialog(QtWidgets.QDialog):
    def __init__(self, api: PinpointAPI, parent=None):
        super().__init__(parent)
        self._api = api
        self._config = load_config(api)
        self.setWindowTitle("Demo Settings")
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.center_lat = QtWidgets.QLineEdit(str(self._config.get("center_lat", "")))
        self.center_lon = QtWidgets.QLineEdit(str(self._config.get("center_lon", "")))
        self.radius_m = QtWidgets.QLineEdit(str(self._config.get("radius_m", "")))
        self.speed_mps = QtWidgets.QLineEdit(str(self._config.get("speed_mps", "")))
        self.target_bearing = QtWidgets.QLineEdit(str(self._config.get("target_bearing_deg", "")))
        self.target_distance = QtWidgets.QLineEdit(str(self._config.get("target_distance_m", "")))
        self.antenna_count = QtWidgets.QLineEdit(str(self._config.get("antenna_count", "")))
        self.update_interval = QtWidgets.QLineEdit(str(self._config.get("update_interval_s", "")))
        self.satellite_count = QtWidgets.QLineEdit(str(self._config.get("satellite_count", "")))
        self.signal_noise = QtWidgets.QLineEdit(str(self._config.get("signal_noise", "")))
        self.scenario_period = QtWidgets.QLineEdit(str(self._config.get("scenario_period_s", "")))
        self.beamwidth = QtWidgets.QLineEdit(str(self._config.get("antenna_beamwidth_deg", "")))
        self.front_back = QtWidgets.QLineEdit(str(self._config.get("antenna_front_back_db", "")))
        self.fault_simulation = QtWidgets.QCheckBox("Cycle through interference, GPS degradation, SDR dropout, stops, and multipath")
        self.fault_simulation.setChecked(bool(self._config.get("fault_simulation", True)))

        self.center_lat.setValidator(QtGui.QDoubleValidator(-90.0, 90.0, 6))
        self.center_lon.setValidator(QtGui.QDoubleValidator(-180.0, 180.0, 6))
        self.radius_m.setValidator(QtGui.QDoubleValidator(1.0, 10_000.0, 2))
        self.speed_mps.setValidator(QtGui.QDoubleValidator(0.1, 100.0, 2))
        self.target_bearing.setValidator(QtGui.QDoubleValidator(0.0, 360.0, 2))
        self.target_distance.setValidator(QtGui.QDoubleValidator(1.0, 10_000.0, 2))
        self.antenna_count.setValidator(QtGui.QIntValidator(1, 16))
        self.update_interval.setValidator(QtGui.QDoubleValidator(0.0, 60.0, 2))
        self.satellite_count.setValidator(QtGui.QIntValidator(4, 32))
        self.signal_noise.setValidator(QtGui.QDoubleValidator(0.0, 0.5, 3))
        self.scenario_period.setValidator(QtGui.QDoubleValidator(30.0, 3600.0, 1))
        self.beamwidth.setValidator(QtGui.QDoubleValidator(10.0, 180.0, 1))
        self.front_back.setValidator(QtGui.QDoubleValidator(1.0, 60.0, 1))

        form = QtWidgets.QFormLayout()
        form.addRow("Center Latitude", self.center_lat)
        form.addRow("Center Longitude", self.center_lon)
        form.addRow("Patrol Extent (m)", self.radius_m)
        form.addRow("Speed (m/s)", self.speed_mps)
        form.addRow("Target Bearing (deg)", self.target_bearing)
        form.addRow("Target Distance (m)", self.target_distance)
        form.addRow("Antenna Count", self.antenna_count)
        form.addRow("Update Interval (s, 0=auto)", self.update_interval)
        form.addRow("Satellite Count", self.satellite_count)
        form.addRow("Signal Noise (0-0.5)", self.signal_noise)
        form.addRow("Scenario Cycle (s)", self.scenario_period)
        form.addRow("Directional Beamwidth (deg)", self.beamwidth)
        form.addRow("Front/Back Ratio (dB)", self.front_back)
        form.addRow("Field Conditions", self.fault_simulation)

        note = QtWidgets.QLabel(
            "Demo Mode generates complex IQ samples and feeds them through Pinpoint's normal DSP, "
            "direction finding, confidence, map, health, and alert paths. The route is an irregular patrol—not a scripted circle."
        )
        note.setWordWrap(True)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(note)
        layout.addLayout(form)
        layout.addWidget(btns)

    def _save(self) -> None:
        try:
            cfg = dict(self._config)
            cfg["center_lat"] = float(self.center_lat.text())
            cfg["center_lon"] = float(self.center_lon.text())
            cfg["radius_m"] = float(self.radius_m.text())
            cfg["speed_mps"] = float(self.speed_mps.text())
            cfg["target_bearing_deg"] = float(self.target_bearing.text())
            cfg["target_distance_m"] = float(self.target_distance.text())
            cfg["antenna_count"] = int(self.antenna_count.text())
            cfg["update_interval_s"] = float(self.update_interval.text())
            cfg["satellite_count"] = int(self.satellite_count.text())
            cfg["signal_noise"] = float(self.signal_noise.text())
            cfg["scenario_period_s"] = float(self.scenario_period.text())
            cfg["antenna_beamwidth_deg"] = float(self.beamwidth.text())
            cfg["antenna_front_back_db"] = float(self.front_back.text())
            cfg["fault_simulation"] = self.fault_simulation.isChecked()
        except Exception:
            QtWidgets.QMessageBox.critical(self, "Invalid Input", "Please enter valid numeric values.")
            return
        if not save_config(self._api, cfg):
            QtWidgets.QMessageBox.warning(self, "Save Failed", "Failed to save demo settings.")
            return
        self.accept()


def _start_demo(api: PinpointAPI) -> None:
    win = _get_main_window(api)
    if win is None:
        return
    if getattr(win, "demo_active", False):
        win.stop_demo()
        return
    win.start_demo(load_config(api))


def _open_demo_settings(api: PinpointAPI) -> None:
    win = _get_main_window(api)
    dlg = DemoSettingsDialog(api, parent=win)
    dlg.exec()


def plugin_entry(api: PinpointAPI) -> AddonPlugin:
    return AddonPlugin(
        id="demo_mode",
        name="Demo Mode",
        version="2.0.0",
        description="Scenario-driven GPS/RF demo with generated IQ, interference, faults, mapping, and alerts.",
        menu=[
            AddonAction(
                id="demo_start",
                label="Start Demo",
                handler=_start_demo,
            ),
            AddonAction(
                id="demo_settings",
                label="Demo Settings",
                handler=_open_demo_settings,
            ),
        ],
    )
