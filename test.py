# Pinpoint for Windows Test File

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


def _should_stub_rtlsdr():
    if os.environ.get("PINPOINT_TEST_STUB_RTLSDR") == "1":
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return any("pytest" in arg for arg in sys.argv)


def _ensure_rtlsdr_available():
    if not _should_stub_rtlsdr():
        return
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

# Keep Qt headless-friendly in CI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import funcs


class DummyPort:
    def __init__(self, device, description="", manufacturer="", hwid=""):
        self.device = device
        self.description = description
        self.manufacturer = manufacturer
        self.hwid = hwid


class DummySerial:
    def __init__(self, port, baudrate=9600, timeout=1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout

    def readline(self):
        return b""

    def close(self):
        pass


class DummyNmeaReader:
    def __init__(self, messages):
        self._messages = list(messages)

    def next(self, data):
        if not self._messages:
            return []
        return self._messages.pop(0)


def _dummy_png_bytes():
    img = Image.new("RGB", (4, 4), color=(120, 80, 200))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture(scope="module")
def app_module():
    try:
        import main as app
        return app
    except Exception as exc:
        pytest.skip(f"Skipping main.py tests (PyQt6 unavailable): {exc}")


def test_process_samples_abs():
    samples = np.array([1 + 1j, -3 - 4j])
    processed = funcs.processSamples(samples)
    assert processed[0] == pytest.approx(np.sqrt(2))
    assert processed[1] == pytest.approx(5.0)


def test_calculate_signal_strength_clamps():
    samples = np.array([0.0, 0.0, 0.0])
    assert funcs.calculateSignalStrength(samples) == 1

    samples = np.array([2.0, 2.0])
    assert funcs.calculateSignalStrength(samples) == 1000


def test_calculate_signal_quality_empty():
    result = funcs.calculateSignalQuality([])
    assert result["mean"] == 0.0
    assert result["std"] == 0.0
    assert result["snr"] == 0.0
    assert result["quality"] == 0.0


def test_calculate_signal_quality_nonempty():
    samples = np.array([1.0, 3.0, 5.0])
    result = funcs.calculateSignalQuality(samples)
    assert result["mean"] == pytest.approx(3.0)
    assert result["std"] > 0.0
    assert 0.0 <= result["quality"] <= 1.0


def test_predict_transmitter_location_weighted_centroid():
    history = {
        (10.0, 20.0): {"strength": 500, "quality": 1.0},
        (12.0, 22.0): {"strength": 300, "quality": 0.5},
    }
    logger = logging.getLogger("test")
    lat, lon = funcs.predictTransmitterLocation(history, logger)
    assert lat == pytest.approx(10.4615, rel=1e-3)
    assert lon == pytest.approx(20.4615, rel=1e-3)


def test_predict_transmitter_location_dead_zone():
    history = {
        (10.0, 20.0): 200,
        (12.0, 22.0): 150,
    }
    logger = logging.getLogger("test")
    with pytest.raises(ValueError):
        funcs.predictTransmitterLocation(history, logger)


def test_map_function_saves_image(tmp_path, monkeypatch):
    def fake_get(url, timeout=5):
        return types.SimpleNamespace(status_code=200, content=_dummy_png_bytes(), text="")

    monkeypatch.setattr(funcs.requests, "get", fake_get)
    history = {(10.0, 20.0): {"strength": 350, "quality": 0.9, "ts": 1}}
    output = tmp_path / "map.png"
    funcs.mapFunction(history, "token", logging.getLogger("test"), output_file=str(output))
    assert output.exists()
    assert output.stat().st_size > 0


def test_map_function_http_error(tmp_path, monkeypatch):
    def fake_get(url, timeout=5):
        return types.SimpleNamespace(status_code=500, content=b"", text="bad")

    monkeypatch.setattr(funcs.requests, "get", fake_get)
    history = {(10.0, 20.0): {"strength": 350, "quality": 0.9, "ts": 1}}
    output = tmp_path / "map.png"
    funcs.mapFunction(history, "token", logging.getLogger("test"), output_file=str(output))
    assert not output.exists()


def test_map_function_request_exception(tmp_path, monkeypatch):
    def fake_get(url, timeout=5):
        raise funcs.requests.RequestException("boom")

    monkeypatch.setattr(funcs.requests, "get", fake_get)
    history = {(10.0, 20.0): {"strength": 350, "quality": 0.9, "ts": 1}}
    output = tmp_path / "map.png"
    funcs.mapFunction(history, "token", logging.getLogger("test"), output_file=str(output))
    assert not output.exists()


def test_find_gps_port_env_missing(monkeypatch):
    monkeypatch.setenv("GPS_PORT", "COM9")
    monkeypatch.setattr(funcs.list_ports, "comports", lambda: [DummyPort("COM1")])
    with pytest.raises(Exception, match="GPS_PORT"):
        funcs._find_gps_port()


def test_find_gps_port_no_ports(monkeypatch):
    monkeypatch.delenv("GPS_PORT", raising=False)
    monkeypatch.setattr(funcs.list_ports, "comports", lambda: [])
    with pytest.raises(Exception, match="No serial ports"):
        funcs._find_gps_port()


def test_find_gps_port_keyword_match(monkeypatch):
    monkeypatch.setattr(funcs.list_ports, "comports", lambda: [DummyPort("COM5", description="u-blox gps")])
    monkeypatch.setattr(funcs, "_probe_nmea", lambda *args, **kwargs: False)
    assert funcs._find_gps_port() == "COM5"


def test_find_gps_port_probe_success(monkeypatch):
    ports = [DummyPort("COM3"), DummyPort("COM7")]
    monkeypatch.setattr(funcs.list_ports, "comports", lambda: ports)

    def probe(port, baudrate=9600, timeout=0.5, probe_seconds=4.0):
        return port == "COM7"

    monkeypatch.setattr(funcs, "_probe_nmea", probe)
    assert funcs._find_gps_port() == "COM7"


def test_open_gps_uses_find(monkeypatch):
    monkeypatch.setattr(funcs, "_find_gps_port", lambda baudrate=9600: "COM5")
    monkeypatch.setattr(funcs.serial, "Serial", DummySerial)
    port, reader = funcs.openGPS()
    assert port.port == "COM5"
    assert reader is not None


def test_list_serial_ports(monkeypatch):
    monkeypatch.setattr(
        funcs.list_ports,
        "comports",
        lambda: [DummyPort("COM1", description="gps", manufacturer="u-blox", hwid="x")],
    )
    ports = funcs.list_serial_ports()
    assert ports == [
        {"device": "COM1", "description": "gps", "manufacturer": "u-blox", "hwid": "x"}
    ]


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

    monkeypatch.setattr(funcs, "rtlsdr", types.SimpleNamespace(librtlsdr=StubLib, RtlSdr=StubRtlSdr))
    devices = funcs.list_sdr_devices()
    assert devices == [
        {"index": 0, "name": "RTL-SDR", "serial": "abc"},
        {"index": 1, "name": "RTL-SDR", "serial": "def"},
    ]


def test_read_gps_stop_event():
    stop_event = threading.Event()
    stop_event.set()
    serial_port = DummySerial("COM1")
    reader = DummyNmeaReader([])
    result = funcs.readGPS(logging.getLogger("test"), serial_port, reader, stop_event=stop_event)
    assert result is None


def test_read_gps_returns_fix():
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
    serial_port = DummySerial("COM1")
    result = funcs.readGPS(logging.getLogger("test"), serial_port, reader, max_wait_s=1)
    assert result[0] == pytest.approx(12.34)
    assert result[1] == pytest.approx(56.78)
    assert result[2] == 7
    assert result[3][0]["prn"] == "1"


def test_safe_float():
    assert funcs._safe_float("3.5") == 3.5
    assert funcs._safe_float("") is None
    assert funcs._safe_float(None) is None


def test_resource_path_meipass(app_module, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resolved = app_module._resource_path("assets", "gifs", "general.gif")
    assert resolved == os.path.join(str(tmp_path), "assets", "gifs", "general.gif")


def test_resource_path_default(app_module, monkeypatch):
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    resolved = app_module._resource_path("app.ico")
    assert resolved == os.path.join(os.path.dirname(os.path.abspath(app_module.__file__)), "app.ico")


def test_normalize_bearing(app_module):
    assert app_module._normalize_bearing(-10.0) == pytest.approx(350.0)
    assert app_module._normalize_bearing(370.0) == pytest.approx(10.0)


def test_relative_bearing(app_module):
    assert app_module._relative_bearing(10.0, 350.0) == pytest.approx(20.0)
    assert app_module._relative_bearing(None, 10.0) is None


def test_bearing_deg(app_module):
    bearing = app_module._bearing_deg(0.0, 0.0, 0.0, 1.0)
    assert bearing == pytest.approx(90.0, abs=1.0)


def test_bearing_to_cardinal(app_module):
    assert app_module._bearing_to_cardinal(0.0) == "N 0deg"
    assert app_module._bearing_to_cardinal(45.0).startswith("NE")


def test_spacing_helpers(app_module):
    ideal = app_module._ideal_spacing_inches(100.0)
    expected = (299_792_458.0 / (100.0 * 1_000_000.0)) / 2.0 / 0.0254
    assert ideal == pytest.approx(expected)
    assert app_module._effective_spacing_inches(100.0, 12.0) == 12.0
    assert app_module._spacing_factor(100.0, ideal * 2) == pytest.approx(1.0)


def test_estimate_target_from_history(app_module):
    history = {(1.0, 2.0): 100, (3.0, 4.0): {"strength": 500}}
    assert app_module._estimate_target_from_history(history) == (3.0, 4.0)


def test_antenna_angles(app_module):
    assert app_module._antenna_angles(1) == [0.0]
    assert app_module._antenna_angles(2) == [0.0, 180.0]
    assert app_module._antenna_angles(4) == [0.0, 90.0, 180.0, 270.0]


def test_aoa_from_strengths(app_module):
    bearing, confidence = app_module._aoa_from_strengths([1.0, 0.0], [0.0, 180.0])
    assert bearing == pytest.approx(0.0)
    assert confidence == pytest.approx(1.0)


def test_fuse_bearings(app_module):
    bearing, confidence = app_module._fuse_bearings([(0.0, 1.0), (90.0, 1.0)])
    assert bearing == pytest.approx(45.0, abs=1.0)
    assert confidence == pytest.approx(1.0)


def test_map_confidence(app_module):
    history = {(1.0, 2.0): {"strength": 400}, (3.0, 4.0): {"strength": 300}}
    conf = app_module._map_confidence(history)
    assert 0.0 <= conf <= 1.0
    assert conf > 0.1


def test_device_key(app_module):
    assert app_module._device_key(0, "abc") == "serial:abc"
    assert app_module._device_key(1, None) == "index:1"


def test_calibration_profile_roundtrip(app_module, tmp_path, monkeypatch):
    path = tmp_path / "calibration.json"
    monkeypatch.setattr(app_module, "CALIBRATION_FILE", str(path))
    data = {"default": {"gain": 5}}
    app_module._save_calibration_profiles(data)
    loaded = app_module._load_calibration_profiles()
    assert loaded == data


def test_transparentize_gif(app_module, tmp_path):
    src = tmp_path / "sample.gif"
    img = Image.new("RGB", (4, 4), color=(255, 255, 255))
    img.save(src, format="GIF")
    out = app_module._transparentize_gif(str(src))
    assert os.path.exists(out)
    assert out.endswith(".gif")


if __name__ == "__main__":
    try:
        gps_coordinates = funcs.readGPS(logging.getLogger("manual"))
        print(f"GPS Data: {gps_coordinates}")
        radio = funcs.selectRadio()
        print("SDR initialized successfully.")
    except Exception as e:
        print(f"Initialization error: {e}")
