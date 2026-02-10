"""
PINPOINT Direction Finding v7.5.0-hotfix4 Tests and CI Update
"""

import os
import sys
import shutil
import time
import threading
import logging
import math
import json
import hashlib
import tempfile
import base64
import datetime
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from typing import Callable, Optional
import numpy as np

# Third-party
from PIL import Image, ImageSequence
from PyQt6 import QtCore, QtGui, QtWidgets

from addons.report_generator import ReportGeneratorDialog
from addons.meshtastic_connectivity import (
    MeshtasticConnectivityDialog,
    MeshtasticReadNodeDialog,
    LiveNetworkDataViewer,
    MeshtasticManager,
)

# Optional colored console logging
try:
    import colorlog
except Exception:  # pragma: no cover
    colorlog = None

# Optional system stats
try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None

# ---------------------------
# Windows SDR DLL bootstrap
# ---------------------------
def _add_dll_dir(path: str) -> None:
    if not path or not os.path.isdir(path):
        return
    # Prefer Python 3.8+ DLL search path API
    if hasattr(os, "add_dll_directory"):
        try:
            os.add_dll_directory(path)
        except OSError:
            # If already added or invalid, ignore
            pass
    # Also prepend PATH for any downstream native loads
    cur_path = os.environ.get("PATH", "")
    if path not in cur_path.split(os.pathsep):
        os.environ["PATH"] = path + os.pathsep + cur_path


def _is_bundled() -> bool:
    # Detect frozen/bundled app (Nuitka/onefile/other)
    if getattr(sys, "frozen", False):
        return True
    if hasattr(sys, "_MEIPASS"):
        return True
    if "__compiled__" in globals():
        return True
    return False


def _resource_path(*parts: str) -> str:
    """
    Resolve a resource path that works both in dev and bundled (PyInstaller) builds.
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def _ensure_librtlsdr_windows() -> None:
    """
    Ensure librtlsdr is available on Windows.
    If missing, install pyrtlsdrlib (bundles the DLL) and add its folder to PATH.
    """
    if os.name != "nt":
        return

    def _try_import() -> bool:
        try:
            # This triggers the DLL loader inside pyrtlsdr
            from rtlsdr import librtlsdr as _librtlsdr  # noqa: F401
            return True
        except Exception:
            return False

    # If it already works, we're done.
    if _try_import():
        return

    # In a bundled app, attempt to locate packaged DLLs before failing.
    if _is_bundled():
        try:
            from pathlib import Path
            base = Path(getattr(sys, "_MEIPASS", ""))
            candidates = []
            if base and base.exists():
                candidates.append(base)
            try:
                import importlib
                mod = importlib.import_module("pyrtlsdrlib")
                mod_dir = Path(mod.__file__).resolve().parent
                candidates.extend(
                    [
                        mod_dir,
                        mod_dir / "libs",
                        mod_dir / ".libs",
                        mod_dir / "bin",
                    ]
                )
            except Exception:
                pass
            try:
                if base and base.exists():
                    for root, _, files in os.walk(base):
                        if "librtlsdr.dll" in files:
                            candidates.append(Path(root))
            except Exception:
                pass
            for candidate in candidates:
                _add_dll_dir(str(candidate))
        except Exception:
            pass

        if _try_import():
            return

        raise ImportError(
            "librtlsdr failed to load in the bundled build. "
            "Ensure the DLLs are included in the onefile package "
            "(e.g., via pyrtlsdrlib) and rebuild."
        )

    # Attempt to install the bundled DLL package on Windows.
    try:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pyrtlsdrlib"])
    except Exception as e:
        raise ImportError(
            "Failed to install 'pyrtlsdrlib' needed for librtlsdr on Windows. "
            "Please install it manually and re-run."
        ) from e

    # Add the pyrtlsdrlib folder(s) to the DLL search path / PATH
    try:
        import importlib
        from pathlib import Path

        mod = importlib.import_module("pyrtlsdrlib")
        mod_dir = Path(mod.__file__).resolve().parent
        for candidate in (
            mod_dir,
            mod_dir / "libs",
            mod_dir / ".libs",
            mod_dir / "bin",
        ):
            _add_dll_dir(str(candidate))
    except Exception:
        # Best-effort; loader may still succeed without explicit PATH changes
        pass

    # Final attempt to load librtlsdr
    if not _try_import():
        raise ImportError(
            "librtlsdr still failed to load after installing 'pyrtlsdrlib'. "
            "Ensure the DLL is on PATH or set RTLSDR_LIBRARY_PATH."
        )


_ensure_librtlsdr_windows()

# Your data pipeline
import funcs

def _transparentize_gif(src_path: str, allow_processing: bool = True) -> str:
    """
    Create a cached GIF with white/near-white pixels made transparent.
    Falls back to the original path on any failure.
    """
    try:
        mtime = os.path.getmtime(src_path)
        key = f"{src_path}|{mtime}|{GIF_WHITE_THRESHOLD}"
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()  # nosec - non-crypto use
        cache_name = f"pinpoint_gif_{digest}.gif"
        cache_path = os.path.join(tempfile.gettempdir(), cache_name)
        if os.path.exists(cache_path):
            return cache_path
        if not allow_processing:
            return src_path

        frames = []
        durations = []
        with Image.open(src_path) as im:
            for frame in ImageSequence.Iterator(im):
                rgba = frame.convert("RGBA")
                data = list(rgba.getdata())
                new_data = []
                for r, g, b, a in data:
                    if r >= GIF_WHITE_THRESHOLD and g >= GIF_WHITE_THRESHOLD and b >= GIF_WHITE_THRESHOLD:
                        new_data.append((*GIF_TRANSPARENT_KEY, 0))
                    else:
                        new_data.append((r, g, b, 255))
                rgba.putdata(new_data)
                pal = rgba.convert("P", palette=Image.ADAPTIVE, colors=255)
                palette = pal.getpalette() or []
                key_idx = None
                for i in range(0, len(palette), 3):
                    if palette[i:i + 3] == list(GIF_TRANSPARENT_KEY):
                        key_idx = i // 3
                        break
                if key_idx is None:
                    key_idx = 0
                pal.info["transparency"] = key_idx
                frames.append(pal)
                durations.append(frame.info.get("duration", 100))

        if not frames:
            return src_path

        frames[0].save(
            cache_path,
            save_all=True,
            append_images=frames[1:],
            loop=0,
            duration=durations,
            transparency=frames[0].info.get("transparency", 0),
            disposal=2,
        )
        return cache_path
    except Exception:
        return src_path


def _status_anim_for_mode(mode: str) -> Optional[str]:
    return {
        "general": LOADING_ANIM_GENERAL,
        "checking": LOADING_ANIM_CHECKING,
        "calibrating": LOADING_ANIM_CAL,
        "gps": LOADING_ANIM_GPS,
        "stopping": LOADING_ANIM_STOP,
        "starting": LOADING_ANIM_START,
        "running": LOADING_ANIM_RUNNING,
        "playback": LOADING_ANIM_PLAYBACK,
        "paused": LOADING_ANIM_PAUSED,
        "import": LOADING_ANIM_IMPORT,
    }.get(mode)


def _guess_gps_port_no_open() -> Optional[str]:
    """
    Best-effort GPS port detection without opening the serial device.
    This avoids locking/permission issues during the splash screen.
    """
    try:
        env_port = os.environ.get("GPS_PORT")
        ports = funcs.list_serial_ports()
        if env_port:
            for p in ports:
                if (p.get("device") or "").upper() == env_port.upper():
                    return p.get("device")
            return None

        if not ports:
            return None

        gps_keywords = ("gps", "u-blox", "ublox", "gnss", "nmea")
        keyword_matches = []
        for p in ports:
            haystack = " ".join(
                [
                    str(p.get("device") or ""),
                    str(p.get("description") or ""),
                    str(p.get("manufacturer") or ""),
                    str(p.get("hwid") or ""),
                ]
            ).lower()
            if any(k in haystack for k in gps_keywords):
                keyword_matches.append(p.get("device"))

        candidates = keyword_matches if keyword_matches else [p.get("device") for p in ports]
        candidates = [c for c in candidates if c]

        if len(keyword_matches) == 1:
            return keyword_matches[0]
        if len(candidates) == 1:
            return candidates[0]

        non_default = [p.get("device") for p in ports if (p.get("device") or "").upper() not in ("COM1", "COM2")]
        usb_serial = [p.get("device") for p in ports if "usb serial" in (p.get("description") or "").lower()]
        if len(non_default) == 1:
            return non_default[0]
        if len(usb_serial) == 1:
            return usb_serial[0]

        return None
    except Exception:
        return None


def _detect_gps_nmea_present() -> bool:
    # Be conservative: only treat GPS as present if we observe NMEA on a candidate port.
    try:
        ports = funcs.list_serial_ports()
    except Exception:
        return False
    if not ports:
        return False

    env_port = os.environ.get("GPS_PORT")
    candidates = []
    if env_port:
        for p in ports:
            if (p.get("device") or "").upper() == env_port.upper():
                candidates = [p.get("device")]
                break
    if not candidates:
        gps_keywords = ("gps", "u-blox", "ublox", "gnss", "nmea")
        keyword_matches = []
        for p in ports:
            haystack = " ".join(
                [
                    str(p.get("device") or ""),
                    str(p.get("description") or ""),
                    str(p.get("manufacturer") or ""),
                    str(p.get("hwid") or ""),
                ]
            ).lower()
            if any(k in haystack for k in gps_keywords):
                keyword_matches.append(p.get("device"))
        if keyword_matches:
            candidates = keyword_matches
        else:
            candidates = [
                p.get("device")
                for p in ports
                if (p.get("device") or "").upper() not in ("COM1", "COM2")
            ]

    candidates = [c for c in candidates if c]
    if not candidates:
        return False

    probe_fn = getattr(funcs, "_probe_nmea", None)
    if not callable(probe_fn):
        return False
    for dev in candidates:
        try:
            if probe_fn(dev, probe_seconds=1.0, timeout=0.4):
                return True
        except Exception:
            continue
    return False


class RecordingSession:
    def __init__(self, path: str, settings_snapshot: Optional[dict] = None, app_version: Optional[str] = None):
        self.path = path
        self.start_time = time.time()
        self.last_map_mtime: Optional[float] = None
        self._fh = open(path, "w", encoding="utf-8")
        header = {
            "type": "pinplyr",
            "version": 1,
            "created_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "app": APP_TITLE,
            "app_version": app_version or APP_VERSION,
            "settings": settings_snapshot or {},
        }
        self._write_line(header)

    def _write_line(self, obj: dict) -> None:
        self._fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
        self._fh.flush()

    def record_frame(self, telemetry: dict) -> None:
        t = time.time() - self.start_time
        map_b64 = None
        try:
            if os.path.exists(IMAGE_PATH):
                mtime = os.path.getmtime(IMAGE_PATH)
                if self.last_map_mtime != mtime:
                    with open(IMAGE_PATH, "rb") as f:
                        map_b64 = base64.b64encode(f.read()).decode("ascii")
                    self.last_map_mtime = mtime
        except Exception:
            map_b64 = None
        frame = {
            "t": round(t, 3),
            "telemetry": telemetry,
        }
        if map_b64:
            frame["map_png"] = map_b64
        self._write_line(frame)

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass
def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        logger = logging.getLogger()
        logger.warning("Failed to load .env file.", exc_info=True)


# Load .env (if present) before reading tokens.
_load_env_file(".env")

# Read Mapbox token from environment to avoid hard-coding secrets.
MAPBOX_TOKEN = os.environ.get("MAPBOX_TOKEN")
MAPBOX_TOKEN_OVERRIDE: Optional[str] = None


def _get_mapbox_token() -> Optional[str]:
    token = MAPBOX_TOKEN or (MAPBOX_TOKEN_OVERRIDE or "")
    token = token.strip()
    return token if token else None

APP_TITLE = "PINPOINT Direction Finding"
APP_VERSION = "v7.5.0-hotfix4"
APP_ICON_PATH = _resource_path("app.ico")
IMAGE_PATH = "map.png"
PINPOINT_IMAGE_FALLBACK = _resource_path("pinpoint.png")  # used by Clear App
LOG_FILE = "main.log"
MAX_WIDTH = 800
MAX_HEIGHT = 600
MAP_UPDATE_INTERVAL_S = 3.0
GPS_MAX_WAIT_S = 10
SDR_SCAN_INTERVAL_S = 5.0
CALIBRATION_FILE = "calibration_profiles.json"
LOADING_ANIM_GENERAL = _resource_path("assets", "gifs", "general.gif")
LOADING_ANIM_CHECKING = _resource_path("assets", "gifs", "checking.gif")
LOADING_ANIM_CAL = _resource_path("assets", "gifs", "calibrating.gif")
LOADING_ANIM_GPS = _resource_path("assets", "gifs", "gps_search.gif")
LOADING_ANIM_STOP = _resource_path("assets", "gifs", "stopping.gif")
LOADING_ANIM_START = _resource_path("assets", "gifs", "starting.gif")
LOADING_ANIM_RUNNING = _resource_path("assets", "gifs", "running.gif")
QUESTION_GIF = _resource_path("assets", "gifs", "question.gif")
LOADING_ANIM_PLAYBACK = _resource_path("assets", "gifs", "playback.gif")
LOADING_ANIM_PAUSED = _resource_path("assets", "gifs", "paused.gif")
ALERT_GIF = _resource_path("assets", "gifs", "alert.gif")
LOADING_ANIM_IMPORT = _resource_path("assets", "gifs", "import_file.gif")
LOADING_ICON_PX = 64
GIF_WHITE_THRESHOLD = 245
GIF_TRANSPARENT_KEY = (255, 0, 255)
SPLASH_DURATION_MS = 5000

# ---------------------------
# Logging setup
# ---------------------------
LOG_FORMAT = "%(log_color)s[%(levelname)s] - %(asctime)s - %(message)s" if colorlog else "[%(levelname)s] - %(asctime)s - %(message)s"
LOG_LEVEL = logging.DEBUG
logger = logging.getLogger()

# Remove pre-existing handlers to avoid duplicates (important for live restarts)
for h in list(logger.handlers):
    logger.removeHandler(h)

if colorlog:
    console_handler = colorlog.StreamHandler()
    console_formatter = colorlog.ColoredFormatter(LOG_FORMAT)
    console_handler.setFormatter(console_formatter)
else:
    console_handler = logging.StreamHandler()
    console_formatter = logging.Formatter(LOG_FORMAT)
    console_handler.setFormatter(console_formatter)

logger.addHandler(console_handler)

# Use a rotating file handler to avoid unbounded log growth and reduce file-lock issues.
file_formatter = logging.Formatter("[%(levelname)s] - %(asctime)s - %(message)s")
file_handler = RotatingFileHandler(LOG_FILE, mode="a", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

logger.setLevel(LOG_LEVEL)

# ---------------------------
# Settings (dataclass + shared)
# ---------------------------
@dataclass
class Settings:
    frequency: float = 141.575
    gain: int = 5
    collection_time: int = 2
    antenna_count: int = 2
    antenna_spacing_in: float = 0.0  # 0 = auto (half-wavelength)
    info_refresh_s: int = 3
    calibration_profile: str = "default"
    fusion_aoa_weight: float = 0.7
    fusion_map_weight: float = 0.3
    confidence_threshold: float = 0.4

    def to_dict(self):
        return {
            "frequency": self.frequency,
            "gain": self.gain,
            "collection_time": self.collection_time,
            "antenna_count": self.antenna_count,
            "antenna_spacing_in": self.antenna_spacing_in,
            "info_refresh_s": self.info_refresh_s,
            "calibration_profile": self.calibration_profile,
            "fusion_aoa_weight": self.fusion_aoa_weight,
            "fusion_map_weight": self.fusion_map_weight,
            "confidence_threshold": self.confidence_threshold,
        }

# Global shared settings with a lock for thread-safety
settings = Settings()
settings_lock = threading.Lock()
calibration_lock = threading.Lock()
calibration_data = {}

# ---------------------------
# Helpers
# ---------------------------
_APP_ICON_CACHE: Optional[QtGui.QIcon] = None


def _get_app_icon() -> QtGui.QIcon:
    global _APP_ICON_CACHE
    if _APP_ICON_CACHE is None:
        if os.path.exists(APP_ICON_PATH):
            _APP_ICON_CACHE = QtGui.QIcon(APP_ICON_PATH)
        else:
            _APP_ICON_CACHE = QtGui.QIcon()
    return _APP_ICON_CACHE


def _apply_app_icon(widget: QtWidgets.QWidget) -> None:
    try:
        icon = _get_app_icon()
        if not icon.isNull():
            widget.setWindowIcon(icon)
    except Exception:
        pass


def _show_startup_splash(app: QtWidgets.QApplication, duration_ms: int = SPLASH_DURATION_MS) -> None:
    try:
        if not os.path.exists(PINPOINT_IMAGE_FALLBACK):
            return
        pixmap = QtGui.QPixmap(PINPOINT_IMAGE_FALLBACK)
        if pixmap.isNull():
            return
        splash = QtWidgets.QSplashScreen(pixmap)
        _apply_app_icon(splash)
        splash.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, True)
        splash.show()
        app.processEvents()
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(duration_ms, loop.quit)
        loop.exec()
        splash.close()
        app.processEvents()
    except Exception:
        logging.getLogger().debug("Failed to show startup splash screen.", exc_info=True)

def _normalize_bearing(deg: float) -> float:
    return (deg + 360.0) % 360.0


def _relative_bearing(target_deg: Optional[float], current_deg: Optional[float]) -> Optional[float]:
    if target_deg is None or current_deg is None:
        return None
    diff = (target_deg - current_deg + 180.0) % 360.0 - 180.0
    return diff


def _bearing_deg(lat1, lon1, lat2, lon2) -> Optional[float]:
    try:
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dlon = math.radians(lon2 - lon1)
        y = math.sin(dlon) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
        bearing = math.degrees(math.atan2(y, x))
        return _normalize_bearing(bearing)
    except Exception:
        return None


def _bearing_to_cardinal(deg: Optional[float]) -> str:
    if deg is None:
        return "--"
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) // 45) % 8
    return f"{dirs[idx]} {deg:.0f}deg"


def _ideal_spacing_inches(freq_mhz: float) -> Optional[float]:
    if not freq_mhz:
        return None
    try:
        wavelength_m = 299_792_458.0 / (freq_mhz * 1_000_000.0)
        spacing_m = wavelength_m / 2.0
        return spacing_m / 0.0254
    except Exception:
        return None


def _effective_spacing_inches(freq_mhz: Optional[float], spacing_in: Optional[float]) -> Optional[float]:
    if spacing_in is not None and spacing_in > 0:
        return float(spacing_in)
    return _ideal_spacing_inches(freq_mhz) if freq_mhz else None


def _spacing_factor(freq_mhz: Optional[float], spacing_in: Optional[float]) -> float:
    ideal = _ideal_spacing_inches(freq_mhz) if freq_mhz else None
    if not ideal or ideal <= 0:
        return 1.0
    actual = _effective_spacing_inches(freq_mhz, spacing_in)
    if not actual:
        return 1.0
    return max(0.0, min(1.0, float(actual) / float(ideal)))


def _quality_to_color(quality: Optional[float]) -> QtGui.QColor:
    if quality is None:
        return QtGui.QColor("#9ca3af")
    q = max(0.0, min(1.0, float(quality)))
    r = int(220 * (1.0 - q) + 35)
    g = int(200 * q + 40)
    return QtGui.QColor(r, g, 80)


def _estimate_target_from_history(history: dict) -> Optional[tuple]:
    if not history:
        return None
    best_loc = None
    best_strength = -1
    for (lat, lon), value in history.items():
        if isinstance(value, dict):
            strength = value.get("strength", 0)
        else:
            strength = value
        if strength is None:
            continue
        if strength > best_strength:
            best_strength = strength
            best_loc = (lat, lon)
    return best_loc


def _antenna_angles(n: int) -> list[float]:
    if n <= 1:
        return [0.0]
    if n == 2:
        return [0.0, 180.0]
    step = 360.0 / n
    return [i * step for i in range(n)]


def _aoa_from_strengths(strengths: list[Optional[float]], angles_deg: list[float]) -> tuple[Optional[float], float]:
    if not strengths or not angles_deg:
        return None, 0.0
    x = 0.0
    y = 0.0
    total = 0.0
    for strength, angle in zip(strengths, angles_deg):
        if strength is None:
            continue
        w = max(0.0, float(strength))
        rad = math.radians(angle)
        x += w * math.sin(rad)
        y += w * math.cos(rad)
        total += w
    if total <= 0.0:
        return None, 0.0
    magnitude = math.hypot(x, y)
    bearing = math.degrees(math.atan2(x, y))
    confidence = max(0.0, min(1.0, magnitude / total))
    return _normalize_bearing(bearing), confidence


def _map_confidence(history: dict) -> float:
    if not history:
        return 0.0
    count = len(history)
    count_factor = min(1.0, count / 5.0)
    strengths = []
    for value in history.values():
        if isinstance(value, dict):
            strengths.append(value.get("strength", 0) or 0)
        else:
            strengths.append(value or 0)
    if not strengths:
        return 0.0
    avg_strength = float(np.mean(strengths))
    strength_factor = min(1.0, avg_strength / 300.0)
    return max(0.0, min(1.0, 0.6 * count_factor + 0.4 * strength_factor))


def _fuse_bearings(bearings_with_weight: list[tuple[Optional[float], float]]) -> tuple[Optional[float], float]:
    x = 0.0
    y = 0.0
    total = 0.0
    for bearing, weight in bearings_with_weight:
        if bearing is None or weight <= 0:
            continue
        rad = math.radians(bearing)
        x += weight * math.sin(rad)
        y += weight * math.cos(rad)
        total += weight
    if total <= 0:
        return None, 0.0
    fused = math.degrees(math.atan2(x, y))
    return _normalize_bearing(fused), max(0.0, min(1.0, total / (sum(w for _, w in bearings_with_weight) or total)))


def _device_key(index: int, serial: Optional[str]) -> str:
    return f"serial:{serial}" if serial else f"index:{index}"


def _load_calibration_profiles() -> dict:
    if not os.path.exists(CALIBRATION_FILE):
        return {}
    try:
        with open(CALIBRATION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_calibration_profiles(data: dict) -> None:
    try:
        with open(CALIBRATION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        logger.warning("Failed to save calibration profiles.", exc_info=True)

# ---------------------------
# Worker Thread for Data Collection
# ---------------------------
class CollectorThread(QtCore.QThread):
    """Runs the record loop without freezing the UI."""

    status = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    telemetry = QtCore.pyqtSignal(dict)

    def __init__(self, logger: logging.Logger, stop_event: threading.Event, gps_port: Optional[str] = None, parent=None):
        super().__init__(parent)
        self.logger = logger
        self.stop_event = stop_event
        self.gps_port = gps_port
        self.radio_index = 0

    def run(self):
        history = {}
        sdr_state = {}
        last_sdr_scan = 0.0
        gps_serial = None
        gps_reader = None
        last_fix = None
        last_fix_time = None
        last_satellites = []
        prev_fix = None
        current_bearing = None
        target_bearing = None
        target_relative = None
        sdr_connected = False
        sdr_error = None
        sdr_sample_rate = None
        aoa_confidence = 0.0
        map_confidence = 0.0
        fusion_confidence = 0.0
        bearing_source = None
        last_strength = None
        last_quality = None
        consecutive_failures = 0
        max_failures = 3
        last_map_update = 0.0
        gps_failures = 0
        gps_next_retry = 0.0
        gps_backoff_base_s = 2.0
        gps_backoff_max_s = 10.0
        self.logger.info("Collector thread started.")
        try:
            # Initial SDR scan
            devices = funcs.list_sdr_devices()
            for d in devices:
                sdr_state[d["index"]] = {
                    "radio": None,
                    "connected": False,
                    "error": None,
                    "name": d.get("name"),
                    "serial": d.get("serial"),
                    "sample_rate": None,
                    "strength": None,
                    "snr": None,
                    "quality": None,
                }
            last_sdr_scan = time.time()
            while not self.stop_event.is_set():
                try:
                    # take a thread-safe snapshot of settings
                    with settings_lock:
                        s = settings.to_dict()
                    antenna_count = s.get("antenna_count", 1)
                    spacing_in = s.get("antenna_spacing_in", 0.0)
                    freq_mhz = s.get("frequency")

                    # Periodic SDR scan for new devices
                    now_scan = time.time()
                    if now_scan - last_sdr_scan >= SDR_SCAN_INTERVAL_S:
                        devices = funcs.list_sdr_devices()
                        for d in devices:
                            if d["index"] not in sdr_state:
                                sdr_state[d["index"]] = {
                                    "radio": None,
                                    "connected": False,
                                    "error": None,
                                    "name": d.get("name"),
                                    "serial": d.get("serial"),
                                    "sample_rate": None,
                                    "strength": None,
                                    "snr": None,
                                    "quality": None,
                                }
                        last_sdr_scan = now_scan

                    # Ensure SDR connections
                    for idx in sorted(sdr_state.keys()):
                        state = sdr_state[idx]
                        if state["radio"] is None or not state["connected"]:
                            try:
                                state["radio"] = funcs.selectRadio(idx)
                                state["connected"] = True
                                state["error"] = None
                                self.logger.info("SDR index %s connected", idx)
                            except Exception as e:
                                state["connected"] = False
                                state["error"] = str(e)
                                state["radio"] = None

                    sdr_connected = any(state.get("connected") for state in sdr_state.values())

                    gps_data = None
                    if gps_serial is None or gps_reader is None:
                        now = time.time()
                        if now >= gps_next_retry:
                            try:
                                gps_serial, gps_reader = funcs.openGPS(port=self.gps_port)
                                gps_failures = 0
                                self.logger.info("GPS connected.")
                            except Exception as e:
                                gps_failures += 1
                                backoff = min(gps_backoff_max_s, gps_backoff_base_s * gps_failures)
                                gps_next_retry = time.time() + backoff
                                self.logger.warning("GPS open failed; retrying in %.1fs: %s", backoff, e)
                    if gps_serial is not None and gps_reader is not None:
                        try:
                            gps_data = funcs.readGPS(
                                logger=self.logger,
                                serial_port=gps_serial,
                                nmea_reader=gps_reader,
                                stop_event=self.stop_event,
                                max_wait_s=GPS_MAX_WAIT_S,
                            )
                        except Exception as e:
                            self.logger.warning("GPS read error; resetting GPS: %s", e)
                            try:
                                gps_serial.close()
                            except Exception:
                                self.logger.debug("Failed to close GPS serial after error.", exc_info=True)
                            gps_serial = None
                            gps_reader = None
                            gps_failures += 1
                            backoff = min(gps_backoff_max_s, gps_backoff_base_s * gps_failures)
                            gps_next_retry = time.time() + backoff
                            self.status.emit("GPS error; reconnecting")
                            gps_data = None
                    if gps_data is None:
                        # Timeout or stop; fall back to last fix if we have one
                        if last_fix is None:
                            self.logger.warning("No GPS fix available; skipping this cycle.")
                            self.status.emit("Waiting for GPS fix")
                            present_gps_loc = None
                        else:
                            present_gps_loc = last_fix
                        num_sats = None
                        satellites = last_satellites
                    else:
                        lat, lon = gps_data[0], gps_data[1]
                        num_sats = gps_data[2]
                        satellites = gps_data[3] if len(gps_data) > 3 else []
                        if satellites is not None:
                            last_satellites = satellites
                        has_fix = lat is not None and lon is not None
                        if not has_fix:
                            self.status.emit("Waiting for GPS fix")
                            present_gps_loc = last_fix
                        else:
                            present_gps_loc = (lat, lon)
                            last_fix = present_gps_loc
                            last_fix_time = time.time()
                            if prev_fix is not None:
                                current_bearing = _bearing_deg(prev_fix[0], prev_fix[1], lat, lon)
                            prev_fix = present_gps_loc

                    if present_gps_loc is not None:
                        if num_sats is not None:
                            self.logger.info(
                                f"Receiver position: ({present_gps_loc[0]}, {present_gps_loc[1]}) (GPS, sats={num_sats})"
                            )
                        else:
                            self.logger.info(
                                f"Receiver position: ({present_gps_loc[0]}, {present_gps_loc[1]}) (GPS, last known fix)"
                            )
                    else:
                        self.logger.info("Receiver position: (no GPS fix)")

                    fix_age = None if last_fix_time is None else max(0.0, time.time() - last_fix_time)
                    antenna_states = []
                    strengths = []
                    qualities = []
                    snrs = []
                    with calibration_lock:
                        cal_data = dict(calibration_data)
                    for idx in sorted(sdr_state.keys()):
                        state = sdr_state[idx]
                        if not state.get("connected") or state.get("radio") is None:
                            antenna_states.append(
                                {
                                    "index": idx,
                                    "name": state.get("name"),
                                    "serial": state.get("serial"),
                                    "connected": False,
                                    "error": state.get("error"),
                                    "sample_rate": state.get("sample_rate"),
                                    "strength": None,
                                    "snr": None,
                                    "quality": None,
                                }
                            )
                            continue
                        try:
                            samples = funcs.readRadio(state["radio"], s["collection_time"], s["frequency"], s["gain"])
                            if samples is None or len(samples) == 0:
                                raise Exception("No SDR samples")
                            processed = funcs.processSamples(samples)
                            if processed is None or len(processed) == 0:
                                raise Exception("Empty processed samples")
                            if not np.isfinite(processed).all():
                                raise Exception("Invalid processed samples")

                            raw_strength = funcs.calculateSignalStrength(processed)
                            quality = funcs.calculateSignalQuality(processed)
                            key = _device_key(idx, state.get("serial"))
                            cal = cal_data.get(key, cal_data.get(f"index:{idx}", {}))
                            offset = cal.get("offset", 0) or 0
                            scale = cal.get("scale", 1.0) or 1.0
                            strength = int(max(1, (raw_strength - offset) * scale)) if raw_strength is not None else None
                            state["strength"] = strength
                            state["snr"] = quality.get("snr", 0.0)
                            state["quality"] = quality.get("quality", 0.0)
                            state["sample_rate"] = getattr(state["radio"], "sample_rate", None)

                            strengths.append(strength)
                            qualities.append(state["quality"])
                            snrs.append(state["snr"])

                            antenna_states.append(
                                {
                                    "index": idx,
                                    "name": state.get("name"),
                                    "serial": state.get("serial"),
                                    "connected": True,
                                    "error": None,
                                    "sample_rate": state.get("sample_rate"),
                                    "strength": strength,
                                    "snr": state["snr"],
                                    "quality": state["quality"],
                                }
                            )
                        except Exception as e:
                            state["connected"] = False
                            state["error"] = str(e)
                            self.logger.warning("SDR index %s error: %s", idx, e)
                            try:
                                if state.get("radio") is not None:
                                    state["radio"].close()
                            except Exception:
                                self.logger.debug("Failed to close SDR after error.", exc_info=True)
                            state["radio"] = None
                            antenna_states.append(
                                {
                                    "index": idx,
                                    "name": state.get("name"),
                                    "serial": state.get("serial"),
                                    "connected": False,
                                    "error": state.get("error"),
                                    "sample_rate": state.get("sample_rate"),
                                    "strength": None,
                                    "snr": None,
                                    "quality": None,
                                }
                            )

                    if not strengths:
                        self.logger.warning("No SDR samples available; skipping this cycle.")
                        self.status.emit("No SDR samples")
                    strength = int(np.mean(strengths)) if strengths else None
                    quality = {
                        "snr": float(np.mean(snrs)) if snrs else None,
                        "quality": float(np.mean(qualities)) if qualities else None,
                    }
                    sdr_error = next(
                        (state.get("error") for state in sdr_state.values() if state.get("error")),
                        None,
                    )
                    sdr_sample_rate = next(
                        (state.get("sample_rate") for state in sdr_state.values() if state.get("sample_rate") is not None),
                        None,
                    )
                    sdr_connected = any(state.get("connected") for state in sdr_state.values())
                    last_strength = strength
                    last_quality = quality
                    self.logger.info(f"Estimated signal strength: {strength} / 1000")
                    self.logger.debug("Signal quality: %s", quality)

                    if present_gps_loc is not None and strength is not None:
                        ts = time.time()
                        history[present_gps_loc[0], present_gps_loc[1]] = {
                            "strength": strength,
                            "quality": quality.get("quality", 1.0),
                            "snr": quality.get("snr", 0.0),
                            "ts": ts,
                        }
                    # Update map using token from environment or settings override. Skip update if token missing.
                    token = _get_mapbox_token()
                    if token and history:
                        now = time.time()
                        if now - last_map_update >= MAP_UPDATE_INTERVAL_S:
                            try:
                                funcs.mapFunction(history, token, self.logger)
                                last_map_update = now
                            except Exception:
                                # Ensure map errors don't kill the record loop and include traceback
                                self.logger.exception("Error while updating map")
                    else:
                        self.logger.debug("MAPBOX_TOKEN not set; skipping map update.")

                    map_target_bearing = None
                    aoa_confidence = 0.0
                    map_confidence = 0.0
                    fusion_confidence = 0.0
                    bearing_source = None
                    target_loc = _estimate_target_from_history(history)
                    if present_gps_loc and target_loc:
                        map_target_bearing = _bearing_deg(
                            present_gps_loc[0], present_gps_loc[1], target_loc[0], target_loc[1]
                        )

                    aoa_relative = None
                    aoa_bearing = None
                    if antenna_states:
                        angles = _antenna_angles(len(antenna_states))
                        aoa_relative, aoa_confidence = _aoa_from_strengths(
                            [a.get("strength") if a.get("connected") else None for a in antenna_states],
                            angles,
                        )
                        aoa_confidence *= _spacing_factor(freq_mhz, spacing_in)
                        if aoa_relative is not None and current_bearing is not None:
                            aoa_bearing = _normalize_bearing(current_bearing + aoa_relative)

                    map_confidence = _map_confidence(history)

                    with settings_lock:
                        aoa_w = float(settings.fusion_aoa_weight)
                        map_w = float(settings.fusion_map_weight)
                        conf_threshold = float(settings.confidence_threshold)
                    weight_total = max(1e-6, aoa_w + map_w)
                    aoa_w /= weight_total
                    map_w /= weight_total

                    fused_bearing, fusion_confidence = _fuse_bearings(
                        [
                            (aoa_bearing, aoa_w * aoa_confidence),
                            (map_target_bearing, map_w * map_confidence),
                        ]
                    )

                    bearing_source = None
                    target_bearing = None
                    target_relative = None

                    if fused_bearing is not None and fusion_confidence >= conf_threshold:
                        target_bearing = fused_bearing
                        bearing_source = "fused"
                    elif aoa_bearing is not None and aoa_confidence >= conf_threshold:
                        target_bearing = aoa_bearing
                        bearing_source = "aoa"
                    elif map_target_bearing is not None and map_confidence >= conf_threshold:
                        target_bearing = map_target_bearing
                        bearing_source = "map"
                    else:
                        # fall back to the strongest available source
                        if aoa_bearing is not None:
                            target_bearing = aoa_bearing
                            bearing_source = "aoa"
                        else:
                            target_bearing = map_target_bearing
                            bearing_source = "map" if map_target_bearing is not None else None

                    target_relative = _relative_bearing(target_bearing, current_bearing)

                    actual_antenna_count = len(antenna_states) if antenna_states else antenna_count
                    self.telemetry.emit(
                        {
                            "gps_fix": num_sats is not None and present_gps_loc is not None,
                            "sats": num_sats,
                            "fix_age_s": fix_age,
                            "strength": strength,
                            "snr": quality.get("snr", None),
                            "quality": quality.get("quality", None),
                            "satellites": satellites,
                            "sdr_connected": sdr_connected,
                            "sdr_error": sdr_error,
                            "sdr_sample_rate": sdr_sample_rate,
                            "antenna_count": actual_antenna_count,
                            "antenna_states": antenna_states,
                            "current_bearing": current_bearing,
                            "target_bearing": target_bearing,
                            "target_relative": target_relative,
                            "aoa_bearing": aoa_bearing,
                            "aoa_relative": aoa_relative,
                            "aoa_confidence": aoa_confidence,
                            "map_target_bearing": map_target_bearing,
                            "map_confidence": map_confidence,
                            "fusion_confidence": fusion_confidence,
                            "bearing_source": bearing_source,
                        }
                    )
                    self.status.emit("Collecting")
                    consecutive_failures = 0
                    # Light pacing to reduce thrash; respects collection_time already
                    time.sleep(0.1)

                except Exception as e:  # Never crash the thread
                    # Log full traceback for diagnostics and emit a short message to the UI
                    self.logger.exception("Error in record loop")
                    self.error.emit(str(e))
                    self.status.emit("Error")
                    consecutive_failures += 1
                    if consecutive_failures >= max_failures:
                        self.logger.warning("Repeated failures detected; resetting SDR connections.")
                        for state in sdr_state.values():
                            try:
                                if state.get("radio") is not None:
                                    state["radio"].close()
                            except Exception:
                                self.logger.debug("Failed to close SDR cleanly during reset.", exc_info=True)
                            state["radio"] = None
                            state["connected"] = False
                        consecutive_failures = 0
                    # brief backoff to avoid error tight-loops
                    time.sleep(0.5)
        finally:
            for state in sdr_state.values():
                try:
                    if state.get("radio") is not None:
                        state["radio"].close()
                except Exception:
                    self.logger.debug("Failed to close SDR cleanly.", exc_info=True)
            if gps_serial is not None:
                try:
                    gps_serial.close()
                except Exception:
                    self.logger.debug("Failed to close GPS serial cleanly.", exc_info=True)

        self.logger.info("Collector thread stopped.")
        self.status.emit("stopped")

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
        self.antenna_input = QtWidgets.QLineEdit()
        self.spacing_input = QtWidgets.QLineEdit()
        self.spacing_mode = QtWidgets.QComboBox()
        self.refresh_input = QtWidgets.QLineEdit()
        self.profile_input = QtWidgets.QLineEdit()
        self.aoa_weight_input = QtWidgets.QLineEdit()
        self.map_weight_input = QtWidgets.QLineEdit()
        self.conf_threshold_input = QtWidgets.QLineEdit()
        self.mapbox_input = QtWidgets.QLineEdit()

        # Validators
        self.freq_input.setValidator(QtGui.QDoubleValidator(bottom=0.0))
        self.gain_input.setValidator(QtGui.QIntValidator(0, 1000))
        self.time_input.setValidator(QtGui.QIntValidator(1, 3600))
        self.antenna_input.setValidator(QtGui.QIntValidator(1, 16))
        self.spacing_input.setValidator(QtGui.QDoubleValidator(0.0, 1000.0, 2))
        self.refresh_input.setValidator(QtGui.QIntValidator(1, 60))
        self.aoa_weight_input.setValidator(QtGui.QDoubleValidator(0.0, 1.0, 2))
        self.map_weight_input.setValidator(QtGui.QDoubleValidator(0.0, 1.0, 2))
        self.conf_threshold_input.setValidator(QtGui.QDoubleValidator(0.0, 1.0, 2))

        with settings_lock:
            self.freq_input.setText(str(settings.frequency))
            self.gain_input.setText(str(settings.gain))
            self.time_input.setText(str(settings.collection_time))
            self.antenna_input.setText(str(settings.antenna_count))
            self.spacing_input.setText("" if not settings.antenna_spacing_in else str(settings.antenna_spacing_in))
            self.refresh_input.setText(str(settings.info_refresh_s))
            self.profile_input.setText(str(settings.calibration_profile))
            self.aoa_weight_input.setText(str(settings.fusion_aoa_weight))
            self.map_weight_input.setText(str(settings.fusion_map_weight))
            self.conf_threshold_input.setText(str(settings.confidence_threshold))
        if MAPBOX_TOKEN:
            self.mapbox_input.setText(MAPBOX_TOKEN)
            self.mapbox_input.setEnabled(False)
            self.mapbox_input.setToolTip("Loaded from MAPBOX_TOKEN environment variable.")
        else:
            self.mapbox_input.setText(MAPBOX_TOKEN_OVERRIDE or "")

        form = QtWidgets.QFormLayout()
        form.addRow("Frequency (MHz)", self.freq_input)
        form.addRow("Gain", self.gain_input)
        form.addRow("Collection Time (s)", self.time_input)
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
        form.addRow("Calibration Profile", self.profile_input)
        form.addRow("Fusion Weight (AoA)", self.aoa_weight_input)
        form.addRow("Fusion Weight (Map)", self.map_weight_input)
        form.addRow("Mapbox API Token", self.mapbox_input)
        form.addRow("Confidence Threshold", self.conf_threshold_input)

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
            antenna_count = int(self.antenna_input.text())
            spacing_in = self._resolve_spacing_in(freq)
            refresh_s = int(self.refresh_input.text())
            profile = self.profile_input.text().strip() or "default"
            aoa_weight = float(self.aoa_weight_input.text())
            map_weight = float(self.map_weight_input.text())
            conf_threshold = float(self.conf_threshold_input.text())
            with settings_lock:
                settings.frequency = freq
                settings.gain = gain
                settings.collection_time = ctime
                settings.antenna_count = antenna_count
                settings.antenna_spacing_in = max(0.0, spacing_in)
                settings.info_refresh_s = refresh_s
                settings.calibration_profile = profile
                settings.fusion_aoa_weight = aoa_weight
                settings.fusion_map_weight = map_weight
                settings.confidence_threshold = conf_threshold
            global MAPBOX_TOKEN_OVERRIDE
            if not MAPBOX_TOKEN:
                MAPBOX_TOKEN_OVERRIDE = self.mapbox_input.text().strip()
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
            satellites = self._get_satellites() if callable(self._get_satellites) else []
        except Exception:
            satellites = []
        self._set_satellites(satellites)
        self._timer.setInterval(self._refresh_interval_ms())

    def _set_satellites(self, satellites):
        sats = satellites or []
        self.table.setRowCount(len(sats))
        self.empty_label.setVisible(len(sats) == 0)
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
        box_w = radius * 1.2
        box_h = radius * 0.5
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

        painter.setPen(QtGui.QColor("#10b981"))
        painter.drawText(top_rect.adjusted(8, 4, -8, -4), QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop, "Current Heading")
        painter.drawText(top_rect.adjusted(8, 4, -8, -4), QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignTop, cur_text)
        painter.setPen(QtGui.QColor("#6b7280"))
        painter.drawText(top_rect.adjusted(8, 4, -8, -4), QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignBottom, cur_abs)

        painter.setPen(QtGui.QColor("#ef4444"))
        painter.drawText(bot_rect.adjusted(8, 4, -8, -4), QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignTop, "Target Bearing")
        painter.drawText(bot_rect.adjusted(8, 4, -8, -4), QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignTop, tgt_rel)
        painter.setPen(QtGui.QColor("#6b7280"))
        painter.drawText(bot_rect.adjusted(8, 4, -8, -4), QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignBottom, tgt_abs)

        if self._source:
            conf_text = "--" if self._confidence is None else f"{self._confidence:.2f}"
            painter.setPen(QtGui.QColor("#6b7280"))
            painter.drawText(
                QtCore.QPointF(center.x() - radius, center.y() + radius + 18),
                f"Source: {self._source.upper()}  Conf: {conf_text}",
            )


class AntennaInfoDialog(QtWidgets.QDialog):
    def __init__(self, get_info, get_refresh_s, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("Antenna Info")
        self.resize(1100, 520)

        self._get_info = get_info
        self._get_refresh_s = get_refresh_s
        self._selected_index = None
        self._refreshing = False

        self.layout_widget = AntennaLayoutWidget()
        self.compass_widget = CompassWidget()

        self.meta_label = QtWidgets.QLabel("Calibration: --  |  Fusion: --")
        self.meta_label.setStyleSheet("color: #6b7280;")
        self.spacing_label = QtWidgets.QLabel("Spacing: --")
        self.spacing_label.setStyleSheet("color: #6b7280;")

        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Antenna", "Strength", "SNR", "Status"])
        self.table.horizontalHeader().setStretchLastSection(True)
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
            "Antenna Position": QtWidgets.QLabel("--"),
            "SDR Health": QtWidgets.QLabel("--"),
            "Last Error": QtWidgets.QLabel("--"),
        }
        for key, label in self.detail_labels.items():
            self.detail_layout.addRow(key, label)
        self.detail_frame.setVisible(False)

        right_panel = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        right_panel.addWidget(self.table)
        right_panel.addWidget(self.detail_frame)
        right_panel.setSizes([320, 160])

        plots = QtWidgets.QHBoxLayout()
        plots.addWidget(self.layout_widget, stretch=1)
        plots.addWidget(self.compass_widget, stretch=1)
        plots.addWidget(right_panel, stretch=1)

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

            angles = self._angles(antenna_count)
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
            self._populate_table(antenna_states)
            if aoa_conf is not None and map_conf is not None and fusion_conf is not None:
                source_text = (bearing_source or "--").upper()
                self.meta_label.setText(
                    f"Calibration: {profile_name or '--'}  |  Source: {source_text}  |  AoA Conf: {aoa_conf:.2f}  |  Map Conf: {map_conf:.2f}  |  Fusion Conf: {fusion_conf:.2f}"
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

            status = "Connected" if state.get("connected") else "Disconnected"
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(status))

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
        health = self._health_status(connected, strength, snr, quality)
        self.detail_labels["Connection"].setText("Connected" if connected else "Disconnected")
        self.detail_labels["Sample Rate"].setText("--" if sample_rate is None else f"{sample_rate:.0f} Hz")
        self.detail_labels["Signal Quality"].setText("--" if quality is None else f"{quality:.2f}")
        self.detail_labels["SNR"].setText("--" if snr is None else f"{snr:.2f}")
        self.detail_labels["Antenna Position"].setText(state.get("position") or "--")
        self.detail_labels["SDR Health"].setText(health)
        self.detail_labels["Last Error"].setText("--" if not sdr_error else str(sdr_error))
        self.detail_frame.setVisible(True)


class GPSSetupWizard(QtWidgets.QDialog):
    """
    Simple GPS port selection wizard for field operators.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        _apply_app_icon(self)
        self.setWindowTitle("GPS Configuration")
        self.setMinimumWidth(520)
        self.setModal(True)

        self.port_combo = QtWidgets.QComboBox()
        self.desc_label = QtWidgets.QLabel("")
        self.desc_label.setWordWrap(True)
        self.refresh_btn = QtWidgets.QPushButton("Refresh")

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
        layout.addWidget(btns)

        self._ports = []
        self._load_ports()

    def _load_ports(self):
        self._ports = funcs.list_serial_ports()
        self.port_combo.clear()
        for p in self._ports:
            label = f"{p['device']} - {p['description']}".strip(" -")
            self.port_combo.addItem(label, p)
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
            processed_asset = _transparentize_gif(asset, allow_processing=False)
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
                max_wait_s=None,
            )
            if result is not None:
                lat, lon = result[0], result[1]
                if lat is not None and lon is not None:
                    self.fix_acquired.emit()
                    return
            self.error.emit(Exception("No GPS fix received."))
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
        try:
            has_sdr = bool(funcs.list_sdr_devices())
        except Exception:
            has_sdr = False
        try:
            has_gps = _detect_gps_nmea_present()
        except Exception:
            has_gps = False
        self.result.emit(has_sdr, has_gps)


class GPSStartupDialog(QtWidgets.QDialog):
    """
    Splash-style GPS initialization dialog with progress and prompts.
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
        wizard = GPSSetupWizard(self)
        if wizard.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            port = wizard.selected_port()
            if port:
                self._selected_port = port
                self.accept()
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
        self._probe_thread.finished.connect(self._probe_thread.deleteLater)
        self._probe_thread.start()

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
        self._fix_thread.error.connect(self._on_fix_error)
        self._fix_thread.finished.connect(self._fix_thread.deleteLater)
        self._fix_thread.start()

    def _on_fix_acquired(self) -> None:
        self._set_status("Signal acquired!", mode="gps")
        self.progress.setValue(100)
        QtCore.QTimer.singleShot(1000, self.accept)

    def _on_fix_error(self, error: Exception) -> None:
        self._last_error = error
        self._set_status("Acquiring GPS Signal...", mode="gps")
        QtCore.QTimer.singleShot(1000, self._start_fix_wait)

    def selected_port(self) -> Optional[str]:
        return self._selected_port

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
        if self._icon_movie is not None:
            self._icon_movie.stop()
            self._icon_movie.deleteLater()
            self._icon_movie = None

        asset = _status_anim_for_mode(mode)
        if asset and os.path.exists(asset):
            processed_asset = _transparentize_gif(asset)
            movie = QtGui.QMovie(processed_asset)
            if movie.isValid():
                movie.setScaledSize(QtCore.QSize(LOADING_ICON_PX, LOADING_ICON_PX))
                self.icon_label.setMovie(movie)
                movie.start()
                self._icon_movie = movie
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
        for idx, dev in enumerate(devices, start=1):
            serial_val = dev.get("serial")
            if serial_val:
                serial_text = str(serial_val)
            else:
                serial_text = str(dev.get("index") if dev.get("index") is not None else idx)
            self.progress.emit(idx - 1, total, f"Calibrating SDR #{serial_text}...")
            radio = None
            key = _device_key(dev.get("index"), dev.get("serial"))
            try:
                radio = funcs.selectRadio(dev["index"])
                radio.center_freq = self.frequency_mhz * 1e6
                radio.sample_rate = 2.048e6
                radio.gain = self.gain
                sample_count = int(0.5 * radio.sample_rate)
                samples = radio.read_samples(sample_count)
                processed = np.abs(samples)
                mean_val = float(np.mean(processed)) if len(processed) else 0.0
                strength = int(np.clip(mean_val * 1000, 1, 1000))
                baseline_strengths[key] = strength
                calibration[key] = {
                    "offset": strength,
                    "scale": 1.0,
                    "baseline": strength,
                }
            except Exception as e:
                fallback = existing_profile.get(key, {})
                calibration[key] = {
                    "offset": fallback.get("offset", 0),
                    "scale": fallback.get("scale", 1.0),
                    "baseline": fallback.get("baseline"),
                    "error": str(e),
                }
            finally:
                try:
                    if radio is not None:
                        radio.close()
                except Exception:
                    pass
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
        self.setMinimumSize(1000, 900)
        self.resize(1100, 950)
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

        # Image display
        self.image_label = QtWidgets.QLabel()
        self.image_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.image_label.setContentsMargins(12, 12, 12, 12)
        self.image_label.setMinimumSize(820, 620)
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

        self.exit_btn.clicked.connect(self.close)
        self.clear_btn.clicked.connect(self.clear_app)
        self.settings_btn.clicked.connect(self.open_settings)
        self.log_btn.clicked.connect(self.open_log)
        self.open_recording_btn.clicked.connect(self.open_recording)
        self.gps_info_btn.clicked.connect(self.open_gps_info)
        self.antenna_info_btn.clicked.connect(self.open_antenna_info)
        self.start_btn.clicked.connect(self.toggle_collection)

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
        self.playback_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.playback_slider.setMinimum(0)
        self.playback_slider.setMaximum(0)
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
        layout.addWidget(self.image_label, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
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
        self.playback_index = 0
        self.playback_speed_factor = 1.0
        self.playback_timer = QtCore.QTimer(self)
        self.playback_timer.setInterval(30)
        self.playback_timer.timeout.connect(self._on_playback_tick)
        self._playback_playing = False
        self._playback_start_wall = 0.0
        self._playback_start_t = 0.0
        self._playback_last_map_bytes: Optional[bytes] = None
        self._playback_slider_dragging = False
        self._gps_info_dialog: Optional[GPSInfoDialog] = None
        self._antenna_info_dialog: Optional[AntennaInfoDialog] = None
        self._last_info_dialog_refresh = 0.0
        self.latest_satellites = []
        self.latest_gps_fix = None
        self.latest_sats = None
        self.latest_fix_age = None
        self.latest_strength = None
        self.latest_snr = None
        self.latest_quality = None
        self.sdr_connected = True
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
        self._meshtastic_manager = MeshtasticManager(parent=self)
        self._meshtastic_manager.status_changed.connect(self._refresh_meshtastic_actions)
        self._meshtastic_manager.link_changed.connect(self._refresh_meshtastic_actions)

        # Clear app on startup to match original behavior
        QtCore.QTimer.singleShot(50, self.clear_app)

        # Initialize info panel after state is ready
        self._update_info_panel()
        self._refresh_meshtastic_actions()

        if self.playback_only or self.meshtastic_only:
            label = "Playback Only" if self.playback_only else "Meshtastic Only"
            self._set_start_state(label, "primary", enabled=False)
            if hasattr(self, "start_action") and self.start_action:
                self.start_action.setEnabled(False)

    # ---------- UI helpers ----------
    def _build_menus(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu("File")
        view_menu = menubar.addMenu("View")
        settings_menu = menubar.addMenu("Settings")
        collection_menu = menubar.addMenu("Collection")
        addons_menu = menubar.addMenu("Add-ons")

        self.open_recording_action = QtGui.QAction("Open Recording...", self)
        self.open_recording_action.triggered.connect(self.open_recording)
        self.exit_playback_action = QtGui.QAction("Exit Playback", self)
        self.exit_playback_action.setEnabled(False)
        self.exit_playback_action.triggered.connect(self._exit_playback)
        self.exit_action = QtGui.QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)

        self.log_action = QtGui.QAction("View Log", self)
        self.log_action.triggered.connect(self.open_log)
        self.gps_info_action = QtGui.QAction("GPS Info", self)
        self.gps_info_action.triggered.connect(self.open_gps_info)
        self.antenna_info_action = QtGui.QAction("Antenna Info", self)
        self.antenna_info_action.triggered.connect(self.open_antenna_info)
        self.live_network_action = QtGui.QAction("Live Network Data Viewer", self)
        self.live_network_action.setEnabled(False)
        self.live_network_action.triggered.connect(self.open_live_network_viewer)

        self.settings_action = QtGui.QAction("Update Settings", self)
        self.settings_action.triggered.connect(self.open_settings)

        self.start_action = QtGui.QAction("Start Data Collection", self)
        self.start_action.triggered.connect(self.toggle_collection)
        self.clear_action = QtGui.QAction("Clear App", self)
        self.clear_action.triggered.connect(self.clear_app)

        self.report_action = QtGui.QAction("Report Generator", self)
        self.report_action.setEnabled(False)
        self.report_action.triggered.connect(self.open_report_generator)
        self.meshtastic_action = QtGui.QAction("Meshtastic Connectivity", self)
        self.meshtastic_action.triggered.connect(self.open_meshtastic_connectivity)

        file_menu.addAction(self.open_recording_action)
        file_menu.addAction(self.exit_playback_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)

        view_menu.addAction(self.log_action)
        view_menu.addAction(self.gps_info_action)
        view_menu.addAction(self.antenna_info_action)
        view_menu.addAction(self.live_network_action)

        settings_menu.addAction(self.settings_action)

        collection_menu.addAction(self.start_action)
        collection_menu.addAction(self.clear_action)

        addons_menu.addAction(self.meshtastic_action)
        addons_menu.addAction(self.report_action)

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
        if self.playback_only and not self.playback_mode:
            mode = "Playback Only"
        elif self.meshtastic_only and not self.playback_mode:
            mode = "Meshtastic Only"
        if self.recording_session is not None and not self.playback_mode:
            mode = "Live (Recording)"

        status_mode = "playback" if self.playback_mode else ("running" if self.collecting else "paused")
        self._set_info_status_gif(status_mode)

        playback_text = "--"
        if self.playback_mode:
            playback_text = f"ON {self.playback_speed_factor:.0f}x"

        record_text = "ON" if self.recording_session is not None else "OFF"
        if self.recording_session is not None and self.recording_path:
            record_text = f"ON ({os.path.basename(self.recording_path)})"

        activity = getattr(self, "last_status_msg", None) or ("Collecting" if self.collecting else "Idle")

        gps_text = "--"
        if self.latest_gps_fix is True:
            gps_text = "FIX"
        elif self.latest_gps_fix is False:
            gps_text = "NO FIX"

        sats_text = "--" if self.latest_sats is None else str(self.latest_sats)
        fix_age_text = "--" if self.latest_fix_age is None else f"{self.latest_fix_age:.0f}s"

        sdr_text = "Connected" if self.sdr_connected else "No SDR"
        if self.sdr_sample_rate:
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
            conf_text = f"A:{aoa}  M:{mp}  F:{fu}"

        self.info_summary.setText(
            f"Mode: {mode}  |  Activity: {activity}  |  Recording: {record_text}  |  Playback: {playback_text}"
        )
        self._info_values["mode"].setText(mode)
        self._info_values["activity"].setText(activity)
        self._info_values["gps"].setText(gps_text)
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

    def open_log(self):
        LogWindow(self).exec()

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
            header, frames = self._load_pinplyr(path, progress_cb=_update_progress)
        except Exception as e:
            load_error = e
            header, frames = {}, []
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
        self._refresh_report_action()
        self._enter_playback(frames, header=header)
        return True

    def open_gps_info(self):
        dlg = GPSInfoDialog(self._get_latest_satellites, self._get_info_refresh_s, self)
        self._gps_info_dialog = dlg
        try:
            dlg.exec()
        finally:
            self._gps_info_dialog = None

    def _get_latest_satellites(self):
        return list(self.latest_satellites or [])

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

    def _get_antenna_info(self):
        with settings_lock:
            freq = settings.frequency
            antenna_count = settings.antenna_count
            profile = settings.calibration_profile
            spacing_in = settings.antenna_spacing_in
        return {
            "frequency_mhz": freq,
            "antenna_count": antenna_count,
            "antenna_spacing_in": spacing_in,
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
    def _load_pinplyr(path: str, progress_cb: Optional[Callable[[int], None]] = None) -> tuple[dict, list[dict]]:
        header = {}
        frames = []
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
                obj = json.loads(line)
                if obj.get("type") == "pinplyr":
                    header = obj
                else:
                    frames.append(obj)
        if progress_cb:
            progress_cb(100)
        return header, frames

    def toggle_collection(self):
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
            self._reset_report_cache()
            self._maybe_prompt_recording()
            self._set_start_state("Stop Data Collection", "danger")
            self._show_starting_dialog()
            QtWidgets.QApplication.processEvents()
            QtCore.QTimer.singleShot(0, self._start_collection_thread)
        else:
            self.stop_collection()

    def stop_collection(self):
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

    def _on_thread_finished(self):
        # Defensive: ensure UI is reset even if "stopped" status isn't emitted
        if not self.collecting:
            self._finish_stop_ui()
        # Now that the thread has fully finished, release the reference.
        self.thread = None

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
            self._set_start_state("Playback Only", "primary", enabled=False)
            if hasattr(self, "start_action") and self.start_action:
                self.start_action.setEnabled(False)
            self._update_info_panel()
            return

        if dlg.meshtastic_only():
            self.meshtastic_only = True
            self.playback_only = False
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
        self._set_start_state("Start Data Collection", "primary", enabled=True)
        if hasattr(self, "start_action") and self.start_action:
            self.start_action.setEnabled(True)
        self._update_info_panel()

    # ---------- Report cache ----------
    def _reset_report_cache(self):
        self.report_cache_frames = []
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

    def _refresh_report_action(self):
        available = bool(self.report_cache_frames) and not self.collecting
        if hasattr(self, "report_action") and self.report_action:
            self.report_action.setEnabled(available)

    def open_report_generator(self):
        if self.collecting:
            QtWidgets.QMessageBox.information(
                self, "Report Unavailable", "Stop data collection before generating a report."
            )
            return
        if not self.report_cache_frames:
            QtWidgets.QMessageBox.information(
                self, "No Data", "There is no cached session to report on."
            )
            return
        dlg = ReportGeneratorDialog(self, self._get_report_data)
        dlg.exec()

    def _get_report_data(self) -> dict:
        with settings_lock:
            s = settings.to_dict()
        if self.report_header and isinstance(self.report_header.get("settings"), dict):
            s = self.report_header.get("settings") or s
        map_png_b64 = None
        # Prefer last embedded map from playback if available
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

    # ---------- Meshtastic ----------
    def _refresh_meshtastic_actions(self, *_args) -> None:
        ready = self._meshtastic_manager.enabled and self._meshtastic_manager.peer_linked
        if hasattr(self, "live_network_action") and self.live_network_action:
            self.live_network_action.setEnabled(ready)

    def open_meshtastic_connectivity(self):
        if not self._meshtastic_manager.connected:
            prompt = MeshtasticReadNodeDialog(self._meshtastic_manager, self)
            if prompt.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
        dlg = MeshtasticConnectivityDialog(self._meshtastic_manager, self)
        dlg.exec()

    def open_live_network_viewer(self):
        if not (self._meshtastic_manager.enabled and self._meshtastic_manager.peer_linked):
            QtWidgets.QMessageBox.information(
                self,
                "Meshtastic Offline",
                "Enable Meshtastic and wait for a peer node before viewing live network data.",
            )
            return
        dlg = LiveNetworkDataViewer(self._meshtastic_manager, self)
        dlg.exec()

    # ---------- Playback ----------
    def _enter_playback(self, frames: list[dict], header: Optional[dict] = None):
        self.playback_mode = True
        self.playback_frames = frames
        self.playback_index = 0
        self.playback_speed_factor = 1.0
        self._playback_playing = False
        self._playback_last_map_bytes = None
        self.playback_slider.setMinimum(0)
        self.playback_slider.setMaximum(max(0, len(frames) - 1))
        self.playback_slider.setValue(0)
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
        self._apply_playback_frame(0)
        self._update_playback_time_label()
        self._update_info_panel()
        self._refresh_report_action()

    def _exit_playback(self):
        if not self.playback_mode:
            return
        self._pause_playback()
        self.playback_mode = False
        self.playback_frames = []
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
        self.update_image(force=True)
        self.image_timer.start()
        self._update_info_panel()
        self._refresh_report_action()

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
        if "map_png" in frame and frame["map_png"]:
            try:
                png_bytes = base64.b64decode(frame["map_png"])
                self._playback_last_map_bytes = png_bytes
                self._set_map_image_bytes(png_bytes)
            except Exception:
                pass
        elif self._playback_last_map_bytes:
            self._set_map_image_bytes(self._playback_last_map_bytes)
        telemetry = frame.get("telemetry") or {}
        self._apply_telemetry(telemetry)
        self._refresh_info_dialogs()

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

    def _start_collection_thread(self):
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
                self.recording_session.close()
                logger.info("Recording saved: %s", self.recording_path)
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

    def _on_telemetry(self, data: dict):
        if self.recording_session is not None:
            try:
                self.recording_session.record_frame(data)
            except Exception as e:
                logger.error("Failed to record frame: %s", e)
        self._cache_report_frame(data)
        if self.playback_mode:
            return
        self._apply_telemetry(data)

    def _apply_telemetry(self, data: dict):
        gps_fix = data.get("gps_fix")
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
        if satellites is not None:
            self.latest_satellites = satellites
        if strength is not None:
            self.latest_strength = strength
        if snr is not None:
            self.latest_snr = snr
        if quality is not None:
            self.latest_quality = quality
        if sdr_connected is not None:
            self.sdr_connected = bool(sdr_connected)
        if sdr_error is not None:
            self.sdr_error = sdr_error
        if sdr_sample_rate is not None:
            self.sdr_sample_rate = sdr_sample_rate
        if antenna_count is not None:
            self.antenna_count = int(antenna_count)
        if antenna_states is not None:
            self.antenna_states = antenna_states
        if current_bearing is not None:
            self.current_bearing = current_bearing
        if target_bearing is not None:
            self.target_bearing = target_bearing
        if target_relative is not None:
            self.target_relative = target_relative
        if aoa_conf is not None:
            self.aoa_confidence = aoa_conf
        if map_conf is not None:
            self.map_confidence = map_conf
        if fusion_conf is not None:
            self.fusion_confidence = fusion_conf
        if bearing_source is not None:
            self.bearing_source = bearing_source
        if gps_fix is not None:
            self.latest_gps_fix = bool(gps_fix)
        if sats is not None:
            self.latest_sats = sats
        if fix_age is not None:
            self.latest_fix_age = fix_age
        if gps_fix:
            self.gps_label.setText(f"GPS: FIX (sats={sats})")
        else:
            if fix_age is not None:
                self.gps_label.setText(f"GPS: last fix {fix_age:.0f}s ago")
            else:
                self.gps_label.setText("GPS: no fix")
        if strength is not None:
            snr_text = "--" if snr is None else f"{snr:.2f}"
            self.status_label.setText(f"Status: S={strength}  SNR={snr_text}")
        self._update_info_panel()
    def clear_app(self):
        global file_handler
        # Reset map image
        try:
            if os.path.exists(PINPOINT_IMAGE_FALLBACK):
                shutil.copy(PINPOINT_IMAGE_FALLBACK, IMAGE_PATH)
        except Exception as e:
            logger.warning(f"Could not copy fallback image: {e}")

        # Reset log file
        try:
            logger.removeHandler(file_handler)
            file_handler.close()
            if os.path.exists(LOG_FILE):
                os.remove(LOG_FILE)
            # Re-add file handler after deletion
            new_handler = RotatingFileHandler(
                LOG_FILE, mode="a", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            new_handler.setFormatter(file_formatter)
            logger.addHandler(new_handler)
            # rebind global for future removal
            globals()["file_handler"] = new_handler
        except PermissionError as e:
            logger.error(f"Permission error while clearing app: {e}")
        except FileNotFoundError:
            logger.warning("main.log not found during clear.")

        logger.info("Application cleared.")
        # Force an immediate image update
        self.update_image(force=True)

    # ---------- Image handling ----------
    def _set_map_image_bytes(self, png_bytes: bytes):
        try:
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

    @staticmethod
    def _pil_to_qimage(pil_img: Image.Image) -> QtGui.QImage:
        if pil_img.mode != "RGBA":
            pil_img = pil_img.convert("RGBA")
        data = pil_img.tobytes("raw", "RGBA")
        qimg = QtGui.QImage(data, pil_img.width, pil_img.height, QtGui.QImage.Format.Format_RGBA8888)
        return qimg

    # ---------- Lifecycle ----------
    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        # Gracefully stop the worker if running
        try:
            if self.collecting:
                self.stop_collection()
            if hasattr(self, "_meshtastic_manager") and self._meshtastic_manager:
                self._meshtastic_manager.shutdown()
        finally:
            logger.info("Exiting application.")
            super().closeEvent(event)

# ---------------------------
# App bootstrap
# ---------------------------
def main():
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
