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

try:
    import rtlsdr
    _rtlsdr_import_error = None
except Exception as exc:  # pragma: no cover - handled at runtime
    rtlsdr = None
    _rtlsdr_import_error = exc


def _require_rtlsdr():
    if rtlsdr is None:
        raise ImportError(
            "RTL-SDR support is unavailable (failed to import 'rtlsdr'). "
            "Ensure librtlsdr.dll is present or install pyrtlsdr/pyrtlsdrlib."
        ) from _rtlsdr_import_error

def selectRadio(index=0):
    """
    Select the first available SDR device.
    Returns:
        radio (pyrtlsdr.RtlSdr): An instance of the SDR device.
    """
    try:
        _require_rtlsdr()
        radio = rtlsdr.RtlSdr(index)
        return radio
    except Exception as e:
        raise Exception(f"Error initializing SDR: {e}")

def readRadio(radio, seconds, frequency, gain=30, close_radio=False, configure=True, max_samples=524288):
    """
    Read samples from the SDR device.
    
    Args:
        index (int): Index of the SDR device.
        seconds (int): Duration in seconds for which samples are needed.
        frequency (float): Frequency in MHz to tune the SDR.
        
    Returns:
        numpy_array: Samples from the SDR device.
    """
    if configure:
        radio.center_freq = frequency * 1e6  # Convert MHz to Hz
        radio.sample_rate = 2.048e6          # Sample rate (default)
        radio.gain = gain
    
    try:
        sample_count = max(1, int(float(seconds) * int(radio.sample_rate)))
        if max_samples is not None:
            sample_count = min(sample_count, max(1, int(max_samples)))
        samples = radio.read_samples(sample_count)
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
    rms = float(np.sqrt(np.mean(np.square(processed_samples))))
    noise_rms = max(1e-12, std_val)
    snr = float(20.0 * np.log10(max(rms, 1e-12) / noise_rms))
    quality = float(np.clip((snr + 5.0) / 30.0, 0.0, 1.0))
    power_dbfs = float(20.0 * np.log10(max(rms, 1e-12)))
    return {
        "mean": mean_val,
        "std": std_val,
        "snr": snr,
        "quality": quality,
        "power_dbfs": power_dbfs,
    }


def calculateSpectrum(samples, bins=128, fft_size=4096):
    """Return a compact, display-ready FFT row without retaining raw captures."""
    if samples is None or len(samples) == 0:
        return []
    size = min(len(samples), max(64, int(fft_size)))
    segment = np.asarray(samples[:size], dtype=np.complex128)
    window = np.hanning(size)
    spectrum = np.fft.fftshift(np.fft.fft(segment * window))
    magnitude = 20.0 * np.log10(np.maximum(np.abs(spectrum), 1e-12))
    bins = max(16, int(bins))
    chunks = np.array_split(magnitude, bins)
    return [round(float(np.max(chunk)), 2) for chunk in chunks if len(chunk)]

def list_sdr_devices():
    """
    Return list of SDR devices with index, name, serial (best-effort).
    """
    devices = []
    if rtlsdr is None:
        return devices
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
