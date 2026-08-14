"""
PINPOINT Software Project
pinpoint/ui_components.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Defines reusable dialogs, widgets, and worker threads used by the UI.
Centralizes shared UI helpers such as settings, startup, and info panels.
---

https://crayton.dev/
"""
from .core import *  # noqa: F401,F403

# ---------------------------
# Dialogs
# ---------------------------
class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("Update Settings")
        self.setMinimumWidth(420)
        self.setModal(True)

        # Inputs
        self.freq_input = QtWidgets.QLineEdit()
        self.gain_input = QtWidgets.QLineEdit()
        self.time_input = QtWidgets.QLineEdit()
        self.sample_window_input = QtWidgets.QLineEdit()
        self.antenna_input = QtWidgets.QLineEdit()
        self.spacing_input = QtWidgets.QLineEdit()
        self.spacing_mode = QtWidgets.QComboBox()
        self.refresh_input = QtWidgets.QLineEdit()
        self.movement_threshold_input = QtWidgets.QLineEdit()
        self.movement_accuracy_factor_input = QtWidgets.QLineEdit()
        self.gps_accuracy_floor_input = QtWidgets.QLineEdit()
        self.adaptive_movement_checkbox = QtWidgets.QCheckBox("Use GPS accuracy to increase movement threshold")
        self.orientation_input = QtWidgets.QLineEdit()
        self.directional_array_checkbox = QtWidgets.QCheckBox("Enable characterized directional amplitude array")
        self.beamwidth_input = QtWidgets.QLineEdit()
        self.front_back_input = QtWidgets.QLineEdit()
        self.heading_min_speed_input = QtWidgets.QLineEdit()
        self.heading_baseline_input = QtWidgets.QLineEdit()
        self.heading_stale_input = QtWidgets.QLineEdit()
        self.alert_debounce_input = QtWidgets.QLineEdit()
        self.profile_input = QtWidgets.QLineEdit()
        self.aoa_weight_input = QtWidgets.QLineEdit()
        self.map_weight_input = QtWidgets.QLineEdit()
        self.conf_threshold_input = QtWidgets.QLineEdit()
        self.mapbox_input = QtWidgets.QLineEdit()
        self.auto_tune_checkbox = QtWidgets.QCheckBox("Auto-tune fusion weights and threshold")

        # Validators
        self.freq_input.setValidator(QtGui.QDoubleValidator(bottom=0.0))
        self.gain_input.setValidator(QtGui.QIntValidator(0, 1000))
        self.time_input.setValidator(QtGui.QIntValidator(1, 3600))
        self.sample_window_input.setValidator(QtGui.QDoubleValidator(0.01, 2.0, 3))
        self.antenna_input.setValidator(QtGui.QIntValidator(1, 16))
        self.spacing_input.setValidator(QtGui.QDoubleValidator(0.0, 1000.0, 2))
        self.refresh_input.setValidator(QtGui.QIntValidator(1, 60))
        self.movement_threshold_input.setValidator(QtGui.QDoubleValidator(0.0, 100000.0, 2))
        self.movement_accuracy_factor_input.setValidator(QtGui.QDoubleValidator(0.0, 10.0, 2))
        self.gps_accuracy_floor_input.setValidator(QtGui.QDoubleValidator(0.0, 1000.0, 2))
        self.alert_debounce_input.setValidator(QtGui.QIntValidator(1, 20))
        self.aoa_weight_input.setValidator(QtGui.QDoubleValidator(0.0, 1.0, 2))
        self.map_weight_input.setValidator(QtGui.QDoubleValidator(0.0, 1.0, 2))
        self.conf_threshold_input.setValidator(QtGui.QDoubleValidator(0.0, 1.0, 2))
        self.beamwidth_input.setValidator(QtGui.QDoubleValidator(10.0, 180.0, 1))
        self.front_back_input.setValidator(QtGui.QDoubleValidator(1.0, 60.0, 1))
        self.heading_min_speed_input.setValidator(QtGui.QDoubleValidator(0.0, 100.0, 1))
        self.heading_baseline_input.setValidator(QtGui.QDoubleValidator(1.0, 1000.0, 1))
        self.heading_stale_input.setValidator(QtGui.QDoubleValidator(0.5, 120.0, 1))

        with settings_lock:
            self.freq_input.setText(str(settings.frequency))
            self.gain_input.setText(str(settings.gain))
            self.time_input.setText(str(settings.collection_time))
            self.sample_window_input.setText(str(settings.sample_window_s))
            self.antenna_input.setText(str(settings.antenna_count))
            self.spacing_input.setText("" if not settings.antenna_spacing_in else str(settings.antenna_spacing_in))
            self.refresh_input.setText(str(settings.info_refresh_s))
            self.movement_threshold_input.setText(str(settings.movement_threshold_m))
            self.movement_accuracy_factor_input.setText(str(settings.movement_accuracy_factor))
            self.gps_accuracy_floor_input.setText(str(settings.gps_accuracy_floor_m))
            self.adaptive_movement_checkbox.setChecked(bool(settings.adaptive_movement_pause))
            self.orientation_input.setText(", ".join(str(v) for v in settings.antenna_orientations_deg))
            self.directional_array_checkbox.setChecked(bool(settings.directional_array_enabled))
            self.beamwidth_input.setText(str(settings.antenna_beamwidth_deg))
            self.front_back_input.setText(str(settings.antenna_front_back_db))
            self.heading_min_speed_input.setText(str(settings.heading_min_speed_knots))
            self.heading_baseline_input.setText(str(settings.heading_min_baseline_m))
            self.heading_stale_input.setText(str(settings.heading_stale_s))
            self.alert_debounce_input.setText(str(settings.alert_debounce_cycles))
            self.profile_input.setText(str(settings.calibration_profile))
            self.aoa_weight_input.setText(str(settings.fusion_aoa_weight))
            self.map_weight_input.setText(str(settings.fusion_map_weight))
            self.conf_threshold_input.setText(str(settings.confidence_threshold))
            self.auto_tune_checkbox.setChecked(bool(settings.auto_tune_fusion))
        if MAPBOX_TOKEN:
            self.mapbox_input.setText(MAPBOX_TOKEN)
            self.mapbox_input.setEnabled(False)
            self.mapbox_input.setToolTip("Loaded from MAPBOX_TOKEN environment variable.")
        else:
            self.mapbox_input.setText(get_mapbox_token_override())

        form = QtWidgets.QFormLayout()
        form.addRow("Frequency (MHz)", self.freq_input)
        form.addRow("Gain", self.gain_input)
        form.addRow("Collection Cycle (s)", self.time_input)
        form.addRow("SDR Sample Window (s, max 2)", self.sample_window_input)
        form.addRow("Antenna Count", self.antenna_input)
        self.spacing_mode.addItems(
            [
                "Auto (0.5λ)",
                "0.25λ",
                "0.5λ",
                "0.75λ",
                "1.0λ",
                "Custom (in)",
            ]
        )
        form.addRow("Antenna Spacing Mode", self.spacing_mode)
        form.addRow("Antenna Spacing (in)", self.spacing_input)
        form.addRow("Info Refresh (s)", self.refresh_input)
        form.addRow("Map Movement Threshold (m, 0=off)", self.movement_threshold_input)
        form.addRow("Adaptive Movement", self.adaptive_movement_checkbox)
        form.addRow("GPS Accuracy Multiplier", self.movement_accuracy_factor_input)
        form.addRow("GPS Accuracy Floor (m)", self.gps_accuracy_floor_input)
        form.addRow("Antenna Orientations (deg CSV)", self.orientation_input)
        form.addRow("Directional Array", self.directional_array_checkbox)
        form.addRow("Antenna 3 dB Beamwidth (deg)", self.beamwidth_input)
        form.addRow("Antenna Front/Back Ratio (dB)", self.front_back_input)
        form.addRow("Heading Minimum Speed (kn)", self.heading_min_speed_input)
        form.addRow("Heading GPS Baseline (m)", self.heading_baseline_input)
        form.addRow("Heading Expiry (s)", self.heading_stale_input)
        form.addRow("Alert Debounce (cycles)", self.alert_debounce_input)
        form.addRow("Calibration Profile", self.profile_input)
        form.addRow("Fusion Weight (Amplitude)", self.aoa_weight_input)
        form.addRow("Fusion Weight (Map)", self.map_weight_input)
        form.addRow("Mapbox API Token", self.mapbox_input)
        form.addRow("Confidence Threshold", self.conf_threshold_input)
        form.addRow("Auto-Tune Fusion", self.auto_tune_checkbox)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.save)
        btns.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(btns)

        self.spacing_mode.currentTextChanged.connect(self._on_spacing_mode_changed)
        self._sync_spacing_mode()

        self.freq_input.textChanged.connect(self._on_spacing_mode_changed)

    def save(self):
        try:
            freq = float(self.freq_input.text())
            gain = int(self.gain_input.text())
            ctime = int(self.time_input.text())
            sample_window_s = float(self.sample_window_input.text())
            antenna_count = int(self.antenna_input.text())
            spacing_in = self._resolve_spacing_in(freq)
            refresh_s = int(self.refresh_input.text())
            movement_threshold_m = float(self.movement_threshold_input.text())
            movement_accuracy_factor = float(self.movement_accuracy_factor_input.text())
            gps_accuracy_floor_m = float(self.gps_accuracy_floor_input.text())
            orientations = []
            if self.orientation_input.text().strip():
                orientations = [
                    float(value.strip()) % 360.0
                    for value in self.orientation_input.text().split(",")
                    if value.strip()
                ]
            if self.directional_array_checkbox.isChecked() and len(orientations) != antenna_count:
                QtWidgets.QMessageBox.critical(
                    self,
                    "Directional Array Profile Required",
                    "Directional amplitude comparison requires one physical orientation for every configured antenna. "
                    f"Enter exactly {antenna_count} comma-separated orientations, or disable Directional Array.",
                )
                return
            alert_debounce_cycles = int(self.alert_debounce_input.text())
            profile = self.profile_input.text().strip() or "default"
            aoa_weight = float(self.aoa_weight_input.text())
            map_weight = float(self.map_weight_input.text())
            conf_threshold = float(self.conf_threshold_input.text())
            auto_tune = self.auto_tune_checkbox.isChecked()
            beamwidth_deg = float(self.beamwidth_input.text())
            front_back_db = float(self.front_back_input.text())
            heading_min_speed = float(self.heading_min_speed_input.text())
            heading_baseline = float(self.heading_baseline_input.text())
            heading_stale = float(self.heading_stale_input.text())
            with settings_lock:
                settings.frequency = freq
                settings.gain = gain
                settings.collection_time = ctime
                settings.sample_window_s = max(0.01, min(2.0, sample_window_s))
                settings.antenna_count = antenna_count
                settings.antenna_spacing_in = max(0.0, spacing_in)
                settings.info_refresh_s = refresh_s
                settings.movement_threshold_m = max(0.0, movement_threshold_m)
                settings.adaptive_movement_pause = self.adaptive_movement_checkbox.isChecked()
                settings.movement_accuracy_factor = max(0.0, movement_accuracy_factor)
                settings.gps_accuracy_floor_m = max(0.0, gps_accuracy_floor_m)
                settings.antenna_orientations_deg = orientations
                settings.directional_array_enabled = self.directional_array_checkbox.isChecked()
                settings.antenna_beamwidth_deg = max(10.0, min(180.0, beamwidth_deg))
                settings.antenna_front_back_db = max(1.0, min(60.0, front_back_db))
                settings.heading_min_speed_knots = max(0.0, heading_min_speed)
                settings.heading_min_baseline_m = max(1.0, heading_baseline)
                settings.heading_stale_s = max(0.5, heading_stale)
                settings.alert_debounce_cycles = max(1, alert_debounce_cycles)
                settings.calibration_profile = profile
                settings.fusion_aoa_weight = aoa_weight
                settings.fusion_map_weight = map_weight
                settings.confidence_threshold = conf_threshold
                settings.auto_tune_fusion = auto_tune
            if not MAPBOX_TOKEN:
                set_mapbox_token_override(self.mapbox_input.text().strip())
            profiles = _load_calibration_profiles()
            with calibration_lock:
                calibration_data.clear()
                calibration_data.update(profiles.get(profile, {}))
            save_settings()
            logger.info(f"Settings updated: {settings.to_dict()}")
            self.accept()
        except ValueError:
            QtWidgets.QMessageBox.critical(self, "Invalid Input", "Please enter valid numeric values.")

    def _resolve_spacing_in(self, freq_mhz: float) -> float:
        mode = self.spacing_mode.currentText().strip()
        lambda_in = None
        ideal_half = _ideal_spacing_inches(freq_mhz)
        if ideal_half is not None:
            lambda_in = ideal_half * 2.0

        if mode.startswith("0.25") and lambda_in:
            return 0.25 * lambda_in
        if mode.startswith("0.5") and lambda_in:
            return 0.5 * lambda_in
        if mode.startswith("0.75") and lambda_in:
            return 0.75 * lambda_in
        if mode.startswith("1.0") and lambda_in:
            return 1.0 * lambda_in

        spacing_text = self.spacing_input.text().strip()
        return float(spacing_text) if spacing_text else 0.0

    def _sync_spacing_mode(self):
        try:
            freq = float(self.freq_input.text())
        except Exception:
            freq = None
        spacing_in = 0.0
        try:
            spacing_in = float(self.spacing_input.text().strip() or 0.0)
        except Exception:
            spacing_in = 0.0

        ideal_half = _ideal_spacing_inches(freq) if freq else None
        lambda_in = ideal_half * 2.0 if ideal_half else None
        if spacing_in <= 0:
            self.spacing_mode.setCurrentText("Auto (0.5λ)")
            self.spacing_input.setEnabled(False)
            return

        if lambda_in:
            frac = spacing_in / lambda_in
            if abs(frac - 0.25) < 0.02:
                self.spacing_mode.setCurrentText("0.25λ")
                self.spacing_input.setEnabled(False)
                return
            if abs(frac - 0.5) < 0.02:
                self.spacing_mode.setCurrentText("0.5λ")
                self.spacing_input.setEnabled(False)
                return
            if abs(frac - 0.75) < 0.02:
                self.spacing_mode.setCurrentText("0.75λ")
                self.spacing_input.setEnabled(False)
                return
            if abs(frac - 1.0) < 0.02:
                self.spacing_mode.setCurrentText("1.0λ")
                self.spacing_input.setEnabled(False)
                return

        self.spacing_mode.setCurrentText("Custom (in)")
        self.spacing_input.setEnabled(True)

    def _on_spacing_mode_changed(self):
        mode = self.spacing_mode.currentText().strip()
        if mode == "Custom (in)":
            self.spacing_input.setEnabled(True)
            return
        # For presets, compute and display spacing for the current frequency.
        try:
            freq = float(self.freq_input.text())
        except Exception:
            freq = None
        ideal_half = _ideal_spacing_inches(freq) if freq else None
        if not ideal_half:
            self.spacing_input.setEnabled(False)
            return
        lambda_in = ideal_half * 2.0
        if mode.startswith("0.25"):
            spacing_in = 0.25 * lambda_in
        elif mode.startswith("0.5"):
            spacing_in = 0.5 * lambda_in
        elif mode.startswith("0.75"):
            spacing_in = 0.75 * lambda_in
        elif mode.startswith("1.0"):
            spacing_in = 1.0 * lambda_in
        else:
            spacing_in = ideal_half
        self.spacing_input.setText(f"{spacing_in:.2f}")
        self.spacing_input.setEnabled(False)

class LogWindow(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("View Log")
        self.resize(800, 520)

        self.text = QtWidgets.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setStyleSheet("QPlainTextEdit { font-family: Consolas, Menlo, monospace; }")

        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.clicked.connect(self.close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.text)
        layout.addWidget(self.close_btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        # Timer to tail the log file
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(800)
        self.timer.timeout.connect(self.tail_log)
        self.timer.start()

        self._last_size = 0
        self.tail_log(initial=True)

    def tail_log(self, initial=False):
        try:
            if not os.path.exists(LOG_FILE):
                if initial:
                    self.text.appendPlainText("Log file not found.\n")
                return
            cur_size = os.path.getsize(LOG_FILE)
            # Read from start if file truncated/rotated
            start_pos = 0 if cur_size < self._last_size else self._last_size
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(start_pos)
                chunk = f.read()
            if chunk:
                self.text.moveCursor(QtGui.QTextCursor.MoveOperation.End)
                self.text.insertPlainText(chunk)
                self.text.moveCursor(QtGui.QTextCursor.MoveOperation.End)
            self._last_size = cur_size
        except Exception as e:
            # Avoid noisy message boxes; surface in UI text instead
            self.text.appendPlainText(f"\n[Log Tail Error] {e}\n")


class PolarPlotWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._satellites = []
        self.setMinimumHeight(240)

    def set_satellites(self, satellites):
        self._satellites = satellites or []
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

        rect = self.rect().adjusted(10, 10, -10, -10)
        size = min(rect.width(), rect.height())
        center = QtCore.QPointF(rect.center().x(), rect.center().y())
        radius = size / 2.0

        # Background
        painter.fillRect(self.rect(), QtGui.QColor("#ffffff"))
        pen = QtGui.QPen(QtGui.QColor("#d1d5db"))
        painter.setPen(pen)
        painter.drawEllipse(center, radius, radius)
        painter.drawEllipse(center, radius * 0.66, radius * 0.66)
        painter.drawEllipse(center, radius * 0.33, radius * 0.33)
        painter.drawLine(
            QtCore.QPointF(center.x(), center.y() - radius),
            QtCore.QPointF(center.x(), center.y() + radius),
        )
        painter.drawLine(
            QtCore.QPointF(center.x() - radius, center.y()),
            QtCore.QPointF(center.x() + radius, center.y()),
        )

        # Cardinal labels
        painter.setPen(QtGui.QColor("#6b7280"))
        painter.drawText(
            QtCore.QPointF(center.x() - 6, center.y() - radius - 6), "N"
        )
        painter.drawText(
            QtCore.QPointF(center.x() + radius + 2, center.y() + 4), "E"
        )
        painter.drawText(
            QtCore.QPointF(center.x() - 6, center.y() + radius + 14), "S"
        )
        painter.drawText(
            QtCore.QPointF(center.x() - radius - 12, center.y() + 4), "W"
        )

        # Plot satellites
        for sat in self._satellites:
            az = sat.get("azimuth")
            elev = sat.get("elevation")
            snr = sat.get("snr")
            prn = sat.get("prn", "")
            if az is None or elev is None:
                continue
            # Convert to radians; azimuth is degrees clockwise from North.
            angle = np.deg2rad(az)
            r = (90.0 - elev) / 90.0 * radius
            x = center.x() + r * np.sin(angle)
            y = center.y() - r * np.cos(angle)

            # Color by SNR
            if snr is None:
                color = QtGui.QColor("#9ca3af")
                size_pt = 6
            else:
                snr_clamped = max(0.0, min(float(snr), 50.0))
                green = int(80 + (snr_clamped / 50.0) * 175)
                color = QtGui.QColor(40, green, 80)
                size_pt = 6 + int((snr_clamped / 50.0) * 6)

            painter.setBrush(color)
            painter.setPen(QtGui.QPen(QtGui.QColor("#111827")))
            painter.drawEllipse(QtCore.QPointF(x, y), size_pt / 2.0, size_pt / 2.0)
            painter.setPen(QtGui.QColor("#111827"))
            painter.drawText(QtCore.QPointF(x + 6, y - 4), str(prn))


class GPSInfoDialog(QtWidgets.QDialog):
    def __init__(self, get_satellites, get_refresh_s, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("GPS Satellite Info")
        self.resize(700, 520)

        self._get_satellites = get_satellites
        self._get_refresh_s = get_refresh_s

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["PRN", "SNR", "Elevation", "Azimuth"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)

        self.empty_label = QtWidgets.QLabel("Waiting for satellites...")
        self.empty_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self.polar = PolarPlotWidget()
        self.polar.set_satellites([])

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.polar)
        layout.addWidget(close_btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(self._refresh_interval_ms())
        self.refresh()

    def _refresh_interval_ms(self) -> int:
        try:
            s = self._get_refresh_s()
            return max(500, int(s * 1000))
        except Exception:
            return 3000

    def refresh(self):
        try:
            satellite_info = self._get_satellites() if callable(self._get_satellites) else []
        except Exception:
            satellite_info = []
        if isinstance(satellite_info, dict):
            satellites = satellite_info.get("satellites") or []
            satellite_count = satellite_info.get("count")
        else:
            satellites = satellite_info or []
            satellite_count = None
        self._set_satellites(satellites, satellite_count)
        self._timer.setInterval(self._refresh_interval_ms())

    def _set_satellites(self, satellites, satellite_count=None):
        sats = satellites or []
        self.table.setRowCount(len(sats))
        self.empty_label.setVisible(len(sats) == 0)
        if not sats and satellite_count is not None:
            self.empty_label.setText(
                f"{satellite_count} satellites used; waiting for detailed GSV data..."
            )
        elif not sats:
            self.empty_label.setText("Waiting for satellites...")
        for row, sat in enumerate(sats):
            prn = str(sat.get("prn", ""))
            snr = sat.get("snr")
            elev = sat.get("elevation")
            az = sat.get("azimuth")

            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(prn))

            snr_bar = QtWidgets.QProgressBar()
            snr_bar.setRange(0, 50)
            if snr is None:
                snr_bar.setValue(0)
                snr_bar.setFormat("--")
            else:
                snr_bar.setValue(int(max(0, min(50, float(snr)))))
                snr_bar.setFormat("%v")
            self.table.setCellWidget(row, 1, snr_bar)

            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem("--" if elev is None else f"{elev:.0f}deg"))
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem("--" if az is None else f"{az:.0f}deg"))
        self.polar.set_satellites(sats)


class AntennaLayoutWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._antenna_count = 1
        self._frequency_mhz = None
        self._antenna_states = []
        self._spacing_in = None
        self.setMinimumHeight(260)

    def set_layout(self, antenna_count, frequency_mhz, antenna_states, spacing_in: Optional[float] = None):
        self._antenna_count = max(1, int(antenna_count or 1))
        self._frequency_mhz = frequency_mhz
        self._antenna_states = antenna_states or []
        self._spacing_in = spacing_in
        self.update()

    def _angles(self, n):
        return _antenna_angles(n)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#ffffff"))

        rect = self.rect().adjusted(10, 10, -10, -10)
        size = min(rect.width(), rect.height())
        center = QtCore.QPointF(rect.center().x(), rect.center().y())
        radius = size * 0.35

        # Vehicle outline
        vehicle_w = size * 0.6
        vehicle_h = size * 0.35
        vehicle_rect = QtCore.QRectF(
            center.x() - vehicle_w / 2.0,
            center.y() - vehicle_h / 2.0,
            vehicle_w,
            vehicle_h,
        )
        painter.setPen(QtGui.QPen(QtGui.QColor("#d1d5db"), 1.2))
        painter.setBrush(QtGui.QColor("#f9fafb"))
        painter.drawRoundedRect(vehicle_rect, 12, 12)
        painter.setPen(QtGui.QColor("#6b7280"))
        painter.drawText(
            QtCore.QPointF(center.x() - 16, vehicle_rect.top() - 6),
            "Front",
        )

        # Antennas
        angles = self._angles(self._antenna_count)
        points = []
        for idx, angle in enumerate(angles):
            angle_rad = math.radians(angle)
            x = center.x() + radius * math.sin(angle_rad)
            y = center.y() - radius * math.cos(angle_rad)
            points.append(QtCore.QPointF(x, y))

            state = self._antenna_states[idx] if idx < len(self._antenna_states) else {}
            connected = state.get("connected", False)
            quality = state.get("quality", None)
            if connected:
                color = _quality_to_color(quality) if quality is not None else QtGui.QColor("#ef4444")
            else:
                color = QtGui.QColor("#9ca3af")

            painter.setBrush(color)
            painter.setPen(QtGui.QPen(QtGui.QColor("#111827"), 1))
            painter.drawEllipse(QtCore.QPointF(x, y), 8, 8)
            painter.setPen(QtGui.QColor("#111827"))
            painter.drawText(QtCore.QPointF(x + 10, y - 6), f"A{idx + 1}")

        # Spacing labels
        if len(points) > 1:
            spacing_in = _effective_spacing_inches(self._frequency_mhz, self._spacing_in)
            spacing_text = "--" if spacing_in is None else f"{spacing_in:.1f} in"
            pen = QtGui.QPen(QtGui.QColor("#9ca3af"), 1, QtCore.Qt.PenStyle.DashLine)
            painter.setPen(pen)
            edge_count = 1 if len(points) == 2 else len(points)
            for i in range(edge_count):
                p1 = points[i]
                p2 = points[(i + 1) % len(points)]
                painter.drawLine(p1, p2)
                mid = QtCore.QPointF((p1.x() + p2.x()) / 2.0, (p1.y() + p2.y()) / 2.0)
                painter.setPen(QtGui.QColor("#6b7280"))
                painter.drawText(QtCore.QPointF(mid.x() + 4, mid.y() - 4), spacing_text)
                painter.setPen(pen)


class CompassWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_bearing = None
        self._target_bearing = None
        self._target_relative = None
        self._source = None
        self._confidence = None
        self.setMinimumHeight(260)

    def set_bearings(self, current_bearing, target_bearing, target_relative):
        self._current_bearing = current_bearing
        self._target_bearing = target_bearing
        self._target_relative = target_relative
        self._source = None
        self._confidence = None
        self.update()

    def set_bearings_with_meta(self, current_bearing, target_bearing, target_relative, source, confidence):
        self._current_bearing = current_bearing
        self._target_bearing = target_bearing
        self._target_relative = target_relative
        self._source = source
        self._confidence = confidence
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor("#ffffff"))

        rect = self.rect().adjusted(10, 10, -10, -10)
        size = min(rect.width(), rect.height())
        center = QtCore.QPointF(rect.center().x(), rect.center().y())
        radius = size * 0.42

        painter.save()
        painter.translate(center)
        rotation = -float(self._current_bearing or 0.0)
        painter.rotate(rotation)

        # Ticks
        painter.setPen(QtGui.QPen(QtGui.QColor("#d1d5db"), 1))
        for deg in range(0, 360, 2):
            ang = math.radians(deg)
            outer = radius
            inner = radius - (8 if deg % 10 == 0 else 4)
            p1 = QtCore.QPointF(outer * math.sin(ang), -outer * math.cos(ang))
            p2 = QtCore.QPointF(inner * math.sin(ang), -inner * math.cos(ang))
            painter.drawLine(p1, p2)

        # Cardinal labels
        painter.setPen(QtGui.QColor("#6b7280"))
        painter.drawText(QtCore.QPointF(-6, -radius - 6), "N")
        painter.drawText(QtCore.QPointF(radius + 2, 4), "E")
        painter.drawText(QtCore.QPointF(-6, radius + 14), "S")
        painter.drawText(QtCore.QPointF(-radius - 12, 4), "W")

        # North arrow (red)
        painter.setPen(QtGui.QPen(QtGui.QColor("#ef4444"), 2))
        painter.setBrush(QtGui.QColor("#ef4444"))
        painter.drawLine(QtCore.QPointF(0, 0), QtCore.QPointF(0, -radius + 10))
        painter.drawPolygon(
            QtGui.QPolygonF(
                [
                    QtCore.QPointF(0, -radius + 2),
                    QtCore.QPointF(-6, -radius + 12),
                    QtCore.QPointF(6, -radius + 12),
                ]
            )
        )

        # Target arrow (green)
        if self._target_relative is not None:
            ang = math.radians(self._target_relative)
            end = QtCore.QPointF(
                (radius - 14) * math.sin(ang),
                -(radius - 14) * math.cos(ang),
            )
            painter.setPen(QtGui.QPen(QtGui.QColor("#10b981"), 2))
            painter.setBrush(QtGui.QColor("#10b981"))
            painter.drawLine(QtCore.QPointF(0, 0), end)
            painter.drawEllipse(end, 3, 3)

        painter.restore()

        # Center info box
        box_w = radius * 1.4
        box_h = radius * 0.65
        box_rect = QtCore.QRectF(
            center.x() - box_w / 2.0,
            center.y() - box_h / 2.0,
            box_w,
            box_h,
        )
        painter.setPen(QtGui.QPen(QtGui.QColor("#e5e7eb"), 1))
        painter.setBrush(QtGui.QColor("#f9fafb"))
        painter.drawRoundedRect(box_rect, 10, 10)

        top_rect = QtCore.QRectF(box_rect.x(), box_rect.y(), box_rect.width(), box_rect.height() / 2.0)
        bot_rect = QtCore.QRectF(
            box_rect.x(),
            box_rect.y() + box_rect.height() / 2.0,
            box_rect.width(),
            box_rect.height() / 2.0,
        )

        cur_text = _bearing_to_cardinal(self._current_bearing)
        cur_abs = "--" if self._current_bearing is None else f"Abs {self._current_bearing:.0f}deg"
        tgt_rel = "--" if self._target_relative is None else f"Turn {self._target_relative:+.0f}deg"
        tgt_abs = "--" if self._target_bearing is None else f"Abs {self._target_bearing:.0f}deg"

        # Split center box into left/right columns to prevent overlap
        label_font = painter.font()
        value_font = QtGui.QFont(label_font)
        small_font = QtGui.QFont(label_font)
        value_font.setPointSize(max(9, label_font.pointSize()))
        small_font.setPointSize(max(8, label_font.pointSize() - 1))

        def _split(rect: QtCore.QRectF) -> tuple[QtCore.QRectF, QtCore.QRectF]:
            left_w = rect.width() * 0.36
            left = QtCore.QRectF(rect.left() + 8, rect.top() + 4, left_w - 10, rect.height() - 8)
            right = QtCore.QRectF(rect.left() + left_w, rect.top() + 4, rect.width() - left_w - 8, rect.height() - 8)
            return left, right

        def _elide(text: str, font: QtGui.QFont, width: float) -> str:
            fm = QtGui.QFontMetrics(font)
            return fm.elidedText(text, QtCore.Qt.TextElideMode.ElideRight, int(max(0, width)))

        top_left, top_right = _split(top_rect)
        bot_left, bot_right = _split(bot_rect)

        def _draw_value_abs(rect: QtCore.QRectF, value_text: str, abs_text: str, value_color: QtGui.QColor):
            v_font = QtGui.QFont(value_font)
            s_font = QtGui.QFont(small_font)
            gap = 3
            while True:
                fm_val = QtGui.QFontMetrics(v_font)
                fm_small = QtGui.QFontMetrics(s_font)
                line_h_val = fm_val.height()
                line_h_small = fm_small.height()
                needed = line_h_val + line_h_small + gap
                if needed <= rect.height():
                    break
                shrunk = False
                if v_font.pointSize() > 7:
                    v_font.setPointSize(v_font.pointSize() - 1)
                    shrunk = True
                if s_font.pointSize() > 6:
                    s_font.setPointSize(s_font.pointSize() - 1)
                    shrunk = True
                if gap > 1:
                    gap -= 1
                    shrunk = True
                if not shrunk:
                    break
            value_rect = QtCore.QRectF(rect.left(), rect.top(), rect.width(), line_h_val)
            abs_rect = QtCore.QRectF(rect.left(), rect.top() + line_h_val + gap, rect.width(), line_h_small)
            painter.setFont(v_font)
            painter.setPen(value_color)
            painter.drawText(value_rect, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignTop, _elide(value_text, v_font, rect.width()))
            painter.setFont(s_font)
            painter.setPen(QtGui.QColor("#6b7280"))
            painter.drawText(abs_rect, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignTop, _elide(abs_text, s_font, rect.width()))

        painter.setFont(label_font)
        painter.setPen(QtGui.QColor("#10b981"))
        label_cur = _elide("Current", label_font, top_left.width())
        painter.drawText(top_left, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop, label_cur)
        _draw_value_abs(top_right, cur_text, cur_abs, QtGui.QColor("#10b981"))

        painter.setFont(label_font)
        painter.setPen(QtGui.QColor("#ef4444"))
        label_tgt = _elide("Target", label_font, bot_left.width())
        painter.drawText(bot_left, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop, label_tgt)
        _draw_value_abs(bot_right, tgt_rel, tgt_abs, QtGui.QColor("#ef4444"))

        if self._source:
            conf_text = "--" if self._confidence is None else f"{self._confidence:.2f}"
            painter.setPen(QtGui.QColor("#6b7280"))
            painter.drawText(
                QtCore.QPointF(center.x() - radius, center.y() + radius + 18),
                f"Source: {self._source.upper()}  Conf: {conf_text}",
            )


class SparklineWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = []
        self.setMinimumHeight(26)

    def set_data(self, values):
        self._values = list(values or [])
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor("#ffffff"))

        if not self._values:
            painter.setPen(QtGui.QColor("#9ca3af"))
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "--")
            return

        vals = [v for v in self._values if v is not None]
        if not vals:
            painter.setPen(QtGui.QColor("#9ca3af"))
            painter.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, "--")
            return

        min_val = 0.0
        max_val = 1000.0
        span = max(1e-6, max_val - min_val)
        margin = 4
        w = max(1, rect.width() - 2 * margin)
        h = max(1, rect.height() - 2 * margin)
        step = w / max(1, (len(self._values) - 1))

        path = QtGui.QPainterPath()
        started = False
        for i, v in enumerate(self._values):
            if v is None:
                started = False
                continue
            try:
                val = float(v)
            except Exception:
                started = False
                continue
            val = max(min_val, min(max_val, val))
            x = rect.left() + margin + i * step
            y = rect.top() + margin + (1.0 - (val - min_val) / span) * h
            if not started:
                path.moveTo(x, y)
                started = True
            else:
                path.lineTo(x, y)

        painter.setPen(QtGui.QPen(QtGui.QColor("#10b981"), 1.6))
        painter.drawPath(path)


class AntennaInfoDialog(QtWidgets.QDialog):
    def __init__(self, get_info, get_refresh_s, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("Antenna Info")
        self.resize(1300, 560)

        self._get_info = get_info
        self._get_refresh_s = get_refresh_s
        self._selected_index = None
        self._refreshing = False
        self._spark_history = {}
        self._spark_max_points = 40

        self.layout_widget = AntennaLayoutWidget()
        self.compass_widget = CompassWidget()

        self.meta_label = QtWidgets.QLabel("Calibration: --  |  Fusion: --")
        self.meta_label.setStyleSheet("color: #6b7280;")
        self.spacing_label = QtWidgets.QLabel("Spacing: --")
        self.spacing_label.setStyleSheet("color: #6b7280;")

        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Antenna", "Strength", "SNR", "Trend", "Status"])
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_selection)

        self.detail_frame = QtWidgets.QFrame()
        self.detail_frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        self.detail_layout = QtWidgets.QFormLayout(self.detail_frame)
        self.detail_labels = {
            "Connection": QtWidgets.QLabel("--"),
            "Sample Rate": QtWidgets.QLabel("--"),
            "Signal Quality": QtWidgets.QLabel("--"),
            "SNR": QtWidgets.QLabel("--"),
            "Power": QtWidgets.QLabel("--"),
            "Antenna Position": QtWidgets.QLabel("--"),
            "SDR Health": QtWidgets.QLabel("--"),
            "Health Reason": QtWidgets.QLabel("--"),
            "Read Latency": QtWidgets.QLabel("--"),
            "Samples": QtWidgets.QLabel("--"),
            "Last Success": QtWidgets.QLabel("--"),
            "Failures": QtWidgets.QLabel("--"),
            "Reconnects": QtWidgets.QLabel("--"),
            "Last Error": QtWidgets.QLabel("--"),
        }
        for key, label in self.detail_labels.items():
            self.detail_layout.addRow(key, label)
        self.detail_frame.setVisible(False)

        right_panel = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        right_panel.addWidget(self.table)
        right_panel.addWidget(self.detail_frame)
        right_panel.setSizes([320, 160])

        self.layout_widget.setMaximumWidth(340)
        self.compass_widget.setMaximumWidth(340)
        right_panel.setMinimumWidth(560)

        plots = QtWidgets.QHBoxLayout()
        plots.addWidget(self.layout_widget, stretch=1)
        plots.addWidget(self.compass_widget, stretch=1)
        plots.addWidget(right_panel, stretch=2)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.meta_label)
        layout.addWidget(self.spacing_label)
        layout.addLayout(plots)
        layout.addWidget(close_btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(self._refresh_interval_ms())
        self.refresh()

    def _refresh_interval_ms(self) -> int:
        try:
            s = self._get_refresh_s()
            return max(500, int(s * 1000))
        except Exception:
            return 3000

    def _angles(self, n):
        return _antenna_angles(n)

    def _position_label(self, angle):
        dirs = ["Front", "Front-Right", "Right", "Rear-Right", "Rear", "Rear-Left", "Left", "Front-Left"]
        idx = int(((angle + 22.5) % 360) // 45)
        return dirs[idx]

    def _health_status(self, connected, strength, snr, quality):
        if not connected:
            return "Unhealthy"
        if quality is not None and quality >= 0.3 and snr is not None and snr >= 5:
            return "Healthy"
        if strength is not None and strength >= 100:
            return "Healthy"
        return "Unhealthy"

    def refresh(self):
        if self._refreshing:
            return
        self._refreshing = True
        try:
            info = {}
            try:
                info = self._get_info() if callable(self._get_info) else {}
            except Exception:
                info = {}

            raw_states = info.get("antenna_states") or []
            antenna_count = len(raw_states) if raw_states else int(info.get("antenna_count") or 1)
            frequency_mhz = info.get("frequency_mhz")
            spacing_in = info.get("antenna_spacing_in")
            ideal_spacing_in = info.get("ideal_spacing_in")
            strength = info.get("strength")
            snr = info.get("snr")
            quality = info.get("quality")
            sdr_connected = bool(info.get("sdr_connected", False))
            sdr_error = info.get("sdr_error")
            sdr_sample_rate = info.get("sdr_sample_rate")
            current_bearing = info.get("current_bearing")
            target_bearing = info.get("target_bearing")
            target_relative = info.get("target_relative")
            aoa_conf = info.get("aoa_confidence")
            map_conf = info.get("map_confidence")
            fusion_conf = info.get("fusion_confidence")
            bearing_source = info.get("bearing_source")
            profile_name = info.get("calibration_profile")
            configured_angles = info.get("antenna_orientations_deg") or []

            angles = (
                [float(v) % 360.0 for v in configured_angles[:antenna_count]]
                if len(configured_angles) >= antenna_count
                else self._angles(antenna_count)
            )
            antenna_states = []
            if raw_states:
                for i, state in enumerate(raw_states):
                    antenna_states.append(
                        {
                            "index": state.get("index", i),
                            "name": state.get("name"),
                            "serial": state.get("serial"),
                            "connected": bool(state.get("connected")),
                            "error": state.get("error"),
                            "sample_rate": state.get("sample_rate"),
                            "strength": state.get("strength"),
                            "snr": state.get("snr"),
                            "quality": state.get("quality"),
                            "power_dbfs": state.get("power_dbfs"),
                            "health": state.get("health"),
                            "health_reason": state.get("health_reason"),
                            "read_latency_ms": state.get("read_latency_ms"),
                            "sample_count": state.get("sample_count"),
                            "last_success_ts": state.get("last_success_ts"),
                            "consecutive_failures": state.get("consecutive_failures", 0),
                            "reconnect_count": state.get("reconnect_count", 0),
                            "position": self._position_label(angles[i]),
                        }
                    )
            else:
                for i in range(antenna_count):
                    connected = sdr_connected and i == 0
                    antenna_states.append(
                        {
                            "connected": connected,
                            "strength": strength if connected else None,
                            "snr": snr if connected else None,
                            "quality": quality if connected else None,
                            "position": self._position_label(angles[i]),
                        }
                    )

            self.layout_widget.set_layout(antenna_count, frequency_mhz, antenna_states, spacing_in)
            self.compass_widget.set_bearings_with_meta(
                current_bearing,
                target_bearing,
                target_relative,
                bearing_source or "--",
                fusion_conf if fusion_conf is not None else 0.0,
            )
            self._update_spark_history(antenna_states)
            self._populate_table(antenna_states)
            if aoa_conf is not None and map_conf is not None and fusion_conf is not None:
                source_text = (bearing_source or "--").upper()
                self.meta_label.setText(
                    f"Calibration: {profile_name or '--'}  |  Source: {source_text}  |  Amplitude Conf: {aoa_conf:.2f}  |  Map Conf: {map_conf:.2f}  |  Fusion Conf: {fusion_conf:.2f}"
                )
            else:
                self.meta_label.setText(f"Calibration: {profile_name or '--'}")
            actual_spacing_in = _effective_spacing_inches(frequency_mhz, spacing_in)
            ideal_in = ideal_spacing_in if ideal_spacing_in is not None else _ideal_spacing_inches(frequency_mhz)
            if actual_spacing_in and ideal_in:
                lambda_in = ideal_in * 2.0
                lambda_frac = actual_spacing_in / lambda_in if lambda_in else 0.0
                self.spacing_label.setText(
                    f"Spacing: {actual_spacing_in:.1f} in ({lambda_frac:.2f}λ)  |  Ideal ½λ: {ideal_in:.1f} in"
                )
            elif actual_spacing_in:
                self.spacing_label.setText(f"Spacing: {actual_spacing_in:.1f} in")
            else:
                self.spacing_label.setText("Spacing: --")
            self._timer.setInterval(self._refresh_interval_ms())
        finally:
            self._refreshing = False

    def _update_spark_history(self, antenna_states):
        active = set(range(len(antenna_states)))
        for idx in list(self._spark_history.keys()):
            if idx not in active:
                self._spark_history.pop(idx, None)
        for idx, state in enumerate(antenna_states):
            hist = self._spark_history.setdefault(idx, [])
            hist.append(state.get("strength"))
            if len(hist) > self._spark_max_points:
                self._spark_history[idx] = hist[-self._spark_max_points:]

    def _populate_table(self, antenna_states):
        selected = self._selected_index
        self.table.setRowCount(len(antenna_states))
        for row, state in enumerate(antenna_states):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(f"A{row + 1}"))

            strength_bar = QtWidgets.QProgressBar()
            strength_bar.setRange(0, 1000)
            if state.get("strength") is None:
                strength_bar.setValue(0)
                strength_bar.setFormat("--")
            else:
                strength_bar.setValue(int(max(0, min(1000, float(state.get("strength"))))))
                strength_bar.setFormat("%v")
            self.table.setCellWidget(row, 1, strength_bar)

            snr_bar = QtWidgets.QProgressBar()
            snr_bar.setRange(0, 50)
            if state.get("snr") is None:
                snr_bar.setValue(0)
                snr_bar.setFormat("--")
            else:
                snr_bar.setValue(int(max(0, min(50, float(state.get("snr"))))))
                snr_bar.setFormat("%v")
            self.table.setCellWidget(row, 2, snr_bar)

            spark = SparklineWidget()
            spark.set_data(self._spark_history.get(row, []))
            self.table.setCellWidget(row, 3, spark)

            status = state.get("health") or ("Connected" if state.get("connected") else "Disconnected")
            self.table.setItem(row, 4, QtWidgets.QTableWidgetItem(status))

        if selected is not None and 0 <= selected < len(antenna_states):
            self.table.selectRow(selected)
            self._update_detail(antenna_states[selected])
        else:
            self.detail_frame.setVisible(False)

    def _on_selection(self):
        if self._refreshing:
            return
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            self._selected_index = None
            self.detail_frame.setVisible(False)
            return
        self._selected_index = rows[0].row()
        self.refresh()

    def _update_detail(self, state):
        connected = state.get("connected")
        strength = state.get("strength")
        snr = state.get("snr")
        quality = state.get("quality")
        sample_rate = state.get("sample_rate")
        sdr_error = state.get("error")
        health = state.get("health") or self._health_status(connected, strength, snr, quality)
        self.detail_labels["Connection"].setText("Connected" if connected else "Disconnected")
        self.detail_labels["Sample Rate"].setText("--" if sample_rate is None else f"{sample_rate:.0f} Hz")
        self.detail_labels["Signal Quality"].setText("--" if quality is None else f"{quality:.2f}")
        self.detail_labels["SNR"].setText("--" if snr is None else f"{snr:.2f}")
        power_dbfs = state.get("power_dbfs")
        self.detail_labels["Power"].setText("--" if power_dbfs is None else f"{power_dbfs:.1f} dBFS")
        self.detail_labels["Antenna Position"].setText(state.get("position") or "--")
        self.detail_labels["SDR Health"].setText(health)
        self.detail_labels["Health Reason"].setText(state.get("health_reason") or "--")
        latency = state.get("read_latency_ms")
        self.detail_labels["Read Latency"].setText("--" if latency is None else f"{latency:.1f} ms")
        sample_count = state.get("sample_count")
        self.detail_labels["Samples"].setText("--" if sample_count is None else f"{int(sample_count):,}")
        last_success = state.get("last_success_ts")
        if last_success is None:
            last_success_text = "--"
        else:
            last_success_text = f"{max(0.0, time.time() - float(last_success)):.1f}s ago"
        self.detail_labels["Last Success"].setText(last_success_text)
        self.detail_labels["Failures"].setText(str(state.get("consecutive_failures", 0)))
        self.detail_labels["Reconnects"].setText(str(state.get("reconnect_count", 0)))
        self.detail_labels["Last Error"].setText("--" if not sdr_error else str(sdr_error))
        self.detail_frame.setVisible(True)


class GPSSetupWizard(QtWidgets.QDialog):
    """
    Simple GPS port selection wizard for field operators.
    """

    def __init__(self, parent=None, current_port: Optional[str] = None):
        super().__init__(parent)
        self._current_port = current_port
        _apply_app_icon(self)
        self.setWindowTitle("GPS Configuration")
        self.setMinimumWidth(520)
        self.setModal(True)

        self.port_combo = QtWidgets.QComboBox()
        self.desc_label = QtWidgets.QLabel("")
        self.desc_label.setWordWrap(True)
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        self.remember_checkbox = QtWidgets.QCheckBox("Remember COM Port")
        self.remember_checkbox.setChecked(False)
        self.remember_checkbox.setToolTip(
            "Save the selected GPS port as the default for future launches. "
            "Leave unchecked to use it for this run only."
        )

        self.refresh_btn.clicked.connect(self._load_ports)
        self.port_combo.currentIndexChanged.connect(self._update_desc)

        form = QtWidgets.QFormLayout()
        form.addRow("COM Port", self.port_combo)
        form.addRow("Device Description", self.desc_label)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Save
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.refresh_btn, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.remember_checkbox)
        layout.addWidget(btns)

        self._ports = []
        self._load_ports()

    def _load_ports(self):
        self._ports = funcs.list_serial_ports()
        self.port_combo.clear()
        for p in self._ports:
            label = f"{p['device']} - {p['description']}".strip(" -")
            self.port_combo.addItem(label, p)
        if self._current_port:
            for index in range(self.port_combo.count()):
                data = self.port_combo.itemData(index)
                if isinstance(data, dict) and (data.get("device") or "").upper() == self._current_port.upper():
                    self.port_combo.setCurrentIndex(index)
                    break
        self._update_desc()

    def _update_desc(self):
        data = self.selected_port_info()
        if not data:
            self.desc_label.setText("No ports detected.")
            return
        desc = data.get("description") or ""
        mfg = data.get("manufacturer") or ""
        hwid = data.get("hwid") or ""
        parts = [x for x in [desc, mfg, hwid] if x]
        self.desc_label.setText(" | ".join(parts) if parts else "No description available.")

    def selected_port_info(self):
        data = self.port_combo.currentData()
        if isinstance(data, dict) and data.get("device"):
            return data
        return None

    def selected_port(self) -> Optional[str]:
        info = self.selected_port_info()
        return info.get("device") if info else None

    def remember_port(self) -> bool:
        return bool(self.remember_checkbox.isChecked())


class BusyDialog(QtWidgets.QDialog):
    def __init__(self, title: str, text: str, mode: str = "general", parent=None, show_progress: bool = False):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.status_label = QtWidgets.QLabel(text)
        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(LOADING_ICON_PX + 8, LOADING_ICON_PX + 8)
        self.icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._icon_movie: Optional[QtGui.QMovie] = None
        self._icon_mode: Optional[str] = None
        self._last_status_msg: Optional[str] = None
        self._last_status_time: float = 0.0

        layout = QtWidgets.QVBoxLayout(self)
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.icon_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        top_row.addWidget(self.status_label, stretch=1, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)

        self.progress_bar: Optional[QtWidgets.QProgressBar] = None
        if show_progress:
            self.progress_bar = QtWidgets.QProgressBar()
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setTextVisible(True)
            layout.addSpacing(6)
            layout.addWidget(self.progress_bar)

        self.set_status(text, mode=mode)

    def set_status(self, text: str, mode: str = "general") -> None:
        self.status_label.setText(text)
        self._set_status_icon(mode)

    def _set_status_icon(self, mode: str) -> None:
        if self._icon_movie is not None:
            self._icon_movie.stop()
            self._icon_movie.deleteLater()
            self._icon_movie = None

        asset = _status_anim_for_mode(mode)
        if asset and os.path.exists(asset):
            processed_asset = _transparentize_gif(asset, allow_processing=True)
            movie = QtGui.QMovie(processed_asset)
            if movie.isValid():
                movie.setScaledSize(QtCore.QSize(LOADING_ICON_PX, LOADING_ICON_PX))
                self.icon_label.setMovie(movie)
                movie.start()
                self._icon_movie = movie
                return

        if mode == "stopping":
            icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserStop)
        else:
            icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
        self.icon_label.setPixmap(icon.pixmap(LOADING_ICON_PX, LOADING_ICON_PX))

    def set_progress(self, value: int) -> None:
        if self.progress_bar is None:
            return
        value = max(0, min(100, int(value)))
        self.progress_bar.setValue(value)


class RecordingPromptDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("Record Session")
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.result_record = False

        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(LOADING_ICON_PX + 8, LOADING_ICON_PX + 8)
        self.icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._icon_movie: Optional[QtGui.QMovie] = None

        self.text_label = QtWidgets.QLabel("Would you like to record this session?")

        btn_record = QtWidgets.QPushButton("Record")
        btn_skip = QtWidgets.QPushButton("Don't Record")
        btn_record.clicked.connect(self._choose_record)
        btn_skip.clicked.connect(self._choose_skip)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(btn_record)
        btn_row.addWidget(btn_skip)

        layout = QtWidgets.QVBoxLayout(self)
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.icon_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        top_row.addWidget(self.text_label, stretch=1, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)
        layout.addSpacing(6)
        layout.addLayout(btn_row)

        self._set_icon()

    def _set_icon(self):
        asset = QUESTION_GIF
        if asset and os.path.exists(asset):
            processed = _transparentize_gif(asset)
            movie = QtGui.QMovie(processed)
            if movie.isValid():
                movie.setScaledSize(QtCore.QSize(LOADING_ICON_PX, LOADING_ICON_PX))
                self.icon_label.setMovie(movie)
                movie.start()
                self._icon_movie = movie
                return
        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxQuestion)
        self.icon_label.setPixmap(icon.pixmap(LOADING_ICON_PX, LOADING_ICON_PX))

    def _choose_record(self):
        self.result_record = True
        self.accept()

    def _choose_skip(self):
        self.result_record = False
        self.accept()


class FlagDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("Flag Event")
        self.setMinimumWidth(360)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.reason_combo = QtWidgets.QComboBox()
        self.reason_combo.addItems(list(FLAG_REASON_OPTIONS))
        self.note_input = QtWidgets.QPlainTextEdit()
        self.note_input.setPlaceholderText("Optional notes...")

        form = QtWidgets.QFormLayout()
        form.addRow("Reason", self.reason_combo)
        form.addRow("Notes", self.note_input)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(btns)

    def flag_data(self) -> dict:
        return {
            "reason": (self.reason_combo.currentText() or "").strip(),
            "note": self.note_input.toPlainText().strip(),
        }


class NoHardwareDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("No SDR/GPS Detected")
        self.setMinimumWidth(460)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.result_choice: Optional[str] = None

        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(LOADING_ICON_PX + 8, LOADING_ICON_PX + 8)
        self.icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._icon_movie: Optional[QtGui.QMovie] = None

        self.text_label = QtWidgets.QLabel(
            "No SDRs or GPS devices were detected.\n"
            "You can continue in Playback mode, Meshtastic Viewer mode, rescan, or exit."
        )
        self.text_label.setWordWrap(True)

        btn_playback = QtWidgets.QPushButton("Playback Mode Only")
        btn_mesh = QtWidgets.QPushButton("Meshtastic Viewer Only")
        btn_rescan = QtWidgets.QPushButton("Rescan / Reinitialize")
        btn_exit = QtWidgets.QPushButton("Exit")
        btn_playback.clicked.connect(lambda: self._choose("playback"))
        btn_mesh.clicked.connect(lambda: self._choose("meshtastic"))
        btn_rescan.clicked.connect(lambda: self._choose("rescan"))
        btn_exit.clicked.connect(lambda: self._choose("exit"))

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(btn_playback)
        btn_row.addWidget(btn_mesh)
        btn_row.addWidget(btn_rescan)
        btn_row.addWidget(btn_exit)

        layout = QtWidgets.QVBoxLayout(self)
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.icon_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        top_row.addWidget(self.text_label, stretch=1, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)
        layout.addSpacing(8)
        layout.addLayout(btn_row)

        self._set_icon()

    def _set_icon(self):
        asset = ALERT_GIF
        if asset and os.path.exists(asset):
            processed = _transparentize_gif(asset)
            movie = QtGui.QMovie(processed)
            if movie.isValid():
                movie.setScaledSize(QtCore.QSize(LOADING_ICON_PX, LOADING_ICON_PX))
                self.icon_label.setMovie(movie)
                movie.start()
                self._icon_movie = movie
                return
        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)
        self.icon_label.setPixmap(icon.pixmap(LOADING_ICON_PX, LOADING_ICON_PX))

    def _choose(self, choice: str):
        self.result_choice = choice
        self.accept()


class PlaybackOnlyDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("Playback Only")
        self.setMinimumWidth(440)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.result_choice: Optional[str] = None

        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(LOADING_ICON_PX + 8, LOADING_ICON_PX + 8)
        self.icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._icon_movie: Optional[QtGui.QMovie] = None

        self.text_label = QtWidgets.QLabel(
            "This session is locked to Playback mode only.\n"
            "Rescan for SDR/GPS hardware or continue in Playback mode."
        )
        self.text_label.setWordWrap(True)

        btn_rescan = QtWidgets.QPushButton("Rescan / Reinitialize")
        btn_close = QtWidgets.QPushButton("Close")
        btn_rescan.clicked.connect(lambda: self._choose("rescan"))
        btn_close.clicked.connect(lambda: self._choose("close"))

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(btn_rescan)
        btn_row.addWidget(btn_close)

        layout = QtWidgets.QVBoxLayout(self)
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.icon_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        top_row.addWidget(self.text_label, stretch=1, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)
        layout.addSpacing(8)
        layout.addLayout(btn_row)

        self._set_icon()

    def _set_icon(self):
        asset = ALERT_GIF
        if asset and os.path.exists(asset):
            processed = _transparentize_gif(asset)
            movie = QtGui.QMovie(processed)
            if movie.isValid():
                movie.setScaledSize(QtCore.QSize(LOADING_ICON_PX, LOADING_ICON_PX))
                self.icon_label.setMovie(movie)
                movie.start()
                self._icon_movie = movie
                return
        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)
        self.icon_label.setPixmap(icon.pixmap(LOADING_ICON_PX, LOADING_ICON_PX))

    def _choose(self, choice: str):
        self.result_choice = choice
        self.accept()


class MeshtasticOnlyDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("Meshtastic Viewer Only")
        self.setMinimumWidth(460)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.result_choice: Optional[str] = None

        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(LOADING_ICON_PX + 8, LOADING_ICON_PX + 8)
        self.icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._icon_movie: Optional[QtGui.QMovie] = None

        self.text_label = QtWidgets.QLabel(
            "This session is locked to Meshtastic Viewer mode only.\n"
            "Rescan for SDR/GPS hardware or continue in Meshtastic mode."
        )
        self.text_label.setWordWrap(True)

        btn_rescan = QtWidgets.QPushButton("Rescan / Reinitialize")
        btn_close = QtWidgets.QPushButton("Close")
        btn_rescan.clicked.connect(lambda: self._choose("rescan"))
        btn_close.clicked.connect(lambda: self._choose("close"))

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(btn_rescan)
        btn_row.addWidget(btn_close)

        layout = QtWidgets.QVBoxLayout(self)
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.icon_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        top_row.addWidget(self.text_label, stretch=1, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)
        layout.addSpacing(8)
        layout.addLayout(btn_row)

        self._set_icon()

    def _set_icon(self):
        asset = ALERT_GIF
        if asset and os.path.exists(asset):
            processed = _transparentize_gif(asset)
            movie = QtGui.QMovie(processed)
            if movie.isValid():
                movie.setScaledSize(QtCore.QSize(LOADING_ICON_PX, LOADING_ICON_PX))
                self.icon_label.setMovie(movie)
                movie.start()
                self._icon_movie = movie
                return
        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxWarning)
        self.icon_label.setPixmap(icon.pixmap(LOADING_ICON_PX, LOADING_ICON_PX))

    def _choose(self, choice: str):
        self.result_choice = choice
        self.accept()

class GPSProbeThread(QtCore.QThread):
    found = QtCore.pyqtSignal(str)
    not_found = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        try:
            port = _guess_gps_port_no_open()
            if port:
                self.found.emit(port)
            else:
                self.not_found.emit()
        except Exception as e:
            self.error.emit(e)


class GPSFixWaitThread(QtCore.QThread):
    fix_acquired = QtCore.pyqtSignal()
    connected_without_fix = QtCore.pyqtSignal()
    error = QtCore.pyqtSignal(object)

    def __init__(self, port: str, parent=None):
        super().__init__(parent)
        self.port = port

    def run(self):
        gps_serial = None
        gps_reader = None
        try:
            gps_serial, gps_reader = funcs.openGPS(port=self.port)
            result = funcs.readGPS(
                logger,
                serial_port=gps_serial,
                nmea_reader=gps_reader,
                max_wait_s=GPS_MAX_WAIT_S,
            )
            if result is not None:
                lat, lon = result[0], result[1]
                if lat is not None and lon is not None:
                    self.fix_acquired.emit()
                    return
                self.connected_without_fix.emit()
                return
            self.error.emit(Exception("No NMEA data received from the selected port."))
        except Exception as e:
            self.error.emit(e)
        finally:
            try:
                if gps_serial is not None:
                    gps_serial.close()
            except Exception:
                pass


class HardwareCheckThread(QtCore.QThread):
    result = QtCore.pyqtSignal(bool, bool)

    def run(self):
        has_sdr = False
        has_gps = False
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue()
        proc = ctx.Process(target=_hardware_check_worker, args=(queue,))
        proc.daemon = True
        proc.start()
        proc.join(HARDWARE_CHECK_TIMEOUT_S)
        if proc.is_alive():
            proc.terminate()
            proc.join(1)
        else:
            try:
                has_sdr, has_gps = queue.get_nowait()
            except Exception:
                has_sdr = False
                has_gps = False
        self.result.emit(has_sdr, has_gps)


class GPSStartupDialog(QtWidgets.QDialog):
    """
    GPS and SDR initialization dialog with progress and prompts.
    """

    def __init__(self, timeout_s: float = 8.0, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("Initializing")
        self.setMinimumWidth(520)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self.status_label = QtWidgets.QLabel("Reading device information...")
        self.icon_label = QtWidgets.QLabel()
        self.icon_label.setFixedSize(LOADING_ICON_PX + 8, LOADING_ICON_PX + 8)
        self.icon_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._icon_movie: Optional[QtGui.QMovie] = None
        self._icon_mode: Optional[str] = None
        self._last_status_msg: Optional[str] = None
        self._last_status_time: float = 0.0
        self.progress = QtWidgets.QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

        layout = QtWidgets.QVBoxLayout(self)
        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(self.icon_label, alignment=QtCore.Qt.AlignmentFlag.AlignLeft)
        top_row.addWidget(self.status_label, stretch=1, alignment=QtCore.Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(top_row)
        layout.addWidget(self.progress)

        self._timeout_s = timeout_s
        self._start_time = None
        self._selected_port: Optional[str] = None
        self._remember_port = False
        self._last_error = None
        self._calibration_done = False
        self._calibration = {}

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._tick)
        self._probe_thread: Optional[GPSProbeThread] = None
        self._next_probe_time = 0.0
        self._probe_cooldown_s = 1.5
        self._fix_thread: Optional[GPSFixWaitThread] = None
        self._calibration_started = False
        self._playback_only = False
        self._meshtastic_only = False
        self._hw_thread: Optional[HardwareCheckThread] = None

        self._set_status("Initializing...", mode="general")
        QtCore.QTimer.singleShot(50, self._run_startup_checks)

    def _run_startup_checks(self):
        self._set_status("Reading device information...", mode="general")
        required = [
            LOADING_ANIM_GENERAL,
            LOADING_ANIM_CHECKING,
            LOADING_ANIM_CAL,
            LOADING_ANIM_GPS,
            LOADING_ANIM_STOP,
            LOADING_ANIM_START,
            LOADING_ANIM_RUNNING,
            QUESTION_GIF,
            LOADING_ANIM_PLAYBACK,
            LOADING_ANIM_PAUSED,
            LOADING_ANIM_IMPORT,
            ALERT_GIF,
        ]
        missing = [p for p in required if p and not os.path.exists(p)]
        if missing:
            logger.warning("Missing loading GIFs: %s", ", ".join(missing))
        self._set_status("Reading device information...", mode="checking")
        if self._hw_thread is not None:
            try:
                if self._hw_thread.isRunning():
                    return
            except RuntimeError:
                self._hw_thread = None
        self._hw_thread = HardwareCheckThread(self)
        self._hw_thread.result.connect(self._on_hardware_check)
        self._hw_thread.finished.connect(self._on_hardware_check_finished)
        self._hw_thread.start()

    def _on_hardware_check_finished(self):
        # Clear reference safely once the thread finishes to avoid stale pointer access.
        self._hw_thread = None

    def _on_hardware_check(self, has_sdr: bool, has_gps: bool):
        if not (has_sdr or has_gps):
            dlg = NoHardwareDialog(self)
            dlg.exec()
            choice = dlg.result_choice
            if choice == "exit" or choice is None:
                self.reject()
                return
            if choice == "rescan":
                QtCore.QTimer.singleShot(500, self._run_startup_checks)
                return
            if choice == "meshtastic":
                self._meshtastic_only = True
                self._playback_only = False
            else:
                # Playback-only
                self._playback_only = True
                self._meshtastic_only = False
            self.accept()
            return

        # Let UI settle before heavy calibration work starts.
        QtCore.QTimer.singleShot(1000, self._start_calibration)

    def _start_calibration(self):
        if self._calibration_started:
            return
        self._calibration_started = True
        self._set_status("Calibrating antennas...", mode="calibrating")
        with settings_lock:
            freq = settings.frequency
            gain = settings.gain
            profile = settings.calibration_profile
        self._cal_thread = CalibrationThread(freq, gain, profile, parent=self)
        self._cal_thread.progress.connect(self._on_calibration_progress)
        self._cal_thread.done.connect(self._on_calibration_done)
        self._cal_thread.start()

    def _on_calibration_progress(self, done, total, msg):
        if total <= 0:
            pct = 50
        else:
            pct = int((done / total) * 50)
        self.progress.setValue(max(0, min(50, pct)))
        if msg:
            self._set_status(msg, mode="calibrating")

    def _on_calibration_done(self, calibration):
        self._calibration = calibration or {}
        with calibration_lock:
            calibration_data.clear()
            calibration_data.update(self._calibration)
        self._calibration_done = True
        self._set_status("Connecting to GPS receiver...", mode="gps")
        self._start_time = time.time()
        self._timer.start()

    def _tick(self):
        if not self._calibration_done:
            return
        elapsed = time.time() - self._start_time
        pct = 50 + min(50, int((elapsed / self._timeout_s) * 50))
        self.progress.setValue(pct)

        # Try to discover the GPS port (non-blocking, best-effort)
        self._maybe_start_probe()

        if elapsed >= self._timeout_s:
            self._timer.stop()
            self._set_status("GPS not detected. Opening configuration wizard...", mode="gps")
            QtCore.QTimer.singleShot(300, self._open_wizard)

    def _open_wizard(self):
        with settings_lock:
            preferred_port = settings.preferred_gps_port
        wizard = GPSSetupWizard(
            self,
            current_port=self._selected_port or os.environ.get("GPS_PORT") or preferred_port,
        )
        if wizard.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            port = wizard.selected_port()
            if port:
                self._selected_port = port
                self._remember_port = wizard.remember_port()
                QtCore.QTimer.singleShot(0, self._start_fix_wait)
                return
        # If user cancels, keep app running but without a selected GPS port.
        self.reject()

    def _maybe_start_probe(self) -> None:
        now = time.time()
        if self._probe_thread and self._probe_thread.isRunning():
            return
        if now < self._next_probe_time:
            return
        self._next_probe_time = now + self._probe_cooldown_s

        self._probe_thread = GPSProbeThread(self)
        self._probe_thread.found.connect(self._on_probe_found)
        self._probe_thread.not_found.connect(self._on_probe_not_found)
        self._probe_thread.error.connect(self._on_probe_error)
        self._probe_thread.finished.connect(self._on_probe_finished)
        self._probe_thread.start()

    def _on_probe_finished(self) -> None:
        thread = self._probe_thread
        self._probe_thread = None
        if thread is not None:
            thread.deleteLater()

    def _on_probe_found(self, port: str) -> None:
        self._set_status(f"Found GPS on Port: {port}", mode="gps")
        self._selected_port = port
        self._timer.stop()
        self.progress.setValue(max(75, self.progress.value()))
        QtCore.QTimer.singleShot(350, self._start_fix_wait)

    def _on_probe_not_found(self) -> None:
        pass

    def _on_probe_error(self, error: Exception) -> None:
        self._last_error = error
        self._set_status("GPS error; reconnecting...", mode="gps")

    def _start_fix_wait(self) -> None:
        if not self._selected_port:
            return
        if self._fix_thread and self._fix_thread.isRunning():
            return
        self._set_status("Acquiring GPS Signal...", mode="gps")
        self.progress.setValue(max(85, self.progress.value()))
        self._fix_thread = GPSFixWaitThread(self._selected_port, self)
        self._fix_thread.fix_acquired.connect(self._on_fix_acquired)
        self._fix_thread.connected_without_fix.connect(self._on_connected_without_fix)
        self._fix_thread.error.connect(self._on_fix_error)
        self._fix_thread.finished.connect(self._on_fix_wait_finished)
        self._fix_thread.start()

    def _on_fix_wait_finished(self) -> None:
        thread = self._fix_thread
        self._fix_thread = None
        if thread is not None:
            thread.deleteLater()

    def _on_fix_acquired(self) -> None:
        self._set_status("Signal acquired!", mode="gps")
        self.progress.setValue(100)
        QtCore.QTimer.singleShot(1000, self.accept)

    def _on_connected_without_fix(self) -> None:
        self._set_status("GPS connected; waiting for satellite fix in the main window.", mode="gps")
        self.progress.setValue(100)
        QtCore.QTimer.singleShot(1000, self.accept)

    def _on_fix_error(self, error: Exception) -> None:
        self._last_error = error
        self._selected_port = None
        self._remember_port = False
        self._set_status("No GPS data received. Choose the GPS port manually.", mode="gps")
        QtCore.QTimer.singleShot(500, self._open_wizard)

    def selected_port(self) -> Optional[str]:
        return self._selected_port

    def remember_port(self) -> bool:
        return bool(self._remember_port)

    def calibration(self) -> dict:
        return dict(self._calibration or {})

    def playback_only(self) -> bool:
        return bool(self._playback_only)

    def meshtastic_only(self) -> bool:
        return bool(self._meshtastic_only)

    def _set_status(self, text: str, mode: str) -> None:
        self.status_label.setText(text)
        self._set_status_icon(mode)

    def _set_status_icon(self, mode: str) -> None:
        if getattr(self, "_icon_mode", None) == mode and self._icon_movie is not None:
            # Keep existing animation running for same mode.
            return
        if self._icon_movie is not None:
            self._icon_movie.stop()
            self._icon_movie.deleteLater()
            self._icon_movie = None

        asset = _status_anim_for_mode(mode)
        if asset and os.path.exists(asset):
            processed_asset = _transparentize_gif(asset, allow_processing=True)
            movie = QtGui.QMovie(processed_asset)
            if movie.isValid():
                movie.setScaledSize(QtCore.QSize(LOADING_ICON_PX, LOADING_ICON_PX))
                self.icon_label.setMovie(movie)
                movie.start()
                self._icon_movie = movie
                self._icon_mode = mode
                return

        if mode == "calibrating":
            icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload)
        elif mode == "gps":
            icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DriveNetIcon)
        elif mode == "stopping":
            icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserStop)
        else:
            icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
        self.icon_label.setPixmap(icon.pixmap(LOADING_ICON_PX, LOADING_ICON_PX))
        self._icon_mode = mode


class CalibrationThread(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int, str)
    done = QtCore.pyqtSignal(dict)

    def __init__(self, frequency_mhz: float, gain: int, profile_name: str, parent=None):
        super().__init__(parent)
        self.frequency_mhz = frequency_mhz
        self.gain = gain
        self.profile_name = profile_name or "default"

    def run(self):
        calibration = {}
        profiles = _load_calibration_profiles()
        existing_profile = profiles.get(self.profile_name, {})
        devices = funcs.list_sdr_devices()
        total = len(devices)
        if total == 0:
            self.progress.emit(0, 0, "No SDRs detected. Skipping calibration.")
            self.done.emit(calibration)
            return

        baseline_strengths = {}
        ctx = multiprocessing.get_context("spawn")
        for idx, dev in enumerate(devices, start=1):
            serial_val = dev.get("serial")
            if serial_val:
                serial_text = str(serial_val)
            else:
                serial_text = str(dev.get("index") if dev.get("index") is not None else idx)
            self.progress.emit(idx - 1, total, f"Calibrating SDR #{serial_text}...")
            key = _device_key(dev.get("index"), dev.get("serial"))
            strength = None
            error_text = None
            try:
                queue = ctx.Queue()
                proc = ctx.Process(
                    target=_calibration_worker,
                    args=(dev.get("index"), self.frequency_mhz, self.gain, CALIBRATION_SAMPLE_SECONDS, queue),
                )
                proc.daemon = True
                proc.start()
                proc.join(CALIBRATION_TIMEOUT_S)
                if proc.is_alive():
                    proc.terminate()
                    proc.join(1)
                    error_text = f"Calibration timed out after {CALIBRATION_TIMEOUT_S:.0f}s"
                else:
                    try:
                        result = queue.get_nowait()
                    except Exception:
                        result = {"error": "No calibration data returned."}
                    if isinstance(result, dict) and result.get("error"):
                        error_text = result.get("error")
                    else:
                        strength = result.get("strength") if isinstance(result, dict) else None
            except Exception as e:
                error_text = str(e)
            if strength is not None:
                baseline_strengths[key] = strength
                calibration[key] = {
                    "offset": strength,
                    "scale": 1.0,
                    "baseline": strength,
                }
            else:
                fallback = existing_profile.get(key, {})
                calibration[key] = {
                    "offset": fallback.get("offset", 0),
                    "scale": fallback.get("scale", 1.0),
                    "baseline": fallback.get("baseline"),
                    "error": error_text or "Calibration failed.",
                }
            self.progress.emit(idx, total, f"Calibrated SDR #{serial_text}")

        # Normalize scales to the median baseline strength
        if baseline_strengths:
            baselines = [v for v in baseline_strengths.values() if v is not None]
            if baselines:
                target = float(np.median(baselines))
                for key, base in baseline_strengths.items():
                    if base and base > 0:
                        calibration[key]["scale"] = target / float(base)

        profiles[self.profile_name] = calibration
        _save_calibration_profiles(profiles)

        self.done.emit(calibration)

class FlaggedSlider(QtWidgets.QSlider):
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._flags = []
        self._total_time = 0.0
        self._last_tooltip = None
        self._marker_pixmap = None
        self._marker_size_px = 16
        self.setMouseTracking(True)

    def _get_marker_pixmap(self) -> Optional[QtGui.QPixmap]:
        if self._marker_pixmap is not None:
            return self._marker_pixmap
        if not MARKER_PNG or not os.path.exists(MARKER_PNG):
            self._marker_pixmap = None
            return None
        try:
            processed = _transparentize_png(MARKER_PNG)
            pix = QtGui.QPixmap(processed)
            if pix.isNull():
                self._marker_pixmap = None
                return None
            pix = pix.scaled(
                self._marker_size_px,
                self._marker_size_px,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            self._marker_pixmap = pix
            return pix
        except Exception:
            self._marker_pixmap = None
            return None

    def set_flags(self, flags: list[dict], total_time: float) -> None:
        self._flags = list(flags or [])
        try:
            self._total_time = max(0.0, float(total_time or 0.0))
        except Exception:
            self._total_time = 0.0
        self.update()

    def _marker_positions(self):
        if not self._flags or self._total_time <= 0:
            return []
        pix = self._get_marker_pixmap()
        if pix is None or pix.isNull():
            return []
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            opt,
            QtWidgets.QStyle.SubControl.SC_SliderGroove,
            self,
        )
        if groove.width() <= 0:
            return []
        markers = []
        for flag in self._flags:
            try:
                t = float(flag.get("t", 0.0) or 0.0)
            except Exception:
                t = 0.0
            frac = max(0.0, min(1.0, t / self._total_time))
            x = groove.left() + int(frac * groove.width()) - int(pix.width() / 2)
            top_space = groove.top()
            bottom_space = self.height() - groove.bottom()
            if bottom_space >= top_space:
                y = min(self.height() - pix.height() - 2, groove.bottom() + 2)
            else:
                y = max(2, groove.top() - pix.height() - 2)
            rect = QtCore.QRectF(x, y, pix.width(), pix.height())
            markers.append(
                {
                    "flag": flag,
                    "rect": rect,
                    "pix": pix,
                }
            )
        return markers

    def paintEvent(self, event):
        super().paintEvent(event)
        markers = self._marker_positions()
        if not markers:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        for marker in markers:
            rect = marker["rect"]
            pix = marker["pix"]
            painter.drawPixmap(rect.topLeft(), pix)

    def _tooltip_text(self, flag: dict) -> str:
        reason = str(flag.get("reason") or "Flag").strip()
        note = str(flag.get("note") or "").strip()
        t = flag.get("t", None)
        if t is None:
            return f"{reason}\n{note}".strip()
        return f"{reason} @ {self._fmt_time(t)}\n{note}".strip()

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        try:
            seconds = max(0, int(seconds))
        except Exception:
            seconds = 0
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        for marker in self._marker_positions():
            flag = marker["flag"]
            rect = marker["rect"].adjusted(-2, -2, 2, 2)
            if rect.contains(QtCore.QPointF(pos)):
                text = self._tooltip_text(flag)
                if text and text != self._last_tooltip:
                    QtWidgets.QToolTip.showText(self.mapToGlobal(pos), text, self)
                    self._last_tooltip = text
                return
        if self._last_tooltip:
            QtWidgets.QToolTip.hideText()
            self._last_tooltip = None
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._last_tooltip:
            QtWidgets.QToolTip.hideText()
            self._last_tooltip = None
        super().leaveEvent(event)

