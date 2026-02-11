"""
PINPOINT Software Project
funcs/map.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Provides mapping helpers to predict transmitter location and render static maps.
Supports offline rendering and Mapbox-backed map generation.
---

https://nexus.crayton.dev/
"""

import logging
from io import BytesIO
from urllib.parse import quote

import numpy as np
import requests
from PIL import Image, ImageDraw

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
