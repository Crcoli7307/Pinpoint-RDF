# Pinpoint Direction Finding v8

Pinpoint is a PyQt6-based direction finding application with a plugin-first architecture. Core UI and data collection live in `pinpoint/`, utilities are in `funcs/`, and add-ons are loaded dynamically from `addons/` at runtime. The app can run with zero add-ons present, and add-ons can be hot-swapped without restarting the app.

**Quick Start**
1. Install dependencies.
2. Run the app.
3. Use the Add-ons menu to open diagnostics or other tools.

```powershell
pip install -r requirements.txt
python main.py
```

**Project Layout**
- `main.py` Launches the app.
- `pinpoint/app.py` Application bootstrap and startup flow.
- `pinpoint/core.py` Core configuration, utilities, and shared state.
- `pinpoint/ui_components.py` UI dialogs, widgets, and worker threads.
- `pinpoint/main_window.py` Main application window and UI logic.
- `pinpoint/plugin_api.py` Internal add-on API and event bus.
- `pinpoint/plugin_manager.py` Add-on discovery, loading, and menu wiring.
- `pinpoint/version.py` Central version metadata.
- `funcs/` SDR, GPS, and map utilities.
- `addons/` Add-on plugins loaded dynamically at runtime.
- `compile_command.txt` Nuitka build command for Windows.

**Getting Started Guide**
1. Install Python 3.10+ on Windows.
2. Create a virtual environment.
3. Install requirements.
4. Launch the app.
5. Use the startup wizard to select GPS, playback, or Meshtastic-only mode.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

**Running Without Hardware**
Pinpoint will start even if no SDR or GPS hardware is connected. The startup dialog will offer:
1. Playback-only mode.
2. Meshtastic-only mode.
3. Rescan hardware.
4. Exit.

If automatic GPS selection fails or points at the wrong serial device, the GPS configuration screen allows another COM port to be selected. Enable **Remember COM Port** to store that receiver as the default for future launches; leave it disabled to use the selection only for the current run. The same option is available from **Settings → Change GPS Port** while the application is running.

**Field Data and Settings**

Mutable application data is stored outside the installation directory. On Windows, Pinpoint uses `%LOCALAPPDATA%\Pinpoint` for `settings.json`, `main.log`, `map.png`, and calibration profiles. Settings persist between launches, and the application no longer requires administrator privileges solely to write runtime files.

The collection cycle controls how often a telemetry cycle begins. The SDR sample window is a separate bounded capture duration (maximum two seconds), preventing accidental multi-gigabyte sample allocations. Configured SDRs are captured concurrently where the hardware permits.

**Movement Pausing and GPS Accuracy**

The map movement threshold is specified in meters. With adaptive movement enabled, Pinpoint increases the effective threshold using the receiver's HDOP-derived accuracy estimate and the configured multiplier. Paused cycles remain in recordings and reports but do not add map points or request a new static map.

The map occupies the right side of the main workspace. The left side remains blank until a current or historical fix is selected. Clicking an interactive marker—or a marker in the static-map fallback—opens that cycle's full telemetry snapshot, including GPS data, signal/SNR, per-antenna health and measurements, movement gating, acquisition timing, bearing/fusion results, and the configured and effective calculation parameters. New recordings preserve those calculation parameters with every cycle.

Playback rebuilds the map from the GPS fixes stored in the recording as the timeline advances or is scrubbed. It uses the interactive map when Mapbox and Qt WebEngine are available, and otherwise renders a local static map; embedded maps remain a fallback for older recordings without usable GPS telemetry.

New playback files store the visible, post-debounce field alerts with every telemetry frame. Replay displays those recorded error, warning, informational, and debug banners at the same points in the timeline, including on locally rendered static maps. Older playback files remain compatible but have no historical alert snapshots. The Live SDR Waterfall is disabled during playback because recordings do not contain the original SDR sample stream.

The Report Generator produces a formal mission record with an executive overview, operator purpose and remarks, configuration disclosure, automated mission narrative, sensor/data-quality findings, signal and antenna statistics, recorded field-alert summary, mission map, and cycle-by-cycle appendix. The appendix is one continuous table: headers repeat automatically and complete cycle rows move together across PDF page boundaries. Reports prefer the visible interactive map and can reconstruct a mission plot from accepted recorded fixes when no usable image was embedded.

**Alerts**

System alerts debounce transient readings and automatically disappear when their underlying condition resolves. Error, warning, informational, and debug notices render red, yellow, green, and blue respectively.

**Versioning**
All version metadata lives in `pinpoint/version.py`.
1. `APP_VERSION` is the canonical display version.
2. `APP_VERSION_NAME` can be used for packaging tags or metadata.
3. The UI reads version info from `pinpoint/version.py` via `pinpoint/core.py`.

**Build (Windows / Nuitka)**
The build command lives in `compile_command.txt`. Run it from the repo root:

```powershell
Get-Content compile_command.txt | ForEach-Object { $_ }
```

If you modify the build command, ensure:
1. `--include-data-dir=addons=addons` is present to bundle add-ons.
2. `--include-package=pinpoint` and `--include-package=funcs` are included.

**Developer Notes**
1. Main state is kept in `pinpoint/main_window.py`. Telemetry updates enter via `_on_telemetry`.
2. GPS and SDR I/O are in `funcs/`. Keep it lightweight and dependency-minimal.
3. Long-running operations should use threads or subprocesses. Avoid blocking the UI thread.
4. The startup wizard and calibration run in worker threads to keep the UI responsive.
5. The add-on system supports hot reload by scanning the `addons/` directory.

**Internal API (PinpointAPI)**
All core API calls use dict-in/dict-out payloads.

Examples:
```python
api.call("core.get_version", {})
api.call("core.get_settings", {})
api.call("data.get_history_points", {})
api.call("ui.show_message", {"title": "Hello", "message": "World", "level": "info"})
```

**Event Bus**
Add-ons can subscribe to events for telemetry and lifecycle signals.
1. `telemetry` Emits each telemetry payload.
2. `collection.started` Emitted when recording starts.
3. `collection.stopped` Emitted when recording stops.
4. `collection.error` Emitted on collector errors.
5. `playback.started` Emitted when playback starts.
6. `playback.stopped` Emitted when playback ends.
7. `app.ready` Emitted after the add-on system is initialized.

**Add-on System**
Add-ons are loaded from `addons/` at runtime.
1. File-based add-ons can be a single `.py` file with `plugin_entry(api)`.
2. Folder-based add-ons can use `plugin.py` or `__init__.py`.
3. Each add-on gets its own submenu under the Add-ons menu.
4. Each action can have its own enable predicate and callback handler.

**Add-on Template**
Copy this into `addons/my_addon.py` and reload add-ons from the UI.

```python
from __future__ import annotations

from PyQt6 import QtWidgets
from pinpoint.plugin_api import AddonAction, AddonPlugin, PinpointAPI


def _open_dialog(api: PinpointAPI) -> None:
    parent = api.call("ui.get_main_window").get("window")
    QtWidgets.QMessageBox.information(parent, "My Add-on", "Hello from the add-on!")


def _enabled(api: PinpointAPI) -> bool:
    return True


def plugin_entry(api: PinpointAPI) -> AddonPlugin:
    return AddonPlugin(
        id="my_addon",
        name="My Add-on",
        version="0.1.0",
        description="Example add-on template.",
        menu=[
            AddonAction(id="open", label="Open Dialog", handler=_open_dialog, enabled=_enabled),
        ],
    )
```

**Diagnostics Add-on**
The Diagnostics add-on provides a practical troubleshooting UI.
1. Device inventory for SDRs and serial/GPS ports.
2. Settings snapshot and report availability.
3. Log file and calibration file presence checks.
4. Live telemetry event counters.
5. A live, smoothly scrolling SDR waterfall with per-device spectrum, power, strength, and SNR.

Open it via:
1. Add-ons menu.
2. Diagnostics.
3. Open Diagnostics.

The waterfall is available from **Add-ons → Diagnostics → Live SDR Waterfall** while live collection telemetry is available.

**Troubleshooting Guide (Field Operators)**
1. App freezes on startup.
   - Wait for the startup dialog to finish the hardware scan.
   - If the UI stays unresponsive, restart and choose Playback-only mode.
2. No SDR devices detected.
   - Re-seat the SDR USB connection.
   - Use Diagnostics to verify device count.
   - Ensure RTL-SDR drivers are installed.
3. GPS not detected.
   - Verify the GPS is powered and has a clear sky view.
   - Use the GPS Setup Wizard to select the correct COM port.
   - Check for other apps locking the serial port.
4. Map not updating.
   - Verify GPS fix in the status panel.
   - Ensure the history cache has valid points.
5. Calibration errors.
   - Re-run calibration from the startup wizard.
   - Check SDR devices for intermittent disconnects.
6. Playback-only mode.
   - Use File menu to load a recording.
   - Playback does not require SDR or GPS hardware.
7. Meshtastic-only mode.
   - Ensure Meshtastic Python package is installed.
   - Connect a node and enable Meshtastic in the add-on UI.

**Recommended Field Workflow**
1. Launch Pinpoint.
2. Run Diagnostics and confirm SDR/GPS visibility.
3. Start data collection.
4. Generate a report after collection ends.

**Testing**
```powershell
pytest -q
```

**Release Checklist**
1. Update `pinpoint/version.py`.
2. Run tests.
3. Build with `compile_command.txt`.
4. Smoke test startup, diagnostics, and add-on loading.
