"""
PINPOINT Software Project
pinpoint/core.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Holds core configuration, shared state, and utility functions for Pinpoint.
Provides settings, logging, resource paths, and hardware/data helpers used across the app.
---

https://nexus.crayton.dev/
"""

import os
import sys
import shutil
import time
import threading
import logging
import math
import random
import json
import hashlib
import tempfile
import base64
import datetime
import multiprocessing
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass
from typing import Callable, Optional

from .version import APP_VERSION
import numpy as np

# Third-party
from PIL import Image, ImageSequence
from PyQt6 import QtCore, QtGui, QtWidgets

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
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
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


_SDR_BOOTSTRAP_OK = True
_SDR_BOOTSTRAP_ERROR = None
try:
    _ensure_librtlsdr_windows()
except Exception as exc:
    _SDR_BOOTSTRAP_OK = False
    _SDR_BOOTSTRAP_ERROR = str(exc)
    if not _is_bundled():
        raise

# Your data pipeline
import funcs

def _hardware_check_worker(out_q) -> None:
    try:
        has_sdr = bool(funcs.list_sdr_devices())
    except Exception:
        has_sdr = False
    try:
        has_gps = _detect_gps_nmea_present()
    except Exception:
        has_gps = False
    try:
        out_q.put((has_sdr, has_gps))
    except Exception:
        pass


def _calibration_worker(index: int, frequency_mhz: float, gain: int, sample_seconds: float, out_q) -> None:
    radio = None
    try:
        radio = funcs.selectRadio(index)
        radio.center_freq = frequency_mhz * 1e6
        radio.sample_rate = SDR_DEFAULT_SAMPLE_RATE
        radio.gain = gain
        sample_count = int(max(1, sample_seconds * radio.sample_rate))
        samples = radio.read_samples(sample_count)
        processed = np.abs(samples)
        mean_val = float(np.mean(processed)) if len(processed) else 0.0
        strength = int(np.clip(mean_val * 1000, 1, 1000))
        out_q.put({"strength": strength})
    except Exception as e:
        try:
            out_q.put({"error": str(e)})
        except Exception:
            pass
    finally:
        try:
            if radio is not None:
                radio.close()
        except Exception:
            pass

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


def _transparentize_png(src_path: str) -> str:
    """
    Create a cached PNG with white/near-white pixels made transparent.
    Falls back to the original path on any failure.
    """
    try:
        mtime = os.path.getmtime(src_path)
        key = f"{src_path}|{mtime}|{GIF_WHITE_THRESHOLD}"
        digest = hashlib.md5(key.encode("utf-8")).hexdigest()  # nosec - non-crypto use
        cache_name = f"pinpoint_png_{digest}.png"
        cache_path = os.path.join(tempfile.gettempdir(), cache_name)
        if os.path.exists(cache_path):
            return cache_path

        with Image.open(src_path) as im:
            rgba = im.convert("RGBA")
            data = list(rgba.getdata())
            new_data = []
            for r, g, b, a in data:
                if r >= GIF_WHITE_THRESHOLD and g >= GIF_WHITE_THRESHOLD and b >= GIF_WHITE_THRESHOLD:
                    new_data.append((r, g, b, 0))
                else:
                    new_data.append((r, g, b, a))
            rgba.putdata(new_data)
            rgba.save(cache_path, format="PNG")
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

    def record_flag(self, reason: str, note: str = "") -> None:
        t = time.time() - self.start_time
        flag = {
            "type": "flag",
            "t": round(t, 3),
            "reason": reason,
            "note": note,
            "ts": time.time(),
        }
        self._write_line(flag)

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

def get_mapbox_token_override() -> str:
    return MAPBOX_TOKEN_OVERRIDE or ""


def set_mapbox_token_override(value: str) -> None:
    global MAPBOX_TOKEN_OVERRIDE
    MAPBOX_TOKEN_OVERRIDE = (value or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default

def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default

APP_TITLE = "PINPOINT Direction Finding"
APP_ICON_PATH = _resource_path("app.ico")
IMAGE_PATH = "map.png"
PINPOINT_IMAGE_FALLBACK = _resource_path("pinpoint.png")  # used by Clear App
LOG_FILE = "main.log"
MAX_WIDTH = 800
MAX_HEIGHT = 600
MAP_UPDATE_INTERVAL_S = 3.0
MAPBOX_URL_MAX = _env_int("MAPBOX_URL_MAX", 1800)
HISTORY_MAX_POINTS = _env_int("HISTORY_MAX_POINTS", 150)
HISTORY_MAX_AGE_S = _env_float("HISTORY_MAX_AGE_S", 3600.0)
REPORT_CACHE_MAX_FRAMES = _env_int("REPORT_CACHE_MAX_FRAMES", 5000)
GPS_MAX_WAIT_S = 10
SDR_SCAN_INTERVAL_S = 5.0
SDR_DEFAULT_SAMPLE_RATE = 2.048e6
HARDWARE_CHECK_TIMEOUT_S = 6.0
CALIBRATION_SAMPLE_SECONDS = 0.5
CALIBRATION_TIMEOUT_S = 8.0
CALIBRATION_FILE = "calibration_profiles.json"
LOADING_ANIM_GENERAL = _resource_path("assets", "gifs", "general.gif")
LOADING_ANIM_CHECKING = _resource_path("assets", "gifs", "checking.gif")
LOADING_ANIM_CAL = _resource_path("assets", "gifs", "calibrating.gif")
LOADING_ANIM_GPS = _resource_path("assets", "gifs", "gps_search.gif")
LOADING_ANIM_STOP = _resource_path("assets", "gifs", "stopping.gif")
LOADING_ANIM_START = _resource_path("assets", "gifs", "starting.gif")
LOADING_ANIM_RUNNING = _resource_path("assets", "gifs", "running.gif")
QUESTION_GIF = _resource_path("assets", "gifs", "question.gif")
MARKER_PNG = _resource_path("assets", "pngs", "marker.png")
LOADING_ANIM_PLAYBACK = _resource_path("assets", "gifs", "playback.gif")
LOADING_ANIM_PAUSED = _resource_path("assets", "gifs", "paused.gif")
ALERT_GIF = _resource_path("assets", "gifs", "alert.gif")
LOADING_ANIM_IMPORT = _resource_path("assets", "gifs", "import_file.gif")
LOADING_ICON_PX = 64
GIF_WHITE_THRESHOLD = 245
GIF_TRANSPARENT_KEY = (255, 0, 255)
SPLASH_DURATION_MS = 5000
FLAG_REASON_OPTIONS = [
    "Entering Search Area",
    "Signal Spike",
    "Target Acquired",
    "Target Lost",
    "Interference Observed",
    "Operator Note",
    "Other",
]

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

if _SDR_BOOTSTRAP_ERROR:
    logger.warning("RTL-SDR bootstrap failed: %s", _SDR_BOOTSTRAP_ERROR)

def reset_log_file() -> None:
    global file_handler
    try:
        logger.removeHandler(file_handler)
        file_handler.close()
        if os.path.exists(LOG_FILE):
            os.remove(LOG_FILE)
        new_handler = RotatingFileHandler(
            LOG_FILE, mode="a", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        new_handler.setFormatter(file_formatter)
        logger.addHandler(new_handler)
        file_handler = new_handler
    except PermissionError as e:
        logger.error(f"Permission error while clearing app: {e}")
    except FileNotFoundError:
        logger.warning("main.log not found during clear.")

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
    auto_tune_fusion: bool = True

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
            "auto_tune_fusion": self.auto_tune_fusion,
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


def _prune_history(history: dict, now: float) -> None:
    if not history:
        return
    max_age = HISTORY_MAX_AGE_S
    if max_age and max_age > 0:
        cutoff = now - max_age
        stale_keys = [
            k for k, v in history.items()
            if isinstance(v, dict) and (v.get("ts") or 0) < cutoff
        ]
        for k in stale_keys:
            history.pop(k, None)
    max_points = HISTORY_MAX_POINTS
    if max_points and max_points > 0 and len(history) > max_points:
        items = sorted(
            history.items(),
            key=lambda kv: (kv[1].get("ts") if isinstance(kv[1], dict) else 0) or 0,
        )
        for k, _ in items[:-max_points]:
            history.pop(k, None)



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
DEMO_DEFAULTS = {
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
    "seed": 1337,
}


class DemoCollectorThread(QtCore.QThread):
    """Simulated data collection loop for demo mode."""

    status = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    telemetry = QtCore.pyqtSignal(dict)

    def __init__(self, logger: logging.Logger, stop_event: threading.Event, config: Optional[dict] = None, parent=None):
        super().__init__(parent)
        self.logger = logger
        self.stop_event = stop_event
        self.config = dict(DEMO_DEFAULTS)
        if isinstance(config, dict):
            self.config.update({k: v for k, v in config.items() if v is not None})
        seed = self._cfg_int("seed", DEMO_DEFAULTS["seed"])
        self._rng = random.Random(seed)

    def _cfg_float(self, key: str, default: float) -> float:
        try:
            return float(self.config.get(key, default))
        except Exception:
            return float(default)

    def _cfg_int(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except Exception:
            return int(default)

    def _offset_lat_lon(self, lat: float, lon: float, distance_m: float, angle_rad: float) -> tuple[float, float]:
        meters_per_deg_lat = 111_320.0
        meters_per_deg_lon = 111_320.0 * math.cos(math.radians(lat))
        dlat = (distance_m * math.cos(angle_rad)) / meters_per_deg_lat
        dlon = 0.0
        if meters_per_deg_lon != 0.0:
            dlon = (distance_m * math.sin(angle_rad)) / meters_per_deg_lon
        return lat + dlat, lon + dlon

    def _approx_distance_m(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        meters_per_deg_lat = 111_320.0
        meters_per_deg_lon = 111_320.0 * math.cos(math.radians((lat1 + lat2) * 0.5))
        dlat_m = (lat2 - lat1) * meters_per_deg_lat
        dlon_m = (lon2 - lon1) * meters_per_deg_lon
        return math.hypot(dlat_m, dlon_m)

    def _build_satellites(self, count: int) -> list[dict]:
        sats = []
        for idx in range(max(0, int(count))):
            sats.append(
                {
                    "prn": str(idx + 1),
                    "elevation": round(self._rng.uniform(10.0, 85.0), 1),
                    "azimuth": round(self._rng.uniform(0.0, 359.0), 1),
                    "snr": round(self._rng.uniform(20.0, 45.0), 1),
                }
            )
        return sats

    def run(self):
        history = {}
        last_map_update = 0.0
        last_fix = None
        last_fix_time = None
        last_satellites = []
        last_sat_update = 0.0
        start_time = time.time()
        start_angle_deg = self._cfg_float("start_angle_deg", self._rng.uniform(0.0, 360.0))
        self.logger.info("Demo collector thread started.")
        try:
            while not self.stop_event.is_set():
                now = time.time()
                with settings_lock:
                    s = settings.to_dict()
                freq_mhz = s.get("frequency")
                spacing_in = s.get("antenna_spacing_in", 0.0)
                base_interval = float(s.get("collection_time") or 1.0)
                update_interval = self._cfg_float("update_interval_s", 0.0)
                if update_interval <= 0:
                    update_interval = max(0.25, base_interval)

                center_lat = self._cfg_float("center_lat", DEMO_DEFAULTS["center_lat"])
                center_lon = self._cfg_float("center_lon", DEMO_DEFAULTS["center_lon"])
                radius_m = max(10.0, self._cfg_float("radius_m", DEMO_DEFAULTS["radius_m"]))
                speed_mps = max(0.5, self._cfg_float("speed_mps", DEMO_DEFAULTS["speed_mps"]))
                target_bearing_deg = self._cfg_float("target_bearing_deg", DEMO_DEFAULTS["target_bearing_deg"])
                target_distance_m = max(10.0, self._cfg_float("target_distance_m", DEMO_DEFAULTS["target_distance_m"]))
                antenna_count = max(1, self._cfg_int("antenna_count", int(s.get("antenna_count") or 1)))
                satellite_count = max(4, self._cfg_int("satellite_count", DEMO_DEFAULTS["satellite_count"]))
                noise = max(0.0, min(0.5, self._cfg_float("signal_noise", DEMO_DEFAULTS["signal_noise"])))

                # Movement along a circular path
                elapsed = now - start_time
                angular_speed = speed_mps / max(radius_m, 1.0)
                angle = math.radians(start_angle_deg) + angular_speed * elapsed
                lat, lon = self._offset_lat_lon(center_lat, center_lon, radius_m, angle)

                current_bearing = None
                if last_fix is not None:
                    current_bearing = _bearing_deg(last_fix[0], last_fix[1], lat, lon)
                last_fix = (lat, lon)
                last_fix_time = now

                target_angle = math.radians(target_bearing_deg)
                target_lat, target_lon = self._offset_lat_lon(center_lat, center_lon, target_distance_m, target_angle)
                target_bearing = _bearing_deg(lat, lon, target_lat, target_lon)
                target_relative = _relative_bearing(target_bearing, current_bearing)

                dist_m = self._approx_distance_m(lat, lon, target_lat, target_lon)
                base_strength = 1000.0 / (1.0 + (dist_m / 120.0) ** 2)
                strength = int(max(1, min(1000, base_strength + self._rng.uniform(-30.0, 30.0))))
                snr = max(0.1, 5.0 + (strength / 1000.0) * 20.0 + self._rng.uniform(-1.5, 1.5))
                quality = max(0.0, min(1.0, (strength / 1000.0) + self._rng.uniform(-noise, noise)))

                if now - last_sat_update >= 5.0 or not last_satellites:
                    last_satellites = self._build_satellites(satellite_count)
                    last_sat_update = now

                # Simulate per-antenna strengths
                angles = _antenna_angles(antenna_count)
                rel_for_calc = target_relative if target_relative is not None else 0.0
                strengths = []
                antenna_states = []
                for idx, angle_deg in enumerate(angles):
                    diff = math.radians((angle_deg - rel_for_calc) % 360.0)
                    directional = max(0.05, math.cos(diff))
                    ant_strength = max(
                        1,
                        int(strength * (0.4 + 0.6 * directional) + self._rng.uniform(-15.0, 15.0)),
                    )
                    strengths.append(ant_strength)
                    antenna_states.append(
                        {
                            "index": idx,
                            "name": "Demo SDR",
                            "serial": f"DEMO-{idx + 1:02d}",
                            "connected": True,
                            "error": None,
                            "sample_rate": 2.048e6,
                            "strength": ant_strength,
                            "snr": snr,
                            "quality": quality,
                        }
                    )

                aoa_relative, aoa_confidence = _aoa_from_strengths(strengths, angles)
                aoa_confidence *= _spacing_factor(freq_mhz, spacing_in)
                aoa_bearing = None
                if aoa_relative is not None and current_bearing is not None:
                    aoa_bearing = _normalize_bearing(current_bearing + aoa_relative)

                history[(lat, lon)] = {
                    "strength": strength,
                    "quality": quality,
                    "snr": snr,
                    "ts": now,
                }
                _prune_history(history, now)

                # Update map image
                if history:
                    if now - last_map_update >= MAP_UPDATE_INTERVAL_S:
                        try:
                            funcs.mapFunction(
                                history,
                                _get_mapbox_token(),
                                self.logger,
                                max_markers=HISTORY_MAX_POINTS,
                                max_url_len=MAPBOX_URL_MAX,
                            )
                            last_map_update = now
                        except Exception:
                            self.logger.exception("Error while updating demo map")

                map_target_bearing = None
                map_confidence = _map_confidence(history)
                target_loc = _estimate_target_from_history(history)
                if target_loc:
                    map_target_bearing = _bearing_deg(lat, lon, target_loc[0], target_loc[1])

                with settings_lock:
                    aoa_w = float(settings.fusion_aoa_weight)
                    map_w = float(settings.fusion_map_weight)
                    conf_threshold = float(settings.confidence_threshold)
                    auto_tune = bool(getattr(settings, "auto_tune_fusion", False))
                if auto_tune:
                    aoa_factor = 0.5 + 0.5 * (aoa_confidence or 0.0)
                    map_factor = 0.5 + 0.5 * (map_confidence or 0.0)
                    if last_fix is None:
                        map_factor *= 0.1
                    aoa_w *= aoa_factor
                    map_w *= map_factor
                    conf_threshold = max(0.05, min(0.95, conf_threshold * (1.1 - 0.6 * float(quality))))
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
                target_bearing_out = None
                target_relative_out = None

                if fused_bearing is not None and fusion_confidence >= conf_threshold:
                    target_bearing_out = fused_bearing
                    bearing_source = "fused"
                elif aoa_bearing is not None and aoa_confidence >= conf_threshold:
                    target_bearing_out = aoa_bearing
                    bearing_source = "aoa"
                elif map_target_bearing is not None and map_confidence >= conf_threshold:
                    target_bearing_out = map_target_bearing
                    bearing_source = "map"
                else:
                    if aoa_bearing is not None:
                        target_bearing_out = aoa_bearing
                        bearing_source = "aoa"
                    else:
                        target_bearing_out = map_target_bearing
                        bearing_source = "map" if map_target_bearing is not None else None

                target_relative_out = _relative_bearing(target_bearing_out, current_bearing)

                fix_age = None if last_fix_time is None else max(0.0, now - last_fix_time)
                self.telemetry.emit(
                    {
                        "gps_fix": True,
                        "gps_loc": (lat, lon),
                        "sats": len(last_satellites),
                        "fix_age_s": fix_age,
                        "strength": strength,
                        "snr": snr,
                        "quality": quality,
                        "satellites": last_satellites,
                        "sdr_connected": True,
                        "sdr_error": None,
                        "sdr_sample_rate": 2.048e6,
                        "antenna_count": antenna_count,
                        "antenna_states": antenna_states,
                        "current_bearing": current_bearing,
                        "target_bearing": target_bearing_out,
                        "target_relative": target_relative_out,
                        "aoa_bearing": aoa_bearing,
                        "aoa_relative": aoa_relative,
                        "aoa_confidence": aoa_confidence,
                        "map_target_bearing": map_target_bearing,
                        "map_confidence": map_confidence,
                        "fusion_confidence": fusion_confidence,
                        "bearing_source": bearing_source,
                    }
                )
                self.status.emit("Demo Collecting")
                time.sleep(update_interval)
        except Exception as e:
            self.logger.exception("Error in demo loop")
            self.error.emit(str(e))
            self.status.emit("Error")
        finally:
            self.logger.info("Demo collector thread stopped.")
            self.status.emit("stopped")


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
                        _prune_history(history, ts)
                    # Update map using token from environment or settings override.
                    # If no token or network, a local offline map will be generated.
                    token = _get_mapbox_token()
                    if history:
                        now = time.time()
                        if now - last_map_update >= MAP_UPDATE_INTERVAL_S:
                            try:
                                funcs.mapFunction(
                                    history,
                                    token,
                                    self.logger,
                                    max_markers=HISTORY_MAX_POINTS,
                                    max_url_len=MAPBOX_URL_MAX,
                                )
                                last_map_update = now
                            except Exception:
                                # Ensure map errors don't kill the record loop and include traceback
                                self.logger.exception("Error while updating map")

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
                        auto_tune = bool(getattr(settings, "auto_tune_fusion", False))
                    if auto_tune:
                        aoa_factor = 0.5 + 0.5 * (aoa_confidence or 0.0)
                        map_factor = 0.5 + 0.5 * (map_confidence or 0.0)
                        if present_gps_loc is None:
                            map_factor *= 0.1
                        aoa_w *= aoa_factor
                        map_w *= map_factor
                        q = quality.get("quality") if isinstance(quality, dict) else None
                        if q is not None:
                            conf_threshold = max(0.05, min(0.95, conf_threshold * (1.1 - 0.6 * float(q))))
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
                            "gps_loc": present_gps_loc,
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

__all__ = [name for name in globals().keys() if not name.startswith("__")]
