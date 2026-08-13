"""
PINPOINT Software Project
addons/report_generator.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Implements the Report Generator add-on for building mission reports.
Provides a multi-tab UI and helpers for stats, maps, and PDF export.
---

https://nexus.crayton.dev/
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import html
import math
import os
import re
from io import BytesIO
from typing import Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets, QtPrintSupport
from PIL import Image

from pinpoint.plugin_api import AddonAction, AddonPlugin, PinpointAPI


def _avg(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _min_avg_max(values: List[float]) -> tuple[Optional[float], Optional[float], Optional[float]]:
    if not values:
        return None, None, None
    return min(values), _avg(values), max(values)


def _fmt_num(value: Optional[float], digits: int = 2, unit: str = "") -> str:
    if value is None:
        return "--"
    return f"{value:.{digits}f}{unit}"


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _paragraphs(value, fallback: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return f"<p>{_esc(fallback)}</p>" if fallback else ""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    return "".join(f"<p>{_esc(part).replace(chr(10), '<br/>')}</p>" for part in paragraphs)


def _duration_text(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    if seconds >= 3600:
        return f"{seconds / 3600.0:.1f} hours"
    if seconds >= 60:
        return f"{seconds / 60.0:.1f} minutes"
    return f"{seconds:.0f} seconds"


def _distance_m(lat1, lon1, lat2, lon2) -> float:
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = phi2 - phi1
    dlon = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlon / 2.0) ** 2
    return 6_371_000.0 * 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))


def _chunk_list(items: List[str], size: int) -> List[List[str]]:
    if not items:
        return []
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]


def _report_map_history(frames: List[Dict]) -> Dict:
    """Build map-renderer history from accepted fixes in a report session."""
    history = {}
    for frame_index, frame in enumerate(frames or []):
        telemetry = frame.get("telemetry") or {}
        if telemetry.get("cycle_paused"):
            continue
        gps_loc = telemetry.get("gps_loc")
        if not gps_loc:
            continue
        try:
            lat, lon = float(gps_loc[0]), float(gps_loc[1])
        except (TypeError, ValueError, IndexError):
            continue
        history[(lat, lon)] = {
            "strength": telemetry.get("strength") or 0,
            "quality": telemetry.get("quality") if telemetry.get("quality") is not None else 1.0,
            "snr": telemetry.get("snr"),
            "ts": telemetry.get("measurement_ts", frame.get("t", frame_index)),
        }
    return history


def _load_app_version_from_main() -> Optional[str]:
    try:
        from pinpoint import core as app_core

        return getattr(app_core, "APP_VERSION", None)
    except Exception:
        pass
    try:
        from pinpoint import version as app_version

        return getattr(app_version, "APP_VERSION", None)
    except Exception:
        pass
    try:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        version_path = os.path.join(base_dir, "pinpoint", "version.py")
        if os.path.exists(version_path):
            with open(version_path, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r'^\s*APP_VERSION\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
            if match:
                return match.group(1).strip()
    except Exception:
        return None
    return None


class ReportGeneratorDialog(QtWidgets.QDialog):
    def __init__(self, parent, data_provider):
        super().__init__(parent)
        self.setWindowTitle("Report Generator")
        self.setMinimumSize(900, 700)
        self.setModal(True)

        self._data_provider = data_provider
        self._data = self._data_provider() or {}
        self._frames = self._data.get("frames") or []
        self._source = self._data.get("source") or "Session"
        self._settings = self._data.get("settings") or {}
        self._map_png_b64 = self._data.get("map_png_b64")
        self._start_time = self._parse_start_time(self._data.get("start_time"))
        self._app_version = self._data.get("app_version") or _load_app_version_from_main() or "unknown"

        self._primary_color = QtGui.QColor("#0f766e")
        self._accent_color = QtGui.QColor("#334155")
        self._logo_cache = {}
        self._map_cache = {}
        self._stats_cache = None
        self._stats_cache_key = None
        self._cycles_cache = None
        self._cycles_cache_key = None
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(300)
        self._preview_timer.timeout.connect(self._sync_preview)

        self._build_ui()
        self._sync_preview()

    # ---------- UI ----------
    def _build_ui(self):
        self.tabs = QtWidgets.QTabWidget()

        # Summary tab
        summary_tab = QtWidgets.QWidget()
        summary_layout = QtWidgets.QFormLayout(summary_tab)
        self.title_input = QtWidgets.QLineEdit("PINPOINT Mission Report")
        self.mission_input = QtWidgets.QLineEdit(self._source)
        self.operator_input = QtWidgets.QLineEdit("")
        self.version_input = QtWidgets.QLineEdit(self._app_version)
        self.abstract_input = QtWidgets.QPlainTextEdit()
        self.remarks_input = QtWidgets.QPlainTextEdit()
        summary_layout.addRow("Report Title", self.title_input)
        summary_layout.addRow("Mission Name", self.mission_input)
        summary_layout.addRow("Operator / Agency", self.operator_input)
        summary_layout.addRow("Software Version", self.version_input)
        summary_layout.addRow("Summary / Abstract", self.abstract_input)
        summary_layout.addRow("Remarks / Purpose", self.remarks_input)

        # Sections tab
        sections_tab = QtWidgets.QWidget()
        sections_layout = QtWidgets.QVBoxLayout(sections_tab)
        self.section_summary = QtWidgets.QCheckBox("Include Summary")
        self.section_summary.setChecked(True)
        self.section_narrative = QtWidgets.QCheckBox("Include Automated Narrative")
        self.section_narrative.setChecked(True)
        self.section_cycles = QtWidgets.QCheckBox("Include Cycle-by-Cycle Summary")
        self.section_cycles.setChecked(True)
        self.section_overall = QtWidgets.QCheckBox("Include Overall Stats")
        self.section_overall.setChecked(True)
        self.section_per_antenna = QtWidgets.QCheckBox("Include Per-Antenna Stats")
        self.section_per_antenna.setChecked(True)
        self.section_gps = QtWidgets.QCheckBox("Include GPS Summary")
        self.section_gps.setChecked(True)
        self.section_map = QtWidgets.QCheckBox("Include Map Snapshot")
        self.section_map.setChecked(True)
        sections_layout.addWidget(self.section_summary)
        sections_layout.addWidget(self.section_narrative)
        sections_layout.addWidget(self.section_cycles)
        sections_layout.addWidget(self.section_overall)
        sections_layout.addWidget(self.section_per_antenna)
        sections_layout.addWidget(self.section_gps)
        sections_layout.addWidget(self.section_map)
        sections_layout.addStretch(1)

        cycle_row = QtWidgets.QHBoxLayout()
        self.cycle_len_input = QtWidgets.QSpinBox()
        self.cycle_len_input.setRange(1, 600)
        self.cycle_len_input.setValue(int(self._settings.get("collection_time") or 2))
        cycle_row.addWidget(QtWidgets.QLabel("Cycle Length (s)"))
        cycle_row.addWidget(self.cycle_len_input)
        cycle_row.addStretch(1)
        sections_layout.addLayout(cycle_row)

        # Appearance tab
        appearance_tab = QtWidgets.QWidget()
        appearance_layout = QtWidgets.QFormLayout(appearance_tab)
        self.logo_path = QtWidgets.QLineEdit(self._data.get("default_logo") or "")
        self.logo_browse = QtWidgets.QPushButton("Browse")
        self.logo_browse.clicked.connect(self._pick_logo)
        logo_row = QtWidgets.QHBoxLayout()
        logo_row.addWidget(self.logo_path)
        logo_row.addWidget(self.logo_browse)
        appearance_layout.addRow("Logo", logo_row)

        self.primary_color_btn = QtWidgets.QPushButton("Primary Color")
        self.primary_color_btn.clicked.connect(self._pick_primary_color)
        self.accent_color_btn = QtWidgets.QPushButton("Accent Color")
        self.accent_color_btn.clicked.connect(self._pick_accent_color)
        appearance_layout.addRow("Primary", self.primary_color_btn)
        appearance_layout.addRow("Accent", self.accent_color_btn)

        # Preview tab
        preview_tab = QtWidgets.QWidget()
        preview_layout = QtWidgets.QVBoxLayout(preview_tab)
        self.preview_browser = QtWidgets.QTextBrowser()
        preview_layout.addWidget(self.preview_browser, 1)

        preview_actions = QtWidgets.QHBoxLayout()
        self.preview_btn = QtWidgets.QPushButton("Preview PDF")
        self.export_btn = QtWidgets.QPushButton("Export PDF")
        preview_actions.addStretch(1)
        preview_actions.addWidget(self.preview_btn)
        preview_actions.addWidget(self.export_btn)
        preview_layout.addLayout(preview_actions)

        self.preview_btn.clicked.connect(self._open_preview_dialog)
        self.export_btn.clicked.connect(self._export_pdf)

        self.tabs.addTab(summary_tab, "Summary")
        self.tabs.addTab(sections_tab, "Sections")
        self.tabs.addTab(appearance_tab, "Appearance")
        self.tabs.addTab(preview_tab, "Preview")

        # Buttons
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.clicked.connect(self.close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.close_btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        # Hook changes to preview (debounced to avoid UI lag on large sessions)
        self.title_input.textChanged.connect(self._schedule_preview)
        self.mission_input.textChanged.connect(self._schedule_preview)
        self.operator_input.textChanged.connect(self._schedule_preview)
        self.version_input.textChanged.connect(self._schedule_preview)
        self.abstract_input.textChanged.connect(self._schedule_preview)
        self.remarks_input.textChanged.connect(self._schedule_preview)
        self.section_summary.stateChanged.connect(self._schedule_preview)
        self.section_narrative.stateChanged.connect(self._schedule_preview)
        self.section_cycles.stateChanged.connect(self._schedule_preview)
        self.section_overall.stateChanged.connect(self._schedule_preview)
        self.section_per_antenna.stateChanged.connect(self._schedule_preview)
        self.section_gps.stateChanged.connect(self._schedule_preview)
        self.section_map.stateChanged.connect(self._schedule_preview)
        self.cycle_len_input.valueChanged.connect(self._schedule_preview)

    # ---------- Data ----------
    def _frames_cache_key(self) -> tuple:
        if not self._frames:
            return (0, None)
        last_t = None
        try:
            last_t = self._frames[-1].get("t")
        except Exception:
            last_t = None
        return (len(self._frames), last_t)

    def _collect_stats(self) -> Dict:
        cache_key = self._frames_cache_key()
        if self._stats_cache_key == cache_key and self._stats_cache is not None:
            return self._stats_cache

        frames = self._frames
        strengths = []
        snrs = []
        qualities = []
        sats = []
        gps_fix = []
        gps_accuracy = []
        fusion_confidences = []
        coordinates = []
        sdr_connected = []
        sdr_error_frames = 0
        paused_frames = 0
        alert_frames = 0
        alert_counts: Dict[str, Dict] = {}
        bearing_sources: Dict[str, int] = {}
        antenna_stats: Dict[int, Dict[str, List[float]]] = {}

        for frame in frames:
            telemetry = frame.get("telemetry") or {}
            strength = telemetry.get("strength")
            snr = telemetry.get("snr")
            quality = telemetry.get("quality")
            if strength is not None:
                strengths.append(float(strength))
            if snr is not None:
                snrs.append(float(snr))
            if quality is not None:
                qualities.append(float(quality))

            if telemetry.get("sats") is not None:
                try:
                    sats.append(int(telemetry.get("sats")))
                except Exception:
                    pass
            if telemetry.get("gps_fix") is not None:
                gps_fix.append(bool(telemetry.get("gps_fix")))
            if telemetry.get("gps_accuracy_m") is not None:
                gps_accuracy.append(float(telemetry.get("gps_accuracy_m")))
            if telemetry.get("fusion_confidence") is not None:
                fusion_confidences.append(float(telemetry.get("fusion_confidence")))
            gps_loc = telemetry.get("gps_loc")
            if gps_loc:
                try:
                    coordinates.append((float(gps_loc[0]), float(gps_loc[1])))
                except (TypeError, ValueError, IndexError):
                    pass
            if telemetry.get("sdr_connected") is not None:
                sdr_connected.append(bool(telemetry.get("sdr_connected")))
            if telemetry.get("sdr_error"):
                sdr_error_frames += 1
            if telemetry.get("cycle_paused"):
                paused_frames += 1
            source = telemetry.get("bearing_source")
            if source:
                source_key = str(source).upper()
                bearing_sources[source_key] = bearing_sources.get(source_key, 0) + 1

            alerts = frame.get("alerts") or []
            if alerts:
                alert_frames += 1
            for alert in alerts:
                if not isinstance(alert, dict) or not alert.get("message"):
                    continue
                key = str(alert.get("key") or alert.get("message"))
                entry = alert_counts.setdefault(
                    key,
                    {
                        "message": str(alert.get("message")),
                        "severity": str(alert.get("severity") or "warning").lower(),
                        "frames": 0,
                    },
                )
                entry["frames"] += 1

            antenna_states = telemetry.get("antenna_states") or []
            for idx, st in enumerate(antenna_states):
                ant = antenna_stats.setdefault(idx, {"strengths": [], "snrs": []})
                if st.get("strength") is not None:
                    ant["strengths"].append(float(st.get("strength")))
                if st.get("snr") is not None:
                    ant["snrs"].append(float(st.get("snr")))

        overall = {
            "strength": _min_avg_max(strengths),
            "snr": _min_avg_max(snrs),
            "quality": _min_avg_max(qualities),
            "sats_avg": _avg(sats),
            "gps_fix_rate": (_avg([1.0 if f else 0.0 for f in gps_fix]) if gps_fix else None),
            "gps_accuracy_avg_m": _avg(gps_accuracy),
            "fusion_confidence_avg": _avg(fusion_confidences),
        }

        distance_traveled_m = 0.0
        for start, end in zip(coordinates, coordinates[1:]):
            try:
                distance_traveled_m += _distance_m(start[0], start[1], end[0], end[1])
            except (TypeError, ValueError, OverflowError):
                pass
        duration_s = 0.0
        if frames:
            try:
                duration_s = float(frames[-1].get("t", 0.0) or 0.0)
            except (TypeError, ValueError):
                pass

        per_ant = {}
        for idx, data in antenna_stats.items():
            per_ant[idx] = {
                "strength": _min_avg_max(data["strengths"]),
                "snr": _min_avg_max(data["snrs"]),
            }

        result = {
            "overall": overall,
            "per_ant": per_ant,
            "mission": {
                "samples": len(frames),
                "duration_s": duration_s,
                "coordinate_samples": len(coordinates),
                "first_coordinate": coordinates[0] if coordinates else None,
                "last_coordinate": coordinates[-1] if coordinates else None,
                "distance_traveled_m": distance_traveled_m,
                "sdr_connected_rate": _avg([1.0 if value else 0.0 for value in sdr_connected]),
                "sdr_error_frames": sdr_error_frames,
                "paused_frames": paused_frames,
                "alert_frames": alert_frames,
                "alert_counts": alert_counts,
                "bearing_sources": bearing_sources,
            },
        }
        self._stats_cache_key = cache_key
        self._stats_cache = result
        return result

    def _collect_cycles(self) -> List[Dict]:
        cycle_len = max(1, int(self.cycle_len_input.value()))
        cache_key = (self._frames_cache_key(), cycle_len)
        if self._cycles_cache_key == cache_key and self._cycles_cache is not None:
            return self._cycles_cache
        cycles: Dict[int, List[Dict]] = {}
        for frame in self._frames:
            t = frame.get("t", 0.0) or 0.0
            idx = int(math.floor(float(t) / cycle_len))
            cycles.setdefault(idx, []).append(frame)

        output = []
        for idx in sorted(cycles.keys()):
            frames = cycles[idx]
            strengths = []
            snrs = []
            gps_fix = []
            paused_samples = 0
            pause_reason = None
            for fr in frames:
                tele = fr.get("telemetry") or {}
                if tele.get("strength") is not None:
                    strengths.append(float(tele.get("strength")))
                if tele.get("snr") is not None:
                    snrs.append(float(tele.get("snr")))
                if tele.get("gps_fix") is not None:
                    gps_fix.append(bool(tele.get("gps_fix")))
                if tele.get("cycle_paused"):
                    paused_samples += 1
                    pause_reason = tele.get("pause_reason") or "Insufficient Movement, Paused Cycle"
            cycle_status = "Mapped"
            if paused_samples:
                cycle_status = pause_reason
                if paused_samples != len(frames):
                    cycle_status += f" ({paused_samples}/{len(frames)} samples)"
            output.append(
                {
                    "index": idx + 1,
                    "start_s": idx * cycle_len,
                    "end_s": (idx + 1) * cycle_len,
                    "strength_avg": _avg(strengths),
                    "snr_avg": _avg(snrs),
                    "gps_fix_rate": (_avg([1.0 if f else 0.0 for f in gps_fix]) if gps_fix else None),
                    "samples": len(frames),
                    "paused_samples": paused_samples,
                    "status": cycle_status,
                }
            )
        self._cycles_cache_key = cache_key
        self._cycles_cache = output
        return output

    def _infer_antenna_count(self) -> int:
        inferred = 0
        for frame in self._frames:
            tele = frame.get("telemetry") or {}
            if tele.get("antenna_states"):
                inferred = max(inferred, len(tele.get("antenna_states") or []))
            if tele.get("antenna_count") is not None:
                try:
                    inferred = max(inferred, int(tele.get("antenna_count")))
                except Exception:
                    pass
        if inferred:
            return inferred
        try:
            return int(self._settings.get("antenna_count") or 0)
        except Exception:
            return 0

    def _parse_start_time(self, value) -> Optional[datetime.datetime]:
        if not value:
            return None
        if isinstance(value, datetime.datetime):
            return self._to_local_time(value)
        if isinstance(value, str):
            try:
                # Handle trailing Z
                v = value.replace("Z", "+00:00")
                return self._to_local_time(datetime.datetime.fromisoformat(v))
            except Exception:
                return None
        return None

    @staticmethod
    def _to_local_time(dt: datetime.datetime) -> datetime.datetime:
        try:
            if dt.tzinfo is None:
                return dt
            return dt.astimezone()
        except Exception:
            return dt

    @staticmethod
    def _tz_label(dt: datetime.datetime) -> str:
        try:
            if dt.tzinfo:
                return dt.tzname() or "local"
        except Exception:
            pass
        return "local"

    def _build_narrative_html(self) -> str:
        if not self._frames:
            return "<p>No telemetry records were available for analysis.</p>"

        stats = self._collect_stats()
        cycles = self._collect_cycles()
        overall = stats.get("overall", {})
        per_ant = stats.get("per_ant", {})
        mission_stats = stats.get("mission", {})

        start_dt = self._start_time or datetime.datetime.now()
        duration_s = float(mission_stats.get("duration_s") or 0.0)
        end_dt = start_dt + datetime.timedelta(seconds=duration_s)

        freq = self._settings.get("frequency")
        gain = self._settings.get("gain")
        ctime = self._settings.get("collection_time")
        antenna_count = self._infer_antenna_count() or len(per_ant) or 0
        version_text = self.version_input.text().strip() or self._app_version

        strength_min, strength_avg, strength_max = overall.get("strength", (None, None, None))
        snr_min, snr_avg, snr_max = overall.get("snr", (None, None, None))
        quality_min, quality_avg, quality_max = overall.get("quality", (None, None, None))
        sats_avg = overall.get("sats_avg")
        gps_fix_rate = overall.get("gps_fix_rate")

        samples = len(self._frames)
        cycle_count = len(cycles)
        mapped_cycles = sum(1 for cycle in cycles if not cycle.get("paused_samples"))
        affected_cycles = cycle_count - mapped_cycles
        mission = self.mission_input.text().strip() or "Untitled Mission"
        operator = self.operator_input.text().strip() or "not identified"
        tz_label = self._tz_label(start_dt)
        configuration = []
        if freq is not None:
            configuration.append(f"{float(freq):.3f} MHz center frequency")
        if gain is not None:
            configuration.append(f"receiver gain {gain}")
        if ctime is not None:
            configuration.append(f"{ctime}-second collection cadence")
        if antenna_count:
            configuration.append(f"{antenna_count}-antenna array")
        config_text = ", ".join(configuration) or "the configuration documented in this report"

        overview = (
            f"Pinpoint version {_esc(version_text)} recorded mission activity for <strong>{_esc(mission)}</strong> "
            f"under the operator or agency designation <strong>{_esc(operator)}</strong>. Collection began on "
            f"{start_dt.strftime('%B %d, %Y')} at {start_dt.strftime('%H:%M:%S')} ({_esc(tz_label)}) and continued "
            f"for approximately {_duration_text(duration_s)}, ending at {end_dt.strftime('%H:%M:%S')}. "
            f"The system retained {samples:,} telemetry records grouped into {cycle_count:,} reporting cycles using {config_text}."
        )

        method = (
            "The system compared relative signal measurements across the antenna array to calculate an amplitude-derived direction. "
            "Where sufficient GPS history was available, Pinpoint also calculated a map-derived direction and combined the available "
            "estimates according to the configured confidence rules. Bearings and location estimates in this report are analytical "
            "outputs intended to support operational decision-making; they are not independent confirmation of a transmitter's identity or location."
        )

        sdr_rate = mission_stats.get("sdr_connected_rate")
        sdr_errors = int(mission_stats.get("sdr_error_frames") or 0)
        quality_parts = []
        if gps_fix_rate is not None:
            quality_parts.append(f"valid GPS fixes were available in {gps_fix_rate * 100.0:.1f}% of evaluated records")
        if sats_avg is not None:
            quality_parts.append(f"the receiver reported an average of {sats_avg:.1f} satellites")
        if sdr_rate is not None:
            quality_parts.append(f"SDR connectivity was reported in {sdr_rate * 100.0:.1f}% of evaluated records")
        quality_statement = "; ".join(quality_parts) or "not enough metadata was present to calculate GPS and SDR availability rates"
        cycle_statement = (
            f"{mapped_cycles:,} cycles were mapped without a movement pause"
            + (f", while {affected_cycles:,} cycles contained one or more movement-paused records" if affected_cycles else "")
        )
        data_quality = (
            f"Data-availability review found that {quality_statement}. {cycle_statement}. "
            f"The session contained {sdr_errors:,} records carrying an SDR error indication. These indicators should be considered "
            "when interpreting apparent changes in signal level or direction."
        )

        signal = (
            f"Recorded relative signal strength ranged from {_fmt_num(strength_min, 1)} to {_fmt_num(strength_max, 1)}, with a mean of "
            f"{_fmt_num(strength_avg, 1)}. Recorded signal-to-noise ratio ranged from {_fmt_num(snr_min, 2)} to "
            f"{_fmt_num(snr_max, 2)}, with a mean of {_fmt_num(snr_avg, 2)}. The mean quality score was "
            f"{_fmt_num(quality_avg, 2)}. Per-antenna statistics were available for {len(per_ant)} antenna channels."
        )

        source_counts = mission_stats.get("bearing_sources") or {}
        dominant_source = max(source_counts.items(), key=lambda item: item[1])[0] if source_counts else None
        bearing_frames = []
        for frame in reversed(self._frames):
            tele = frame.get("telemetry") or {}
            if tele.get("target_bearing") is not None:
                bearing_frames.append(tele)
                break
        if bearing_frames:
            final = bearing_frames[0]
            confidence = final.get("fusion_confidence")
            confidence_text = ""
            if confidence is not None:
                confidence_text = f" with a reported fusion confidence of {float(confidence):.2f}"
            conclusion = (
                f"The final available directional solution was {float(final['target_bearing']):.0f} degrees true, produced by the "
                f"{_esc(str(final.get('bearing_source') or 'unspecified').upper())} method{confidence_text}."
            )
        else:
            conclusion = "No stable directional solution was present in the final available telemetry records."
        if dominant_source:
            conclusion += f" Across the session, {_esc(dominant_source)} was the most frequently reported bearing source."
        conclusion += " Mission staff should review the map, cycle table, sensor-quality indicators, and operator remarks together before drawing conclusions."

        return (
            f"<div class='narrative-block'><h3>Mission overview</h3><p>{overview}</p></div>"
            f"<div class='narrative-block'><h3>Analytical method</h3><p>{method}</p></div>"
            f"<div class='narrative-block'><h3>Data quality</h3><p>{data_quality}</p></div>"
            f"<div class='narrative-block'><h3>Observed results</h3><p>{signal}</p></div>"
            f"<div class='narrative-block'><h3>Operational interpretation</h3><p>{conclusion}</p></div>"
        )

    # ---------- Report ----------
    def _build_html(self) -> str:
        generated_at = datetime.datetime.now().astimezone()
        now = generated_at.strftime("%B %d, %Y at %H:%M:%S %Z")
        stats = self._collect_stats()
        cycles = self._collect_cycles()

        primary = self._primary_color.name()
        accent = self._accent_color.name()

        logo_html = ""
        logo_b64 = self._get_logo_b64()
        if logo_b64:
            logo_html = f"<img class='logo' src='data:image/png;base64,{logo_b64}' />"

        map_html = ""
        map_b64 = self._get_map_b64()
        if self.section_map.isChecked() and map_b64:
            map_html = (
                "<div class='page-break'></div>"
                "<div class='section map-section block-keep'>"
                "<div class='eyebrow'>GEOSPATIAL OVERVIEW</div>"
                "<div class='map-title section-title'>Mission Map</div>"
                f"<div class='section-body map-wrap'><img class='map' width='680' src='data:image/png;base64,{map_b64}' /></div>"
                "<p class='caption'>Map points represent accepted collection locations. The green estimate and confidence area, when present, are analytical outputs and should be interpreted with the data-quality findings in this report.</p>"
                "</div>"
            )
        elif self.section_map.isChecked():
            map_html = """
            <div class='section block-keep'>
              <h2 class='section-title'>Mission Map</h2>
              <p class='note'><strong>Map unavailable:</strong> this session did not contain a usable mission-map image. Telemetry and cycle statistics remain available elsewhere in this report.</p>
            </div>
            """

        overall = stats.get("overall", {})
        mission_stats = stats.get("mission", {})
        duration_s = float(mission_stats.get("duration_s") or 0.0)
        paused_cycles = sum(1 for cycle in cycles if cycle.get("paused_samples"))
        mapped_cycles = max(0, len(cycles) - paused_cycles)
        gps_fix_rate = overall.get("gps_fix_rate")
        sdr_rate = mission_stats.get("sdr_connected_rate")
        gps_rate_text = "--" if gps_fix_rate is None else f"{gps_fix_rate * 100.0:.1f}%"
        sdr_rate_text = "--" if sdr_rate is None else f"{sdr_rate * 100.0:.1f}%"
        first_coordinate = mission_stats.get("first_coordinate")
        last_coordinate = mission_stats.get("last_coordinate")
        first_coordinate_text = (
            f"{first_coordinate[0]:.6f}, {first_coordinate[1]:.6f}" if first_coordinate else "--"
        )
        last_coordinate_text = (
            f"{last_coordinate[0]:.6f}, {last_coordinate[1]:.6f}" if last_coordinate else "--"
        )

        overview_html = f"""
        <div class='section block-keep executive-overview'>
          <div class='eyebrow'>MISSION AT A GLANCE</div>
          <h2 class='section-title'>Executive Overview</h2>
          <table class='metric-grid'>
            <tr>
              <td><span class='metric-label'>Duration</span><br/><span class='metric-value'>{_esc(_duration_text(duration_s))}</span></td>
              <td><span class='metric-label'>Telemetry Records</span><br/><span class='metric-value'>{int(mission_stats.get('samples') or 0):,}</span></td>
              <td><span class='metric-label'>GPS Fix Availability</span><br/><span class='metric-value'>{gps_rate_text}</span></td>
            </tr>
            <tr>
              <td><span class='metric-label'>SDR Availability</span><br/><span class='metric-value'>{sdr_rate_text}</span></td>
              <td><span class='metric-label'>Mapped Cycles</span><br/><span class='metric-value'>{mapped_cycles:,}</span></td>
              <td><span class='metric-label'>Cycles With Pauses</span><br/><span class='metric-value'>{paused_cycles:,}</span></td>
            </tr>
          </table>
        </div>
        """

        configuration_rows = [
            ("Center frequency", _fmt_num(self._settings.get("frequency"), 3, " MHz")),
            ("Receiver gain", "--" if self._settings.get("gain") is None else str(self._settings.get("gain"))),
            ("Collection cadence", _fmt_num(self._settings.get("collection_time"), 0, " s")),
            ("SDR sample window", _fmt_num(self._settings.get("sample_window_s"), 2, " s")),
            ("Antenna channels", str(self._infer_antenna_count() or "--")),
            ("Movement threshold", _fmt_num(self._settings.get("movement_threshold_m"), 1, " m")),
            ("Adaptive movement pause", "Enabled" if self._settings.get("adaptive_movement_pause") else "Disabled"),
            ("Confidence threshold", _fmt_num(self._settings.get("confidence_threshold"), 2)),
        ]
        configuration_html = "".join(
            f"<tr><th>{_esc(label)}</th><td>{_esc(value)}</td></tr>" for label, value in configuration_rows
        )
        mission_detail_html = f"""
        <div class='section block-keep'>
          <h2 class='section-title'>Mission and Configuration Record</h2>
          <table class='two-column-table'>
            <tr><th>Mission start</th><td>{_esc(self._start_time.strftime('%B %d, %Y %H:%M:%S %Z') if self._start_time else '--')}</td><th>Software</th><td>Pinpoint {_esc(self.version_input.text().strip() or self._app_version)}</td></tr>
            <tr><th>First recorded position</th><td>{first_coordinate_text}</td><th>Last recorded position</th><td>{last_coordinate_text}</td></tr>
            <tr><th>Approx. platform travel</th><td>{_fmt_num(mission_stats.get('distance_traveled_m'), 1, ' m')}</td><th>Reporting cycle</th><td>{self.cycle_len_input.value()} s</td></tr>
          </table>
          <table class='configuration-table'>
            <tr><th colspan='2'>Recorded Configuration</th></tr>
            {configuration_html}
          </table>
        </div>
        """

        overall_rows = ""
        if self.section_overall.isChecked():
            overall_rows = f"""
            <tr><th>Relative signal strength</th><td>{_fmt_num(overall.get("strength")[1], 1)}</td><td>{_fmt_num(overall.get("strength")[0], 1)}</td><td>{_fmt_num(overall.get("strength")[2], 1)}</td></tr>
            <tr><th>Signal-to-noise ratio</th><td>{_fmt_num(overall.get("snr")[1], 2, " dB")}</td><td>{_fmt_num(overall.get("snr")[0], 2, " dB")}</td><td>{_fmt_num(overall.get("snr")[2], 2, " dB")}</td></tr>
            <tr><th>Signal quality score</th><td>{_fmt_num(overall.get("quality")[1], 2)}</td><td>{_fmt_num(overall.get("quality")[0], 2)}</td><td>{_fmt_num(overall.get("quality")[2], 2)}</td></tr>
            <tr><th>Fusion confidence</th><td>{_fmt_num(overall.get("fusion_confidence_avg"), 2)}</td><td colspan='2'>Session mean</td></tr>
            """

        gps_html = ""
        if self.section_gps.isChecked():
            gps_html = f"""
            <div class='section block-keep'>
              <h2 class='section-title'>Positioning and Data Quality</h2>
              <div class='section-body'>
                <table class='quality-table'>
                  <tr><th>GPS fix availability</th><td>{gps_rate_text}</td><th>Average satellites</th><td>{_fmt_num(overall.get("sats_avg"), 1)}</td></tr>
                  <tr><th>Average estimated accuracy</th><td>{_fmt_num(overall.get("gps_accuracy_avg_m"), 1, " m")}</td><th>SDR availability</th><td>{sdr_rate_text}</td></tr>
                  <tr><th>Movement-paused records</th><td>{int(mission_stats.get("paused_frames") or 0):,}</td><th>SDR error records</th><td>{int(mission_stats.get("sdr_error_frames") or 0):,}</td></tr>
                </table>
                <p class='note'><strong>Interpretation:</strong> Reduced GPS or SDR availability can lower confidence in mapped positions and directional estimates. Movement-paused records remain part of the mission record but were intentionally excluded from map updates.</p>
              </div>
            </div>
            """

        per_ant_html = ""
        if self.section_per_antenna.isChecked():
            rows_list = []
            for idx in sorted(stats.get("per_ant", {}).keys()):
                vals = stats.get("per_ant", {}).get(idx, {})
                strength = vals.get("strength", (None, None, None))
                snr_vals = vals.get("snr", (None, None, None))
                rows_list.append(
                    "<tr>"
                    f"<th>A{idx + 1}</th>"
                    f"<td>{_fmt_num(strength[1], 1)}</td>"
                    f"<td>{_fmt_num(strength[0], 1)}</td>"
                    f"<td>{_fmt_num(strength[2], 1)}</td>"
                    f"<td>{_fmt_num(snr_vals[1], 2)}</td>"
                    f"<td>{_fmt_num(snr_vals[0], 2)}</td>"
                    f"<td>{_fmt_num(snr_vals[2], 2)}</td>"
                    "</tr>"
                )
            if not rows_list:
                per_ant_html = """
                <div class='section block-keep'>
                  <h2 class='section-title'>Antenna Performance</h2>
                  <div class='section-body'>
                    <table>
                      <tr><th>Antenna</th><th>Strength Avg</th><th>Strength Min</th><th>Strength Max</th><th>SNR Avg</th><th>SNR Min</th><th>SNR Max</th></tr>
                      <tr><td colspan='7'>No antenna data captured.</td></tr>
                    </table>
                  </div>
                </div>
                """
            else:
                chunks = _chunk_list(rows_list, 10)
                parts = []
                for i, chunk in enumerate(chunks):
                    title = "Antenna Performance" if i == 0 else "Antenna Performance (continued)"
                    page_break = "" if i == 0 else "<div class='page-break'></div>"
                    parts.append(
                        f"""
                        {page_break}
                        <div class='section block-keep'>
                          <h2 class='section-title'>{title}</h2>
                          <div class='section-body'>
                            <table>
                              <tr><th>Antenna</th><th>Strength Avg</th><th>Strength Min</th><th>Strength Max</th><th>SNR Avg</th><th>SNR Min</th><th>SNR Max</th></tr>
                              {''.join(chunk)}
                            </table>
                          </div>
                        </div>
                        """
                    )
                per_ant_html = "".join(parts)

        cycles_html = ""
        if self.section_cycles.isChecked():
            cycle_rows_list = []
            for c in cycles:
                gps_cycle_rate = c.get("gps_fix_rate")
                gps_cycle_text = "--" if gps_cycle_rate is None else f"{gps_cycle_rate * 100.0:.1f}%"
                status_text = str(c.get("status") or "--")
                if status_text.startswith("Insufficient Movement, Paused Cycle"):
                    suffix = status_text[len("Insufficient Movement, Paused Cycle"):]
                    status_text = f"Paused — insufficient movement{suffix}"
                row_class = "cycle-paused" if c.get("paused_samples") else "cycle-mapped"
                cycle_rows_list.append(
                    f"<tr class='{row_class}'>"
                    f"<th>{c['index']}</th>"
                    f"<td>{c['start_s']}–{c['end_s']} s</td>"
                    f"<td>{c['samples']}</td>"
                    f"<td>{_fmt_num(c['strength_avg'], 1)}</td>"
                    f"<td>{_fmt_num(c['snr_avg'], 2)}</td>"
                    f"<td>{gps_cycle_text}</td>"
                    f"<td>{_esc(status_text)}</td>"
                    "</tr>"
                )
            if not cycle_rows_list:
                cycles_html = """
                <div class='section block-keep'>
                  <h2 class='section-title'>Collection Cycle Record</h2>
                  <div class='section-body'>
                    <table>
                      <tr><th>Cycle</th><th>Window</th><th>Samples</th><th>Avg Strength</th><th>Avg SNR</th><th>GPS Fix %</th><th>Status</th></tr>
                      <tr><td colspan='7'>No cycle data available.</td></tr>
                    </table>
                  </div>
                </div>
                """
            else:
                # Keep one continuous table. Qt paginates it at natural page
                # boundaries; each cycle row is protected from page splitting.
                cycles_html = f"""
                <div class='page-break'></div>
                <div class='section cycle-section'>
                  <div class='eyebrow'>DETAILED TELEMETRY REVIEW</div>
                  <h2 class='section-title'>Collection Cycle Record</h2>
                  <p class='section-intro'>Each row summarizes telemetry recorded within the selected {self.cycle_len_input.value()}-second reporting window. A paused status means the record was retained but the map was not refreshed because the effective movement threshold was not met.</p>
                  <div class='section-body'>
                    <table class='cycle-table'>
                      <thead>
                      <tr><th width='8%'>Cycle</th><th width='14%'>Window</th><th width='10%'>Samples</th><th width='13%'>Avg Strength</th><th width='12%'>Avg SNR</th><th width='12%'>GPS Fix</th><th width='31%'>Status</th></tr>
                      </thead>
                      <tbody>
                      {''.join(cycle_rows_list)}
                      </tbody>
                    </table>
                  </div>
                </div>
                """

        summary_html = ""
        if self.section_summary.isChecked():
            abstract = self.abstract_input.toPlainText().strip()
            remarks = self.remarks_input.toPlainText().strip()
            abstract_html = _paragraphs(
                abstract,
                "This report documents the mission timeline, sensor availability, signal observations, directional estimates, and collection-cycle status recorded by Pinpoint.",
            )
            remarks_html = _paragraphs(remarks, "No operator remarks were entered for this report.")
            summary_html = f"""
            <div class='section summary-section'>
              <h2 class='section-title'>Purpose and Scope</h2>
              <div class='section-body'>
                {abstract_html}
                <div class='operator-note'><strong>Operator remarks</strong>{remarks_html}</div>
              </div>
            </div>
            """

        narrative_html = ""
        if self.section_narrative.isChecked():
            narrative_html = f"""
            <div class='section narrative-section'>
              <div class='eyebrow'>AUTOMATED ANALYTICAL SUMMARY</div>
              <h2 class='section-title'>Mission Narrative</h2>
              <div class='section-body'>{self._build_narrative_html()}</div>
            </div>
            """

        overall_html = ""
        if self.section_overall.isChecked():
            overall_html = f"""
            <div class='section block-keep'>
              <h2 class='section-title'>Signal and Direction-Finding Statistics</h2>
              <div class='section-body'>
                <table class='stats-table'>
                  <tr><th>Metric</th><th>Mean</th><th>Minimum</th><th>Maximum / Context</th></tr>
                  {overall_rows or "<tr><td colspan='4'>No signal data captured.</td></tr>"}
                </table>
                <p class='caption'>Signal strength and quality are relative Pinpoint processing values. SNR is reported in decibels where available.</p>
              </div>
            </div>
            """

        alert_html = ""
        alert_counts = mission_stats.get("alert_counts") or {}
        if alert_counts:
            alert_rows = []
            for item in sorted(alert_counts.values(), key=lambda value: (value.get("severity", ""), value.get("message", ""))):
                severity = str(item.get("severity") or "warning").upper()
                alert_rows.append(
                    f"<tr><td><span class='severity severity-{_esc(severity.lower())}'>{_esc(severity)}</span></td>"
                    f"<td>{_esc(item.get('message'))}</td><td>{int(item.get('frames') or 0):,}</td></tr>"
                )
            alert_html = f"""
            <div class='section block-keep'>
              <h2 class='section-title'>Recorded Field Alerts</h2>
              <table class='alerts-table'>
                <tr><th>Severity</th><th>Alert shown to field team</th><th>Frames visible</th></tr>
                {''.join(alert_rows)}
              </table>
              <p class='caption'>Counts represent recorded telemetry frames on which each post-debounce alert was visible; they are not a count of distinct incidents.</p>
            </div>
            """

        css = f"""
          @page {{ size: A4; margin: 18mm 18mm 20mm 18mm; }}
          body {{ font-family: Arial, Helvetica, sans-serif; color: #1e293b; line-height: 1.38; font-size: 9.5pt; }}
          h1, h2 {{ color: {primary}; }}
          h1 {{ margin: 2px 0 5px 0; font-size: 22pt; font-weight: 700; letter-spacing: 0.2px; }}
          h2 {{ margin: 8px 0 5px 0; font-size: 13.5pt; page-break-after: avoid; break-after: avoid; }}
          h3 {{ margin: 8px 0 1px 0; color: {accent}; font-size: 10pt; text-transform: uppercase; letter-spacing: 0.3px; page-break-after: avoid; }}
          p {{ margin: 3px 0 7px 0; }}
          .header {{ margin-bottom: 12px; padding-bottom: 10px; text-align: left; border-bottom: 3px solid {primary}; }}
          .header-table {{ width: 100%; border: 0; margin: 0; }}
          .header-table td {{ border: 0; padding: 0; vertical-align: middle; }}
          .header-logo {{ width: 30%; text-align: left; }}
          .logo {{ max-width: 190px; max-height: 82px; width: auto; height: auto; object-fit: contain; }}
          .header-copy {{ text-align: right; }}
          .header-meta {{ text-align: right; margin-top: 1px; }}
          .document-label {{ color: {accent}; font-size: 8pt; font-weight: 700; letter-spacing: 1px; }}
          .section {{ margin-top: 10px; }}
          .section-title {{ font-size: 13.5pt; font-weight: 700; color: {primary}; margin: 5px 0 5px 0; padding-bottom: 3px; border-bottom: 1px solid #cbd5e1; page-break-after: avoid; break-after: avoid; }}
          .section-body {{}}
          .block-keep {{ page-break-inside: avoid; break-inside: avoid; }}
          .page-break {{ page-break-before: always; break-before: page; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 5px; }}
          th, td {{ border: 1px solid #cbd5e1; padding: 5px 7px; font-size: 8.5pt; vertical-align: top; }}
          th {{ background: #f1f5f9; color: {accent}; text-align: left; font-weight: 700; }}
          tr {{ page-break-inside: avoid; break-inside: avoid; }}
          thead {{ display: table-header-group; }}
          .meta {{ color: #475569; font-size: 8.5pt; }}
          .eyebrow {{ color: {accent}; font-size: 7.5pt; font-weight: 700; letter-spacing: 1px; margin-bottom: 1px; }}
          .metric-grid {{ margin-top: 5px; page-break-inside: avoid; }}
          .metric-grid td {{ width: 33.33%; background: #f8fafc; border-color: #dbe3ea; padding: 8px; }}
          .metric-label {{ color: #64748b; font-size: 7.5pt; text-transform: uppercase; letter-spacing: 0.4px; }}
          .metric-value {{ color: {primary}; font-size: 14pt; font-weight: 700; }}
          .two-column-table th {{ width: 18%; }}
          .two-column-table td {{ width: 32%; }}
          .configuration-table {{ margin-top: 8px; }}
          .configuration-table th {{ width: 42%; }}
          .quality-table th {{ width: 28%; }}
          .cycle-table {{ table-layout: fixed; page-break-inside: auto; break-inside: auto; }}
          .cycle-table th, .cycle-table td {{ font-size: 7.7pt; padding: 4px 5px; }}
          .cycle-table tr {{ page-break-inside: avoid; break-inside: avoid; }}
          .cycle-table th:nth-child(1) {{ width: 9%; }}
          .cycle-table th:nth-child(2) {{ width: 14%; }}
          .cycle-table th:nth-child(3) {{ width: 9%; }}
          .cycle-table th:nth-child(7) {{ width: 29%; }}
          .cycle-paused td, .cycle-paused th {{ background: #fffbeb; color: #854d0e; }}
          .section-intro {{ color: #475569; font-size: 8.3pt; margin: 2px 0 6px 0; }}
          .operator-note {{ margin-top: 8px; padding: 7px 9px; background: #f8fafc; border-left: 3px solid {primary}; }}
          .operator-note p {{ margin-bottom: 2px; }}
          .narrative-block {{ page-break-inside: avoid; break-inside: avoid; }}
          .note {{ padding: 6px 8px; background: #f8fafc; border-left: 3px solid #94a3b8; color: #475569; }}
          .caption {{ color: #64748b; font-size: 7.5pt; line-height: 1.25; margin-top: 4px; }}
          .severity {{ font-size: 7pt; font-weight: 700; }}
          .severity-error {{ color: #b91c1c; }}
          .severity-warning {{ color: #a16207; }}
          .severity-info {{ color: #15803d; }}
          .severity-debug {{ color: #0369a1; }}
          .map {{ display: block; margin: 5px auto 0; max-width: 100%; height: auto; border: 1px solid #cbd5e1; }}
          .map-title {{ font-size: 13.5pt; font-weight: 700; color: {primary}; margin: 5px 0; display: block; page-break-after: avoid; break-after: avoid; }}
          .map-wrap {{ display: block; page-break-inside: avoid; break-inside: avoid; }}
          img {{ page-break-inside: avoid; }}
        """

        sections = []
        self._append_sections(sections, overview_html)
        self._append_sections(sections, summary_html)
        self._append_sections(sections, mission_detail_html)
        self._append_sections(sections, narrative_html)
        self._append_sections(sections, overall_html)
        self._append_sections(sections, gps_html)
        self._append_sections(sections, alert_html)
        self._append_sections(sections, per_ant_html)
        self._append_sections(sections, map_html)
        self._append_sections(sections, cycles_html)

        header_html = (
            f"<div class=\"header\">"
            f"<table class=\"header-table\"><tr>"
            f"<td class=\"header-logo\">{logo_html}</td>"
            f"<td class=\"header-copy\">"
            f"<div class=\"document-label\">PINPOINT DIRECTION-FINDING • MISSION RECORD</div>"
            f"<h1>{_esc(self.title_input.text().strip() or 'Mission Report')}</h1>"
            f"<div class=\"header-meta meta\"><strong>Mission:</strong> {_esc(self.mission_input.text().strip() or '--')}</div>"
            f"<div class=\"header-meta meta\"><strong>Operator / Agency:</strong> {_esc(self.operator_input.text().strip() or '--')}</div>"
            f"<div class=\"header-meta meta\"><strong>Generated:</strong> {_esc(now)}</div>"
            f"</td></tr></table>"
            f"</div>"
        )
        header_height = self._measure_html_height(header_html, css, self._page_metrics()[0])
        body_html = self._paginate_sections(sections, css, start_height=header_height)

        html = f"""
        <html>
        <head>
        <style>
          {css}
        </style>
        </head>
        <body>
          {header_html}
          {body_html}
        </body>
        </html>
        """
        return html

    def _append_sections(self, sections: List[dict], html: str):
        if not html:
            return
        parts = html.split("<div class='page-break'></div>")
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            sections.append({"html": part, "force_break": i > 0})

    def _paginate_sections(self, sections: List[dict], css: str, start_height: float = 0.0) -> str:
        width_pt, height_pt = self._page_metrics()
        current = max(0.0, float(start_height))
        if height_pt > 0 and current > height_pt:
            current = current % height_pt
        out = []
        for section in sections:
            if section.get("force_break"):
                out.append("<div class='page-break'></div>")
                current = 0.0
            h = self._measure_html_height(section.get("html", ""), css, width_pt)
            if current > 0 and h <= height_pt and (current + h) > height_pt:
                out.append("<div class='page-break'></div>")
                current = 0.0
            elif current > 0 and h > height_pt:
                out.append("<div class='page-break'></div>")
                current = 0.0
            out.append(section.get("html", ""))
            if h >= height_pt:
                current = h % height_pt
            else:
                current += h
        return "".join(out)

    def _page_metrics(self) -> tuple[float, float]:
        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.PrinterMode.HighResolution)
        printer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
        # PyQt6 expects QMarginsF + QPageLayout.Unit.
        printer.setPageMargins(
            QtCore.QMarginsF(18, 18, 18, 20),
            QtGui.QPageLayout.Unit.Millimeter,
        )
        rect = printer.pageRect(QtPrintSupport.QPrinter.Unit.Point)
        return float(rect.width()), float(rect.height())

    def _measure_html_height(self, html: str, css: str, width_pt: float) -> float:
        doc = QtGui.QTextDocument()
        doc.setDocumentMargin(0)
        doc.setPageSize(QtCore.QSizeF(width_pt, 1e6))
        doc.setHtml(f"<html><head><style>{css}</style></head><body>{html}</body></html>")
        return float(doc.size().height())

    def _get_logo_b64(self) -> Optional[str]:
        logo_path = self.logo_path.text().strip()
        if not logo_path or not os.path.exists(logo_path):
            return None
        mtime = os.path.getmtime(logo_path)
        cached = self._logo_cache
        if cached.get("path") == logo_path and cached.get("mtime") == mtime:
            return cached.get("b64")
        b64 = _encode_image_path(logo_path, 320, 140)
        self._logo_cache = {"path": logo_path, "mtime": mtime, "b64": b64}
        return b64

    def _get_map_b64(self) -> Optional[str]:
        if self._map_png_b64:
            key = hashlib.md5(self._map_png_b64.encode("ascii")).hexdigest()  # nosec
            cache_key = ("embedded", key)
            if cache_key in self._map_cache:
                cached_b64 = self._map_cache.get(cache_key)
                if cached_b64:
                    return cached_b64
            else:
                try:
                    data = base64.b64decode(self._map_png_b64, validate=True)
                except Exception:
                    data = None
                if data and not self._matches_default_logo(data):
                    b64 = _encode_image_bytes(data, 720, 420)
                    self._map_cache[cache_key] = b64
                    if b64:
                        return b64
                self._map_cache[cache_key] = None

        map_path = self._data.get("map_path")
        if map_path and os.path.exists(map_path):
            mtime = os.path.getmtime(map_path)
            cache_key = ("path", map_path, mtime)
            if cache_key in self._map_cache:
                cached_b64 = self._map_cache.get(cache_key)
                if cached_b64:
                    return cached_b64
            else:
                try:
                    with open(map_path, "rb") as stream:
                        map_data = stream.read()
                except OSError:
                    map_data = None
                if map_data and not self._matches_default_logo(map_data):
                    b64 = _encode_image_bytes(map_data, 720, 420)
                    self._map_cache[cache_key] = b64
                    if b64:
                        return b64
                self._map_cache[cache_key] = None

        # Interactive-map sessions may never create a new legacy map.png. Build
        # a self-contained mission plot from accepted recorded fixes so a report
        # still has a map when neither image source above is usable.
        cache_key = ("telemetry", self._frames_cache_key())
        if cache_key in self._map_cache:
            return self._map_cache.get(cache_key)
        history = _report_map_history(self._frames)
        if history:
            try:
                from funcs.map import renderOfflineMapBytes

                map_data = renderOfflineMapBytes(history, size=(960, 600))
                b64 = _encode_image_bytes(map_data, 720, 450) if map_data else None
                self._map_cache[cache_key] = b64
                return b64
            except Exception:
                pass
        self._map_cache[cache_key] = None
        return None

    def _matches_default_logo(self, image_data: bytes) -> bool:
        logo_path = self._data.get("default_logo")
        if not logo_path or not os.path.exists(logo_path):
            return False
        try:
            with open(logo_path, "rb") as stream:
                logo_data = stream.read()
            image_sig = _image_signature(image_data)
            logo_sig = _image_signature(logo_data)
            return image_sig is not None and image_sig == logo_sig
        except OSError:
            return False

    # ---------- Preview / Export ----------
    def _sync_preview(self):
        html = self._build_html()
        self.preview_browser.setHtml(html)

    def _schedule_preview(self):
        if self._preview_timer.isActive():
            self._preview_timer.stop()
        self._preview_timer.start()

    def _build_document(self) -> QtGui.QTextDocument:
        doc = QtGui.QTextDocument()
        doc.setHtml(self._build_html())
        return doc

    def _open_preview_dialog(self):
        doc = self._build_document()
        preview = QtPrintSupport.QPrintPreviewDialog(self)
        preview.setWindowTitle("Report Preview")
        preview.paintRequested.connect(lambda printer: self._print_doc(doc, printer))
        preview.exec()

    def _export_pdf(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Export Report",
            "pinpoint_report.pdf",
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QtPrintSupport.QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        doc = self._build_document()
        self._print_doc(doc, printer)

    @staticmethod
    def _print_doc(doc: QtGui.QTextDocument, printer: QtPrintSupport.QPrinter) -> None:
        printer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
        printer.setPageMargins(
            QtCore.QMarginsF(18, 18, 18, 20),
            QtGui.QPageLayout.Unit.Millimeter,
        )
        if hasattr(doc, "print"):
            doc.print(printer)  # PyQt6
        else:
            doc.print_(printer)  # PyQt5 fallback

    # ---------- Appearance ----------
    def _pick_logo(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Select Logo",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if path:
            self.logo_path.setText(path)
            self._sync_preview()

    def _pick_primary_color(self):
        color = QtWidgets.QColorDialog.getColor(self._primary_color, self, "Select Primary Color")
        if color.isValid():
            self._primary_color = color
            self._sync_preview()

    def _pick_accent_color(self):
        color = QtWidgets.QColorDialog.getColor(self._accent_color, self, "Select Accent Color")
        if color.isValid():
            self._accent_color = color
            self._sync_preview()


def _encode_image_bytes(data: bytes, max_w: int, max_h: int) -> Optional[str]:
    try:
        resized = _resize_image_bytes(data, max_w, max_h)
        return base64.b64encode(resized).decode("ascii")
    except Exception:
        return None


def _encode_image_path(path: str, max_w: int, max_h: int) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            data = f.read()
        return _encode_image_bytes(data, max_w, max_h)
    except Exception:
        return None


def _resize_image_bytes(data: bytes, max_w: int, max_h: int) -> bytes:
    try:
        with Image.open(BytesIO(data)) as img:
            img = img.convert("RGBA")
            img.thumbnail((max_w, max_h), Image.LANCZOS)
            out = BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return data


def _image_signature(data: bytes) -> Optional[str]:
    try:
        with Image.open(BytesIO(data)) as image:
            normalized = image.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR)
            return hashlib.sha256(normalized.tobytes()).hexdigest()
    except Exception:
        return None


def _report_available(api: PinpointAPI) -> bool:
    return bool(api.call("data.report_available").get("available"))


def _open_report_generator(api: PinpointAPI) -> None:
    if not _report_available(api):
        api.call(
            "ui.show_message",
            {
                "title": "Report Unavailable",
                "message": "Stop data collection and ensure a session is cached before generating a report.",
                "level": "info",
            },
        )
        return
    parent = api.call("ui.get_main_window").get("window")
    dlg = ReportGeneratorDialog(parent, lambda: api.call("data.get_report_data").get("data") or {})
    dlg.exec()


def plugin_entry(api: PinpointAPI) -> AddonPlugin:
    return AddonPlugin(
        id="report_generator",
        name="Report Generator",
        version="2.0.1",
        description="Generate publication-ready mission reports from cached sessions.",
        menu=[
            AddonAction(
                id="generate_report",
                label="Generate Report...",
                handler=_open_report_generator,
                enabled=_report_available,
            )
        ],
    )
