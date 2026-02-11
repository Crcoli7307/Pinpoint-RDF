"""
PINPOINT Software Project
funcs/sdr.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Wraps SDR device access and signal processing helpers.
Includes sampling, strength/quality calculations, and device discovery.
---

https://nexus.crayton.dev/
"""

import numpy as np
import rtlsdr

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
