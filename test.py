# Pinpoint for Windows Test File

import importlib
import logging
import os
import sys
import types


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

        stub.RtlSdr = StubRtlSdr
        stub.librtlsdr = types.SimpleNamespace(
            rtlsdr_get_device_count=lambda: 0,
            rtlsdr_get_device_name=lambda i: b"",
        )
        sys.modules["rtlsdr"] = stub


_ensure_rtlsdr_available()

import numpy as np
import pytest

import funcs


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


if __name__ == "__main__":
    try:
        gps_coordinates = funcs.readGPS(logging.getLogger("manual"))
        print(f"GPS Data: {gps_coordinates}")
        radio = funcs.selectRadio()
        print("SDR initialized successfully.")
    except Exception as e:
        print(f"Initialization error: {e}")
