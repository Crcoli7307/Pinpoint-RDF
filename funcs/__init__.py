"""
PINPOINT Software Project
funcs/__init__.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Aggregates SDR, GPS, and map helpers into a single import surface.
Re-exports commonly used third-party modules for convenience.
---

https://crayton.dev/
"""

from .sdr import (
    selectRadio,
    readRadio,
    processSamples,
    calculateSignalStrength,
    calculateSignalQuality,
    calculateSpectrum,
    list_sdr_devices,
)
from .gps import (
    _probe_nmea,
    _find_gps_port,
    openGPS,
    list_serial_ports,
    readGPS,
    get_gps_metadata,
    _safe_float,
)
from .map import (
    predictTransmitterLocation,
    estimateTransmitterLocation,
    estimateTransmitterFromBearings,
    renderOfflineMapBytes,
    overlayAlertsOnMapBytes,
    mapFunction,
)

import requests
import serial
from serial.tools import list_ports
try:
    import rtlsdr  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    rtlsdr = None

__all__ = [
    'selectRadio',
    'readRadio',
    'processSamples',
    'calculateSignalStrength',
    'calculateSignalQuality',
    'calculateSpectrum',
    'list_sdr_devices',
    'predictTransmitterLocation',
    'estimateTransmitterLocation',
    'estimateTransmitterFromBearings',
    'renderOfflineMapBytes',
    'overlayAlertsOnMapBytes',
    'mapFunction',
    '_probe_nmea',
    '_find_gps_port',
    'openGPS',
    'list_serial_ports',
    'readGPS',
    'get_gps_metadata',
    '_safe_float',
    'requests',
    'serial',
    'list_ports',
    'rtlsdr',
]
