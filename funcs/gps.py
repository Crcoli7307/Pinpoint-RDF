"""
PINPOINT Software Project
funcs/gps.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Detects GPS serial ports, probes for NMEA output, and reads fixes and satellite data.
Exposes helper functions for UI selection and continuous reads.
---

https://nexus.crayton.dev/
"""

import os
import re
import time

import serial
from pynmea2 import NMEAStreamReader
from serial.tools import list_ports

_NMEA_POSITION_HEADER = re.compile(r"^\$[A-Z0-9]{2}(?:GGA|RMC)$", re.IGNORECASE)


def _is_nmea_position_sentence(line):
    """Return whether *line* looks like a standard GGA or RMC NMEA sentence."""
    if not isinstance(line, str):
        return False
    header, separator, _payload = line.strip().partition(",")
    return bool(separator and _NMEA_POSITION_HEADER.fullmatch(header))


def _probe_nmea(port_name, baudrate=9600, timeout=0.5, probe_seconds=4.0):
    """
    Try to read NMEA sentences from a serial port.
    Returns True if we see a GGA/RMC sentence within the probe window.
    """
    end_time = time.time() + probe_seconds
    try:
        ser = serial.Serial(port_name, baudrate=baudrate, timeout=timeout)
    except Exception:
        return False
    try:
        while time.time() < end_time:
            try:
                line = ser.readline().decode("ascii", errors="replace").strip()
            except Exception:
                line = ""
            if not line:
                continue
            if _is_nmea_position_sentence(line):
                return True
        return False
    finally:
        try:
            ser.close()
        except Exception:
            pass

def _find_gps_port(baudrate=9600):
    """
    Find the COM port that is actually emitting NMEA sentences.
    """
    env_port = os.environ.get("GPS_PORT")
    env_baud = os.environ.get("GPS_BAUD")
    if env_baud:
        try:
            baudrate = int(env_baud)
        except ValueError:
            raise Exception(f"GPS_BAUD must be an integer (got {env_baud!r}).")
    ports = list(list_ports.comports())

    if env_port:
        for p in ports:
            if p.device.upper() == env_port.upper():
                return p.device
        raise Exception(f"GPS_PORT is set to {env_port} but that port was not found.")

    if not ports:
        raise Exception("No serial ports found. Is the GPS plugged in?")

    # Prefer ports that look like GPS devices by description/manufacturer.
    gps_keywords = ("gps", "u-blox", "ublox", "gnss", "nmea")
    keyword_matches = []
    for p in ports:
        haystack = " ".join(
            [
                str(p.device or ""),
                str(p.description or ""),
                str(p.manufacturer or ""),
                str(p.hwid or ""),
            ]
        ).lower()
        if any(k in haystack for k in gps_keywords):
            keyword_matches.append(p.device)

    candidates = keyword_matches

    if not candidates:
        port_list = ", ".join(
            f"{p.device} ({(p.description or '').strip()})"
            for p in ports
        )
        raise Exception(
            "No serial port identifies itself as a GPS/GNSS receiver. "
            "Select the GPS COM port manually or set GPS_PORT. "
            f"Detected ports: {port_list}"
        )

    # Try common GPS baud rates to improve auto-detection.
    baudrates_to_try = [baudrate, 9600, 4800, 38400, 115200]
    # De-dupe while preserving order
    seen = set()
    baudrates_to_try = [b for b in baudrates_to_try if not (b in seen or seen.add(b))]

    # Probe candidates for actual NMEA output.
    for dev in candidates:
        for br in baudrates_to_try:
            if _probe_nmea(dev, baudrate=br):
                return dev

    # Include a compact port listing to help field operators.
    port_list = ", ".join(
        f"{p.device} ({(p.description or '').strip()})"
        for p in ports
    )
    raise Exception(
        "GPS/GNSS ports were found, but none emitted GGA or RMC NMEA data. "
        "Select the correct COM port manually or set GPS_PORT. "
        f"Detected ports: {port_list}"
    )

def openGPS(port=None, baudrate=9600, timeout=1):
    """
    Open and return a serial port and NMEA reader for reuse.
    """
    if port is None:
        port = _find_gps_port(baudrate=baudrate)
    serial_port = serial.Serial(port, baudrate=baudrate, timeout=timeout)
    nmea_reader = NMEAStreamReader()
    return serial_port, nmea_reader

def list_serial_ports():
    """
    Return a list of serial ports with metadata for UI selection.
    """
    ports = []
    for p in list_ports.comports():
        ports.append(
            {
                "device": p.device,
                "description": p.description or "",
                "manufacturer": p.manufacturer or "",
                "hwid": p.hwid or "",
            }
        )
    return ports


def _gps_reader_state(nmea_reader):
    state = getattr(nmea_reader, "_pinpoint_state", None)
    if not isinstance(state, dict):
        state = {"satellites_by_talker": {}, "last_num_sats": None}
        try:
            setattr(nmea_reader, "_pinpoint_state", state)
        except Exception:
            pass
    return state


def _cached_satellites(reader_state):
    satellites = []
    for talker_satellites in reader_state.get("satellites_by_talker", {}).values():
        satellites.extend(talker_satellites.values())
    return satellites

def readGPS(logger, serial_port=None, nmea_reader=None, stop_event=None, max_wait_s=10):
    """
    Read latitude and longitude from a USB GPS receiver using PyNMEA.

    Returns:
        tuple: (LAT, LON, num_sats, satellites) when a valid fix is received, or None on timeout/stop.
    """
    created_port = False
    position_data_seen = False
    try:
        if serial_port is None or nmea_reader is None:
            serial_port, nmea_reader = openGPS()
            created_port = True
        reader_state = _gps_reader_state(nmea_reader)
        satellites_by_talker = reader_state["satellites_by_talker"]
        last_num_sats = reader_state.get("last_num_sats")
        start_time = time.time()
        while True:
            if stop_event is not None and stop_event.is_set():
                return None
            data = serial_port.readline().decode('ascii', errors='replace')
            for msg in nmea_reader.next(data):
                if msg.sentence_type == 'GSV':
                    # Satellites in view; gather per-satellite info when available
                    talker = str(getattr(msg, "talker", "") or "")
                    satellites_by_prn = satellites_by_talker.setdefault(talker, {})
                    if str(getattr(msg, "msg_num", "")).strip() == "1":
                        satellites_by_prn.clear()
                    for i in range(1, 5):
                        prn = getattr(msg, f"sv_prn_num_{i}", None)
                        if not prn:
                            continue
                        try:
                            prn = str(prn).strip()
                        except Exception:
                            prn = str(prn)
                        elev = getattr(msg, f"elevation_deg_{i}", None)
                        az = getattr(msg, f"azimuth_{i}", None)
                        snr = getattr(msg, f"snr_{i}", None)
                        satellites_by_prn[prn] = {
                            "prn": prn,
                            "elevation": _safe_float(elev),
                            "azimuth": _safe_float(az),
                            "snr": _safe_float(snr),
                        }
                if msg.sentence_type == 'GGA':
                    position_data_seen = True
                    logger.info(f"Number of satellites: {msg.num_sats}")
                    last_num_sats = msg.num_sats
                    reader_state["last_num_sats"] = last_num_sats
                    if msg.latitude != 0.0 and msg.longitude != 0.0:
                        latitude = msg.latitude
                        longitude = msg.longitude
                        satellites = _cached_satellites(reader_state)
                        return (latitude, longitude, msg.num_sats, satellites)
                    else:
                        logger.debug("Waiting for valid coordinates...")
                elif msg.sentence_type == 'RMC':
                    position_data_seen = True
                    if (
                        getattr(msg, "status", "A") == "A"
                        and msg.latitude != 0.0
                        and msg.longitude != 0.0
                    ):
                        satellites = _cached_satellites(reader_state)
                        return (msg.latitude, msg.longitude, last_num_sats, satellites)
                time.sleep(0.1)
            if max_wait_s is not None and (time.time() - start_time) >= max_wait_s:
                satellites = _cached_satellites(reader_state)
                if position_data_seen or satellites or last_num_sats is not None:
                    return (None, None, last_num_sats, satellites)
                return None
    except Exception as e:
        raise Exception(f"Error reading GPS: {e}")
    finally:
        if created_port:
            try:
                serial_port.close()
            except Exception:
                pass

def _safe_float(value):
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None
