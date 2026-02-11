"""
PINPOINT Software Project
addons/history_exporter.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Implements the History Exporter add-on for saving collected points.
Provides CSV and GeoJSON export flows with a small dialog.
---

https://nexus.crayton.dev/
"""

from __future__ import annotations

import csv
import json
from typing import Callable, List, Dict

from PyQt6 import QtCore, QtWidgets

from pinpoint.plugin_api import AddonAction, AddonPlugin, PinpointAPI

class HistoryExportDialog(QtWidgets.QDialog):
    def __init__(self, parent, data_provider: Callable[[], List[Dict]]):
        super().__init__(parent)
        self._data_provider = data_provider
        self.setWindowTitle("Export History")
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.summary_label = QtWidgets.QLabel("History points: --")
        self.format_combo = QtWidgets.QComboBox()
        self.format_combo.addItems(["CSV", "GeoJSON"])

        form = QtWidgets.QFormLayout()
        form.addRow("Format", self.format_combo)

        self.export_btn = QtWidgets.QPushButton("Export")
        self.close_btn = QtWidgets.QPushButton("Close")
        self.export_btn.clicked.connect(self._export)
        self.close_btn.clicked.connect(self.close)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.export_btn)
        btn_row.addWidget(self.close_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        layout.addLayout(form)
        layout.addLayout(btn_row)

        self._refresh_summary()

    def _refresh_summary(self) -> None:
        count = len(self._data_provider() or [])
        self.summary_label.setText(f"History points: {count}")

    def _export(self) -> None:
        points = self._data_provider() or []
        if not points:
            QtWidgets.QMessageBox.information(self, "No Data", "There is no history data to export.")
            return

        fmt = self.format_combo.currentText().strip().lower()
        if fmt == "geojson":
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Export History",
                "history.geojson",
                "GeoJSON (*.geojson)",
            )
            if not path:
                return
            if not path.lower().endswith(".geojson"):
                path += ".geojson"
            self._export_geojson(points, path)
        else:
            path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self,
                "Export History",
                "history.csv",
                "CSV (*.csv)",
            )
            if not path:
                return
            if not path.lower().endswith(".csv"):
                path += ".csv"
            self._export_csv(points, path)

        QtWidgets.QMessageBox.information(self, "Export Complete", f"History exported to:\n{path}")

    @staticmethod
    def _export_csv(points: List[Dict], path: str) -> None:
        fields = [
            "t",
            "lat",
            "lon",
            "strength",
            "snr",
            "quality",
            "gps_fix",
            "sats",
            "bearing_source",
        ]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for p in points:
                row = {k: p.get(k) for k in fields}
                writer.writerow(row)

    @staticmethod
    def _export_geojson(points: List[Dict], path: str) -> None:
        features = []
        for p in points:
            lat = p.get("lat")
            lon = p.get("lon")
            if lat is None or lon is None:
                continue
            props = dict(p)
            props.pop("lat", None)
            props.pop("lon", None)
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": props,
                }
            )
        data = {"type": "FeatureCollection", "features": features}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def _open_history_export(api: PinpointAPI) -> None:
    parent = api.call("ui.get_main_window").get("window")
    dlg = HistoryExportDialog(parent, lambda: api.call("data.get_history_points").get("points") or [])
    dlg.exec()


def _history_available(api: PinpointAPI) -> bool:
    points = api.call("data.get_history_points").get("points") or []
    return bool(points)


def plugin_entry(api: PinpointAPI) -> AddonPlugin:
    return AddonPlugin(
        id="history_exporter",
        name="History Exporter",
        version="1.0.0",
        description="Export history points to CSV or GeoJSON.",
        menu=[
            AddonAction(
                id="export_history",
                label="Export History...",
                handler=_open_history_export,
                enabled=_history_available,
            )
        ],
    )
