"""
PINPOINT Software Project
pinpoint/main_window.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Implements the primary Qt window, including map display, status panels, and controls.
Coordinates data collection, playback, and add-on integration.
---

https://crayton.dev/
"""
from .core import *  # noqa: F401,F403
from .ui_components import *  # noqa: F401,F403
from .version import APP_VERSION_NAME

from .plugin_api import PinpointAPI
from .plugin_manager import AddonManager

try:
    from PyQt6 import QtWebEngineWidgets  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    QtWebEngineWidgets = None

try:
    from PyQt6 import QtWebChannel  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    QtWebChannel = None


class _MapPointBridge(QtCore.QObject):
    """Small WebChannel bridge used by map markers to select a telemetry cycle."""

    point_selected = QtCore.pyqtSignal(str)

    @QtCore.pyqtSlot(str)
    def selectPoint(self, point_id: str) -> None:  # noqa: N802 - JavaScript-facing API
        self.point_selected.emit(str(point_id))

# ---------------------------
# Main Window
# ---------------------------
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, gps_port: Optional[str] = None, playback_only: bool = False, meshtastic_only: bool = False):
        super().__init__()
        self.gps_port = gps_port
        self.playback_only = playback_only
        self.meshtastic_only = meshtastic_only
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(1200, 900)
        self.resize(1450, 950)
        self.setStyleSheet(self._style())
        _apply_app_icon(self)
        self._psutil_proc = psutil.Process(os.getpid()) if psutil else None
        self._cpu_primed = False
        self._last_io = None
        self._last_io_time = None
        self.last_status_msg = "Idle"

        # Central widget
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        self._build_menus()

        # Map display (static image fallback + optional interactive view)
        self.image_label = QtWidgets.QLabel()
        self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.image_label.setContentsMargins(12, 12, 12, 12)
        self.image_label.setMinimumSize(820, 620)
        self.image_label.installEventFilter(self)

        self.map_view = None
        if QtWebEngineWidgets is not None:
            try:
                self.map_view = QtWebEngineWidgets.QWebEngineView()
                self.map_view.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.NoContextMenu)
                self.map_view.setMinimumSize(820, 620)
                self.map_view.loadFinished.connect(self._on_map_load_finished)
            except Exception:
                self.map_view = None

        self.map_container = QtWidgets.QWidget()
        self.map_stack = QtWidgets.QStackedLayout(self.map_container)
        self.map_stack.addWidget(self.image_label)
        if self.map_view is not None:
            self.map_stack.addWidget(self.map_view)
        self.map_stack.setCurrentWidget(self.image_label)

        self._map_point_details: dict[str, dict] = {}
        self._selected_map_point_id: Optional[str] = None
        self._map_point_bridge = _MapPointBridge(self)
        self._map_point_bridge.point_selected.connect(self._select_map_point)
        self._map_web_channel = None
        if self.map_view is not None and QtWebChannel is not None:
            try:
                self._map_web_channel = QtWebChannel.QWebChannel(self.map_view.page())
                self._map_web_channel.registerObject("pointBridge", self._map_point_bridge)
                self.map_view.page().setWebChannel(self._map_web_channel)
            except Exception:
                logger.warning("Interactive map point selection bridge could not be initialized.", exc_info=True)
                self._map_web_channel = None

        # This inspector intentionally starts completely blank. Selecting a map fix
        # reveals the full telemetry snapshot for that one collection cycle.
        self.point_inspector = QtWidgets.QFrame()
        self.point_inspector.setObjectName("pointInspector")
        self.point_inspector.setMinimumWidth(320)
        self.point_inspector.setMaximumWidth(430)
        self.point_inspector_stack = QtWidgets.QStackedLayout(self.point_inspector)
        self.point_inspector_stack.setContentsMargins(0, 0, 0, 0)
        self.point_inspector_blank = QtWidgets.QWidget()
        self.point_inspector_content = QtWidgets.QWidget()
        inspector_layout = QtWidgets.QVBoxLayout(self.point_inspector_content)
        inspector_layout.setContentsMargins(12, 12, 12, 12)
        self.point_inspector_title = QtWidgets.QLabel("Selected Fix")
        self.point_inspector_title.setObjectName("pointInspectorTitle")
        self.point_inspector_close_btn = QtWidgets.QToolButton()
        self.point_inspector_close_btn.setObjectName("pointInspectorClose")
        self.point_inspector_close_btn.setText("×")
        self.point_inspector_close_btn.setToolTip("Clear selected fix")
        self.point_inspector_close_btn.setAccessibleName("Clear selected fix")
        self.point_inspector_close_btn.setFixedSize(28, 28)
        self.point_inspector_close_btn.clicked.connect(self._clear_point_inspector)
        inspector_header = QtWidgets.QHBoxLayout()
        inspector_header.setContentsMargins(0, 0, 0, 0)
        inspector_header.addWidget(self.point_inspector_title)
        inspector_header.addStretch(1)
        inspector_header.addWidget(self.point_inspector_close_btn)
        self.point_inspector_summary = QtWidgets.QLabel()
        self.point_inspector_summary.setObjectName("pointInspectorSummary")
        self.point_inspector_summary.setWordWrap(True)
        self.point_inspector_tree = QtWidgets.QTreeWidget()
        self.point_inspector_tree.setObjectName("pointInspectorTree")
        self.point_inspector_tree.setHeaderLabels(["Field", "Value"])
        self.point_inspector_tree.setAlternatingRowColors(True)
        self.point_inspector_tree.setUniformRowHeights(True)
        self.point_inspector_tree.header().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.point_inspector_tree.header().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        inspector_layout.addLayout(inspector_header)
        inspector_layout.addWidget(self.point_inspector_summary)
        inspector_layout.addWidget(self.point_inspector_tree, stretch=1)
        self.point_inspector_stack.addWidget(self.point_inspector_blank)
        self.point_inspector_stack.addWidget(self.point_inspector_content)
        self.point_inspector_stack.setCurrentWidget(self.point_inspector_blank)

        self.map_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.map_splitter.setObjectName("mapColumns")
        self.map_splitter.setChildrenCollapsible(False)
        self.map_splitter.addWidget(self.point_inspector)
        self.map_splitter.addWidget(self.map_container)
        self.map_splitter.setStretchFactor(0, 0)
        self.map_splitter.setStretchFactor(1, 1)
        self.map_splitter.setSizes([350, 1000])

        self._interactive_map_enabled = False
        self._map_initialized = False
        self._map_ready = False
        self._pending_map_points = None
        self._last_map_update = 0.0
        self._last_map_sig = None
        self._last_image_mtime = 0
        self.update_image(force=True)

        # Status row
        self.gps_label = QtWidgets.QLabel("GPS: --")
        self.status_label = QtWidgets.QLabel("Status: Idle")
        self.gps_label.setObjectName("gpsStatus")
        self.status_label.setObjectName("appStatus")
        self.gps_label.setVisible(False)
        self.status_label.setVisible(False)

        # Buttons row
        self.exit_btn = self._make_btn("Exit", danger=True)
        self.clear_btn = self._make_btn("Clear App")
        self.settings_btn = self._make_btn("Update Settings")
        self.log_btn = self._make_btn("View Log")
        self.open_recording_btn = self._make_btn("Open Recording")
        self.gps_info_btn = self._make_btn("GPS Info")
        self.antenna_info_btn = self._make_btn("Antenna Info")
        self.start_btn = self._make_btn("Start Data Collection", primary=True, wide=False)
        self.flag_btn = self._make_btn("Flag")
        self.flag_btn.setVisible(False)

        self.exit_btn.clicked.connect(self.close)
        self.clear_btn.clicked.connect(self.clear_app)
        self.settings_btn.clicked.connect(self.open_settings)
        self.log_btn.clicked.connect(self.open_log)
        self.open_recording_btn.clicked.connect(self.open_recording)
        self.gps_info_btn.clicked.connect(self.open_gps_info)
        self.antenna_info_btn.clicked.connect(self.open_antenna_info)
        self.start_btn.clicked.connect(self.toggle_collection)
        self.flag_btn.clicked.connect(self.open_flag_dialog)

        # Toolbar removed for cleaner menu-only utility layout

        # Info panel under the map
        self.info_panel = QtWidgets.QFrame()
        self.info_panel.setObjectName("infoPanel")
        self.info_summary = QtWidgets.QLabel("Mode: --  |  Activity: --  |  Recording: --  |  Playback: --")
        self.info_summary.setObjectName("infoSummary")
        self.info_status_gif = QtWidgets.QLabel()
        self.info_status_gif.setFixedSize(LOADING_ICON_PX + 8, LOADING_ICON_PX + 8)
        self.info_status_gif.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._info_status_movie: Optional[QtGui.QMovie] = None
        self._info_status_mode: Optional[str] = None

        self._info_values = {}
        info_grid = QtWidgets.QGridLayout()
        info_grid.setHorizontalSpacing(16)
        info_grid.setVerticalSpacing(6)

        items = [
            ("Mode", "mode"),
            ("Activity", "activity"),
            ("GPS", "gps"),
            ("Coordinates", "coordinates"),
            ("GPS Accuracy", "gps_accuracy"),
            ("Satellites", "sats"),
            ("Fix Age", "fix_age"),
            ("SDR", "sdr"),
            ("Signal", "signal"),
            ("Bearing", "bearing"),
            ("Target Rel", "target_rel"),
            ("Source", "source"),
            ("Confidence", "confidence"),
            ("Recording", "recording"),
        ]
        # Two-column grid of key/value pairs
        left = items[:6]
        right = items[6:]
        for row, (label, key) in enumerate(left):
            k, v = self._make_info_pair(label)
            info_grid.addWidget(k, row, 0)
            info_grid.addWidget(v, row, 1)
            self._info_values[key] = v
        for row, (label, key) in enumerate(right):
            k, v = self._make_info_pair(label)
            info_grid.addWidget(k, row, 2)
            info_grid.addWidget(v, row, 3)
            self._info_values[key] = v

        left_col = QtWidgets.QVBoxLayout()
        left_col.addWidget(self.info_summary)
        left_col.addLayout(info_grid)

        right_col = QtWidgets.QVBoxLayout()
        right_col.addStretch(1)
        right_col.addWidget(self.info_status_gif, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        right_col.addSpacing(6)
        right_col.addWidget(self.flag_btn, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        right_col.addStretch(1)

        info_layout = QtWidgets.QHBoxLayout(self.info_panel)
        info_layout.addLayout(left_col, stretch=1)
        info_layout.addLayout(right_col)

        # Playback controls (hidden until a recording is loaded)
        self.playback_widget = QtWidgets.QWidget()
        self.playback_widget.setVisible(False)
        pb_layout = QtWidgets.QHBoxLayout(self.playback_widget)
        pb_layout.setContentsMargins(0, 0, 0, 0)

        self.playback_play_btn = self._make_btn("Play")
        self.playback_slider = FlaggedSlider(QtCore.Qt.Orientation.Horizontal)
        self.playback_slider.setMinimum(0)
        self.playback_slider.setMaximum(0)
        self.playback_slider.setMinimumHeight(40)
        self.playback_speed = QtWidgets.QComboBox()
        self.playback_speed.addItems(["1x", "2x", "4x", "8x", "16x", "32x"])
        self.playback_speed.setCurrentText("1x")
        self.playback_time_label = QtWidgets.QLabel("00:00 / 00:00")
        self.playback_close_btn = self._make_btn("Exit Playback")

        pb_layout.addWidget(self.playback_play_btn)
        pb_layout.addWidget(self.playback_slider, stretch=1)
        pb_layout.addWidget(self.playback_time_label)
        pb_layout.addWidget(self.playback_speed)
        pb_layout.addWidget(self.playback_close_btn)

        self.playback_play_btn.clicked.connect(self._toggle_playback)
        self.playback_slider.sliderPressed.connect(self._on_playback_slider_pressed)
        self.playback_slider.sliderReleased.connect(self._on_playback_slider_released)
        self.playback_slider.valueChanged.connect(self._on_playback_scrub)
        self.playback_speed.currentTextChanged.connect(self._on_playback_speed_changed)
        self.playback_close_btn.clicked.connect(self._exit_playback)

        # Layout
        layout = QtWidgets.QVBoxLayout(central)
        layout.addWidget(self.map_splitter, stretch=1)
        layout.addSpacing(8)
        layout.addSpacing(6)
        layout.addWidget(self.info_panel)
        layout.addSpacing(6)
        layout.addWidget(self.playback_widget)
        layout.addSpacing(8)

        # Status bar (CPU/RAM/Disk + clock)
        self.stats_label = QtWidgets.QLabel("CPU: --  RAM: --  Disk: --")
        self.clock_label = QtWidgets.QLabel("--:--:--")
        status = self.statusBar()
        status.addWidget(self.stats_label, 1)
        status.addPermanentWidget(self.clock_label)

        self._stats_timer = QtCore.QTimer(self)
        self._stats_timer.setInterval(1000)
        self._stats_timer.timeout.connect(self._update_status_bar)
        self._stats_timer.start()
        self._update_status_bar()

        # Timer to refresh image
        self.image_timer = QtCore.QTimer(self)
        self.image_timer.setInterval(1000)
        self.image_timer.timeout.connect(self.update_image)
        self.image_timer.start()

        # Thread control
        self.collecting = False
        self.stop_event = threading.Event()
        self.thread: CollectorThread | None = None
        self._gps_tracking_stop_event = threading.Event()
        self._gps_tracking_thread: GPSLocationThread | None = None
        self._hardware_monitor_stop_event = threading.Event()
        self._hardware_monitor_thread: HardwarePresenceThread | None = None
        self._collection_start_pending = False
        self._demo_start_pending = False
        self._idle_gps_point: Optional[dict] = None
        self._closing = False
        self.demo_active = False
        self._demo_start_state: Optional[dict] = None
        self._demo_config: Optional[dict] = None
        self._demo_stopped = False
        self._stop_dialog: Optional[BusyDialog] = None
        self._start_dialog: Optional[BusyDialog] = None
        self.recording_session: Optional[RecordingSession] = None
        self.recording_path: Optional[str] = None
        self.report_cache_frames: list[dict] = []
        self.report_cache_active = False
        self.report_cache_started_at: Optional[float] = None
        self.report_source_label: Optional[str] = None
        self.report_header: Optional[dict] = None
        self._main_hw_thread: Optional[HardwareCheckThread] = None
        self._rescan_dialog: Optional[BusyDialog] = None
        self.playback_mode = False
        self.playback_frames: list[dict] = []
        self.playback_flags: list[dict] = []
        self.playback_index = 0
        self.playback_speed_factor = 1.0
        self.playback_timer = QtCore.QTimer(self)
        self.playback_timer.setInterval(30)
        self.playback_timer.timeout.connect(self._on_playback_tick)
        self._playback_playing = False
        self._playback_start_wall = 0.0
        self._playback_start_t = 0.0
        self._playback_last_map_bytes: Optional[bytes] = None
        self._playback_render_cache: dict[int, bytes] = {}
        self.playback_alerts: list[dict] = []
        self._playback_slider_dragging = False
        self._gps_info_dialog: Optional[GPSInfoDialog] = None
        self._antenna_info_dialog: Optional[AntennaInfoDialog] = None
        self._last_info_dialog_refresh = 0.0
        self.latest_satellites = []
        self.latest_gps_fix = None
        self.latest_gps_loc = None
        self.latest_sats = None
        self.latest_fix_age = None
        self.latest_gps_hdop = None
        self.latest_gps_accuracy_m = None
        self.latest_strength = None
        self.latest_snr = None
        self.latest_quality = None
        self.latest_cycle_paused = False
        self.latest_pause_reason = None
        self.latest_target_estimate = None
        self.latest_telemetry = {}
        self.alert_manager = AlertManager()
        try:
            self.sdr_connected = bool(funcs.list_sdr_devices())
        except Exception:
            self.sdr_connected = False
        self.sdr_error = None
        self.sdr_sample_rate = None
        self.antenna_count = settings.antenna_count
        self.antenna_states = []
        self.current_bearing = None
        self.target_bearing = None
        self.target_relative = None
        self.aoa_confidence = 0.0
        self.map_confidence = 0.0
        self.fusion_confidence = 0.0
        self.bearing_source = None
        # Initialize info panel after state is ready
        self._update_info_panel()
        self._refresh_map_mode(force=True)
        self._init_addons()

        if self.playback_only or self.meshtastic_only:
            label = "Playback Only" if self.playback_only else "Meshtastic Only"
            self._set_start_state(label, "primary", enabled=False)
            if hasattr(self, "start_action") and self.start_action:
                self.start_action.setEnabled(False)
        elif self.gps_port:
            QtCore.QTimer.singleShot(0, self._start_idle_gps_tracking)
        QtCore.QTimer.singleShot(0, self._start_hardware_presence_monitor)

    # ---------- UI helpers ----------
    def _build_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        view_menu = menubar.addMenu("View")
        settings_menu = menubar.addMenu("Settings")
        collection_menu = menubar.addMenu("Collection")
        self.addons_menu = menubar.addMenu("Add-ons")

        self.open_recording_action = QtGui.QAction("Open Recording...", self)
        self.open_recording_action.triggered.connect(self.open_recording)
        self.exit_playback_action = QtGui.QAction("Exit Playback", self)
        self.exit_playback_action.setEnabled(False)
        self.exit_playback_action.triggered.connect(self._exit_playback)
        self.info_action = QtGui.QAction("Info / About...", self)
        self.info_action.triggered.connect(self.open_app_info)
        self.exit_action = QtGui.QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)

        self.log_action = QtGui.QAction("View Log", self)
        self.log_action.triggered.connect(self.open_log)
        self.gps_info_action = QtGui.QAction("GPS Info", self)
        self.gps_info_action.triggered.connect(self.open_gps_info)
        self.antenna_info_action = QtGui.QAction("Antenna Info", self)
        self.antenna_info_action.triggered.connect(self.open_antenna_info)
        self.settings_action = QtGui.QAction("Update Settings", self)
        self.settings_action.triggered.connect(self.open_settings)
        self.gps_port_action = QtGui.QAction("Change GPS Port...", self)
        self.gps_port_action.triggered.connect(self.change_gps_port)

        self.start_action = QtGui.QAction("Start Data Collection", self)
        self.start_action.triggered.connect(self.toggle_collection)
        self.clear_action = QtGui.QAction("Clear App", self)
        self.clear_action.triggered.connect(self.clear_app)

        file_menu.addAction(self.open_recording_action)
        file_menu.addAction(self.exit_playback_action)
        file_menu.addAction(self.info_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        view_menu.addAction(self.log_action)
        view_menu.addAction(self.gps_info_action)
        view_menu.addAction(self.antenna_info_action)
        settings_menu.addAction(self.settings_action)
        settings_menu.addAction(self.gps_port_action)

        collection_menu.addAction(self.start_action)
        collection_menu.addAction(self.clear_action)

        self.addons_menu.addAction(QtGui.QAction("Loading add-ons...", self))

    def _init_addons(self) -> None:
        self.api = PinpointAPI(logger=logger)
        self._register_api_handlers()
        addons_dir = _resource_path("addons")
        self.api.set_context(main_window=self, addons_dir=addons_dir)
        if hasattr(self, "addons_menu") and self.addons_menu:
            self.addon_manager = AddonManager(
                api=self.api,
                addons_dir=addons_dir,
                menu=self.addons_menu,
                logger=logger,
                parent=self,
            )
            self.addon_manager.load_all()
            self.addon_manager.start_watch()
            self.addons_menu.aboutToShow.connect(self.addon_manager.refresh_enabled_states)
            self.api.emit("app.ready", {"ts": time.time()})
        else:
            self.addon_manager = None

    def _register_api_handlers(self) -> None:
        self.api.register("core.get_version", lambda _p: {"version": APP_VERSION})
        self.api.register("core.get_title", lambda _p: {"title": APP_TITLE})
        self.api.register("core.get_resource_path", self._api_resource_path)
        self.api.register("core.get_settings", self._api_get_settings)

        self.api.register("ui.get_main_window", lambda _p: {"window": self})
        self.api.register("ui.show_message", self._api_show_message)

        self.api.register("data.get_report_data", lambda _p: {"data": self._get_report_data()})
        self.api.register("data.get_history_points", lambda _p: {"points": self._get_history_points()})
        self.api.register("data.report_available", lambda _p: {"available": self._report_available()})
        self.api.register("data.get_latest_telemetry", lambda _p: {"telemetry": dict(self.latest_telemetry)})
        self.api.register("alerts.get", lambda _p: {"alerts": self.alert_manager.snapshot()})
        self.api.register("alerts.publish", self._api_publish_alert)

        self.api.register("log.debug", lambda p: self._api_log(logging.DEBUG, p))
        self.api.register("log.info", lambda p: self._api_log(logging.INFO, p))
        self.api.register("log.warning", lambda p: self._api_log(logging.WARNING, p))
        self.api.register("log.error", lambda p: self._api_log(logging.ERROR, p))

        self.api.register("bus.emit", self._api_bus_emit)
        self.api.register("bus.subscribe", self._api_bus_subscribe)
        self.api.register("bus.unsubscribe", self._api_bus_unsubscribe)
        self.api.register("addons.reload", lambda _p: self._api_addons_reload())
        self.api.register("addons.refresh_actions", lambda _p: self._api_addons_refresh())

    def _api_resource_path(self, payload: dict) -> dict:
        parts = payload.get("parts") or []
        if not isinstance(parts, (list, tuple)):
            parts = [parts]
        if len(parts) == 1:
            mutable_paths = {
                "main.log": LOG_FILE,
                "map.png": IMAGE_PATH,
                "calibration_profiles.json": CALIBRATION_FILE,
                "settings.json": SETTINGS_FILE,
            }
            if str(parts[0]) in mutable_paths:
                return {"path": mutable_paths[str(parts[0])]}
        return {"path": _resource_path(*[str(p) for p in parts])}

    def _api_get_settings(self, _payload: dict) -> dict:
        with settings_lock:
            return {"settings": settings.to_dict()}

    def _api_show_message(self, payload: dict) -> dict:
        title = payload.get("title") or "Pinpoint"
        message = payload.get("message") or ""
        level = (payload.get("level") or "info").lower()
        if level == "warning":
            QtWidgets.QMessageBox.warning(self, title, message)
        elif level in ("error", "critical"):
            QtWidgets.QMessageBox.critical(self, title, message)
        else:
            QtWidgets.QMessageBox.information(self, title, message)
        return {"ok": True}

    def _api_log(self, level: int, payload: dict) -> dict:
        msg = payload.get("message")
        if not msg:
            return {"ok": False, "error": "Missing 'message'."}
        logger.log(level, str(msg))
        return {"ok": True}

    def _api_publish_alert(self, payload: dict) -> dict:
        key = str(payload.get("key") or "addon-alert")
        severity = str(payload.get("severity") or "info").lower()
        if severity not in ("error", "warning", "info", "debug"):
            return {"ok": False, "error": "Severity must be error, warning, info, or debug."}
        self.alert_manager.update(
            key,
            bool(payload.get("active", True)),
            payload.get("message") or key.upper(),
            severity,
            max(1, int(payload.get("debounce_cycles", 1))),
        )
        self.update_image(force=True)
        return {"alert": key}

    def _api_bus_emit(self, payload: dict) -> dict:
        event = payload.get("event")
        if not event:
            return {"ok": False, "error": "Missing 'event'."}
        data = payload.get("data") or {}
        self.api.emit(str(event), data if isinstance(data, dict) else {"data": data})
        return {"ok": True}

    def _api_bus_subscribe(self, payload: dict) -> dict:
        event = payload.get("event")
        handler = payload.get("handler")
        if not event or not callable(handler):
            return {"ok": False, "error": "Missing 'event' or callable 'handler'."}
        token = self.api.subscribe(str(event), handler)
        return {"ok": True, "token": token}

    def _api_bus_unsubscribe(self, payload: dict) -> dict:
        token = payload.get("token")
        try:
            token = int(token)
        except Exception:
            return {"ok": False, "error": "Invalid 'token'."}
        removed = self.api.unsubscribe(token)
        return {"ok": True, "removed": removed}

    def _api_addons_reload(self) -> dict:
        if hasattr(self, "addon_manager") and self.addon_manager:
            self.addon_manager.reload()
        return {"ok": True}

    def _api_addons_refresh(self) -> dict:
        if hasattr(self, "addon_manager") and self.addon_manager:
            self.addon_manager.refresh_enabled_states()
        return {"ok": True}

    def _build_toolbar(self):
        self.toolbar = QtWidgets.QToolBar("Controls")
        self.toolbar.setMovable(False)
        self.toolbar.setFloatable(False)
        self.toolbar.setIconSize(QtCore.QSize(18, 18))
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, self.toolbar)

        self.toolbar.addWidget(self.start_btn)
        self.toolbar.addWidget(self.clear_btn)
        self.toolbar.addSeparator()
        self.toolbar.addWidget(self.open_recording_btn)
        self.toolbar.addSeparator()
        self.toolbar.addWidget(self.settings_btn)
        self.toolbar.addWidget(self.log_btn)
        self.toolbar.addSeparator()
        self.toolbar.addWidget(self.gps_info_btn)
        self.toolbar.addWidget(self.antenna_info_btn)
        self.toolbar.addSeparator()
        self.toolbar.addWidget(self.exit_btn)

    def _make_btn(self, text, primary=False, danger=False, wide=False):
        btn = QtWidgets.QPushButton(text)
        if primary:
            btn.setProperty("class", "primary")
        if danger:
            btn.setProperty("class", "danger")
        if wide:
            btn.setMinimumWidth(200)
        return btn

    def _make_info_pair(self, text: str):
        key = QtWidgets.QLabel(f"{text}:")
        key.setObjectName("infoKey")
        val = QtWidgets.QLabel("--")
        val.setObjectName("infoValue")
        val.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        return key, val

    def _set_start_state(self, text: str, cls: str, enabled: Optional[bool] = None):
        self.start_btn.setText(text)
        if enabled is not None:
            self.start_btn.setEnabled(enabled)
        self.start_btn.setProperty("class", cls)
        self.start_btn.style().unpolish(self.start_btn); self.start_btn.style().polish(self.start_btn)
        if hasattr(self, "start_action") and self.start_action:
            self.start_action.setText(text)
            if enabled is not None:
                self.start_action.setEnabled(enabled)

    def _set_info_status_gif(self, mode: str) -> None:
        if self._info_status_mode == mode:
            return
        if self._info_status_movie is not None:
            self._info_status_movie.stop()
            self._info_status_movie.deleteLater()
            self._info_status_movie = None

        asset = _status_anim_for_mode(mode)
        if asset and os.path.exists(asset):
            processed = _transparentize_gif(asset)
            movie = QtGui.QMovie(processed)
            if movie.isValid():
                movie.setScaledSize(QtCore.QSize(LOADING_ICON_PX, LOADING_ICON_PX))
                self.info_status_gif.setMovie(movie)
                movie.start()
                self._info_status_movie = movie
                self._info_status_mode = mode
                return

        icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
        self.info_status_gif.setPixmap(icon.pixmap(LOADING_ICON_PX, LOADING_ICON_PX))
        self._info_status_mode = mode

    def _update_info_panel(self):
        mode = "Playback" if self.playback_mode else ("Live" if self.collecting else "Idle")
        if self.demo_active and not self.playback_mode:
            mode = "Demo"
        elif self.playback_only and not self.playback_mode:
            mode = "Playback Only"
        elif self.meshtastic_only and not self.playback_mode:
            mode = "Meshtastic Only"
        if self.recording_session is not None and not self.playback_mode:
            mode = "Demo (Recording)" if self.demo_active else "Live (Recording)"

        status_mode = "playback" if self.playback_mode else ("running" if self.collecting else "paused")
        self._set_info_status_gif(status_mode)

        playback_text = "--"
        if self.playback_mode:
            playback_text = f"ON {self.playback_speed_factor:.0f}x"

        record_text = "ON" if self.recording_session is not None else "OFF"
        if self.recording_session is not None and self.recording_path:
            record_text = f"ON ({os.path.basename(self.recording_path)})"
        flag_active = self.recording_session is not None and self.collecting and not self.playback_mode
        self.flag_btn.setVisible(flag_active)
        self.flag_btn.setEnabled(flag_active)

        activity = getattr(self, "last_status_msg", None) or ("Collecting" if self.collecting else "Idle")
        if self.collecting and self.latest_cycle_paused:
            activity = self.latest_pause_reason or "Insufficient Movement, Paused Cycle"

        gps_text = "--"
        if self.latest_gps_fix is True:
            gps_text = "FIX"
        elif self.latest_gps_fix is False:
            gps_text = "NO FIX"

        sats_text = "--" if self.latest_sats is None else str(self.latest_sats)
        fix_age_text = "--" if self.latest_fix_age is None else f"{self.latest_fix_age:.0f}s"
        gps_accuracy_text = "--"
        if self.latest_gps_accuracy_m is not None:
            gps_accuracy_text = f"±{self.latest_gps_accuracy_m:.1f}m"
            if self.latest_gps_hdop is not None:
                gps_accuracy_text += f" (HDOP {self.latest_gps_hdop:.1f})"
        coordinates_text = "--"
        if self.latest_gps_loc is not None:
            try:
                coordinates_text = f"{float(self.latest_gps_loc[0]):.6f}, {float(self.latest_gps_loc[1]):.6f}"
            except (TypeError, ValueError, IndexError):
                coordinates_text = "--"

        sdr_text = "Connected" if self.sdr_connected else "No SDR"
        if self.sdr_connected and self.sdr_sample_rate:
            sdr_text += f" @ {self.sdr_sample_rate/1e6:.2f}MS/s"
        if self.sdr_error:
            sdr_text += " (Err)"

        strength = self.latest_strength
        snr = self.latest_snr
        quality = self.latest_quality
        signal_text = "S=--  SNR=--  Q=--"
        if strength is not None:
            signal_text = f"S={strength}  SNR={snr:.2f}" if snr is not None else f"S={strength}  SNR=--"
            if quality is not None:
                signal_text += f"  Q={quality:.2f}"

        cur = self.current_bearing
        tgt = self.target_bearing
        rel = self.target_relative
        bearing_text = f"Cur: {cur:.0f}°  Tgt: {tgt:.0f}°" if cur is not None and tgt is not None else "--"
        rel_text = "--" if rel is None else f"{rel:.0f}°"

        src_text = (self.bearing_source or "--").upper()
        conf_text = "--"
        if self.aoa_confidence is not None or self.map_confidence is not None or self.fusion_confidence is not None:
            aoa = "--" if self.aoa_confidence is None else f"{self.aoa_confidence:.2f}"
            mp = "--" if self.map_confidence is None else f"{self.map_confidence:.2f}"
            fu = "--" if self.fusion_confidence is None else f"{self.fusion_confidence:.2f}"
            conf_text = f"AMP:{aoa}  MAP:{mp}  FUSED:{fu}"

        self.info_summary.setText(
            f"Mode: {mode}  |  Activity: {activity}  |  Recording: {record_text}  |  Playback: {playback_text}"
        )
        self._info_values["mode"].setText(mode)
        self._info_values["activity"].setText(activity)
        self._info_values["gps"].setText(gps_text)
        self._info_values["coordinates"].setText(coordinates_text)
        self._info_values["gps_accuracy"].setText(gps_accuracy_text)
        self._info_values["sats"].setText(sats_text)
        self._info_values["fix_age"].setText(fix_age_text)
        self._info_values["sdr"].setText(sdr_text)
        self._info_values["signal"].setText(signal_text)
        self._info_values["bearing"].setText(bearing_text)
        self._info_values["target_rel"].setText(rel_text)
        self._info_values["source"].setText(src_text)
        self._info_values["confidence"].setText(conf_text)
        self._info_values["recording"].setText(record_text)

    def _update_status_bar(self):
        self.clock_label.setText(time.strftime("%H:%M:%S"))
        if not self._psutil_proc:
            self.stats_label.setText("CPU: --  RAM: --  Disk: --")
            return
        try:
            if not self._cpu_primed:
                self._psutil_proc.cpu_percent(None)
                self._cpu_primed = True
                cpu = 0.0
            else:
                cpu = self._psutil_proc.cpu_percent(None)
            mem = self._psutil_proc.memory_info().rss / (1024 * 1024)
            io = self._psutil_proc.io_counters()
            now = time.time()
            disk_text = "--"
            if io and self._last_io is not None and self._last_io_time:
                dt = max(1e-6, now - self._last_io_time)
                read_rate = (io.read_bytes - self._last_io.read_bytes) / dt
                write_rate = (io.write_bytes - self._last_io.write_bytes) / dt
                disk_text = f"{self._fmt_rate(read_rate)}/{self._fmt_rate(write_rate)}"
            self._last_io = io
            self._last_io_time = now
            self.stats_label.setText(f"CPU: {cpu:.0f}%  RAM: {mem:.0f} MB  Disk: {disk_text}")
        except Exception:
            self.stats_label.setText("CPU: --  RAM: --  Disk: --")

    @staticmethod
    def _fmt_rate(bytes_per_s: float) -> str:
        try:
            b = float(bytes_per_s)
        except Exception:
            return "--"
        if b >= 1024 * 1024:
            return f"{b / (1024 * 1024):.1f} MB/s"
        if b >= 1024:
            return f"{b / 1024:.1f} KB/s"
        return f"{b:.0f} B/s"

    def _style(self):
        # Light, clean aesthetic
        return (
            """
            QMainWindow { background: #ffffff; }
            QMenuBar { background: #f3f4f6; padding: 2px 6px; }
            QMenuBar::item { background: transparent; padding: 4px 10px; margin: 0 2px; }
            QMenuBar::item:selected { background: #e5e7eb; }
            QToolBar { background: #f9fafb; border-bottom: 1px solid #e5e7eb; spacing: 6px; padding: 4px; }
            QToolBar QPushButton { padding: 6px 10px; border-radius: 8px; }
            QStatusBar { background: #f9fafb; border-top: 1px solid #e5e7eb; color: #374151; }
            QStatusBar QLabel { padding: 2px 6px; }
            #infoPanel { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px; padding: 8px; }
            #infoSummary { font-weight: 600; color: #111827; padding-bottom: 4px; }
            #infoKey { color: #6b7280; }
            #infoValue { color: #111827; }
            #pointInspector { background: #f9fafb; border: 1px solid #e5e7eb; border-radius: 10px; }
            #pointInspectorTitle { font-size: 18px; font-weight: 700; color: #111827; }
            #pointInspectorSummary { color: #4b5563; padding: 2px 0 8px 0; }
            #pointInspectorTree { border: 1px solid #e5e7eb; border-radius: 7px; background: #ffffff; alternate-background-color: #f9fafb; }
            #pointInspectorTree::item { padding: 3px 2px; }
            #pointInspectorClose { border: 0; border-radius: 14px; background: transparent; color: #6b7280; font-size: 22px; font-weight: 600; padding: 0; }
            #pointInspectorClose:hover { background: #e5e7eb; color: #111827; }
            #mapColumns::handle { background: transparent; width: 8px; }
            #appTitle {
                font-size: 22px; font-weight: 700; padding: 16px 0; color: #111827;
            }
            QPushButton {
                border: 0; border-radius: 12px; padding: 12px 18px; font-weight: 600;
                background: #f3f4f6; color: #111827;
            }
            QPushButton:hover { background: #e5e7eb; }
            QPushButton[class="primary"] { background: #10b981; color: white; }
            QPushButton[class="primary"]:hover { background: #059669; }
            QPushButton[class="danger"] { background: #ef4444; color: white; }
            QPushButton[class="danger"]:hover { background: #dc2626; }
            QLabel { color: #111827; }
            QLineEdit { padding: 8px 10px; border: 1px solid #e5e7eb; border-radius: 8px; }
            QDialogButtonBox QPushButton { padding: 10px 14px; }
            QPlainTextEdit { border: 1px solid #e5e7eb; border-radius: 10px; }
            #gpsStatus, #appStatus { font-size: 12px; color: #374151; padding: 4px 6px; }
            """
        )

    # ---------- Actions ----------
    def open_settings(self):
        SettingsDialog(self).exec()
        self._refresh_map_mode(force=True)

    def change_gps_port(self) -> None:
        if self.playback_mode or self.demo_active or self.playback_only or self.meshtastic_only:
            QtWidgets.QMessageBox.information(
                self,
                "GPS Port",
                "Exit playback or demo mode before changing the live GPS receiver.",
            )
            return

        wizard = GPSSetupWizard(self, current_port=self.gps_port)
        if wizard.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        port = wizard.selected_port()
        if not port:
            return
        remember_port = bool(getattr(wizard, "remember_port", lambda: False)())

        old_port = self.gps_port
        self.gps_port = port
        os.environ["GPS_PORT"] = port
        if remember_port:
            with settings_lock:
                settings.preferred_gps_port = port
            save_settings()
        if self._hardware_monitor_thread is not None:
            self._hardware_monitor_thread.gps_port = port

        self.latest_gps_fix = False
        self.latest_gps_loc = None
        self.latest_sats = None
        self.latest_fix_age = None
        self.latest_gps_hdop = None
        self.latest_gps_accuracy_m = None
        self.latest_satellites = []
        self.latest_cycle_paused = False
        self.latest_pause_reason = None
        self.latest_target_estimate = None
        self._idle_gps_point = None
        self.current_bearing = None
        self.last_status_msg = f"Switching GPS to {port}"

        if self.collecting and self.thread is not None and hasattr(self.thread, "request_gps_port"):
            self.thread.request_gps_port(port)
        else:
            self._stop_idle_gps_tracking()
            QtCore.QTimer.singleShot(0, self._start_idle_gps_tracking)

        logger.info(
            "GPS port changed from %s to %s%s.",
            old_port or "none",
            port,
            " and saved as the default" if remember_port else " for this run",
        )
        self._update_info_panel()
        self._refresh_info_dialogs(force=True)
        self.update_image(force=True)

    def open_log(self):
        LogWindow(self).exec()

    def open_app_info(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("About Pinpoint")
        dlg.setModal(True)
        dlg.setMinimumWidth(520)
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        logo_label = QtWidgets.QLabel()
        pix = QtGui.QPixmap(PINPOINT_IMAGE_FALLBACK)
        if not pix.isNull():
            pix = pix.scaled(
                260,
                140,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
            logo_label.setPixmap(pix)
            logo_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(logo_label)

        title_label = QtWidgets.QLabel(APP_TITLE)
        title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title_label)

        version_text = f"{APP_VERSION}"
        if APP_VERSION_NAME and APP_VERSION_NAME not in APP_VERSION:
            version_text = f"{APP_VERSION} ({APP_VERSION_NAME})"

        pyqt_version = getattr(QtCore, "PYQT_VERSION_STR", "unknown")
        qt_version = getattr(QtCore, "QT_VERSION_STR", "unknown")
        info_html = "<br>".join(
            [
                f"<b>Version:</b> {version_text}",
                f"<b>Python:</b> {sys.version.split()[0]}",
                f"<b>Qt:</b> {qt_version} | <b>PyQt:</b> {pyqt_version}",
                f"<b>Executable:</b> {sys.executable}",
                f"<b>Add-ons:</b> {_resource_path('addons')}",
                f"<b>Log:</b> {os.path.abspath(LOG_FILE)}",
            ]
        )
        info_label = QtWidgets.QLabel(info_html)
        info_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        copyright_label = QtWidgets.QLabel("Copyright 2026 Crayton Litton")
        copyright_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(copyright_label)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)
        layout.addWidget(buttons)

        dlg.exec()

    def open_recording(self, required: bool = False) -> bool:
        if self.collecting:
            QtWidgets.QMessageBox.information(self, "Recording Active", "Stop data collection before opening a recording.")
            return False
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open Recording",
            "",
            "Pinpoint Playback (*.pinplyr)",
        )
        if not path:
            if required:
                QtWidgets.QMessageBox.information(
                    self,
                    "Recording Required",
                    "A recording must be selected to continue in playback-only mode.",
                )
            return False
        import_dialog = BusyDialog(
            title="Importing",
            text="Importing playback...",
            mode="import",
            parent=self,
            show_progress=True,
        )
        import_dialog.show()
        QtWidgets.QApplication.processEvents()
        load_error = None

        def _update_progress(pct: int) -> None:
            import_dialog.set_progress(pct)
            QtWidgets.QApplication.processEvents()

        try:
            header, frames, flags = self._load_pinplyr(path, progress_cb=_update_progress)
        except Exception as e:
            load_error = e
            header, frames, flags = {}, [], []
        finally:
            import_dialog.close()
            import_dialog.deleteLater()
        if load_error is not None:
            QtWidgets.QMessageBox.warning(self, "Open Failed", f"Could not open recording:\n{load_error}")
            return False
        if not frames:
            QtWidgets.QMessageBox.information(self, "Empty Recording", "This recording has no frames.")
            return False
        self.report_cache_frames = frames
        self.report_source_label = os.path.basename(path)
        self.report_cache_active = False
        self.report_header = header
        self.playback_flags = flags
        self._refresh_report_action()
        self._enter_playback(frames, header=header, flags=flags)
        return True

    def open_gps_info(self):
        dlg = GPSInfoDialog(self._get_gps_satellite_info, self._get_info_refresh_s, self)
        self._gps_info_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._gps_info_dialog = None

    def _get_latest_satellites(self):
        return list(self.latest_satellites or [])

    def _get_gps_satellite_info(self):
        return {
            "satellites": self._get_latest_satellites(),
            "count": self.latest_sats,
        }

    def _get_info_refresh_s(self):
        with settings_lock:
            return settings.info_refresh_s

    def open_antenna_info(self):
        dlg = AntennaInfoDialog(self._get_antenna_info, self._get_info_refresh_s, self)
        self._antenna_info_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._antenna_info_dialog = None

    def open_flag_dialog(self):
        if self.recording_session is None or not self.collecting:
            return
        dlg = FlagDialog(self)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            data = dlg.flag_data()
            self._record_flag(data)

    def _record_flag(self, data: dict) -> None:
        if self.recording_session is None:
            return
        reason = (data.get("reason") or "Flag").strip()
        note = (data.get("note") or "").strip()
        try:
            self.recording_session.record_flag(reason, note)
        except Exception as e:
            logger.error("Failed to record flag: %s", e)

    def _get_antenna_info(self):
        with settings_lock:
            freq = settings.frequency
            antenna_count = settings.antenna_count
            profile = settings.calibration_profile
            spacing_in = settings.antenna_spacing_in
            antenna_orientations_deg = list(settings.antenna_orientations_deg)
        return {
            "frequency_mhz": freq,
            "antenna_count": antenna_count,
            "antenna_spacing_in": spacing_in,
            "antenna_orientations_deg": antenna_orientations_deg,
            "ideal_spacing_in": _ideal_spacing_inches(freq),
            "strength": self.latest_strength,
            "snr": self.latest_snr,
            "quality": self.latest_quality,
            "sdr_connected": self.sdr_connected,
            "sdr_error": self.sdr_error,
            "sdr_sample_rate": self.sdr_sample_rate,
            "antenna_states": self.antenna_states,
            "current_bearing": self.current_bearing,
            "target_bearing": self.target_bearing,
            "target_relative": self.target_relative,
            "aoa_confidence": self.aoa_confidence,
            "map_confidence": self.map_confidence,
            "fusion_confidence": self.fusion_confidence,
            "bearing_source": self.bearing_source,
            "calibration_profile": profile,
        }

    @staticmethod
    def _load_pinplyr(path: str, progress_cb: Optional[Callable[[int], None]] = None) -> tuple[dict, list[dict], list[dict]]:
        header = {}
        frames = []
        flags = []
        frame_stride = 1
        seen_frames = 0
        total_bytes = 0
        try:
            total_bytes = os.path.getsize(path)
        except OSError:
            total_bytes = 0
        last_emit = 0
        with open(path, "rb") as f:
            for raw in f:
                if total_bytes:
                    pos = f.tell()
                    if pos - last_emit >= 256 * 1024 or pos >= total_bytes:
                        pct = int(min(100, (pos / total_bytes) * 100))
                        if progress_cb:
                            progress_cb(pct)
                        last_emit = pos
                line = raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Skipping malformed recording line near byte %s.", f.tell())
                    continue
                if not isinstance(obj, dict):
                    logger.warning("Skipping non-object recording entry near byte %s.", f.tell())
                    continue
                if obj.get("type") == "pinplyr":
                    header = obj
                elif obj.get("type") == "flag":
                    flags.append(obj)
                else:
                    telemetry = obj.get("telemetry")
                    if not isinstance(telemetry, dict):
                        logger.warning("Skipping recording frame without telemetry near byte %s.", f.tell())
                        continue
                    seen_frames += 1
                    if seen_frames % frame_stride == 0:
                        frames.append(obj)
                    if PLAYBACK_MAX_FRAMES > 0 and len(frames) > PLAYBACK_MAX_FRAMES:
                        frames = frames[::2]
                        frame_stride *= 2
        if progress_cb:
            progress_cb(100)
        return header, frames, flags

    def toggle_collection(self):
        if self.demo_active:
            QtWidgets.QMessageBox.information(
                self,
                "Demo Active",
                "Stop the demo before starting live data collection.",
            )
            return
        if self.playback_only or self.meshtastic_only:
            dlg = PlaybackOnlyDialog(self) if self.playback_only else MeshtasticOnlyDialog(self)
            dlg.exec()
            if dlg.result_choice == "rescan":
                self._rescan_hardware()
            return
        if not self.collecting:
            if self.playback_mode:
                QtWidgets.QMessageBox.information(self, "Playback Active", "Exit playback before starting collection.")
                return
            logger.info("Starting data collection.")
            self.collecting = True
            if hasattr(self, "api") and self.api:
                self.api.emit("collection.started", {"ts": time.time()})
            self._reset_report_cache()
            self._maybe_prompt_recording()
            self._set_start_state("Stop Data Collection", "danger")
            self._show_starting_dialog()
            QtWidgets.QApplication.processEvents()
            QtCore.QTimer.singleShot(0, self._start_collection_thread)
        else:
            self.stop_collection()

    def stop_collection(self):
        if self.demo_active:
            self.stop_demo()
            return
        logger.info("Stopping data collection.")
        # mark intention and disable button to avoid repeated clicks
        self.collecting = False
        # Update UI immediately to show stopping state
        self._set_start_state("Stopping...", "danger", enabled=False)

        if self.thread and self.thread.isRunning():
            self._show_stopping_dialog()
            QtWidgets.QApplication.processEvents()
            QtCore.QTimer.singleShot(0, self._request_stop)
        else:
            self._finish_stop_ui()

    def _request_stop(self):
        self.stop_event.set()

    def _on_thread_status(self, msg: str):
        if msg:
            self.status_label.setText(f"Status: {msg}")
            self.last_status_msg = msg
            self._update_info_panel()
            if self._start_dialog is not None and msg != "stopped":
                self._hide_starting_dialog()
        if msg == "stopped":
            self.collecting = False
            self._finish_stop_ui()

    def _on_thread_error(self, err: str):
        # Surface as a transient message; also in logs
        if self._start_dialog is not None:
            self._hide_starting_dialog()
        QtWidgets.QToolTip.showText(QtGui.QCursor.pos(), f"Collector error: {err}")
        self.last_status_msg = "Error"
        self._update_info_panel()
        if hasattr(self, "api") and self.api:
            self.api.emit("collection.error", {"error": err, "ts": time.time()})

    def _on_thread_finished(self):
        # Defensive: ensure UI is reset even if "stopped" status isn't emitted
        if not self.collecting:
            self._finish_stop_ui()
        # Now that the thread has fully finished, release the reference.
        self.thread = None
        QtCore.QTimer.singleShot(0, self._start_idle_gps_tracking)

    def _capture_start_state(self) -> dict:
        return {
            "text": self.start_btn.text(),
            "enabled": self.start_btn.isEnabled(),
            "class": self.start_btn.property("class"),
            "action_text": self.start_action.text() if hasattr(self, "start_action") else None,
            "action_enabled": self.start_action.isEnabled() if hasattr(self, "start_action") else None,
        }

    def _restore_start_state(self) -> None:
        if not self._demo_start_state:
            return
        state = self._demo_start_state
        cls = state.get("class") or "primary"
        self._set_start_state(state.get("text") or "Start Data Collection", cls, enabled=state.get("enabled"))
        if hasattr(self, "start_action") and self.start_action:
            action_text = state.get("action_text")
            action_enabled = state.get("action_enabled")
            if action_text is not None:
                self.start_action.setText(action_text)
            if action_enabled is not None:
                self.start_action.setEnabled(action_enabled)
        self._demo_start_state = None

    def start_demo(self, config: Optional[dict] = None) -> None:
        if self.demo_active:
            return
        if self.collecting:
            QtWidgets.QMessageBox.information(
                self,
                "Collection Active",
                "Stop live data collection before starting the demo.",
            )
            return
        if self.playback_mode:
            QtWidgets.QMessageBox.information(
                self,
                "Playback Active",
                "Exit playback before starting the demo.",
            )
            return
        self.demo_active = True
        self.collecting = True
        self._demo_stopped = False
        self._demo_start_state = self._capture_start_state()
        self._demo_config = config or {}
        if hasattr(self, "api") and self.api:
            self.api.emit("collection.started", {"ts": time.time(), "demo": True})
        self._reset_report_cache()
        self.report_source_label = "Demo Session"
        self._set_start_state("Demo Running", "primary", enabled=False)
        if hasattr(self, "start_action") and self.start_action:
            self.start_action.setEnabled(False)
        self.last_status_msg = "Demo Starting"
        self._update_info_panel()
        self._show_starting_dialog()
        QtWidgets.QApplication.processEvents()
        QtCore.QTimer.singleShot(0, self._start_demo_thread)

    def stop_demo(self) -> None:
        if not self.demo_active:
            return
        self.collecting = False
        self._set_start_state("Stopping...", "danger", enabled=False)
        if self.thread and self.thread.isRunning():
            self._show_stopping_dialog()
            QtWidgets.QApplication.processEvents()
            QtCore.QTimer.singleShot(0, self._request_stop)
        else:
            self._finish_demo_ui()

    def _start_demo_thread(self) -> None:
        if self._gps_tracking_thread is not None and self._gps_tracking_thread.isRunning():
            self._demo_start_pending = True
            self._stop_idle_gps_tracking()
            return
        self._demo_start_pending = False
        self._idle_gps_point = None
        self.update_image(force=True)
        self.stop_event.clear()
        self.thread = DemoCollectorThread(
            logger=logger,
            stop_event=self.stop_event,
            config=self._demo_config,
        )
        self.thread.status.connect(self._on_demo_status)
        self.thread.error.connect(self._on_thread_error)
        self.thread.telemetry.connect(self._on_telemetry)
        self.thread.finished.connect(self._on_demo_finished)
        self.thread.start()

    def _on_demo_status(self, msg: str) -> None:
        if msg == "stopped":
            self._demo_stopped = True
            self._finish_demo_ui()
            return
        if msg:
            self._on_thread_status(msg)

    def _on_demo_finished(self) -> None:
        if not self._demo_stopped:
            self._finish_demo_ui()
        self._demo_stopped = False
        self.thread = None
        QtCore.QTimer.singleShot(0, self._start_idle_gps_tracking)

    def _finish_demo_ui(self) -> None:
        if not self.demo_active and not self._demo_start_state:
            return
        self.demo_active = False
        self._hide_starting_dialog()
        self._hide_stopping_dialog()
        self._stop_recording()
        self._finalize_report_cache()
        self.collecting = False
        self.last_status_msg = "stopped"
        self._restore_start_state()
        self._update_info_panel()
        if hasattr(self, "api") and self.api:
            self.api.emit("collection.stopped", {"ts": time.time(), "demo": True})

    def _rescan_hardware(self):
        if self._main_hw_thread is not None:
            try:
                if self._main_hw_thread.isRunning():
                    return
            except RuntimeError:
                self._main_hw_thread = None
        self._rescan_dialog = BusyDialog(
            title="Rescanning",
            text="Rescanning devices...",
            mode="general",
            parent=self,
        )
        self._rescan_dialog.show()
        QtWidgets.QApplication.processEvents()
        self._main_hw_thread = HardwareCheckThread(self)
        self._main_hw_thread.result.connect(self._on_main_hardware_check)
        self._main_hw_thread.finished.connect(self._on_main_hardware_check_finished)
        self._main_hw_thread.start()

    def _on_main_hardware_check(self, has_sdr: bool, has_gps: bool):
        if self._rescan_dialog is not None:
            self._rescan_dialog.close()
            self._rescan_dialog.deleteLater()
            self._rescan_dialog = None
        if has_sdr or has_gps:
            self._reinitialize_hardware()
        else:
            if self.playback_only:
                dlg = PlaybackOnlyDialog(self)
            elif self.meshtastic_only:
                dlg = MeshtasticOnlyDialog(self)
            else:
                dlg = PlaybackOnlyDialog(self)
            dlg.exec()
            if dlg.result_choice == "rescan":
                QtCore.QTimer.singleShot(150, self._rescan_hardware)

    def _on_main_hardware_check_finished(self):
        self._main_hw_thread = None

    def _reinitialize_hardware(self):
        dlg = GPSStartupDialog(parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        if dlg.playback_only():
            self.playback_only = True
            self.meshtastic_only = False
            self._stop_idle_gps_tracking()
            self._idle_gps_point = None
            self._set_start_state("Playback Only", "primary", enabled=False)
            if hasattr(self, "start_action") and self.start_action:
                self.start_action.setEnabled(False)
            self._update_info_panel()
            return

        if dlg.meshtastic_only():
            self.meshtastic_only = True
            self.playback_only = False
            self._stop_idle_gps_tracking()
            self._idle_gps_point = None
            self._set_start_state("Meshtastic Only", "primary", enabled=False)
            if hasattr(self, "start_action") and self.start_action:
                self.start_action.setEnabled(False)
            self._update_info_panel()
            return

        self.playback_only = False
        self.meshtastic_only = False
        self.gps_port = dlg.selected_port()
        if self.gps_port:
            os.environ["GPS_PORT"] = self.gps_port
            if dlg.remember_port():
                with settings_lock:
                    settings.preferred_gps_port = self.gps_port
                save_settings()
        if self._hardware_monitor_thread is not None:
            self._hardware_monitor_thread.gps_port = self.gps_port
        self._stop_idle_gps_tracking()
        self._set_start_state("Start Data Collection", "primary", enabled=True)
        if hasattr(self, "start_action") and self.start_action:
            self.start_action.setEnabled(True)
        self._update_info_panel()
        QtCore.QTimer.singleShot(0, self._start_idle_gps_tracking)

    # ---------- Report cache ----------
    def _reset_report_cache(self):
        self.report_cache_frames = []
        if hasattr(self, "point_inspector_stack"):
            self._clear_point_inspector()
        self.report_cache_active = True
        self.report_cache_started_at = time.time()
        self.report_source_label = "Live Collection"
        self.report_header = None
        self._refresh_report_action()

    def _finalize_report_cache(self):
        self.report_cache_active = False
        if not self.report_cache_frames:
            self.report_source_label = None
        self._refresh_report_action()

    def _cache_report_frame(self, telemetry: dict):
        if not self.report_cache_active:
            return
        if self.report_cache_started_at is None:
            self.report_cache_started_at = time.time()
        t = time.time() - self.report_cache_started_at
        self.report_cache_frames.append({"t": round(t, 3), "telemetry": telemetry})
        max_frames = REPORT_CACHE_MAX_FRAMES
        if max_frames and max_frames > 0 and len(self.report_cache_frames) > max_frames:
            self.report_cache_frames = self.report_cache_frames[-max_frames:]

    def _report_available(self) -> bool:
        return bool(self.report_cache_frames) and not self.collecting

    def _refresh_report_action(self):
        if hasattr(self, "addon_manager") and self.addon_manager:
            self.addon_manager.refresh_enabled_states()

    def _get_history_points(self) -> list[dict]:
        frames = list(self.report_cache_frames or [])
        if getattr(self, "playback_mode", False) and getattr(self, "playback_frames", None):
            frames = list(self.playback_frames[: self.playback_index + 1])
        points = []
        details: dict[str, dict] = {}
        for frame_index, frame in enumerate(frames):
            tele = frame.get("telemetry") or {}
            if tele.get("gps_fix") is False or tele.get("cycle_paused"):
                continue
            gps_loc = tele.get("gps_loc")
            if not gps_loc:
                continue
            try:
                raw_lat, raw_lon = gps_loc
                lat, lon = float(raw_lat), float(raw_lon)
            except (TypeError, ValueError):
                continue
            identity_ts = tele.get("measurement_ts", frame.get("t", frame_index))
            point_id = f"fix:{identity_ts}:{lat:.8f}:{lon:.8f}"
            point = {
                "point_id": point_id,
                "t": frame.get("t"),
                "lat": lat,
                "lon": lon,
                "strength": tele.get("strength"),
                "snr": tele.get("snr"),
                "quality": tele.get("quality"),
                "gps_fix": tele.get("gps_fix"),
                "sats": tele.get("sats"),
                "bearing_source": tele.get("bearing_source"),
            }
            points.append(point)
            details[point_id] = {
                "point": {
                    "source": "Recorded collection fix",
                    "frame_index": frame_index,
                    "elapsed_s": frame.get("t"),
                    "latitude": lat,
                    "longitude": lon,
                },
                "telemetry": dict(tele),
            }
            recorded_alerts = MainWindow._normalize_recorded_alerts(frame.get("alerts"))
            if recorded_alerts:
                details[point_id]["field_alerts"] = recorded_alerts
        if self._idle_gps_point and not self.collecting and not self.playback_mode:
            point = dict(self._idle_gps_point)
            point_id = "idle-current"
            point["point_id"] = point_id
            points.append(point)
            idle_telemetry = dict(getattr(self, "latest_telemetry", {}) or {})
            for key, value in point.items():
                if key not in {"point_id", "location_only"}:
                    idle_telemetry.setdefault(key, value)
            details[point_id] = {
                "point": {
                    "source": "Current idle GPS fix",
                    "observed_at": point.get("t"),
                    "latitude": point.get("lat"),
                    "longitude": point.get("lon"),
                },
                "telemetry": idle_telemetry,
            }
        self._map_point_details = details
        if (
            getattr(self, "_selected_map_point_id", None) == "idle-current"
            and "idle-current" in details
            and hasattr(self, "point_inspector_tree")
        ):
            self._show_map_point_detail("idle-current", preserve_state=True)
        return points

    @staticmethod
    def _humanize_point_field(field: object) -> str:
        text = str(field).strip().replace("_", " ")
        replacements = {
            "gps": "GPS",
            "sdr": "SDR",
            "snr": "SNR",
            "aoa": "Amplitude DF",
            "hdop": "HDOP",
            "fft": "FFT",
            "id": "ID",
            "ms": "ms",
            "mhz": "MHz",
            "deg": "deg",
        }
        return " ".join(replacements.get(part.lower(), part.capitalize()) for part in text.split())

    @staticmethod
    def _format_point_value(value: object, field: str = "") -> str:
        if value is None:
            return "--"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, float):
            if field.endswith("_ts") or field in {"observed_at"}:
                try:
                    return datetime.datetime.fromtimestamp(value).astimezone().isoformat(timespec="milliseconds")
                except (OSError, OverflowError, ValueError):
                    pass
            return f"{value:.8g}"
        return str(value)

    def _add_point_detail_tree_item(
        self,
        parent: QtWidgets.QTreeWidget | QtWidgets.QTreeWidgetItem,
        field: object,
        value: object,
        path: tuple[str, ...] = (),
    ) -> None:
        label = self._humanize_point_field(field)
        item_path = path + (label,)
        if isinstance(value, dict):
            item = QtWidgets.QTreeWidgetItem([label, f"{len(value)} fields"])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "\x1f".join(item_path))
            parent.addChild(item) if isinstance(parent, QtWidgets.QTreeWidgetItem) else parent.addTopLevelItem(item)
            for child_field, child_value in value.items():
                self._add_point_detail_tree_item(item, child_field, child_value, item_path)
            return
        if isinstance(value, (list, tuple)):
            item = QtWidgets.QTreeWidgetItem([label, f"{len(value)} items"])
            item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "\x1f".join(item_path))
            parent.addChild(item) if isinstance(parent, QtWidgets.QTreeWidgetItem) else parent.addTopLevelItem(item)
            for index, child_value in enumerate(value):
                child_label = f"Antenna {index + 1}" if field == "antenna_states" else f"Item {index + 1}"
                self._add_point_detail_tree_item(item, child_label, child_value, item_path)
            return
        item = QtWidgets.QTreeWidgetItem([label, self._format_point_value(value, str(field))])
        item.setData(0, QtCore.Qt.ItemDataRole.UserRole, "\x1f".join(item_path))
        item.setToolTip(1, item.text(1))
        parent.addChild(item) if isinstance(parent, QtWidgets.QTreeWidgetItem) else parent.addTopLevelItem(item)

    def _point_inspector_expanded_paths(self) -> set[str]:
        expanded = set()
        iterator = QtWidgets.QTreeWidgetItemIterator(self.point_inspector_tree)
        while iterator.value() is not None:
            item = iterator.value()
            if item.isExpanded():
                path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
                if path:
                    expanded.add(str(path))
            iterator += 1
        return expanded

    def _restore_point_inspector_expanded_paths(self, expanded: set[str]) -> None:
        iterator = QtWidgets.QTreeWidgetItemIterator(self.point_inspector_tree)
        while iterator.value() is not None:
            item = iterator.value()
            path = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
            item.setExpanded(bool(path and str(path) in expanded))
            iterator += 1

    def _show_map_point_detail(self, point_id: str, preserve_state: bool = False) -> None:
        detail = self._map_point_details.get(point_id)
        if not detail:
            return
        expanded = self._point_inspector_expanded_paths() if preserve_state else set()
        scroll_bar = self.point_inspector_tree.verticalScrollBar()
        scroll_position = scroll_bar.value() if preserve_state else 0
        telemetry = detail.get("telemetry") or {}
        point = detail.get("point") or {}
        lat = point.get("latitude")
        lon = point.get("longitude")
        signal = telemetry.get("strength")
        snr = telemetry.get("snr")
        source = point.get("source", "Map fix")
        self.point_inspector_title.setText("Selected Fix")
        self.point_inspector_summary.setText(
            f"{source}\n{self._format_coord_pair(lat, lon)}\n"
            f"Signal: {self._format_point_value(signal)}  |  SNR: {self._format_point_value(snr)}"
        )
        self.point_inspector_tree.setUpdatesEnabled(False)
        try:
            self.point_inspector_tree.clear()
            for section, value in detail.items():
                self._add_point_detail_tree_item(self.point_inspector_tree, section, value)
            if preserve_state:
                self._restore_point_inspector_expanded_paths(expanded)
                scroll_bar.setValue(min(scroll_position, scroll_bar.maximum()))
            else:
                self.point_inspector_tree.expandToDepth(1)
        finally:
            self.point_inspector_tree.setUpdatesEnabled(True)
            self.point_inspector_tree.viewport().update()
        self.point_inspector_stack.setCurrentWidget(self.point_inspector_content)

    @staticmethod
    def _format_coord_pair(lat: object, lon: object) -> str:
        try:
            return f"{float(lat):.7f}, {float(lon):.7f}"
        except (TypeError, ValueError):
            return "Coordinates unavailable"

    def _select_map_point(self, point_id: str) -> None:
        point_id = str(point_id)
        if point_id not in self._map_point_details:
            # A click can race the one-second map refresh; rebuild the cache once.
            self._get_history_points()
        if point_id not in self._map_point_details:
            return
        preserve_state = self._selected_map_point_id == point_id
        self._selected_map_point_id = point_id
        self._show_map_point_detail(point_id, preserve_state=preserve_state)

    def _clear_point_inspector(self) -> None:
        self._selected_map_point_id = None
        self.point_inspector_tree.clear()
        self.point_inspector_summary.clear()
        self.point_inspector_stack.setCurrentWidget(self.point_inspector_blank)
        if getattr(self, "map_view", None) is not None and getattr(self, "_map_ready", False):
            self.map_view.page().runJavaScript(
                "if (window.clearPointSelection) { window.clearPointSelection(); }"
            )

    def _get_report_data(self) -> dict:
        with settings_lock:
            s = settings.to_dict()
        if self.report_header and isinstance(self.report_header.get("settings"), dict):
            s = self.report_header.get("settings") or s
        map_png_b64 = self._capture_report_map_png_b64()
        # Recordings can supply an embedded map when no live interactive view
        # is available (for example, in playback-only mode).
        if not map_png_b64:
            for frame in reversed(self.report_cache_frames):
                if frame.get("map_png"):
                    map_png_b64 = frame.get("map_png")
                    break
        start_time = None
        if self.report_header and self.report_header.get("created_utc"):
            start_time = self.report_header.get("created_utc")
        elif self.report_cache_started_at:
            start_time = datetime.datetime.fromtimestamp(self.report_cache_started_at).isoformat()
        return {
            "frames": list(self.report_cache_frames),
            "source": self.report_source_label or "Session",
            "settings": s,
            "default_logo": PINPOINT_IMAGE_FALLBACK,
            "map_path": IMAGE_PATH,
            "map_png_b64": map_png_b64,
            "app_version": APP_VERSION,
            "start_time": start_time,
        }

    def _capture_report_map_png_b64(self) -> Optional[str]:
        """Capture the map currently visible to the operator for the report."""
        if not (
            getattr(self, "_interactive_map_enabled", False)
            and getattr(self, "_map_ready", False)
            and getattr(self, "map_view", None) is not None
        ):
            return None
        try:
            pixmap = self.map_view.grab()
            if pixmap.isNull():
                return None
            buffer = QtCore.QBuffer()
            if not buffer.open(QtCore.QIODevice.OpenModeFlag.WriteOnly):
                return None
            if not pixmap.save(buffer, "PNG"):
                return None
            return base64.b64encode(bytes(buffer.data())).decode("ascii")
        except Exception:
            logger.exception("Could not capture the interactive map for the report.")
            return None

    # ---------- Playback ----------
    def _enter_playback(self, frames: list[dict], header: Optional[dict] = None, flags: Optional[list[dict]] = None):
        self.playback_mode = True
        self._clear_point_inspector()
        self._stop_idle_gps_tracking()
        self._idle_gps_point = None
        self.playback_frames = frames
        self.playback_flags = list(flags or [])
        self.playback_index = 0
        self.playback_speed_factor = 1.0
        self._playback_playing = False
        self._playback_last_map_bytes = None
        self._playback_render_cache = {}
        self.playback_alerts = []
        self.playback_slider.setMinimum(0)
        self.playback_slider.setMaximum(max(0, len(frames) - 1))
        self.playback_slider.setValue(0)
        total_time = self._frame_time(len(frames) - 1) if frames else 0.0
        self.playback_slider.set_flags(self.playback_flags, total_time)
        self.playback_speed.setCurrentText("1x")
        self.playback_widget.setVisible(True)
        self.start_btn.setEnabled(False)
        self.open_recording_btn.setEnabled(False)
        if hasattr(self, "start_action"):
            self.start_action.setEnabled(False)
        if hasattr(self, "open_recording_action"):
            self.open_recording_action.setEnabled(False)
        if hasattr(self, "exit_playback_action"):
            self.exit_playback_action.setEnabled(True)
        self.image_timer.stop()
        self._refresh_map_mode(force=True)
        self._apply_playback_frame(0)
        self._update_playback_time_label()
        self._update_info_panel()
        self._refresh_report_action()
        if hasattr(self, "api") and self.api:
            self.api.emit("playback.started", {"ts": time.time(), "frames": len(frames)})

    def _exit_playback(self):
        if not self.playback_mode:
            return
        self._pause_playback()
        self.playback_mode = False
        self._clear_point_inspector()
        self.playback_frames = []
        self.playback_flags = []
        self.playback_index = 0
        self.playback_widget.setVisible(False)
        self.start_btn.setEnabled(True)
        self.open_recording_btn.setEnabled(True)
        if hasattr(self, "start_action"):
            self.start_action.setEnabled(True)
        if hasattr(self, "open_recording_action"):
            self.open_recording_action.setEnabled(True)
        if hasattr(self, "exit_playback_action"):
            self.exit_playback_action.setEnabled(False)
        self._playback_last_map_bytes = None
        self._playback_render_cache = {}
        self.playback_alerts = []
        self.playback_slider.set_flags([], 0.0)
        self.update_image(force=True)
        self.image_timer.start()
        self._update_info_panel()
        self._refresh_report_action()
        self._refresh_map_mode(force=True)
        if hasattr(self, "api") and self.api:
            self.api.emit("playback.stopped", {"ts": time.time()})
        QtCore.QTimer.singleShot(0, self._start_idle_gps_tracking)

    def _toggle_playback(self):
        if not self.playback_mode or not self.playback_frames:
            return
        if self._playback_playing:
            self._pause_playback()
        else:
            self._start_playback()

    def _start_playback(self):
        if not self.playback_frames:
            return
        self._playback_playing = True
        self.playback_play_btn.setText("Pause")
        self._playback_start_wall = time.time()
        self._playback_start_t = self._frame_time(self.playback_index)
        self.playback_timer.start()

    def _pause_playback(self):
        self._playback_playing = False
        self.playback_play_btn.setText("Play")
        self.playback_timer.stop()

    def _on_playback_tick(self):
        if not self._playback_playing or not self.playback_frames:
            return
        now = time.time()
        playback_t = self._playback_start_t + (now - self._playback_start_wall) * self.playback_speed_factor
        last_idx = len(self.playback_frames) - 1
        last_t = self._frame_time(last_idx)
        if playback_t >= last_t:
            self._set_playback_index(last_idx)
            self._pause_playback()
            return
        # advance index while time passes
        while self.playback_index < last_idx and self._frame_time(self.playback_index + 1) <= playback_t:
            self.playback_index += 1
            self._apply_playback_frame(self.playback_index)
        self._update_playback_slider()
        self._update_playback_time_label()

    def _frame_time(self, idx: int) -> float:
        try:
            return float(self.playback_frames[idx].get("t", idx))
        except Exception:
            return float(idx)

    def _apply_playback_frame(self, idx: int):
        frame = self.playback_frames[idx]
        self.playback_alerts = self._normalize_recorded_alerts(frame.get("alerts"))
        telemetry = frame.get("telemetry") or {}
        self._apply_telemetry(telemetry)
        if self._interactive_map_enabled and self.map_view is not None:
            self._update_interactive_map(force=True)
        else:
            self._show_playback_static_map(idx)
        self._refresh_info_dialogs()

    def _show_playback_static_map(self, idx: int) -> None:
        png_bytes = self._render_playback_map(idx)
        if png_bytes:
            self._playback_last_map_bytes = png_bytes
            self._set_map_image_bytes(png_bytes)
            return
        self.image_label.clear()
        self.image_label.setText("No GPS map data in this recording frame.")

    def _playback_history_at(self, idx: int) -> dict:
        history = {}
        frames = self.playback_frames[: max(0, idx) + 1]
        for frame_index, frame in enumerate(frames):
            telemetry = frame.get("telemetry") or {}
            if telemetry.get("gps_fix") is False or telemetry.get("cycle_paused"):
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
        if HISTORY_MAX_POINTS and len(history) > HISTORY_MAX_POINTS:
            items = sorted(history.items(), key=lambda item: item[1].get("ts") or 0)
            history = dict(items[-HISTORY_MAX_POINTS:])
        return history

    def _embedded_playback_map_at(self, idx: int) -> Optional[bytes]:
        for frame in reversed(self.playback_frames[: max(0, idx) + 1]):
            encoded = frame.get("map_png")
            if not encoded:
                continue
            try:
                return base64.b64decode(encoded, validate=True)
            except (ValueError, TypeError, base64.binascii.Error):
                continue
        return None

    def _render_playback_map(self, idx: int) -> Optional[bytes]:
        cached = self._playback_render_cache.get(idx)
        if cached:
            return cached
        history = self._playback_history_at(idx)
        playback_alerts = list(getattr(self, "playback_alerts", []) or [])
        png_bytes = None
        if history:
            try:
                png_bytes = funcs.renderOfflineMapBytes(history, alerts=playback_alerts)
            except Exception:
                logger.exception("Could not reconstruct playback map from recorded GPS fixes.")
        if not png_bytes:
            png_bytes = self._embedded_playback_map_at(idx)
            if png_bytes and playback_alerts:
                try:
                    png_bytes = funcs.overlayAlertsOnMapBytes(png_bytes, playback_alerts)
                except Exception:
                    logger.exception("Could not overlay recorded alerts on the embedded playback map.")
        if png_bytes:
            self._playback_render_cache[idx] = png_bytes
            while len(self._playback_render_cache) > 12:
                self._playback_render_cache.pop(next(iter(self._playback_render_cache)))
        return png_bytes

    def _set_playback_index(self, idx: int):
        self.playback_index = max(0, min(idx, len(self.playback_frames) - 1))
        self._apply_playback_frame(self.playback_index)
        self._update_playback_slider()
        self._update_playback_time_label()
        if self._playback_playing:
            self._playback_start_wall = time.time()
            self._playback_start_t = self._frame_time(self.playback_index)

    def _update_playback_slider(self):
        if self._playback_slider_dragging:
            return
        self.playback_slider.blockSignals(True)
        self.playback_slider.setValue(self.playback_index)
        self.playback_slider.blockSignals(False)

    def _on_playback_scrub(self, value: int):
        if not self.playback_frames:
            return
        if self._playback_slider_dragging or not self._playback_playing:
            self._set_playback_index(value)

    def _on_playback_slider_pressed(self):
        self._playback_slider_dragging = True
        self._slider_was_playing = self._playback_playing
        self._pause_playback()

    def _on_playback_slider_released(self):
        self._playback_slider_dragging = False
        if getattr(self, "_slider_was_playing", False):
            self._start_playback()

    def _on_playback_speed_changed(self, text: str):
        try:
            self.playback_speed_factor = float(text.replace("x", "").strip())
        except Exception:
            self.playback_speed_factor = 1.0
        if self._playback_playing:
            self._playback_start_wall = time.time()
            self._playback_start_t = self._frame_time(self.playback_index)
        self._update_info_panel()

    def _update_playback_time_label(self):
        if not self.playback_frames:
            self.playback_time_label.setText("00:00 / 00:00")
            return
        cur = self._frame_time(self.playback_index)
        total = self._frame_time(len(self.playback_frames) - 1)
        self.playback_time_label.setText(f"{self._fmt_time(cur)} / {self._fmt_time(total)}")

    def _refresh_info_dialogs(self, force: bool = False) -> None:
        if self._gps_info_dialog is None and self._antenna_info_dialog is None:
            return
        now = time.time()
        min_interval = 0.15
        if self.playback_speed_factor >= 16:
            min_interval = 0.08
        elif self.playback_speed_factor >= 8:
            min_interval = 0.1
        if not force and (now - self._last_info_dialog_refresh) < min_interval:
            return
        self._last_info_dialog_refresh = now
        if self._gps_info_dialog is not None:
            self._gps_info_dialog.refresh()
        if self._antenna_info_dialog is not None:
            self._antenna_info_dialog.refresh()

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f"{m:02d}:{s:02d}"

    def _start_idle_gps_tracking(self) -> None:
        if (
            self._closing
            or self.collecting
            or self.demo_active
            or self.playback_mode
            or self.playback_only
            or self.meshtastic_only
            or not self.gps_port
        ):
            return
        if self._gps_tracking_thread is not None:
            if self._gps_tracking_thread.isRunning():
                return
            self._gps_tracking_thread = None

        self._gps_tracking_stop_event.clear()
        self._gps_tracking_thread = GPSLocationThread(
            logger=logger,
            stop_event=self._gps_tracking_stop_event,
            gps_port=self.gps_port,
            parent=self,
        )
        self._gps_tracking_thread.telemetry.connect(self._on_idle_gps_telemetry)
        self._gps_tracking_thread.error.connect(self._on_idle_gps_error)
        self._gps_tracking_thread.finished.connect(self._on_idle_gps_finished)
        self._gps_tracking_thread.start()

    def _start_hardware_presence_monitor(self) -> None:
        if self._closing:
            return
        if self._hardware_monitor_thread is not None:
            if self._hardware_monitor_thread.isRunning():
                self._hardware_monitor_thread.gps_port = self.gps_port
                return
            self._hardware_monitor_thread = None

        self._hardware_monitor_stop_event.clear()
        self._hardware_monitor_thread = HardwarePresenceThread(
            stop_event=self._hardware_monitor_stop_event,
            gps_port=self.gps_port,
            interval_s=1.0,
            parent=self,
        )
        self._hardware_monitor_thread.presence.connect(self._on_hardware_presence)
        self._hardware_monitor_thread.finished.connect(self._on_hardware_monitor_finished)
        self._hardware_monitor_thread.start()

    def _on_hardware_presence(self, sdr_present: bool, gps_present: bool) -> None:
        if self.collecting or self.demo_active or self.playback_mode:
            return

        changed = self.sdr_connected != sdr_present
        if changed:
            self.sdr_connected = sdr_present
            self.sdr_sample_rate = None
            if sdr_present:
                self.sdr_error = None
            else:
                self.sdr_error = "SDR disconnected"
                self.antenna_states = []

        if self.gps_port and not gps_present:
            gps_changed = self.latest_gps_fix is not False or self._idle_gps_point is not None
            self.latest_gps_fix = False
            self.latest_gps_loc = None
            self.latest_sats = None
            self.latest_fix_age = None
            self.latest_satellites = []
            self.latest_cycle_paused = False
            self.latest_pause_reason = None
            self._idle_gps_point = None
            if getattr(self, "_selected_map_point_id", None) == "idle-current":
                self._clear_point_inspector()
            changed = changed or gps_changed

        if changed:
            update_alerts = getattr(self, "_update_alert_manager", None)
            if callable(update_alerts):
                update_alerts({})
            self._update_info_panel()
            self._refresh_info_dialogs(force=True)
            self.update_image(force=True)

    def _on_hardware_monitor_finished(self) -> None:
        thread = self._hardware_monitor_thread
        self._hardware_monitor_thread = None
        if thread is not None:
            thread.deleteLater()

    def _stop_idle_gps_tracking(self) -> None:
        self._gps_tracking_stop_event.set()

    def _on_idle_gps_telemetry(self, data: dict) -> None:
        if self.collecting or self.demo_active or self.playback_mode:
            return
        gps_loc = data.get("gps_loc")
        if gps_loc:
            try:
                lat, lon = gps_loc
                self._idle_gps_point = {
                    "t": time.time(),
                    "lat": lat,
                    "lon": lon,
                    "sats": data.get("sats"),
                    "gps_fix": data.get("gps_fix"),
                    "fix_age_s": data.get("fix_age_s"),
                    "location_only": True,
                }
            except (TypeError, ValueError):
                pass
        self._apply_telemetry(data)
        if hasattr(self, "api") and self.api:
            self.api.emit("gps.position", data)

    def _on_idle_gps_error(self, error: str) -> None:
        logger.warning("Idle GPS tracking error: %s", error)
        if not self.collecting and not self.demo_active and not self.playback_mode:
            self.latest_gps_fix = False
            self.latest_gps_loc = None
            self._idle_gps_point = None
            if self._selected_map_point_id == "idle-current":
                self._clear_point_inspector()
            self._update_info_panel()
            self.update_image(force=True)

    def _on_idle_gps_finished(self) -> None:
        thread = self._gps_tracking_thread
        self._gps_tracking_thread = None
        if thread is not None:
            thread.deleteLater()

        if self._closing:
            return
        if self._collection_start_pending:
            self._collection_start_pending = False
            if self.collecting:
                QtCore.QTimer.singleShot(0, self._start_collection_thread)
                return
        if self._demo_start_pending:
            self._demo_start_pending = False
            if self.demo_active:
                QtCore.QTimer.singleShot(0, self._start_demo_thread)
                return
        QtCore.QTimer.singleShot(0, self._start_idle_gps_tracking)

    def _start_collection_thread(self):
        if self._gps_tracking_thread is not None and self._gps_tracking_thread.isRunning():
            self._collection_start_pending = True
            self._stop_idle_gps_tracking()
            return
        self._collection_start_pending = False
        self._idle_gps_point = None
        self.update_image(force=True)
        self.stop_event.clear()
        self.thread = CollectorThread(logger=logger, stop_event=self.stop_event, gps_port=self.gps_port)
        self.thread.status.connect(self._on_thread_status)
        self.thread.error.connect(self._on_thread_error)
        self.thread.telemetry.connect(self._on_telemetry)
        self.thread.finished.connect(self._on_thread_finished)
        self.thread.start()

    def _maybe_prompt_recording(self):
        dlg = RecordingPromptDialog(self)
        dlg.exec()
        if not dlg.result_record:
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save Recording",
            "session.pinplyr",
            "Pinpoint Playback (*.pinplyr)",
        )
        if not path:
            return
        if not path.lower().endswith(".pinplyr"):
            path += ".pinplyr"
        self._start_recording(path)

    def _start_recording(self, path: str):
        try:
            with settings_lock:
                snapshot = settings.to_dict()
            self.recording_session = RecordingSession(path, settings_snapshot=snapshot, app_version=APP_VERSION)
            self.recording_path = path
            logger.info("Recording started: %s", path)
            self._update_info_panel()
        except Exception as e:
            self.recording_session = None
            self.recording_path = None
            logger.error("Failed to start recording: %s", e)
            QtWidgets.QMessageBox.warning(self, "Recording Failed", f"Could not start recording:\n{e}")

    def _stop_recording(self):
        if self.recording_session is not None:
            try:
                dropped_frames = self.recording_session.dropped_frames
                self.recording_session.close()
                logger.info("Recording saved: %s", self.recording_path)
                if dropped_frames:
                    logger.warning("Recording queue dropped %d frame(s).", dropped_frames)
            except Exception as e:
                logger.error("Failed to close recording: %s", e)
            finally:
                self.recording_session = None
                self.recording_path = None
                self._update_info_panel()

    def _show_starting_dialog(self):
        if self._start_dialog is None:
            self._start_dialog = BusyDialog(
                title="Starting",
                text="Starting Data Collection...",
                mode="starting",
                parent=self,
            )
        self._start_dialog.show()

    def _hide_starting_dialog(self):
        if self._start_dialog is not None:
            self._start_dialog.close()
            self._start_dialog.deleteLater()
            self._start_dialog = None

    def _show_stopping_dialog(self):
        if self._stop_dialog is None:
            self._stop_dialog = BusyDialog(
                title="Stopping",
                text="Stopping Data Collection...",
                mode="stopping",
                parent=self,
            )
        self._stop_dialog.show()

    def _hide_stopping_dialog(self):
        if self._stop_dialog is not None:
            self._stop_dialog.close()
            self._stop_dialog.deleteLater()
            self._stop_dialog = None

    def _finish_stop_ui(self):
        self._hide_stopping_dialog()
        self._stop_recording()
        self._finalize_report_cache()
        self._set_start_state("Start Data Collection", "primary", enabled=True)
        if hasattr(self, "api") and self.api:
            self.api.emit("collection.stopped", {"ts": time.time()})

    def _on_telemetry(self, data: dict):
        if self.playback_mode:
            return
        self._apply_telemetry(data)
        field_alerts = self._get_map_notifications()
        if self.recording_session is not None:
            try:
                self.recording_session.record_frame(data, alerts=field_alerts)
            except Exception as e:
                logger.error("Failed to record frame: %s", e)
        self._cache_report_frame(data)
        if hasattr(self, "api") and self.api:
            self.api.emit("telemetry", data)

    def _apply_telemetry(self, data: dict):
        self.latest_telemetry = dict(data)
        gps_fix = data.get("gps_fix")
        gps_loc = data.get("gps_loc")
        sats = data.get("sats")
        fix_age = data.get("fix_age_s")
        strength = data.get("strength")
        snr = data.get("snr")
        quality = data.get("quality")
        satellites = data.get("satellites")
        sdr_connected = data.get("sdr_connected")
        sdr_error = data.get("sdr_error")
        sdr_sample_rate = data.get("sdr_sample_rate")
        antenna_count = data.get("antenna_count")
        current_bearing = data.get("current_bearing")
        target_bearing = data.get("target_bearing")
        target_relative = data.get("target_relative")
        antenna_states = data.get("antenna_states")
        aoa_conf = data.get("aoa_confidence")
        map_conf = data.get("map_confidence")
        fusion_conf = data.get("fusion_confidence")
        bearing_source = data.get("bearing_source")
        if "gps_hdop" in data:
            self.latest_gps_hdop = data.get("gps_hdop")
        if "gps_accuracy_m" in data:
            self.latest_gps_accuracy_m = data.get("gps_accuracy_m")
        if "target_estimate" in data:
            self.latest_target_estimate = data.get("target_estimate")
        self.latest_cycle_paused = bool(data.get("cycle_paused", False))
        self.latest_pause_reason = data.get("pause_reason") if self.latest_cycle_paused else None
        if gps_loc is not None:
            self.latest_gps_loc = gps_loc
        if satellites:
            self.latest_satellites = satellites
        if strength is not None:
            self.latest_strength = strength
        if snr is not None:
            self.latest_snr = snr
        if quality is not None:
            self.latest_quality = quality
        if "sdr_connected" in data:
            self.sdr_connected = bool(sdr_connected)
        if "sdr_error" in data:
            self.sdr_error = sdr_error
        if "sdr_sample_rate" in data:
            self.sdr_sample_rate = sdr_sample_rate
        if antenna_count is not None:
            self.antenna_count = int(antenna_count)
        if antenna_states is not None:
            self.antenna_states = antenna_states
        if "current_bearing" in data:
            self.current_bearing = current_bearing
        if "target_bearing" in data:
            self.target_bearing = target_bearing
        if "target_relative" in data:
            self.target_relative = target_relative
        if "aoa_confidence" in data:
            self.aoa_confidence = aoa_conf
        if "map_confidence" in data:
            self.map_confidence = map_conf
        if "fusion_confidence" in data:
            self.fusion_confidence = fusion_conf
        if "bearing_source" in data:
            self.bearing_source = bearing_source
        effective_gps_fix = gps_fix
        if (
            not effective_gps_fix
            and gps_loc is not None
            and fix_age is not None
            and fix_age <= GPS_FIX_STALE_S
        ):
            effective_gps_fix = True
        if effective_gps_fix is not None:
            self.latest_gps_fix = bool(effective_gps_fix)
        if sats is not None:
            self.latest_sats = sats
        if fix_age is not None:
            self.latest_fix_age = fix_age
        if effective_gps_fix:
            self.gps_label.setText(f"GPS: FIX (sats={sats})")
        else:
            if fix_age is not None:
                self.gps_label.setText(f"GPS: last fix {fix_age:.0f}s ago")
            else:
                self.gps_label.setText("GPS: no fix")
        if strength is not None:
            snr_text = "--" if snr is None else f"{snr:.2f}"
            self.status_label.setText(f"Status: S={strength}  SNR={snr_text}")
        self._update_alert_manager(data)
        self._update_info_panel()
        self._refresh_info_dialogs()
    def clear_app(self):
        # Reset map image
        try:
            if os.path.exists(PINPOINT_IMAGE_FALLBACK):
                shutil.copy(PINPOINT_IMAGE_FALLBACK, IMAGE_PATH)
        except Exception as e:
            logger.warning(f"Could not copy fallback image: {e}")

        # Reset log file
        reset_log_file()

        logger.info("Application cleared.")
        self._clear_point_inspector()
        # Force an immediate image update
        self.update_image(force=True)

    # ---------- Image handling ----------
    @staticmethod
    def _static_point_positions(points: list[dict]) -> list[tuple[dict, float, float]]:
        """Approximate the static renderer's auto-fit projection in normalized coordinates."""
        valid = [p for p in points if p.get("lat") is not None and p.get("lon") is not None]
        if not valid:
            return []
        lats = [float(p["lat"]) for p in valid]
        lons = [float(p["lon"]) for p in valid]
        min_lat, max_lat = min(lats), max(lats)
        min_lon, max_lon = min(lons), max(lons)
        raw_lat_span = max_lat - min_lat
        raw_lon_span = max_lon - min_lon
        lat_padding = raw_lat_span * 0.08 if raw_lat_span > 0.0 else 0.5e-6
        lon_padding = raw_lon_span * 0.08 if raw_lon_span > 0.0 else 0.5e-6
        min_lat -= lat_padding
        max_lat += lat_padding
        min_lon -= lon_padding
        max_lon += lon_padding
        lat_span = max(1e-6, max_lat - min_lat)
        lon_span = max(1e-6, max_lon - min_lon)
        # funcs.map's offline renderer uses a 24 px margin on an 800 x 500 image.
        margin_x, margin_y = 24.0 / 800.0, 24.0 / 500.0
        result = []
        for point in valid:
            x = margin_x + ((float(point["lon"]) - min_lon) / lon_span) * (1.0 - 2.0 * margin_x)
            y = margin_y + ((max_lat - float(point["lat"])) / lat_span) * (1.0 - 2.0 * margin_y)
            result.append((point, x, y))
        return result

    def _select_static_map_point(self, position: QtCore.QPointF) -> None:
        pixmap = self.image_label.pixmap()
        if pixmap is None or pixmap.isNull():
            return
        contents = self.image_label.contentsRect()
        x0 = contents.x() + (contents.width() - pixmap.width()) / 2.0
        y0 = contents.y() + (contents.height() - pixmap.height()) / 2.0
        local_x = float(position.x()) - x0
        local_y = float(position.y()) - y0
        if local_x < 0 or local_y < 0 or local_x > pixmap.width() or local_y > pixmap.height():
            return
        points = [point for point in self._get_history_points() if not point.get("location_only")]
        if HISTORY_MAX_POINTS and len(points) > HISTORY_MAX_POINTS:
            points = points[-HISTORY_MAX_POINTS:]
        nearest_id = None
        nearest_distance = float("inf")
        for point, norm_x, norm_y in self._static_point_positions(points):
            distance = math.hypot(local_x - norm_x * pixmap.width(), local_y - norm_y * pixmap.height())
            if distance < nearest_distance:
                nearest_distance = distance
                nearest_id = point.get("point_id")
        if nearest_id is not None and nearest_distance <= 40.0:
            self._select_map_point(str(nearest_id))

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if (
            watched is self.image_label
            and event.type() == QtCore.QEvent.Type.MouseButtonRelease
            and event.button() == QtCore.Qt.MouseButton.LeftButton
        ):
            self._select_static_map_point(event.position())
        return super().eventFilter(watched, event)

    def _set_map_image_bytes(self, png_bytes: bytes):
        try:
            if self.map_stack.currentWidget() != self.image_label:
                self.map_stack.setCurrentWidget(self.image_label)
            pix = QtGui.QPixmap()
            if not pix.loadFromData(png_bytes):
                return
            label_size = self.image_label.size()
            pix = pix.scaled(label_size, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
            self.image_label.setPixmap(pix)
        except Exception as e:
            logger.error(f"Error loading playback image: {e}")

    def update_image(self, force: bool = False):
        try:
            if self._interactive_map_enabled:
                self._update_interactive_map(force=force)
                return
            if not os.path.exists(IMAGE_PATH):
                self.image_label.setText("Image not found")
                return
            current_mod_time = os.path.getmtime(IMAGE_PATH)
            if force or current_mod_time != self._last_image_mtime:
                self._last_image_mtime = current_mod_time
                # Load via Pillow and scale to the label's current size for responsive UI
                img = Image.open(IMAGE_PATH)
                # Determine target size from label while respecting MAX_* caps
                label_size = self.image_label.size()
                target_w = min(label_size.width(), MAX_WIDTH)
                target_h = min(label_size.height(), MAX_HEIGHT)
                img.thumbnail((target_w, target_h))
                # Convert to QImage and then pixmap; finally scale pixmap smoothly to label
                qimg = self._pil_to_qimage(img)
                pix = QtGui.QPixmap.fromImage(qimg)
                pix = pix.scaled(label_size, QtCore.Qt.AspectRatioMode.KeepAspectRatio, QtCore.Qt.TransformationMode.SmoothTransformation)
                self.image_label.setPixmap(pix)
                logger.debug("Image updated.")
        except Exception as e:
            logger.error(f"Error loading image: {e}")
            self.image_label.setText("Image not found")

    def _refresh_map_mode(self, force: bool = False) -> None:
        token = _get_mapbox_token()
        should_enable = bool(self.map_view and token)
        if should_enable and (force or not self._interactive_map_enabled):
            self._enable_interactive_map()
        elif not should_enable and (force or self._interactive_map_enabled):
            self._disable_interactive_map()

    def _enable_interactive_map(self) -> None:
        if not self.map_view:
            return
        self._interactive_map_enabled = True
        self._map_initialized = False
        self._map_ready = False
        self._pending_map_points = None
        self.map_stack.setCurrentWidget(self.map_view)
        self._update_interactive_map(force=True)

    def _disable_interactive_map(self) -> None:
        self._interactive_map_enabled = False
        self._map_initialized = False
        self._map_ready = False
        self._pending_map_points = None
        self.map_stack.setCurrentWidget(self.image_label)

    def _on_map_load_finished(self, ok: bool) -> None:
        self._map_ready = bool(ok)
        if not ok:
            if self.playback_mode and self.playback_frames:
                self._disable_interactive_map()
                self._show_playback_static_map(self.playback_index)
            return
        if not self._interactive_map_enabled or not self.map_view:
            return
        if self._pending_map_points is None:
            return
        points = self._pending_map_points
        self._pending_map_points = None
        js = self._build_map_update_js(points)
        self.map_view.page().runJavaScript(js)

    def _map_points_signature(self, points: list[dict]) -> tuple:
        if not points:
            return (0, None)
        last = points[-1]
        return (
            len(points),
            last.get("t"),
            last.get("lat"),
            last.get("lon"),
            last.get("strength"),
        )

    def _map_telemetry_frames(self) -> list[dict]:
        frames = list(getattr(self, "report_cache_frames", []) or [])
        if getattr(self, "playback_mode", False) and getattr(self, "playback_frames", None):
            frames = list(self.playback_frames[: self.playback_index + 1])
        return frames

    @staticmethod
    def _array_channel_count(telemetry: dict) -> int:
        states = telemetry.get("antenna_states") or []
        if states:
            return sum(
                1 for state in states
                if state.get("connected") and state.get("strength") is not None
            )
        try:
            return int(telemetry.get("antenna_count") or 0)
        except (TypeError, ValueError):
            return 0

    def _get_array_bearing_observations(self) -> list[dict]:
        observations = []
        for frame in self._map_telemetry_frames():
            telemetry = frame.get("telemetry") or {}
            if (
                telemetry.get("gps_fix") is False
                or telemetry.get("cycle_paused")
                or self._array_channel_count(telemetry) < 2
            ):
                continue
            gps_loc = telemetry.get("gps_loc")
            bearing = telemetry.get("aoa_bearing")
            confidence = telemetry.get("aoa_confidence")
            if not gps_loc or bearing is None or confidence is None:
                continue
            try:
                confidence = float(confidence)
                if confidence < 0.05:
                    continue
                observations.append(
                    {
                        "lat": float(gps_loc[0]),
                        "lon": float(gps_loc[1]),
                        "bearing_deg": float(bearing),
                        "confidence": float(confidence),
                    }
                )
            except (TypeError, ValueError, IndexError):
                continue
        return observations[-100:]

    def _get_map_target_estimate(self) -> Optional[dict]:
        # Clear the estimate immediately when the current accepted fix no longer
        # has a usable array; do not leave a stale ellipse on screen.
        for frame in reversed(self._map_telemetry_frames()):
            telemetry = frame.get("telemetry") or {}
            if telemetry.get("gps_fix") is False:
                return None
            if telemetry.get("cycle_paused") or not telemetry.get("gps_loc"):
                continue
            if self._array_channel_count(telemetry) < 2:
                return None
            break
        try:
            return funcs.estimateTransmitterFromBearings(
                self._get_array_bearing_observations(), logger
            )
        except ValueError:
            return None
        except Exception:
            logger.exception("Could not calculate the interactive-map transmitter ellipse.")
            return None

    def _get_map_direction_overlay(self) -> Optional[dict]:
        for frame in reversed(self._map_telemetry_frames()):
            telemetry = frame.get("telemetry") or {}
            if telemetry.get("gps_fix") is False:
                return None
            if telemetry.get("cycle_paused") or not telemetry.get("gps_loc"):
                continue
            if self._array_channel_count(telemetry) < 2:
                return None
            gps_loc = telemetry.get("gps_loc")
            bearing = telemetry.get("aoa_bearing")
            confidence = telemetry.get("aoa_confidence")
            if bearing is None or confidence is None:
                return None
            try:
                confidence = float(confidence)
                if confidence < 0.05:
                    return None
                return {
                    "lat": float(gps_loc[0]),
                    "lon": float(gps_loc[1]),
                    "bearing_deg": float(bearing) % 360.0,
                    "confidence": max(0.0, min(1.0, float(confidence))),
                    "antenna_count": self._array_channel_count(telemetry),
                    "length_m": 150.0,
                }
            except (TypeError, ValueError, IndexError):
                continue
        return None

    def _get_map_alerts(self) -> list[str]:
        if self.playback_mode or self.demo_active or self.playback_only or self.meshtastic_only:
            return []
        if hasattr(self, "alert_manager"):
            return []
        alerts = []
        if self.latest_gps_fix is False:
            alerts.append("NO FIX")
        if not self.sdr_connected:
            alerts.append("NO SDR")
        elif self.sdr_error:
            alerts.append("SDR ERROR")
        return alerts

    def _get_map_warnings(self) -> list[str]:
        if (
            getattr(self, "playback_mode", False)
            or getattr(self, "demo_active", False)
            or getattr(self, "playback_only", False)
            or getattr(self, "meshtastic_only", False)
            or not getattr(self, "collecting", False)
        ):
            return []
        if hasattr(self, "alert_manager"):
            return []
        if getattr(self, "latest_cycle_paused", False):
            return ["PAUSED"]
        return []

    def _update_alert_manager(self, data: dict) -> None:
        with settings_lock:
            debounce = max(1, int(settings.alert_debounce_cycles))
        # Demo telemetry intentionally exercises the same alert presentation as
        # field telemetry; only playback and non-collection modes suppress it.
        live = not (self.playback_mode or self.playback_only or self.meshtastic_only)
        antenna_states = data.get("antenna_states") or self.antenna_states or []
        degraded = any(state.get("health") == "Degraded" for state in antenna_states)
        accuracy = data.get("gps_accuracy_m", self.latest_gps_accuracy_m)
        self.alert_manager.update(
            "no_fix", live and self.latest_gps_fix is False, "NO FIX", "error", debounce
        )
        self.alert_manager.update(
            "no_sdr", live and not self.sdr_connected, "NO SDR", "error", 1
        )
        self.alert_manager.update(
            "sdr_error", live and bool(self.sdr_error) and self.sdr_connected,
            "SDR ERROR", "error", 1,
        )
        self.alert_manager.update(
            "paused", live and self.collecting and self.latest_cycle_paused,
            "PAUSED", "warning", 1,
        )
        self.alert_manager.update(
            "gps_accuracy", live and accuracy is not None and float(accuracy) > 25.0,
            "LOW GPS ACCURACY", "warning", debounce,
        )
        self.alert_manager.update(
            "sdr_degraded", live and degraded, "SDR DEGRADED", "warning", debounce
        )
        self.alert_manager.update(
            "interference", live and bool(data.get("interference_detected")),
            "RF INTERFERENCE", "warning", 1,
        )
        self.alert_manager.update(
            "demo_scenario", live and self.demo_active and bool(data.get("demo_scenario")),
            f"DEMO: {data.get('demo_scenario')}", "debug", 1,
        )

    def _get_map_notifications(self) -> list[dict]:
        if getattr(self, "playback_mode", False):
            return list(getattr(self, "playback_alerts", []) or [])
        if not hasattr(self, "alert_manager"):
            return []
        return self.alert_manager.snapshot()

    @staticmethod
    def _normalize_recorded_alerts(alerts: object) -> list[dict]:
        if not isinstance(alerts, list):
            return []
        normalized = []
        for index, alert in enumerate(alerts):
            if not isinstance(alert, dict) or not alert.get("message"):
                continue
            severity = str(alert.get("severity") or "warning").lower()
            if severity not in {"error", "warning", "info", "debug"}:
                severity = "warning"
            normalized.append(
                {
                    "key": str(alert.get("key") or f"recorded-alert-{index}"),
                    "message": str(alert["message"]),
                    "severity": severity,
                }
            )
        return normalized

    def _update_interactive_map(self, force: bool = False) -> None:
        if not self._interactive_map_enabled or not self.map_view:
            return
        token = _get_mapbox_token()
        if not token:
            self._disable_interactive_map()
            return
        points = self._get_history_points()
        target_estimate = self._get_map_target_estimate()
        array_direction = self._get_map_direction_overlay()
        sig = (
            self._map_points_signature(points),
            tuple(self._get_map_alerts()),
            tuple(self._get_map_warnings()),
            tuple((item["key"], item["severity"]) for item in self._get_map_notifications()),
            json.dumps(target_estimate, sort_keys=True),
            json.dumps(array_direction, sort_keys=True),
        )
        now = time.time()
        if not force and (sig == self._last_map_sig) and (now - self._last_map_update) < MAP_UPDATE_INTERVAL_S:
            return
        self._last_map_sig = sig
        self._last_map_update = now
        if not self._map_initialized:
            html = self._build_map_html(token, points)
            self._map_ready = False
            self._pending_map_points = points
            self.map_view.setHtml(html)
            self._map_initialized = True
            return
        if not self._map_ready:
            self._pending_map_points = points
            return
        js = self._build_map_update_js(points)
        self.map_view.page().runJavaScript(js)

    def _build_map_html(self, token: str, points: list[dict]) -> str:
        center = self._map_center(points)
        zoom = 13 if points else 2
        points_js = json.dumps(points)
        center_js = json.dumps(center)
        token_js = json.dumps(token)
        alerts_js = json.dumps(self._get_map_alerts())
        warnings_js = json.dumps(getattr(self, "_get_map_warnings", lambda: [])())
        notifications_js = json.dumps(getattr(self, "_get_map_notifications", lambda: [])())
        estimate_js = json.dumps(getattr(self, "_get_map_target_estimate", lambda: None)())
        direction_js = json.dumps(getattr(self, "_get_map_direction_overlay", lambda: None)())
        return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="initial-scale=1,maximum-scale=1,user-scalable=yes" />
  <script src="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.js"></script>
  <script src="qrc:///qtwebchannel/qwebchannel.js"></script>
  <link href="https://api.mapbox.com/mapbox-gl-js/v2.15.0/mapbox-gl.css" rel="stylesheet" />
  <style>
    html, body, #map {{ height: 100%; margin: 0; padding: 0; }}
    .pin {{
      width: 12px;
      height: 12px;
      border-radius: 50%;
      border: 2px solid #ffffff;
      box-shadow: 0 0 6px rgba(0,0,0,0.35);
    }}
    .pin.current-location {{
      width: 16px;
      height: 16px;
      border: 3px solid #ffffff;
      box-shadow: 0 0 0 3px rgba(16,185,129,0.28), 0 0 8px rgba(0,0,0,0.4);
    }}
    .pin.selected {{
      outline: 4px solid rgba(250, 204, 21, 0.9);
      outline-offset: 3px;
    }}
    .popup {{
      font-family: Arial, sans-serif;
      font-size: 12px;
    }}
    #system-alerts {{
      position: absolute;
      top: 20px;
      left: 50%;
      transform: translateX(-50%);
      z-index: 20;
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 8px;
      pointer-events: none;
    }}
    .system-alert {{
      color: #ff1f1f;
      background: rgba(20, 20, 20, 0.88);
      border: 3px solid #ff1f1f;
      border-radius: 8px;
      padding: 8px 22px;
      font-family: Arial, sans-serif;
      font-size: 32px;
      font-weight: 900;
      line-height: 1.05;
      letter-spacing: 1.5px;
      text-align: center;
      text-shadow: 0 0 8px rgba(255, 31, 31, 0.8);
      box-shadow: 0 3px 12px rgba(0, 0, 0, 0.5);
    }}
    .system-alert.warning {{
      color: #ffb000;
      border-color: #ffb000;
      text-shadow: 0 0 8px rgba(255, 176, 0, 0.8);
    }}
    .system-alert.info {{
      color: #22c55e;
      border-color: #22c55e;
      text-shadow: 0 0 8px rgba(34, 197, 94, 0.8);
    }}
    .system-alert.debug {{
      color: #38bdf8;
      border-color: #38bdf8;
      text-shadow: 0 0 8px rgba(56, 189, 248, 0.8);
    }}
  </style>
</head>
<body>
  <div id="map"></div>
  <div id="system-alerts" role="alert" aria-live="assertive"></div>
  <script>
    mapboxgl.accessToken = {token_js};
    const map = new mapboxgl.Map({{
      container: 'map',
      style: 'mapbox://styles/mapbox/streets-v12',
      center: {center_js},
      zoom: {zoom}
    }});
    map.addControl(new mapboxgl.NavigationControl());
    let markers = [];
    let targetMarker = null;
    let activePopup = null;
    let followMode = true;
    let selectedPointId = null;
    let pointBridge = null;

    if (typeof QWebChannel !== 'undefined' && typeof qt !== 'undefined' && qt.webChannelTransport) {{
      new QWebChannel(qt.webChannelTransport, channel => {{
        pointBridge = channel.objects.pointBridge || null;
      }});
    }}

    map.on('dragstart', () => {{ followMode = false; }});
    map.on('zoomstart', () => {{ followMode = false; }});
    map.on('rotatestart', () => {{ followMode = false; }});
    map.on('pitchstart', () => {{ followMode = false; }});

    function strengthColor(val) {{
      const v = Math.max(0, Math.min(1000, Number(val || 0)));
      const t = v / 1000.0;
      const r = Math.round(255 * t);
      const b = Math.round(255 * (1 - t));
      return `rgb(${{r}}, 0, ${{b}})`;
    }}

    function popupHtml(p) {{
      const lat = (p.lat ?? '--');
      const lon = (p.lon ?? '--');
      if (p.location_only) {{
        const sats = (p.sats ?? '--');
        const fixAge = p.fix_age_s == null ? '--' : `${{Number(p.fix_age_s).toFixed(1)}}s`;
        return `
          <div class="popup">
            <div><strong>Current GPS Location</strong></div>
            <div><strong>Lat:</strong> ${{lat}}</div>
            <div><strong>Lon:</strong> ${{lon}}</div>
            <div><strong>Satellites:</strong> ${{sats}}</div>
            <div><strong>Fix age:</strong> ${{fixAge}}</div>
          </div>`;
      }}
      const strength = (p.strength ?? '--');
      const snr = (p.snr ?? '--');
      const quality = (p.quality ?? '--');
      const t = (p.t ?? '--');
      return `
        <div class="popup">
          <div><strong>Lat:</strong> ${{lat}}</div>
          <div><strong>Lon:</strong> ${{lon}}</div>
          <div><strong>Strength:</strong> ${{strength}}</div>
          <div><strong>SNR:</strong> ${{snr}}</div>
          <div><strong>Quality:</strong> ${{quality}}</div>
          <div><strong>t:</strong> ${{t}}</div>
        </div>`;
    }}

    function clearMarkers() {{
      if (activePopup) {{
        activePopup.remove();
        activePopup = null;
      }}
      markers.forEach(m => {{
        m.popup.remove();
        m.marker.remove();
      }});
      markers = [];
    }}

    function updateSystemAlerts(alerts, warnings, notifications) {{
      const container = document.getElementById('system-alerts');
      container.replaceChildren();
      const messages = [
        ...(alerts || []).map(message => ({{ message, severity: 'error' }})),
        ...(warnings || []).map(message => ({{ message, severity: 'warning' }})),
        ...(notifications || [])
      ];
      const seen = new Set();
      messages.forEach(item => {{
        const message = String(item.message);
        if (seen.has(message)) return;
        seen.add(message);
        const alert = document.createElement('div');
        alert.className = `system-alert ${{item.severity || 'warning'}}`;
        alert.textContent = message;
        container.appendChild(alert);
      }});
    }}

    function confidencePolygon(estimate) {{
      if (!estimate || estimate.lat == null || estimate.lon == null || !estimate.radius_m) return null;
      const coordinates = [];
      const latRad = Number(estimate.lat) * Math.PI / 180.0;
      const major = Number(estimate.major_radius_m || estimate.radius_m);
      const minor = Number(estimate.minor_radius_m || estimate.radius_m);
      const bearing = Number(estimate.ellipse_bearing_deg || 0.0) * Math.PI / 180.0;
      for (let i = 0; i <= 64; i++) {{
        const angle = (i / 64.0) * Math.PI * 2.0;
        const east = major * Math.cos(angle) * Math.sin(bearing) + minor * Math.sin(angle) * Math.cos(bearing);
        const north = major * Math.cos(angle) * Math.cos(bearing) - minor * Math.sin(angle) * Math.sin(bearing);
        const dLat = north / 111320.0;
        const dLon = east / Math.max(1.0, 111320.0 * Math.cos(latRad));
        coordinates.push([Number(estimate.lon) + dLon, Number(estimate.lat) + dLat]);
      }}
      return {{
        type: 'Feature',
        properties: {{ confidence: Number(estimate.confidence || 0), method: String(estimate.method || '') }},
        geometry: {{ type: 'Polygon', coordinates: [coordinates] }}
      }};
    }}

    function updateTargetEstimate(estimate) {{
      if (targetMarker) {{ targetMarker.remove(); targetMarker = null; }}
      const source = map.getSource('target-confidence');
      const polygon = confidencePolygon(estimate);
      if (source) source.setData(polygon || {{ type: 'FeatureCollection', features: [] }});
      if (!estimate || estimate.lat == null || estimate.lon == null) return;
      const el = document.createElement('div');
      el.className = 'pin';
      el.style.backgroundColor = '#10b981';
      targetMarker = new mapboxgl.Marker(el).setLngLat([estimate.lon, estimate.lat]).addTo(map);
    }}

    function destinationPoint(direction) {{
      if (!direction || direction.lat == null || direction.lon == null || direction.bearing_deg == null) return null;
      const distance = Number(direction.length_m || 150.0);
      const bearing = Number(direction.bearing_deg) * Math.PI / 180.0;
      const latRad = Number(direction.lat) * Math.PI / 180.0;
      return [
        Number(direction.lon) + (distance * Math.sin(bearing)) / Math.max(1.0, 111320.0 * Math.cos(latRad)),
        Number(direction.lat) + (distance * Math.cos(bearing)) / 111320.0
      ];
    }}

    function updateArrayDirection(direction) {{
      const lineSource = map.getSource('array-bearing');
      const headSource = map.getSource('array-bearing-head');
      const endpoint = destinationPoint(direction);
      if (!lineSource || !headSource) return;
      if (!endpoint) {{
        lineSource.setData({{ type: 'FeatureCollection', features: [] }});
        headSource.setData({{ type: 'FeatureCollection', features: [] }});
        return;
      }}
      const properties = {{
        confidence: Number(direction.confidence || 0),
        antenna_count: Number(direction.antenna_count || 0),
        rotation: Number(direction.bearing_deg) - 90.0
      }};
      lineSource.setData({{
        type: 'Feature', properties,
        geometry: {{ type: 'LineString', coordinates: [[Number(direction.lon), Number(direction.lat)], endpoint] }}
      }});
      headSource.setData({{
        type: 'Feature', properties,
        geometry: {{ type: 'Point', coordinates: endpoint }}
      }});
    }}

    function addMarkers(data) {{
      data.forEach(p => {{
        if (p.lat == null || p.lon == null) return;
        const el = document.createElement('div');
        el.className = 'pin';
        if (p.location_only) el.classList.add('current-location');
        if (String(p.point_id) === selectedPointId) el.classList.add('selected');
        el.style.backgroundColor = p.location_only ? '#10b981' : strengthColor(p.strength);
        const marker = new mapboxgl.Marker(el).setLngLat([p.lon, p.lat]).addTo(map);
        const popup = new mapboxgl.Popup({{ closeButton: false, closeOnClick: false, offset: 18 }})
          .setLngLat([p.lon, p.lat])
          .setHTML(popupHtml(p));
        el.addEventListener('mouseenter', () => {{
          if (activePopup && activePopup !== popup) activePopup.remove();
          activePopup = popup;
          popup.addTo(map);
        }});
        el.addEventListener('mouseleave', () => {{
          popup.remove();
          if (activePopup === popup) activePopup = null;
        }});
        el.addEventListener('click', event => {{
          event.stopPropagation();
          selectedPointId = String(p.point_id);
          document.querySelectorAll('.pin.selected').forEach(node => node.classList.remove('selected'));
          el.classList.add('selected');
          if (pointBridge && p.point_id != null) pointBridge.selectPoint(selectedPointId);
        }});
        markers.push({{ marker, popup }});
      }});
    }}

    function fitBoundsIfNeeded(data) {{
      if (data.length < 2) return;
      const bounds = new mapboxgl.LngLatBounds();
      data.forEach(p => {{
        if (p.lat == null || p.lon == null) return;
        bounds.extend([p.lon, p.lat]);
      }});
      map.fitBounds(bounds, {{ padding: 50, maxZoom: 16 }});
    }}

    window.setFollowMode = function(enabled) {{
      followMode = !!enabled;
    }};

    window.clearPointSelection = function() {{
      selectedPointId = null;
      document.querySelectorAll('.pin.selected').forEach(node => node.classList.remove('selected'));
    }};

    window.updateMarkers = function(data, center, forceFollow, alerts, warnings, notifications, targetEstimate, arrayDirection) {{
      updateSystemAlerts(alerts, warnings, notifications);
      updateTargetEstimate(targetEstimate);
      updateArrayDirection(arrayDirection);
      if (forceFollow) {{
        followMode = true;
      }}
      clearMarkers();
      addMarkers(data);
      if (!followMode) return;
      if (center && center.length === 2) {{
        map.setCenter(center);
      }} else {{
        fitBoundsIfNeeded(data);
      }}
    }};

    map.on('load', () => {{
      map.addSource('target-confidence', {{ type: 'geojson', data: {{ type: 'FeatureCollection', features: [] }} }});
      map.addLayer({{ id: 'target-confidence-fill', type: 'fill', source: 'target-confidence', paint: {{ 'fill-color': '#10b981', 'fill-opacity': 0.16 }} }});
      map.addLayer({{ id: 'target-confidence-line', type: 'line', source: 'target-confidence', paint: {{ 'line-color': '#10b981', 'line-width': 2 }} }});
      map.addSource('array-bearing', {{ type: 'geojson', data: {{ type: 'FeatureCollection', features: [] }} }});
      map.addLayer({{ id: 'array-bearing-line', type: 'line', source: 'array-bearing', paint: {{ 'line-color': '#f97316', 'line-width': 4, 'line-opacity': 0.9 }} }});
      map.addSource('array-bearing-head', {{ type: 'geojson', data: {{ type: 'FeatureCollection', features: [] }} }});
      map.addLayer({{
        id: 'array-bearing-arrowhead', type: 'symbol', source: 'array-bearing-head',
        layout: {{ 'text-field': '>', 'text-size': 24, 'text-rotate': ['get', 'rotation'], 'text-rotation-alignment': 'map', 'text-allow-overlap': true }},
        paint: {{ 'text-color': '#f97316', 'text-halo-color': '#ffffff', 'text-halo-width': 1.5 }}
      }});
      window.updateMarkers({points_js}, {center_js}, true, {alerts_js}, {warnings_js}, {notifications_js}, {estimate_js}, {direction_js});
    }});
  </script>
</body>
</html>"""

    def _build_map_update_js(self, points: list[dict]) -> str:
        points_js = json.dumps(points)
        center_js = json.dumps(self._map_center(points))
        alerts_js = json.dumps(self._get_map_alerts())
        warnings_js = json.dumps(getattr(self, "_get_map_warnings", lambda: [])())
        notifications_js = json.dumps(getattr(self, "_get_map_notifications", lambda: [])())
        estimate_js = json.dumps(getattr(self, "_get_map_target_estimate", lambda: None)())
        direction_js = json.dumps(getattr(self, "_get_map_direction_overlay", lambda: None)())
        return f"if (window.updateMarkers) {{ window.updateMarkers({points_js}, {center_js}, false, {alerts_js}, {warnings_js}, {notifications_js}, {estimate_js}, {direction_js}); }}"

    def _map_center(self, points: list[dict]) -> list[float]:
        if points:
            last = points[-1]
            lat = last.get("lat")
            lon = last.get("lon")
            if lat is not None and lon is not None:
                return [float(lon), float(lat)]
        return [0.0, 0.0]

    @staticmethod
    def _pil_to_qimage(pil_img: Image.Image) -> QtGui.QImage:
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimg = QtGui.QImage(data, pil_img.width, pil_img.height, QtGui.QImage.Format.Format_RGBA8888)
        return qimg

    # ---------- Lifecycle ----------
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        try:
            self._closing = True
            self._stop_idle_gps_tracking()
            self._hardware_monitor_stop_event.set()
            self.stop_event.set()
            if self._gps_tracking_thread is not None and self._gps_tracking_thread.isRunning():
                self._gps_tracking_thread.wait(3500)
            if self._hardware_monitor_thread is not None and self._hardware_monitor_thread.isRunning():
                self._hardware_monitor_thread.wait(2000)
            if self.thread is not None and self.thread.isRunning():
                if not self.thread.wait(5000):
                    logger.warning("Collector did not stop within the shutdown timeout.")
            self.collecting = False
            self._stop_recording()
            self._finalize_report_cache()
            if hasattr(self, "addon_manager") and self.addon_manager:
                self.addon_manager.shutdown()
        finally:
            logger.info("Exiting application.")
            super().closeEvent(event)

