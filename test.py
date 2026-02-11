"""
PINPOINT Software Project
test.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Defines pytest coverage for SDR, GPS, map rendering, and plugin API behavior.
Includes stubs and fixtures so tests run without hardware or optional dependencies.
---

https://nexus.crayton.dev/
"""

import importlib
import logging
import os
import sys
import threading
import types
from io import BytesIO

import numpy as np
import pytest
from PIL import Image


# Keep Qt headless-friendly in CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _ensure_rtlsdr_available():
    try:
        importlib.import_module("rtlsdr")
        return
    except Exception:
        stub = types.ModuleType("rtlsdr")

        class StubRtlSdr:
            def __init__(self, *args, **kwargs):
                pass

            def close(self):
                pass

            @staticmethod
            def get_device_serial_addresses():
                return []

        stub.RtlSdr = StubRtlSdr
        stub.librtlsdr = types.SimpleNamespace(
            rtlsdr_get_device_count=lambda: 0,
            rtlsdr_get_device_name=lambda i: b"",
        )
        sys.modules["rtlsdr"] = stub


_ensure_rtlsdr_available()

import funcs
import funcs.gps as gps
import funcs.sdr as sdr
import funcs.map as map_mod


class DummyPort:
    def __init__(self, device, description="", manufacturer="", hwid=""):
        self.device = device
        self.description = description
        self.manufacturer = manufacturer
        self.hwid = hwid


class DummySerial:
    def __init__(self, port, baudrate=9600, timeout=1, lines=None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._lines = list(lines or [])
        self.closed = False

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return b""

    def close(self):
        self.closed = True


class DummyNmeaReader:
    def __init__(self, messages):
        self._messages = list(messages)

    def next(self, data):
        if not self._messages:
            return []
        return self._messages.pop(0)


class DummyRadio:
    def __init__(self):
        self.center_freq = None
        self.sample_rate = None
        self.gain = None
        self.closed = False

    def read_samples(self, count):
        return np.array([1 + 1j, 2 + 2j])

    def close(self):
        self.closed = True


def _dummy_png_bytes():
    img = Image.new("RGB", (4, 4), color=(120, 80, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def core_module():
    try:
        from pinpoint import core as app
        return app
    except Exception as exc:
        pytest.skip(f"Skipping core tests (PyQt6 unavailable): {exc}")


# -----------------
# SDR tests
# -----------------

def test_process_samples_abs():
    samples = np.array([1 + 1j, -3 - 4j])
    processed = sdr.processSamples(samples)
    assert processed[0] == pytest.approx(np.sqrt(2))
    assert processed[1] == pytest.approx(5.0)


def test_calculate_signal_strength_clamps():
    samples = np.array([0.0, 0.0, 0.0])
    assert sdr.calculateSignalStrength(samples) == 1

    samples = np.array([2.0, 2.0])
    assert sdr.calculateSignalStrength(samples) == 1000


def test_calculate_signal_quality_empty():
    result = sdr.calculateSignalQuality([])
    assert result["mean"] == 0.0
    assert result["std"] == 0.0
    assert result["snr"] == 0.0
    assert result["quality"] == 0.0


def test_calculate_signal_quality_nonempty():
    samples = np.array([1.0, 3.0, 5.0])
    result = sdr.calculateSignalQuality(samples)
    assert result["mean"] == pytest.approx(3.0)
    assert result["std"] > 0.0
    assert 0.0 <= result["quality"] <= 1.0


def test_read_radio_sets_properties_and_closes():
    radio = DummyRadio()
    samples = sdr.readRadio(radio, seconds=1, frequency=100.0, gain=20, close_radio=True)
    assert radio.center_freq == 100.0 * 1e6
    assert radio.sample_rate == 2.048e6
    assert radio.gain == 20
    assert radio.closed is True
    assert isinstance(samples, np.ndarray)


def test_list_sdr_devices(monkeypatch):
    class StubLib:
        @staticmethod
        def rtlsdr_get_device_count():
            return 2

        @staticmethod
        def rtlsdr_get_device_name(i):
            return b"RTL-SDR"

    class StubRtlSdr:
        @staticmethod
        def get_device_serial_addresses():
            return ["abc", "def"]

    monkeypatch.setattr(sdr, "rtlsdr", types.SimpleNamespace(librtlsdr=StubLib, RtlSdr=StubRtlSdr))
    devices = sdr.list_sdr_devices()
    assert devices == [
        {"index": 0, "name": "RTL-SDR", "serial": "abc"},
        {"index": 1, "name": "RTL-SDR", "serial": "def"},
    ]


# -----------------
# GPS tests
# -----------------

def test_find_gps_port_env_missing(monkeypatch):
    monkeypatch.setenv("GPS_PORT", "COM9")
    monkeypatch.setattr(gps.list_ports, "comports", lambda: [DummyPort("COM1")])
    with pytest.raises(Exception, match="GPS_PORT"):
        gps._find_gps_port()


def test_find_gps_port_env_found(monkeypatch):
    monkeypatch.setenv("GPS_PORT", "COM7")
    monkeypatch.setattr(gps.list_ports, "comports", lambda: [DummyPort("COM7"), DummyPort("COM9")])
    assert gps._find_gps_port() == "COM7"


def test_find_gps_port_no_ports(monkeypatch):
    monkeypatch.delenv("GPS_PORT", raising=False)
    monkeypatch.setattr(gps.list_ports, "comports", lambda: [])
    with pytest.raises(Exception, match="No serial ports"):
        gps._find_gps_port()


def test_find_gps_port_keyword_match(monkeypatch):
    monkeypatch.setattr(gps.list_ports, "comports", lambda: [DummyPort("COM5", description="u-blox gps")])
    monkeypatch.setattr(gps, "_probe_nmea", lambda *args, **kwargs: False)
    assert gps._find_gps_port() == "COM5"


def test_find_gps_port_probe_success(monkeypatch):
    ports = [DummyPort("COM3"), DummyPort("COM7")]
    monkeypatch.setattr(gps.list_ports, "comports", lambda: ports)
    monkeypatch.setattr(gps, "_probe_nmea", lambda port, **kwargs: port == "COM7")
    assert gps._find_gps_port() == "COM7"


def test_open_gps_uses_find(monkeypatch):
    monkeypatch.setattr(gps, "_find_gps_port", lambda baudrate=9600: "COM5")
    monkeypatch.setattr(gps.serial, "Serial", DummySerial)
    monkeypatch.setattr(gps, "NMEAStreamReader", lambda: DummyNmeaReader([]))
    port, reader = gps.openGPS()
    assert port.port == "COM5"
    assert reader is not None


def test_list_serial_ports(monkeypatch):
    monkeypatch.setattr(
        gps.list_ports,
        "comports",
        lambda: [DummyPort("COM1", description="gps", manufacturer="u-blox", hwid="x")],
    )
    ports = gps.list_serial_ports()
    assert ports == [
        {"device": "COM1", "description": "gps", "manufacturer": "u-blox", "hwid": "x"}
    ]


def test_read_gps_stop_event():
    stop_event = threading.Event()
    stop_event.set()
    serial_port = DummySerial("COM1")
    reader = DummyNmeaReader([])
    result = gps.readGPS(logging.getLogger("test"), serial_port, reader, stop_event=stop_event)
    assert result is None


def test_read_gps_returns_fix(monkeypatch):
    gsv = types.SimpleNamespace(
        sentence_type="GSV",
        sv_prn_num_1="1",
        elevation_deg_1=10,
        azimuth_1=20,
        snr_1=30,
        sv_prn_num_2=None,
        elevation_deg_2=None,
        azimuth_2=None,
        snr_2=None,
        sv_prn_num_3=None,
        elevation_deg_3=None,
        azimuth_3=None,
        snr_3=None,
        sv_prn_num_4=None,
        elevation_deg_4=None,
        azimuth_4=None,
        snr_4=None,
    )
    gga = types.SimpleNamespace(sentence_type="GGA", num_sats=7, latitude=12.34, longitude=56.78)
    reader = DummyNmeaReader([[gsv, gga]])
    serial_port = DummySerial("COM1", lines=[b"$GPGGA"])
    monkeypatch.setattr(gps.time, "sleep", lambda _s: None)
    result = gps.readGPS(logging.getLogger("test"), serial_port, reader, max_wait_s=1)
    assert result[0] == pytest.approx(12.34)
    assert result[1] == pytest.approx(56.78)
    assert result[2] == 7
    assert result[3][0]["prn"] == "1"


def test_safe_float():
    assert gps._safe_float("3.5") == 3.5
    assert gps._safe_float("") is None
    assert gps._safe_float(None) is None


# -----------------
# Map tests
# -----------------

def test_predict_transmitter_location_weighted_centroid():
    history = {
        (10.0, 20.0): {"strength": 500, "quality": 1.0},
        (12.0, 22.0): {"strength": 300, "quality": 0.5},
    }
    logger = logging.getLogger("test")
    lat, lon = map_mod.predictTransmitterLocation(history, logger)
    assert lat == pytest.approx(10.4615, rel=1e-3)
    assert lon == pytest.approx(20.4615, rel=1e-3)


def test_predict_transmitter_location_dead_zone():
    history = {
        (10.0, 20.0): 200,
        (12.0, 22.0): 150,
    }
    logger = logging.getLogger("test")
    with pytest.raises(ValueError):
        map_mod.predictTransmitterLocation(history, logger)


def test_map_function_offline(tmp_path):
    history = {(10.0, 20.0): {"strength": 350, "quality": 0.9, "ts": 1}}
    output = tmp_path / "map.png"
    map_mod.mapFunction(history, "", logging.getLogger("test"), output_file=str(output))
    assert output.exists()
    assert output.stat().st_size > 0


def test_map_function_success(tmp_path, monkeypatch):
    def fake_get(url, timeout=5):
        return types.SimpleNamespace(status_code=200, content=_dummy_png_bytes(), text="")

    monkeypatch.setattr(map_mod.requests, "get", fake_get)
    history = {(10.0, 20.0): {"strength": 350, "quality": 0.9, "ts": 1}}
    output = tmp_path / "map.png"
    map_mod.mapFunction(history, "token", logging.getLogger("test"), output_file=str(output))
    assert output.exists()
    assert output.stat().st_size > 0


def test_map_function_http_error(tmp_path, monkeypatch):
    def fake_get(url, timeout=5):
        return types.SimpleNamespace(status_code=500, content=b"", text="bad")

    monkeypatch.setattr(map_mod.requests, "get", fake_get)
    history = {(10.0, 20.0): {"strength": 350, "quality": 0.9, "ts": 1}}
    output = tmp_path / "map.png"
    map_mod.mapFunction(history, "token", logging.getLogger("test"), output_file=str(output))
    assert output.exists()


def test_map_function_request_exception(tmp_path, monkeypatch):
    def fake_get(url, timeout=5):
        raise map_mod.requests.RequestException("boom")

    monkeypatch.setattr(map_mod.requests, "get", fake_get)
    history = {(10.0, 20.0): {"strength": 350, "quality": 0.9, "ts": 1}}
    output = tmp_path / "map.png"
    map_mod.mapFunction(history, "token", logging.getLogger("test"), output_file=str(output))
    assert output.exists()


# -----------------
# Pinpoint API tests
# -----------------

def test_pinpoint_api_call_and_context():
    from pinpoint.plugin_api import PinpointAPI

    api = PinpointAPI(logger=logging.getLogger("test"))
    api.register("core.echo", lambda payload: {"value": payload.get("x")})
    result = api.call("core.echo", {"x": 42})
    assert result["ok"] is True
    assert result["value"] == 42

    missing = api.call("no.such.handler")
    assert missing["ok"] is False

    bad_payload = api.call("core.echo", payload="bad")
    assert bad_payload["ok"] is False

    api.set_context(foo=123)
    assert api.get_context("foo") == 123


def test_event_bus_subscribe_unsubscribe():
    from pinpoint.plugin_api import PinpointAPI

    api = PinpointAPI(logger=logging.getLogger("test"))
    seen = []

    token = api.subscribe("telemetry", lambda payload: seen.append(payload))
    api.emit("telemetry", {"x": 1})
    assert seen == [{"x": 1}]

    assert api.unsubscribe(token) is True
    api.emit("telemetry", {"x": 2})
    assert seen == [{"x": 1}]


# -----------------
# Core tests
# -----------------

def test_resource_path_meipass(core_module, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resolved = core_module._resource_path("assets", "gifs", "general.gif")
    assert resolved == os.path.join(str(tmp_path), "assets", "gifs", "general.gif")


def test_resource_path_default(core_module, monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    resolved = core_module._resource_path("app.ico")
    assert resolved == os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(core_module.__file__))), "app.ico")


def test_normalize_bearing(core_module):
    assert core_module._normalize_bearing(-10.0) == pytest.approx(350.0)
    assert core_module._normalize_bearing(370.0) == pytest.approx(10.0)


def test_relative_bearing(core_module):
    assert core_module._relative_bearing(10.0, 350.0) == pytest.approx(20.0)
    assert core_module._relative_bearing(None, 10.0) is None


def test_bearing_to_cardinal(core_module):
    assert core_module._bearing_to_cardinal(0.0) == "N 0deg"
    assert core_module._bearing_to_cardinal(45.0).startswith("NE")


def test_transparentize_gif(core_module, tmp_path):
    src = tmp_path / "sample.gif"
    img = Image.new("RGB", (4, 4), color=(255, 255, 255))
    img.save(src, format="GIF")
    out = core_module._transparentize_gif(str(src))
    assert os.path.exists(out)
    assert out.endswith(".gif")


def test_version_metadata():
    from pinpoint import version

    assert isinstance(version.APP_VERSION, str)
    assert isinstance(version.APP_VERSION_NAME, str)
