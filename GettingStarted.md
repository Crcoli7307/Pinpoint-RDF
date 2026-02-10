# PINPOINT Getting Started (Field Operator)

## Purpose
PINPOINT Direction Finding is a field tool that combines SDR signal strength, antenna array geometry, and GPS position to estimate the bearing and likely location of a transmitter. This guide focuses on day-of-mission use, not development.

## Core Features
- Real-time signal strength capture from multiple SDR receivers.
- Automatic SDR calibration at startup to normalize antenna inputs.
- GPS-aware mapping with a predicted transmitter location.
- Fused bearing estimate that blends AoA (array) and map-based bearings.
- Live status panel with GPS fix, signal, SNR, confidence, and source.
- Session recording to `.pinplyr` and playback with speed control.
- Report Generator to export a mission PDF.
- Optional Meshtastic connectivity and live network data viewer.

## Hardware Checklist
- Windows laptop with PINPOINT installed.
- One RTL-SDR per antenna (USB connected).
- Antenna array with known spacing and orientation.
- USB GPS receiver that outputs NMEA sentences.
- Optional: Internet access for Mapbox map tiles.
- Optional: Meshtastic node for mesh connectivity.

## Startup Workflow
1. Launch PINPOINT.
2. The app checks for SDR and GPS hardware.
3. If nothing is detected you can choose Playback Only, Meshtastic Viewer Only, rescan, or exit.
4. If SDRs are present, calibration runs automatically.
5. GPS is auto-detected. If it is not found, a GPS Configuration wizard opens for manual port selection.

## Quick Start (Live Collection)
1. Connect SDRs, antennas, and GPS before launching the app.
2. Start PINPOINT and let calibration and GPS detection finish.
3. Confirm the GPS status shows a fix and satellites count in the info panel.
4. Click `Start Data Collection`.
5. Choose whether to record the session when prompted.
6. Move and orient the antenna array deliberately. Keep orientation consistent for best bearings.
7. Watch the Info Panel for `Bearing`, `Target Rel`, `Source`, and `Confidence`.
8. When done, click `Stop Data Collection`.

## What You See On Screen
Map panel shows the latest Mapbox snapshot. The newest point is yellow, older points are color-scaled by strength, and the predicted transmitter location is green.

Info panel fields:
- `Mode`: Live, Playback, or Meshtastic only.
- `GPS`: Fix status and satellites.
- `Signal` and `SNR`: Overall strength and quality.
- `Bearing`: Current heading and target bearing.
- `Target Rel`: Target bearing relative to your current heading.
- `Source`: `fused`, `aoa`, or `map`.
- `Confidence`: Combined confidence used for bearing selection.

Buttons and menus:
- `Start Data Collection` or `Stop Data Collection` toggles the live collector.
- `Clear App` resets the map image and log file.
- `Update Settings` changes frequency, gain, collection time, antenna count, spacing, and fusion weights.
- `GPS Info` shows satellite details.
- `Antenna Info` shows per-antenna status and health.
- `View Log` opens the live log window.
- `File > Open Recording...` loads a `.pinplyr` for playback.
- `Add-ons > Report Generator` exports a mission report.
- `Add-ons > Meshtastic Connectivity` manages mesh links.

## Settings That Matter In The Field
- Frequency (MHz): Must match the transmitter frequency.
- Gain: Increase for weak signals, reduce if the signal is saturating.
- Collection Time (s): Longer times smooth readings but respond slower.
- Antenna Count and Spacing: Must match the physical array.
- Fusion Weights and Confidence Threshold: Leave at defaults unless instructed.

## Recording and Playback
- When you start collection, you can record to a `.pinplyr` file.
- Use `File > Open Recording...` to load a recording.
- Playback controls appear with play/pause, scrub, and speed (1x to 32x).
- `Exit Playback` returns to live mode.

## Reports
- After stopping collection or after loading a recording, open `Add-ons > Report Generator`.
- Fill in the summary fields, select sections, and `Preview` or `Export PDF`.

## Meshtastic (Optional)
- Open `Add-ons > Meshtastic Connectivity`.
- Use `Read Node` to connect, then `Enable Meshtastic`.
- Wait for a peer handshake. Once linked, `Live Network Data Viewer` becomes available under View.

## Troubleshooting Quick Fixes
- GPS not detected: Check USB power, choose the correct COM port in the GPS Configuration wizard, and ensure the receiver is outputting NMEA.
- No SDR detected: Re-seat USB connections, verify drivers, and rescan.
- Map not updating: Mapbox requires `MAPBOX_TOKEN` in `.env` and an internet connection.
- Weak bearings: Confirm frequency, antenna count, spacing, and orientation. Move to improve signal geometry.

## Key Files and Outputs
- `main.log` contains the session log.
- `map.png` is the latest map snapshot.
- `calibration_profiles.json` stores SDR calibration data.
- `.pinplyr` files store recordings for playback and reports.
