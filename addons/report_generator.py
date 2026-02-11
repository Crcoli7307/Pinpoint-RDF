"""Report generator add-on."""

from __future__ import annotations

import base64
import datetime
import hashlib
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


def _chunk_list(items: List[str], size: int) -> List[List[str]]:
    if not items:
        return []
    size = max(1, int(size))
    return [items[i:i + size] for i in range(0, len(items), size)]


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
        }

        per_ant = {}
        for idx, data in antenna_stats.items():
            per_ant[idx] = {
                "strength": _min_avg_max(data["strengths"]),
                "snr": _min_avg_max(data["snrs"]),
            }

        result = {"overall": overall, "per_ant": per_ant}
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
            for fr in frames:
                tele = fr.get("telemetry") or {}
                if tele.get("strength") is not None:
                    strengths.append(float(tele.get("strength")))
                if tele.get("snr") is not None:
                    snrs.append(float(tele.get("snr")))
                if tele.get("gps_fix") is not None:
                    gps_fix.append(bool(tele.get("gps_fix")))
            output.append(
                {
                    "index": idx + 1,
                    "start_s": idx * cycle_len,
                    "end_s": (idx + 1) * cycle_len,
                    "strength_avg": _avg(strengths),
                    "snr_avg": _avg(snrs),
                    "gps_fix_rate": (_avg([1.0 if f else 0.0 for f in gps_fix]) if gps_fix else None),
                    "samples": len(frames),
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
            return "<p>No telemetry data was captured for this session.</p>"

        stats = self._collect_stats()
        cycles = self._collect_cycles()
        overall = stats.get("overall", {})
        per_ant = stats.get("per_ant", {})

        start_dt = self._start_time or datetime.datetime.now()
        duration_s = 0.0
        try:
            duration_s = float(self._frames[-1].get("t", 0.0) or 0.0)
        except Exception:
            duration_s = 0.0
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
        cycle_len = max(1, int(self.cycle_len_input.value()))
        cycle_count = len(cycles)

        last_telemetry = self._frames[-1].get("telemetry", {}) if self._frames else {}
        last_bearing = last_telemetry.get("target_bearing")
        last_source = last_telemetry.get("bearing_source") or "--"
        last_relative = last_telemetry.get("target_relative")

        remarks = self.remarks_input.toPlainText().strip()
        mission = self.mission_input.text().strip() or "this mission"

        tz_label = self._tz_label(start_dt)
        p1 = (
            f"On {start_dt.strftime('%B %d, %Y')} at {start_dt.strftime('%H:%M')} hours ({tz_label}), "
            f"the Pinpoint Direction-Finding Software {version_text} began collecting radio samples for {mission}. "
            f"The system was configured at {freq:.3f} MHz" if freq else
            f"On {start_dt.strftime('%B %d, %Y')} at {start_dt.strftime('%H:%M')} hours ({tz_label}), "
            f"the Pinpoint Direction-Finding Software {version_text} began collecting radio samples for {mission}."
        )
        if freq:
            extras = []
            if gain is not None:
                extras.append(f"gain set to {gain}")
            if ctime is not None:
                extras.append(f"a {ctime}s collection cadence")
            if extras:
                p1 += " with " + " and ".join(extras) + "."
        if antenna_count:
            p1 += f" The array used {antenna_count} antenna(s) during this session."

        if remarks:
            p1 += f" Mission purpose noted by the operator: {remarks}"

        aoa_w = self._settings.get("fusion_aoa_weight")
        map_w = self._settings.get("fusion_map_weight")
        conf_th = self._settings.get("confidence_threshold")
        p2 = (
            "During operation, the software ingests samples from software-defined radios (SDRs), "
            "computes relative signal strengths per antenna, and estimates an angle of arrival using a weighted vector method. "
            "When GPS fixes are available, it calculates a map-based bearing from position history and fuses this with the RF estimate "
            "using confidence weighting to provide a stabilized target bearing. "
        )
        weights = []
        if aoa_w is not None:
            weights.append(f"AoA weight {aoa_w}")
        if map_w is not None:
            weights.append(f"Map weight {map_w}")
        if conf_th is not None:
            weights.append(f"confidence threshold {conf_th}")
        if weights:
            p2 += "Fusion configuration: " + ", ".join(weights) + "."

        duration_text = f"{duration_s/60.0:.1f} minutes" if duration_s >= 60 else f"{duration_s:.0f} seconds"
        sdr_ok = 0
        sdr_err = 0
        for frame in self._frames:
            tele = frame.get("telemetry") or {}
            if tele.get("sdr_connected") is True:
                sdr_ok += 1
            if tele.get("sdr_error"):
                sdr_err += 1
        sdr_rate = (sdr_ok / samples) if samples else None

        p3 = (
            f"The session ran for approximately {duration_text}, producing {samples} samples across {cycle_count} cycle(s) "
            f"with a cycle length of {cycle_len} seconds. "
        )
        if gps_fix_rate is not None:
            p3 += f"GPS fix availability averaged {_fmt_num(gps_fix_rate * 100.0, 1)}%, "
        if sats_avg is not None:
            p3 += f"with an average of {_fmt_num(sats_avg, 1)} satellites in view. "
        if sdr_rate is not None:
            p3 += f"SDR connectivity was stable for {_fmt_num(sdr_rate * 100.0, 1)}% of samples"
        if sdr_err:
            p3 += f", with {sdr_err} logged SDR error event(s)."
        else:
            p3 += "."

        p4 = (
            f"Signal metrics observed during the mission included a maximum relative strength of {_fmt_num(strength_max, 1)}, "
            f"a minimum of {_fmt_num(strength_min, 1)}, and an average of {_fmt_num(strength_avg, 1)}. "
            f"SNR ranged from {_fmt_num(snr_min, 2)} to {_fmt_num(snr_max, 2)} with an average of {_fmt_num(snr_avg, 2)}. "
            f"Overall quality measurements averaged {_fmt_num(quality_avg, 2)}."
        )
        if per_ant:
            p4 += f" Per-antenna statistics were computed for {len(per_ant)} antenna(s), providing localized strength and SNR trends."

        source_counts = {}
        for frame in self._frames:
            tele = frame.get("telemetry") or {}
            src = tele.get("bearing_source")
            if src:
                source_counts[str(src).upper()] = source_counts.get(str(src).upper(), 0) + 1
        dominant_source = None
        if source_counts:
            dominant_source = max(source_counts.items(), key=lambda x: x[1])[0]

        if last_bearing is not None:
            rel_text = "--" if last_relative is None else f"{last_relative:.0f} deg"
            p5 = (
                f"At the end of the session, the system reported a target bearing of {last_bearing:.0f} deg "
                f"with a relative offset of {rel_text} and a source classification of {str(last_source).upper()}."
            )
        else:
            p5 = (
                "At the end of the session, the system did not report a stable target bearing; "
                "operators are advised to consult detailed cycle data and per-antenna metrics for additional context."
            )
        if dominant_source:
            p5 += f" The most frequently used bearing source during the mission was {dominant_source}."
        if duration_s > 0:
            p5 += f" Collection concluded at approximately {end_dt.strftime('%H:%M')} hours ({tz_label})."

        return f"<p>{p1}</p><p>{p2}</p><p>{p3}</p><p>{p4}</p><p>{p5}</p>"

    # ---------- Report ----------
    def _build_html(self) -> str:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                "<div class='section map-section block-keep'>"
                "<div class='map-title section-title'>Map Snapshot</div>"
                f"<div class='section-body map-wrap'><img class='map' width='680' src='data:image/png;base64,{map_b64}' /></div>"
                "</div>"
            )

        overall = stats.get("overall", {})
        overall_rows = ""
        if self.section_overall.isChecked():
            overall_rows = f"""
            <tr><th>Signal Strength</th><td>{_fmt_num(overall.get("strength")[1], 1)}</td><td>{_fmt_num(overall.get("strength")[0], 1)}</td><td>{_fmt_num(overall.get("strength")[2], 1)}</td></tr>
            <tr><th>SNR</th><td>{_fmt_num(overall.get("snr")[1], 2)}</td><td>{_fmt_num(overall.get("snr")[0], 2)}</td><td>{_fmt_num(overall.get("snr")[2], 2)}</td></tr>
            <tr><th>Quality</th><td>{_fmt_num(overall.get("quality")[1], 2)}</td><td>{_fmt_num(overall.get("quality")[0], 2)}</td><td>{_fmt_num(overall.get("quality")[2], 2)}</td></tr>
            """

        gps_html = ""
        if self.section_gps.isChecked():
            gps_html = f"""
            <div class='section block-keep'>
              <h2 class='section-title'>GPS Summary</h2>
              <div class='section-body'>
                <p>Average satellites: {_fmt_num(overall.get("sats_avg"), 1)}<br/>
                Fix rate: {_fmt_num((overall.get("gps_fix_rate") or 0) * 100.0, 1)}%</p>
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
                  <h2 class='section-title'>Per-Antenna Stats</h2>
                  <div class='section-body'>
                    <table>
                      <tr><th>Antenna</th><th>Strength Avg</th><th>Strength Min</th><th>Strength Max</th><th>SNR Avg</th><th>SNR Min</th><th>SNR Max</th></tr>
                      <tr><td colspan='7'>No antenna data captured.</td></tr>
                    </table>
                  </div>
                </div>
                """
            else:
                chunks = _chunk_list(rows_list, 12)
                parts = []
                for i, chunk in enumerate(chunks):
                    title = "Per-Antenna Stats" if i == 0 else "Per-Antenna Stats (cont.)"
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
                cycle_rows_list.append(
                    "<tr>"
                    f"<th>Cycle {c['index']}</th>"
                    f"<td>{c['start_s']}s - {c['end_s']}s</td>"
                    f"<td>{c['samples']}</td>"
                    f"<td>{_fmt_num(c['strength_avg'], 1)}</td>"
                    f"<td>{_fmt_num(c['snr_avg'], 2)}</td>"
                    f"<td>{_fmt_num((c['gps_fix_rate'] or 0) * 100.0, 1)}%</td>"
                    "</tr>"
                )
            if not cycle_rows_list:
                cycles_html = """
                <div class='section block-keep'>
                  <h2 class='section-title'>Cycle Summary</h2>
                  <div class='section-body'>
                    <table>
                      <tr><th>Cycle</th><th>Window</th><th>Samples</th><th>Avg Strength</th><th>Avg SNR</th><th>GPS Fix %</th></tr>
                      <tr><td colspan='6'>No cycle data available.</td></tr>
                    </table>
                  </div>
                </div>
                """
            else:
                chunks = _chunk_list(cycle_rows_list, 24)
                parts = []
                for i, chunk in enumerate(chunks):
                    title = "Cycle Summary" if i == 0 else "Cycle Summary (cont.)"
                    page_break = "" if i == 0 else "<div class='page-break'></div>"
                    parts.append(
                        f"""
                        {page_break}
                        <div class='section block-keep'>
                          <h2 class='section-title'>{title}</h2>
                          <div class='section-body'>
                            <table>
                              <tr><th>Cycle</th><th>Window</th><th>Samples</th><th>Avg Strength</th><th>Avg SNR</th><th>GPS Fix %</th></tr>
                              {''.join(chunk)}
                            </table>
                          </div>
                        </div>
                        """
                    )
                cycles_html = "".join(parts)

        summary_html = ""
        if self.section_summary.isChecked():
            summary_html = f"""
            <div class='section block-keep'>
              <h2 class='section-title'>Summary</h2>
              <div class='section-body'>
                <p>{self.abstract_input.toPlainText().strip() or "No summary provided."}</p>
                <p><strong>Remarks:</strong><br/>{self.remarks_input.toPlainText().strip() or "No remarks provided."}</p>
              </div>
            </div>
            """

        narrative_html = ""
        if self.section_narrative.isChecked():
            narrative_html = f"""
            <div class='section block-keep'>
              <h2 class='section-title'>Automated Narrative</h2>
              <div class='section-body'>{self._build_narrative_html()}</div>
            </div>
            """

        overall_html = ""
        if self.section_overall.isChecked():
            overall_html = f"""
            <div class='section block-keep'>
              <h2 class='section-title'>Overall Stats</h2>
              <div class='section-body'>
                <table>
                  <tr><th>Metric</th><th>Avg</th><th>Min</th><th>Max</th></tr>
                  {overall_rows or "<tr><td colspan='4'>No signal data captured.</td></tr>"}
                </table>
              </div>
            </div>
            """

        css = f"""
          @page {{ size: A4; margin: 20mm; }}
          body {{ font-family: Arial, sans-serif; color: #111827; line-height: 1.25; font-size: 11pt; }}
          h1, h2 {{ color: {primary}; }}
          h1 {{ margin: 0 0 4px 0; font-size: 18pt; }}
          h2 {{ margin: 6px 0 2px 0; font-size: 12pt; page-break-after: avoid; break-after: avoid; }}
          .header {{ margin-bottom: 10px; text-align: center; }}
          .header-logo {{ display: block; margin: 0 auto 6px auto; text-align: center; }}
          .logo {{ max-width: 280px; max-height: 120px; width: auto; height: auto; object-fit: contain; }}
          .header-meta {{ text-align: center; }}
          .section {{ margin-top: 6px; page-break-inside: avoid; break-inside: avoid; }}
          .section-title {{ font-size: 12pt; font-weight: 600; color: {primary}; margin: 6px 0 2px 0; page-break-after: avoid; break-after: avoid; }}
          .section-body {{ page-break-inside: avoid; break-inside: avoid; }}
          .block-keep {{ page-break-inside: avoid; break-inside: avoid; }}
          .page-break {{ page-break-before: always; break-before: page; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 3px; page-break-inside: avoid; break-inside: avoid; }}
          th, td {{ border: 1px solid #e5e7eb; padding: 4px 6px; font-size: 10pt; }}
          th {{ background: #f8fafc; color: {accent}; text-align: left; }}
          .meta {{ color: #475569; font-size: 10pt; }}
          .map {{ display: block; margin: 2px auto 0; max-width: 100%; height: auto; border: 1px solid #e5e7eb; }}
          .map-section {{ }}
          .map-title {{ font-size: 12pt; font-weight: 600; color: {primary}; margin: 6px 0 2px 0; display: block; page-break-after: avoid; break-after: avoid; }}
          .map-wrap {{ display: block; page-break-inside: avoid; break-inside: avoid; }}
          img {{ page-break-inside: avoid; }}
        """

        sections = []
        self._append_sections(sections, summary_html)
        self._append_sections(sections, narrative_html)
        self._append_sections(sections, overall_html)
        self._append_sections(sections, gps_html)
        self._append_sections(sections, per_ant_html)
        self._append_sections(sections, cycles_html)
        self._append_sections(sections, map_html)

        header_html = (
            f"<div class=\"header\">"
            f"<div class=\"header-logo\">{logo_html}</div>"
            f"<h1>{self.title_input.text().strip() or 'Mission Report'}</h1>"
            f"<div class=\"header-meta meta\">Mission: {self.mission_input.text().strip() or '--'}</div>"
            f"<div class=\"header-meta meta\">Operator: {self.operator_input.text().strip() or '--'}</div>"
            f"<div class=\"header-meta meta\">Generated: {now}</div>"
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
            QtCore.QMarginsF(20, 20, 20, 20),
            QtGui.QPageLayout.Unit.Millimeter,
        )
        rect = printer.pageRect(QtPrintSupport.QPrinter.Unit.Point)
        return float(rect.width()), float(rect.height())

    def _measure_html_height(self, html: str, css: str, width_pt: float) -> float:
        doc = QtGui.QTextDocument()
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
            cached = self._map_cache
            if cached.get("key") == key:
                return cached.get("b64")
            try:
                data = base64.b64decode(self._map_png_b64)
            except Exception:
                return None
            b64 = _encode_image_bytes(data, 720, 420)
            self._map_cache = {"key": key, "b64": b64}
            return b64

        map_path = self._data.get("map_path")
        if map_path and os.path.exists(map_path):
            mtime = os.path.getmtime(map_path)
            cached = self._map_cache
            if cached.get("path") == map_path and cached.get("mtime") == mtime:
                return cached.get("b64")
            b64 = _encode_image_path(map_path, 720, 420)
            self._map_cache = {"path": map_path, "mtime": mtime, "b64": b64}
            return b64
        return None

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
        version="1.0.0",
        description="Generate mission reports from cached sessions.",
        menu=[
            AddonAction(
                id="generate_report",
                label="Generate Report...",
                handler=_open_report_generator,
                enabled=_report_available,
            )
        ],
    )
