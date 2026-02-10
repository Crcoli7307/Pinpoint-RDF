import os
import logging
import numpy as np
import rtlsdr
from pynmea2 import NMEAStreamReader
import serial
from serial.tools import list_ports
import requests
from PIL import Image, ImageDraw
from io import BytesIO
from urllib.parse import quote
import time

def selectRadio(index=0):
    """
    Select the first available SDR device.
    Returns:
        radio (pyrtlsdr.RtlSdr): An instance of the SDR device.
    """
    try:
        radio = rtlsdr.RtlSdr(index)
        return radio
    except Exception as e:
        raise Exception(f"Error initializing SDR: {e}")

def readRadio(radio, seconds, frequency, gain=30, close_radio=False):
    """
    Read samples from the SDR device.
    
    Args:
        index (int): Index of the SDR device.
        seconds (int): Duration in seconds for which samples are needed.
        frequency (float): Frequency in MHz to tune the SDR.
        
    Returns:
        numpy_array: Samples from the SDR device.
    """
    radio.center_freq = frequency * 1e6  # Convert MHz to Hz
    radio.sample_rate = 2.048e6          # Sample rate (default)
    radio.gain = gain                    # Auto gain
    
    try:
        samples = radio.read_samples(seconds * int(radio.sample_rate))
        # rtlsdr already returns a NumPy array; avoid an extra copy.
        return samples
    finally:
        if close_radio:
            radio.close()

def processSamples(samples):
    """
    Process raw samples from the SDR device.
    
    Args:
        samples (numpy_array): Raw samples from the SDR device.
        
    Returns:
        numpy_array: Processed samples (e.g., magnitude of complex samples).
    """
    return np.abs(samples)

def calculateSignalStrength(processed_samples):
    """
    Estimate signal strength based on processed samples.
    
    Args:
        processed_samples (numpy_array): Processed samples from the SDR device.
        
    Returns:
        int: Signal strength (1 to 1000).
    """
    avg_power = np.mean(processed_samples)
    signal_strength = int(np.clip(avg_power * 1000, 1, 1000))
    return signal_strength

def calculateSignalQuality(processed_samples):
    """
    Estimate signal quality metrics for weighting/validation.

    Returns:
        dict: mean, std, snr, quality (0-1)
    """
    if processed_samples is None or len(processed_samples) == 0:
        return {"mean": 0.0, "std": 0.0, "snr": 0.0, "quality": 0.0}
    mean_val = float(np.mean(processed_samples))
    std_val = float(np.std(processed_samples))
    snr = mean_val / std_val if std_val > 0 else 0.0
    # squash into 0-1 range for stable weighting
    quality = float(np.clip(np.tanh(snr / 10.0), 0.0, 1.0))
    return {"mean": mean_val, "std": std_val, "snr": snr, "quality": quality}

def predictTransmitterLocation(history, logger):
    """
    Predict the transmitter location using signal strengths and coordinates.

    Args:
        history (dict): Dictionary with keys as coordinates (lat, lon) and values as signal strength
            or a dict containing "strength" and optional "quality".

    Returns:
        tuple: Predicted coordinates (latitude, longitude) of the transmitter.
    """
    if not history:
        raise ValueError("History is empty. Cannot predict transmitter location.")

    # Define the dead zone threshold
    DEAD_ZONE_THRESHOLD = 200

    def _strength_from_value(value):
        if isinstance(value, dict):
            return value.get("strength", 0)
        return value

    def _quality_from_value(value):
        if isinstance(value, dict):
            return value.get("quality", 1.0)
        return 1.0

    # Filter out points within the dead zone
    filtered_points = []
    for (lat, lon), value in history.items():
        strength = _strength_from_value(value)
        if strength > DEAD_ZONE_THRESHOLD:
            filtered_points.append((lat, lon, strength, _quality_from_value(value)))

    if not filtered_points:
        raise ValueError("All signal strengths are within the dead zone; cannot determine location.\n\nThis typically occurs if the transmitter is too far, there is no transmitter, or the signal strength is too weak.\n\nThe predict location function cannot track the transmitter if the signal strength is below the threshold.")

    strengths = [strength for _, _, strength, _ in filtered_points]
    median = float(np.median(strengths))
    mad = float(np.median([abs(s - median) for s in strengths]))
    if mad > 0:
        # Reject outliers beyond 3 * MAD
        filtered_points = [
            (lat, lon, strength, quality)
            for (lat, lon, strength, quality) in filtered_points
            if abs(strength - median) <= 3 * mad
        ]
        if not filtered_points:
            raise ValueError("All signal strengths were rejected as outliers; cannot determine location.")

    # Calculate the weighted centroid
    total_weight = sum(strength * quality for _, _, strength, quality in filtered_points)
    if total_weight == 0:
        raise ValueError("Total signal strength is zero; cannot determine location.")

    weighted_lat = sum(lat * strength * quality for lat, _, strength, quality in filtered_points) / total_weight
    weighted_lon = sum(lon * strength * quality for _, lon, strength, quality in filtered_points) / total_weight

    logger.warning("Estimated transmitter location: %.6f, %.6f", weighted_lat, weighted_lon)

    return weighted_lat, weighted_lon


def _render_offline_map(history, output_file="map.png", size=(800, 500)):
    if not history:
        return
    width, height = size
    margin = 24
    img = Image.new("RGB", (width, height), "#f8fafc")
    draw = ImageDraw.Draw(img)

    lats = [lat for (lat, _), _v in history.items()]
    lons = [lon for (_, lon), _v in history.items()]
    if not lats or not lons:
        return

    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    lat_span = max(1e-6, max_lat - min_lat)
    lon_span = max(1e-6, max_lon - min_lon)
    # Add some padding
    min_lat -= lat_span * 0.08
    max_lat += lat_span * 0.08
    min_lon -= lon_span * 0.08
    max_lon += lon_span * 0.08
    lat_span = max(1e-6, max_lat - min_lat)
    lon_span = max(1e-6, max_lon - min_lon)

    def _xy(lat, lon):
        x = margin + int((lon - min_lon) / lon_span * (width - 2 * margin))
        y = margin + int((max_lat - lat) / lat_span * (height - 2 * margin))
        return x, y

    # Subtle grid
    grid_color = "#e5e7eb"
    for i in range(1, 4):
        x = margin + int((width - 2 * margin) * (i / 4.0))
        y = margin + int((height - 2 * margin) * (i / 4.0))
        draw.line([(x, margin), (x, height - margin)], fill=grid_color, width=1)
        draw.line([(margin, y), (width - margin, y)], fill=grid_color, width=1)

    # Determine strengths for coloring
    strengths = []
    latest_key = None
    latest_ts = -1
    for (lat, lon), value in history.items():
        if isinstance(value, dict):
            strengths.append(value.get("strength", 0) or 0)
            ts = value.get("ts", 0) or 0
        else:
            strengths.append(value or 0)
            ts = 0
        if ts >= latest_ts:
            latest_ts = ts
            latest_key = (lat, lon)

    min_val = min(strengths) if strengths else 0
    max_val = max(strengths) if strengths else 1
    span = max(1e-6, max_val - min_val)

    def _color_for_strength(val):
        norm = max(0.0, min(1.0, (val - min_val) / span))
        r = int(255 * norm)
        b = int(255 * (1.0 - norm))
        return (r, 0, b)

    for (lat, lon), value in history.items():
        if isinstance(value, dict):
            strength = value.get("strength", 0) or 0
        else:
            strength = value or 0
        x, y = _xy(lat, lon)
        if latest_key == (lat, lon):
            color = (255, 255, 0)
            r = 5
        else:
            color = _color_for_strength(strength)
            r = 4
        draw.ellipse((x - r, y - r, x + r, y + r), fill=color, outline="#111827")

    # Predicted location marker
    try:
        pred_lat, pred_lon = predictTransmitterLocation(history, logging.getLogger("offline-map"))
        px, py = _xy(pred_lat, pred_lon)
        draw.ellipse((px - 6, py - 6, px + 6, py + 6), fill="#10b981", outline="#065f46")
    except Exception:
        pass

    draw.rectangle(
        (margin - 6, margin - 6, width - margin + 6, height - margin + 6),
        outline="#d1d5db",
        width=1,
    )
    draw.text((margin, 6), "Offline Map", fill="#374151")
    img.save(output_file)


def mapFunction(history, access_token, logger, output_file="map.png", max_markers=None, max_url_len=None):
    """
    Create and save a static map with points colored based on the dictionary values.
    Also adds the predicted transmitter location in green.

    Args:
        history (dict): Dictionary where keys are coordinates (lat, lon) and values are intensity
            or a dict containing "strength" and optional metadata.
        access_token (str): Mapbox access token (optional; offline map is used if missing).
        output_file (str): Output file name for the static map image.
        max_markers (int): Optional cap on number of markers to include.
        max_url_len (int): Optional cap on URL length for Mapbox requests.
    """
    base_url = "https://api.mapbox.com/styles/v1/mapbox/streets-v12/static"

    def _strength_from_value(value):
        if isinstance(value, dict):
            return value.get("strength", 0)
        return value

    def _timestamp_from_value(value):
        if isinstance(value, dict):
            return value.get("ts", 0)
        return 0

    map_history = history
    if max_markers is not None:
        try:
            max_markers = int(max_markers)
        except Exception:
            max_markers = None
    if max_markers and len(history) > max_markers:
        items = sorted(history.items(), key=lambda kv: _timestamp_from_value(kv[1]), reverse=True)
        items = items[:max_markers]
        map_history = {k: v for k, v in items}
    if not map_history:
        return
    history = map_history

    access_token = (access_token or "").strip()
    if not access_token:
        _render_offline_map(history, output_file=output_file)
        return

    strengths = [_strength_from_value(v) for v in history.values()]
    # Normalize intensity values to a 0-1 range for gradient mapping
    min_value = min(strengths)
    max_value = max(strengths)

    def normalize(value):
        return (value - min_value) / (max_value - min_value) if max_value > min_value else 0

    # Map normalized values to hex colors
    def get_hex_color(value):
        norm_value = normalize(value)
        r = int(255 * norm_value)
        b = int(255 * (1 - norm_value))
        return f"{r:02x}00{b:02x}"

    # Build markers for each point in the history
    features = []
    # Choose most recent point for highlight
    items = sorted(history.items(), key=lambda kv: _timestamp_from_value(kv[1]), reverse=True)
    latest_key = items[0][0] if items else None

    for (lat, lon), value in items:
        strength = _strength_from_value(value)
        if latest_key == (lat, lon):
            color = "ffff00"  # Hex for yellow (last point)
        else:
            color = get_hex_color(strength)
        marker = f"pin-l+{color}({lon},{lat})"
        features.append(marker)

    # Predict transmitter location and add a green marker
    pred_marker = None
    try:
        predicted_location = predictTransmitterLocation(history, logger)
        pred_lat, pred_lon = predicted_location
        pred_marker = f"pin-l+00ff00({pred_lon},{pred_lat})"  # Hex for green
    except ValueError as e:
        logger.warning(f"Cannot predict transmitter location: {e}")

    # Generate the URL for the map
    def _make_url(point_markers):
        all_features = list(point_markers)
        if pred_marker:
            all_features.append(pred_marker)
        features_str = ",".join(quote(f) for f in all_features)
        return features_str, f"{base_url}/{features_str}/auto/800x500?access_token={access_token}"

    if max_url_len:
        try:
            max_url_len = int(max_url_len)
        except Exception:
            max_url_len = None

    if max_url_len and features:
        trimmed = list(features)
        while trimmed and len(_make_url(trimmed)[1]) > max_url_len:
            if len(trimmed) <= 1:
                break
            trimmed = trimmed[: max(1, int(len(trimmed) * 0.8))]
        features = trimmed

    features_str, url = _make_url(features)
    if max_url_len and pred_marker and len(url) > max_url_len:
        pred_marker = None
        features_str, url = _make_url(features)

    logger.debug("Map request URL: %s", url)

    # Request the map image
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            image = Image.open(BytesIO(response.content))
            image.save(output_file)
            logger.info("Map saved to %s", output_file)
        else:
            logger.warning("Failed to retrieve map: %s, %s", response.status_code, response.text)
            _render_offline_map(history, output_file=output_file)
    except requests.RequestException as e:
        logger.warning("Failed to retrieve map: %s", e)
        _render_offline_map(history, output_file=output_file)

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
            # Basic NMEA sentence check
            if line.startswith("$") and (",GGA" in line or ",RMC" in line):
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

    candidates = keyword_matches if keyword_matches else [p.device for p in ports]

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

    # If only one obvious GPS-like device exists, assume it.
    if keyword_matches and len(keyword_matches) == 1:
        return keyword_matches[0]

    if len(candidates) == 1:
        return candidates[0]

    # If there's exactly one non-default USB serial port, assume it's the GPS.
    # This helps u-blox devices that are quiet until they have a fix.
    non_default = [p.device for p in ports if (p.device or "").upper() not in ("COM1", "COM2")]
    usb_serial = [p.device for p in ports if "usb serial" in (p.description or "").lower()]
    if len(non_default) == 1:
        return non_default[0]
    if len(usb_serial) == 1:
        return usb_serial[0]

    # Include a compact port listing to help field operators.
    port_list = ", ".join(
        f"{p.device} ({(p.description or '').strip()})"
        for p in ports
    )
    raise Exception(
        "Unable to identify GPS COM port. "
        "Set GPS_PORT to the correct COM port (e.g., COM5) and retry. "
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

def list_sdr_devices():
    """
    Return list of SDR devices with index, name, serial (best-effort).
    """
    devices = []
    try:
        count = int(rtlsdr.librtlsdr.rtlsdr_get_device_count())
    except Exception:
        return devices
    serials = []
    try:
        serials = rtlsdr.RtlSdr.get_device_serial_addresses()
    except Exception:
        serials = []
    for i in range(count):
        name = None
        try:
            raw = rtlsdr.librtlsdr.rtlsdr_get_device_name(i)
            if raw:
                name = raw.decode("utf-8", errors="replace")
        except Exception:
            name = None
        serial = serials[i] if i < len(serials) else None
        devices.append({"index": i, "name": name, "serial": serial})
    return devices

def readGPS(logger, serial_port=None, nmea_reader=None, stop_event=None, max_wait_s=10):
    """
    Read latitude and longitude from a USB GPS receiver using PyNMEA.

    Returns:
        tuple: (LAT, LON, num_sats, satellites) when a valid fix is received, or None on timeout/stop.
    """
    created_port = False
    satellites_by_prn = {}
    last_num_sats = None
    try:
        if serial_port is None or nmea_reader is None:
            serial_port, nmea_reader = openGPS()
            created_port = True
        start_time = time.time()
        while True:
            if stop_event is not None and stop_event.is_set():
                return None
            data = serial_port.readline().decode('ascii', errors='replace')
            for msg in nmea_reader.next(data):
                if msg.sentence_type == 'GSV':
                    # Satellites in view; gather per-satellite info when available
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
                    logger.info(f"Number of satellites: {msg.num_sats}")
                    last_num_sats = msg.num_sats
                    if msg.latitude != 0.0 and msg.longitude != 0.0:
                        latitude = msg.latitude
                        longitude = msg.longitude
                        satellites = list(satellites_by_prn.values())
                        return (latitude, longitude, msg.num_sats, satellites)
                    else:
                        logger.debug("Waiting for valid coordinates...")
                time.sleep(0.1)
            # If we timed out but have satellite data, return it with no fix.
            if max_wait_s is not None and (time.time() - start_time) > max_wait_s:
                if satellites_by_prn or last_num_sats is not None:
                    satellites = list(satellites_by_prn.values())
                    return (None, None, last_num_sats, satellites)
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
