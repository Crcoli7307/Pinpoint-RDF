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


def test_read_radio_can_reuse_existing_configuration():
    radio = DummyRadio()
    radio.center_freq = 90.0e6
    radio.sample_rate = 1.0e6
    radio.gain = 10

    samples = sdr.readRadio(radio, seconds=1, frequency=100.0, gain=20, configure=False)

    assert radio.center_freq == 90.0e6
    assert radio.sample_rate == 1.0e6
    assert radio.gain == 10
    assert isinstance(samples, np.ndarray)


def test_startup_gps_guess_ignores_unrelated_port(core_module, monkeypatch):
    monkeypatch.delenv("GPS_PORT", raising=False)
    monkeypatch.setattr(
        core_module.funcs,
        "list_serial_ports",
        lambda: [{"device": "COM8", "description": "USB modem", "manufacturer": "", "hwid": ""}],
    )
    assert core_module._guess_gps_port_no_open() is None


def test_hardware_check_does_not_probe_unrelated_port(core_module, monkeypatch):
    monkeypatch.delenv("GPS_PORT", raising=False)
    monkeypatch.setattr(
        core_module.funcs,
        "list_serial_ports",
        lambda: [{"device": "COM8", "description": "USB modem", "manufacturer": "", "hwid": ""}],
    )
    probed = []
    monkeypatch.setattr(core_module.funcs, "_probe_nmea", lambda port, **kwargs: probed.append(port))
    assert core_module._detect_gps_nmea_present() is False
    assert probed == []


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


def test_find_gps_port_keyword_without_nmea_is_rejected(monkeypatch):
    monkeypatch.setattr(gps.list_ports, "comports", lambda: [DummyPort("COM5", description="u-blox gps")])
    monkeypatch.setattr(gps, "_probe_nmea", lambda *args, **kwargs: False)
    with pytest.raises(Exception, match="none emitted"):
        gps._find_gps_port()


def test_find_gps_port_probe_success(monkeypatch):
    ports = [DummyPort("COM3", description="GPS receiver"), DummyPort("COM7", description="GNSS receiver")]
    monkeypatch.setattr(gps.list_ports, "comports", lambda: ports)
    monkeypatch.setattr(gps, "_probe_nmea", lambda port, **kwargs: port == "COM7")
    assert gps._find_gps_port() == "COM7"


def test_find_gps_port_does_not_probe_unrelated_serial_port(monkeypatch):
    monkeypatch.setattr(gps.list_ports, "comports", lambda: [DummyPort("COM8", description="USB modem")])
    probed = []
    monkeypatch.setattr(gps, "_probe_nmea", lambda port, **kwargs: probed.append(port))
    with pytest.raises(Exception, match="identifies itself"):
        gps._find_gps_port()
    assert probed == []


@pytest.mark.parametrize("sentence", [
    "$GPGGA,123519,4807.038,N,01131.000,E,1,08",
    "$GNRMC,123519,A,4807.038,N,01131.000,E",
])
def test_nmea_position_sentence_recognizes_standard_headers(sentence):
    assert gps._is_nmea_position_sentence(sentence)


def test_nmea_position_sentence_rejects_unrelated_data():
    assert not gps._is_nmea_position_sentence("modem ready")
    assert not gps._is_nmea_position_sentence("$GPVTG,054.7,T,034.4,M")


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


def test_read_gps_returns_rmc_fix(monkeypatch):
    rmc = types.SimpleNamespace(
        sentence_type="RMC",
        status="A",
        latitude=12.34,
        longitude=56.78,
    )
    reader = DummyNmeaReader([[rmc]])
    serial_port = DummySerial("COM1", lines=[b"$GPRMC"])
    monkeypatch.setattr(gps.time, "sleep", lambda _s: None)
    result = gps.readGPS(logging.getLogger("test"), serial_port, reader, max_wait_s=1)
    assert result[:2] == pytest.approx((12.34, 56.78))


def test_read_gps_preserves_gsv_details_across_fix_cycles(monkeypatch):
    gga = types.SimpleNamespace(sentence_type="GGA", num_sats=7, latitude=12.34, longitude=56.78)
    gsv = types.SimpleNamespace(
        sentence_type="GSV",
        talker="GP",
        msg_num="1",
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
    rmc = types.SimpleNamespace(
        sentence_type="RMC",
        status="A",
        latitude=12.34,
        longitude=56.78,
    )
    reader = DummyNmeaReader([[gga], [gsv, rmc], [gga]])
    serial_port = DummySerial("COM1", lines=[b"$GPGGA", b"$GPGSV", b"$GPGGA"])
    monkeypatch.setattr(gps.time, "sleep", lambda _s: None)

    first = gps.readGPS(logging.getLogger("test"), serial_port, reader, max_wait_s=1)
    second = gps.readGPS(logging.getLogger("test"), serial_port, reader, max_wait_s=1)
    third = gps.readGPS(logging.getLogger("test"), serial_port, reader, max_wait_s=1)

    assert first[3] == []
    assert second[3][0]["prn"] == "1"
    assert third[3][0]["prn"] == "1"


def test_gps_info_reports_satellite_count_while_waiting_for_gsv():
    from pinpoint.ui_components import GPSInfoDialog

    label_state = {}
    dialog = types.SimpleNamespace(
        table=types.SimpleNamespace(setRowCount=lambda count: label_state.update(rows=count)),
        empty_label=types.SimpleNamespace(
            setVisible=lambda visible: label_state.update(visible=visible),
            setText=lambda value: label_state.update(text=value),
        ),
        polar=types.SimpleNamespace(set_satellites=lambda satellites: label_state.update(polar=satellites)),
    )

    GPSInfoDialog._set_satellites(dialog, [], satellite_count=11)

    assert label_state["visible"] is True
    assert label_state["text"] == "11 satellites used; waiting for detailed GSV data..."


def test_read_gps_reports_connection_without_fix():
    gga = types.SimpleNamespace(sentence_type="GGA", num_sats=0, latitude=0.0, longitude=0.0)
    result = gps.readGPS(
        logging.getLogger("test"),
        DummySerial("COM1", lines=[b"$GPGGA"]),
        DummyNmeaReader([[gga]]),
        max_wait_s=0,
    )
    assert result == (None, None, 0, [])


def test_read_gps_returns_none_after_timeout_without_nmea():
    result = gps.readGPS(
        logging.getLogger("test"),
        DummySerial("COM1"),
        DummyNmeaReader([]),
        max_wait_s=0,
    )
    assert result is None


def test_idle_gps_thread_emits_location(core_module, monkeypatch):
    stop_event = threading.Event()
    serial_port = DummySerial("COM7")
    reader = DummyNmeaReader([])
    calls = 0

    monkeypatch.setattr(core_module.funcs, "openGPS", lambda port: (serial_port, reader))

    def fake_read_gps(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (12.34, 56.78, 8, [{"prn": "1"}])
        stop_event.set()
        return None

    monkeypatch.setattr(core_module.funcs, "readGPS", fake_read_gps)
    updates = []
    worker = core_module.GPSLocationThread(logging.getLogger("test"), stop_event, "COM7")
    worker.telemetry.connect(updates.append)
    worker.run()

    assert updates
    assert updates[0]["gps_fix"] is True
    assert updates[0]["gps_loc"] == pytest.approx((12.34, 56.78))
    assert updates[0]["location_only"] is True
    assert serial_port.closed is True


def test_hardware_presence_thread_reports_selected_devices(core_module, monkeypatch):
    stop_event = threading.Event()
    monkeypatch.setattr(
        core_module.funcs,
        "list_sdr_devices",
        lambda: [{"index": 0, "name": "RTL-SDR", "serial": "abc"}],
    )
    monkeypatch.setattr(
        core_module.funcs,
        "list_serial_ports",
        lambda: [{"device": "COM7", "description": "GPS"}],
    )
    updates = []

    def on_presence(sdr_present, gps_present):
        updates.append((sdr_present, gps_present))
        stop_event.set()

    worker = core_module.HardwarePresenceThread(stop_event, gps_port="COM7", interval_s=0.25)
    worker.presence.connect(on_presence)
    worker.run()

    assert updates == [(True, True)]


def test_idle_hardware_presence_updates_disconnect_and_reconnect():
    from pinpoint.main_window import MainWindow

    refreshes = []
    window = types.SimpleNamespace(
        collecting=False,
        demo_active=False,
        playback_mode=False,
        sdr_connected=True,
        sdr_sample_rate=2.048e6,
        sdr_error=None,
        antenna_states=[{"connected": True}],
        gps_port="COM7",
        latest_gps_fix=True,
        latest_sats=8,
        latest_fix_age=0.0,
        latest_satellites=[{"prn": "1"}],
        _idle_gps_point={"lat": 12.34, "lon": 56.78},
        _update_info_panel=lambda: refreshes.append("panel"),
        _refresh_info_dialogs=lambda force=False: refreshes.append(("dialogs", force)),
        update_image=lambda force=False: refreshes.append(("map", force)),
    )

    MainWindow._on_hardware_presence(window, False, False)
    assert window.sdr_connected is False
    assert window.sdr_sample_rate is None
    assert window.sdr_error == "SDR disconnected"
    assert window.latest_gps_fix is False
    assert window._idle_gps_point is None
    assert ("map", True) in refreshes

    MainWindow._on_hardware_presence(window, True, True)
    assert window.sdr_connected is True
    assert window.sdr_error is None


def test_idle_gps_thread_keeps_recent_fix_during_sentence_gap(core_module, monkeypatch):
    stop_event = threading.Event()
    serial_port = DummySerial("COM7")
    calls = 0

    monkeypatch.setattr(core_module.funcs, "openGPS", lambda port: (serial_port, object()))

    def fake_read_gps(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (12.34, 56.78, 8, [])
        if calls == 2:
            return None
        stop_event.set()
        return None

    monkeypatch.setattr(core_module.funcs, "readGPS", fake_read_gps)
    updates = []
    worker = core_module.GPSLocationThread(logging.getLogger("test"), stop_event, "COM7")
    worker.telemetry.connect(updates.append)
    worker.run()

    assert len(updates) == 2
    assert updates[0]["gps_fix"] is True
    assert updates[1]["gps_fix"] is True
    assert updates[1]["gps_loc"] == pytest.approx((12.34, 56.78))


def test_gps_fix_expires_only_after_stale_window(core_module):
    last_fix = (12.34, 56.78)
    assert core_module._gps_fix_is_current(last_fix, 100.0, now=114.9) is True
    assert core_module._gps_fix_is_current(last_fix, 100.0, now=115.1) is False


def test_gps_distance_is_measured_in_meters(core_module):
    assert core_module._distance_m(0.0, 0.0, 0.0, 0.001) == pytest.approx(111.2, abs=0.2)


def test_settings_include_map_movement_threshold(core_module):
    configured = core_module.Settings(movement_threshold_m=12.5)
    assert configured.to_dict()["movement_threshold_m"] == pytest.approx(12.5)


def test_collector_keeps_sdr_configured_and_gps_fixed(core_module, monkeypatch):
    stop_event = threading.Event()
    radio = DummyRadio()
    configure_calls = []

    monkeypatch.setattr(
        core_module.funcs,
        "list_sdr_devices",
        lambda: [{"index": 0, "name": "RTL-SDR", "serial": "abc"}],
    )
    monkeypatch.setattr(core_module.funcs, "selectRadio", lambda _index: radio)
    monkeypatch.setattr(core_module.funcs, "openGPS", lambda port=None: (DummySerial(port or "COM7"), object()))
    monkeypatch.setattr(
        core_module.funcs,
        "readGPS",
        lambda **_kwargs: (12.34, 56.78, None, []),
    )

    def fake_read_radio(_radio, _seconds, _frequency, _gain, configure=True):
        configure_calls.append(configure)
        if configure:
            radio.sample_rate = 2.048e6
        return np.array([1 + 1j, 2 + 2j])

    monkeypatch.setattr(core_module.funcs, "readRadio", fake_read_radio)
    monkeypatch.setattr(core_module.funcs, "mapFunction", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core_module.time, "sleep", lambda _seconds: None)

    updates = []

    def on_telemetry(data):
        updates.append(data)
        if len(updates) >= 2:
            stop_event.set()

    worker = core_module.CollectorThread(logging.getLogger("test"), stop_event, gps_port="COM7")
    worker.telemetry.connect(on_telemetry)
    worker.run()

    assert configure_calls == [True, False]
    assert all(update["sdr_connected"] is True for update in updates)
    assert all(update["gps_fix"] is True for update in updates)


def test_collector_pauses_map_until_movement_reaches_threshold(core_module, monkeypatch):
    stop_event = threading.Event()
    radio = DummyRadio()
    fixes = iter(
        [
            (35.0, -80.0, 8, []),
            (35.00002, -80.0, 8, []),
            (35.00006, -80.0, 8, []),
        ]
    )
    map_histories = []

    monkeypatch.setattr(core_module.settings, "movement_threshold_m", 5.0)
    monkeypatch.setattr(core_module, "MAP_UPDATE_INTERVAL_S", 0.0)
    monkeypatch.setattr(
        core_module.funcs,
        "list_sdr_devices",
        lambda: [{"index": 0, "name": "RTL-SDR", "serial": "abc"}],
    )
    monkeypatch.setattr(core_module.funcs, "selectRadio", lambda _index: radio)
    monkeypatch.setattr(core_module.funcs, "openGPS", lambda port=None: (DummySerial(port or "COM7"), object()))
    monkeypatch.setattr(core_module.funcs, "readGPS", lambda **_kwargs: next(fixes))
    monkeypatch.setattr(
        core_module.funcs,
        "readRadio",
        lambda *_args, **_kwargs: np.array([1 + 1j, 2 + 2j]),
    )
    monkeypatch.setattr(
        core_module.funcs,
        "mapFunction",
        lambda history, *_args, **_kwargs: map_histories.append(dict(history)),
    )
    monkeypatch.setattr(core_module.time, "sleep", lambda _seconds: None)

    updates = []

    def on_telemetry(data):
        updates.append(data)
        if len(updates) >= 3:
            stop_event.set()

    worker = core_module.CollectorThread(logging.getLogger("test"), stop_event, gps_port="COM7")
    worker.telemetry.connect(on_telemetry)
    worker.run()

    assert [update["cycle_paused"] for update in updates] == [False, True, False]
    assert updates[1]["pause_reason"] == "Insufficient Movement, Paused Cycle"
    assert updates[1]["gps_loc"] == pytest.approx((35.00002, -80.0))
    assert updates[1]["movement_distance_m"] < 5.0
    assert updates[2]["movement_distance_m"] >= 5.0
    assert len(map_histories) == 2
    assert len(map_histories[0]) == 1
    assert len(map_histories[1]) == 2


def test_collector_switches_gps_port_without_stopping(core_module, monkeypatch):
    stop_event = threading.Event()
    radio = DummyRadio()
    opened_ports = []
    serial_ports = []

    monkeypatch.setattr(
        core_module.funcs,
        "list_sdr_devices",
        lambda: [{"index": 0, "name": "RTL-SDR", "serial": "abc"}],
    )
    monkeypatch.setattr(core_module.funcs, "selectRadio", lambda _index: radio)

    def fake_open_gps(port=None):
        opened_ports.append(port)
        serial_port = DummySerial(port)
        serial_ports.append(serial_port)
        return serial_port, object()

    monkeypatch.setattr(core_module.funcs, "openGPS", fake_open_gps)
    monkeypatch.setattr(core_module.funcs, "readGPS", lambda **_kwargs: (12.34, 56.78, 8, []))
    monkeypatch.setattr(
        core_module.funcs,
        "readRadio",
        lambda *_args, **_kwargs: np.array([1 + 1j, 2 + 2j]),
    )
    monkeypatch.setattr(core_module.funcs, "mapFunction", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core_module.time, "sleep", lambda _seconds: None)

    updates = []
    worker = core_module.CollectorThread(logging.getLogger("test"), stop_event, gps_port="COM7")

    def on_telemetry(data):
        updates.append(data)
        if len(updates) == 1:
            worker.request_gps_port("COM9")
        elif len(updates) == 2:
            stop_event.set()

    worker.telemetry.connect(on_telemetry)
    worker.run()

    assert opened_ports == ["COM7", "COM9"]
    assert serial_ports[0].closed is True
    assert len(updates) == 2


def test_change_gps_port_updates_running_collector(monkeypatch):
    import pinpoint.main_window as main_window_mod

    requested = []

    class FakeWizard:
        def __init__(self, parent=None, current_port=None):
            assert current_port == "COM7"

        def exec(self):
            return main_window_mod.QtWidgets.QDialog.DialogCode.Accepted

        def selected_port(self):
            return "COM9"

    monkeypatch.setattr(main_window_mod, "GPSSetupWizard", FakeWizard)
    monitor = types.SimpleNamespace(gps_port="COM7")
    collector = types.SimpleNamespace(request_gps_port=lambda port: requested.append(port))
    refreshed = []
    window = types.SimpleNamespace(
        playback_mode=False,
        demo_active=False,
        playback_only=False,
        meshtastic_only=False,
        gps_port="COM7",
        _hardware_monitor_thread=monitor,
        latest_gps_fix=True,
        latest_sats=8,
        latest_fix_age=0.0,
        latest_satellites=[{"prn": "1"}],
        _idle_gps_point={"lat": 12.34, "lon": 56.78},
        current_bearing=90.0,
        last_status_msg="Collecting",
        collecting=True,
        thread=collector,
        _stop_idle_gps_tracking=lambda: None,
        _start_idle_gps_tracking=lambda: None,
        _update_info_panel=lambda: refreshed.append("panel"),
        _refresh_info_dialogs=lambda force=False: refreshed.append(("dialogs", force)),
        update_image=lambda force=False: refreshed.append(("map", force)),
    )

    main_window_mod.MainWindow.change_gps_port(window)

    assert window.gps_port == "COM9"
    assert monitor.gps_port == "COM9"
    assert requested == ["COM9"]
    assert window.latest_gps_fix is False
    assert window._idle_gps_point is None
    assert ("map", True) in refreshed


def test_idle_gps_point_is_not_part_of_active_collection():
    from pinpoint.main_window import MainWindow

    window = types.SimpleNamespace(
        report_cache_frames=[],
        _idle_gps_point={"lat": 12.34, "lon": 56.78, "location_only": True},
        collecting=False,
        playback_mode=False,
    )
    assert MainWindow._get_history_points(window) == [window._idle_gps_point]

    window.collecting = True
    assert MainWindow._get_history_points(window) == []


def test_paused_cycles_are_kept_out_of_interactive_map_history():
    from pinpoint.main_window import MainWindow

    window = types.SimpleNamespace(
        report_cache_frames=[
            {"t": 0.0, "telemetry": {"gps_loc": (1.0, 2.0), "strength": 100}},
            {
                "t": 2.0,
                "telemetry": {
                    "gps_loc": (1.00001, 2.0),
                    "strength": 200,
                    "cycle_paused": True,
                },
            },
        ],
        _idle_gps_point=None,
        collecting=True,
        playback_mode=False,
    )

    points = MainWindow._get_history_points(window)
    assert [(point["lat"], point["lon"]) for point in points] == [(1.0, 2.0)]


def test_interactive_map_urgent_alert_states():
    from pinpoint.main_window import MainWindow

    window = types.SimpleNamespace(
        playback_mode=False,
        demo_active=False,
        playback_only=False,
        meshtastic_only=False,
        latest_gps_fix=False,
        sdr_connected=False,
        sdr_error=None,
    )
    assert MainWindow._get_map_alerts(window) == ["NO FIX", "NO SDR"]

    window.sdr_connected = True
    window.sdr_error = "read failed"
    assert MainWindow._get_map_alerts(window) == ["NO FIX", "SDR ERROR"]

    window.playback_mode = True
    assert MainWindow._get_map_alerts(window) == []


def test_interactive_map_paused_warning_state():
    from pinpoint.main_window import MainWindow

    window = types.SimpleNamespace(
        playback_mode=False,
        demo_active=False,
        playback_only=False,
        meshtastic_only=False,
        collecting=True,
        latest_cycle_paused=True,
    )
    assert MainWindow._get_map_warnings(window) == ["PAUSED"]

    window.collecting = False
    assert MainWindow._get_map_warnings(window) == []


def test_interactive_map_html_contains_alert_overlay():
    from pinpoint.main_window import MainWindow

    window = types.SimpleNamespace(
        _map_center=lambda _points: [0.0, 0.0],
        _get_map_alerts=lambda: ["NO FIX", "NO SDR"],
    )
    html = MainWindow._build_map_html(window, "token", [])
    assert 'id="system-alerts"' in html
    assert ".system-alert" in html
    assert 'true, ["NO FIX", "NO SDR"]' in html


def test_report_cycle_marks_insufficient_movement_pause():
    from addons.report_generator import ReportGeneratorDialog

    dialog = types.SimpleNamespace(
        _frames=[
            {
                "t": 0.0,
                "telemetry": {
                    "strength": 100,
                    "snr": 2.0,
                    "gps_fix": True,
                    "cycle_paused": True,
                    "pause_reason": "Insufficient Movement, Paused Cycle",
                },
            }
        ],
        cycle_len_input=types.SimpleNamespace(value=lambda: 2),
        _cycles_cache=None,
        _cycles_cache_key=None,
    )
    dialog._frames_cache_key = types.MethodType(ReportGeneratorDialog._frames_cache_key, dialog)

    cycles = ReportGeneratorDialog._collect_cycles(dialog)
    assert cycles[0]["paused_samples"] == 1
    assert cycles[0]["status"] == "Insufficient Movement, Paused Cycle"


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
