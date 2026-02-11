"""
PINPOINT Software Project
addons/meshtastic_connectivity.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Implements the Meshtastic connectivity add-on with connection, handshake, and status handling.
Provides UI wiring and helpers for linking nodes and displaying messages.
---

https://nexus.crayton.dev/
"""


from __future__ import annotations

import inspect
import logging
import os
import time
from typing import Optional, Dict, Any, List

from PyQt6 import QtCore, QtWidgets

from pinpoint.plugin_api import AddonAction, AddonPlugin, PinpointAPI

import funcs

try:
    from meshtastic.serial_interface import SerialInterface
    from pubsub import pub
except Exception:  # pragma: no cover - optional dependency
    SerialInterface = None
    pub = None


HANDSHAKE_TEXT = "PINPOINT_HELLO"
ACK_TEXT = "PINPOINT_ACK"
MAX_LOG_MESSAGES = 500


def _safe_int(value, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except Exception:
        return default


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return ""
    return str(value)


def _port_label(port: Dict[str, str]) -> str:
    device = port.get("device") or ""
    desc = port.get("description") or ""
    if desc:
        return f"{device} ({desc})"
    return device


def _is_permission_error(exc: Exception) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if getattr(exc, "errno", None) == 13:
        return True
    msg = str(exc).lower()
    return "permission" in msg or "access is denied" in msg


def _format_connect_error(port: str, exc: Exception) -> str:
    if _is_permission_error(exc):
        return (
            f"Access denied opening {port}. "
            "Close other apps using this port (Meshtastic app/CLI, GPS, serial monitor) and retry."
        )
    return f"Failed to connect to {port}: {exc}"


class MeshtasticManager(QtCore.QObject):
    status_changed = QtCore.pyqtSignal(dict)
    message_received = QtCore.pyqtSignal(dict)
    link_changed = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.interface = None
        self.connected = False
        self.enabled = False
        self.peer_linked = False
        self.last_error: Optional[str] = None
        self.node_info: Dict[str, Any] = {}
        self.channels: List[Dict[str, Any]] = []
        self.port: Optional[str] = None
        self.baud: int = 115200
        self.selected_channel_index: int = 0
        self.auto_handshake = True
        self.messages: List[Dict[str, Any]] = []
        self._subscribed = False
        self._handshake_timer = QtCore.QTimer(self)
        self._handshake_timer.setInterval(10_000)
        self._handshake_timer.timeout.connect(self._send_handshake)
        self.connect_timeout_s = self._env_int("MESHTASTIC_CONNECT_TIMEOUT", 8)

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except Exception:
            return default

    def library_available(self) -> bool:
        return SerialInterface is not None

    def connect(self, port: str, baud: int) -> bool:
        self.last_error = None
        if SerialInterface is None:
            self.last_error = "Meshtastic Python library not installed. Install the 'meshtastic' package."
            self._emit_status()
            return False

        self.disconnect()
        self.port = port
        self.baud = int(baud)
        try:
            logging.getLogger(__name__).debug("Meshtastic connect requested: %s @ %s", port, self.baud)
            self.interface = self._create_interface(port, self.baud)
        except Exception as exc:  # pragma: no cover - serial errors
            self.interface = None
            self.connected = False
            self.last_error = _format_connect_error(port, exc)
            logging.getLogger(__name__).warning("Meshtastic connect failed: %s", self.last_error)
            self._emit_status()
            return False

        self.connected = True
        self.peer_linked = False
        self.enabled = False
        self._subscribe_pubsub()
        self._refresh_node_info()
        self._refresh_channels()
        self._emit_status()
        return True

    def _create_interface(self, port: str, baud: int):
        timeout_s = max(1, int(self.connect_timeout_s or 8))
        # Try a few common constructor signatures without ever passing baud positionally.
        base_candidates = [
            {"port": port, "baud": baud},
            {"devPath": port, "baud": baud},
            {"device": port, "baud": baud},
            {"serialPort": port, "baud": baud},
            {"serial_port": port, "baud": baud},
            {"port": port},
            {"devPath": port},
            {"device": port},
            {"serialPort": port},
            {"serial_port": port},
        ]
        sig = None
        var_kw = False
        params = set()
        try:
            sig = inspect.signature(SerialInterface.__init__)
        except Exception:
            sig = None
        if sig is not None:
            params = set(sig.parameters.keys())
            params.discard("self")
            var_kw = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )

        timeout_key = None
        if var_kw:
            timeout_key = "connectTimeout"
        else:
            for name in ("connectTimeout", "connect_timeout", "timeout"):
                if name in params:
                    timeout_key = name
                    break

        candidates = []
        if timeout_key:
            for cand in base_candidates:
                cand_timeout = dict(cand)
                cand_timeout[timeout_key] = timeout_s
                candidates.append(cand_timeout)
        candidates.extend(base_candidates)

        last_exc = None
        for cand in candidates:
            if sig is not None and not var_kw:
                if not set(cand.keys()).issubset(params):
                    continue
            try:
                return SerialInterface(**cand)
            except Exception as exc:
                last_exc = exc
                if _is_permission_error(exc):
                    break
                continue
        # Last resort: use the single positional device path only (avoid baud as positional debugOut).
        if last_exc is not None and _is_permission_error(last_exc):
            raise last_exc
        return SerialInterface(port)

    def disconnect(self) -> None:
        self._handshake_timer.stop()
        self.enabled = False
        self.peer_linked = False
        if self.interface is not None:
            try:
                if hasattr(self.interface, "close"):
                    self.interface.close()
                elif hasattr(self.interface, "disconnect"):
                    self.interface.disconnect()
            except Exception:
                pass
        self.interface = None
        self.connected = False
        self._emit_status()

    def enable(self) -> bool:
        if not self.connected:
            self.last_error = "No Meshtastic node connected."
            self._emit_status()
            return False
        self.enabled = True
        self.peer_linked = False
        if self.auto_handshake:
            self._send_handshake()
            if not self._handshake_timer.isActive():
                self._handshake_timer.start()
        self._emit_status()
        return True

    def disable(self) -> None:
        self.enabled = False
        self.peer_linked = False
        self._handshake_timer.stop()
        self._emit_status()

    def set_selected_channel(self, index: int) -> None:
        self.selected_channel_index = max(0, int(index))

    def set_auto_handshake(self, value: bool) -> None:
        self.auto_handshake = bool(value)
        if not self.auto_handshake:
            self._handshake_timer.stop()
        elif self.enabled and not self.peer_linked:
            self._handshake_timer.start()

    def send_text(self, text: str, channel_index: Optional[int] = None) -> bool:
        if not self.interface or not self.connected:
            self.last_error = "Meshtastic interface not connected."
            self._emit_status()
            return False
        send_fn = getattr(self.interface, "sendText", None)
        if not callable(send_fn):
            self.last_error = "Meshtastic interface does not support sendText."
            self._emit_status()
            return False
        ch = self.selected_channel_index if channel_index is None else int(channel_index)
        try:
            send_fn(text, channelIndex=ch)
        except TypeError:
            try:
                send_fn(text, channel=ch)
            except TypeError:
                send_fn(text)
        return True

    def shutdown(self) -> None:
        self.disconnect()

    # ---------- Internal ----------
    def _emit_status(self) -> None:
        self.status_changed.emit(
            {
                "connected": self.connected,
                "enabled": self.enabled,
                "peer_linked": self.peer_linked,
                "port": self.port,
                "baud": self.baud,
                "node_info": dict(self.node_info),
                "channels": list(self.channels),
                "last_error": self.last_error,
            }
        )

    def _subscribe_pubsub(self) -> None:
        if pub is None or self._subscribed:
            return
        try:
            pub.subscribe(self._on_meshtastic_receive, "meshtastic.receive")
            pub.subscribe(self._on_meshtastic_connection_lost, "meshtastic.connection.lost")
        except Exception:
            # Best effort; missing topics are ok
            pass
        self._subscribed = True

    def _on_meshtastic_connection_lost(self, interface=None) -> None:
        self.connected = False
        self.enabled = False
        self.peer_linked = False
        self._handshake_timer.stop()
        self._emit_status()

    def _on_meshtastic_receive(self, packet=None, **kwargs) -> None:
        data = packet or kwargs.get("packet") or kwargs.get("message") or kwargs
        msg = self._parse_message(data)
        if not msg:
            return
        my_id = self.node_info.get("id") or self.node_info.get("nodeId") or self.node_info.get("myId")
        from_self = False
        if msg.get("from") and my_id:
            from_self = str(msg.get("from")) == str(my_id)
        self.messages.append(msg)
        if len(self.messages) > MAX_LOG_MESSAGES:
            self.messages = self.messages[-MAX_LOG_MESSAGES:]
        self.message_received.emit(msg)
        text = _to_text(msg.get("text")).strip()
        if text.startswith(HANDSHAKE_TEXT) or text.startswith(ACK_TEXT):
            if from_self:
                return
            if not self.peer_linked:
                self.peer_linked = True
                self._handshake_timer.stop()
                self.link_changed.emit(True)
                self._emit_status()
            if text.startswith(HANDSHAKE_TEXT) and self.enabled:
                self.send_text(ACK_TEXT, msg.get("channel"))

    def _send_handshake(self) -> None:
        if not self.enabled or not self.connected:
            return
        if self.peer_linked:
            self._handshake_timer.stop()
            return
        node_id = self.node_info.get("id") or ""
        text = f"{HANDSHAKE_TEXT}|{node_id}".strip("|")
        self.send_text(text)

    def _refresh_node_info(self) -> None:
        info: Dict[str, Any] = {}
        if self.interface is not None:
            for meth in ("getMyNodeInfo", "getMyNodeNum"):
                fn = getattr(self.interface, meth, None)
                if callable(fn):
                    try:
                        value = fn()
                        if isinstance(value, dict):
                            info.update(value)
                    except Exception:
                        pass
            node = getattr(self.interface, "localNode", None)
            if node is not None:
                for key, attr in (
                    ("longName", "longName"),
                    ("shortName", "shortName"),
                    ("hwModel", "hwModel"),
                    ("id", "id"),
                    ("id", "nodeId"),
                ):
                    if key not in info:
                        try:
                            value = getattr(node, attr, None)
                        except Exception:
                            value = None
                        if value:
                            info[key] = value
        self.node_info = info

    def _refresh_channels(self) -> None:
        channels: List[Dict[str, Any]] = []
        node = getattr(self.interface, "localNode", None)
        if node is not None:
            node_channels = getattr(node, "channels", None)
            if node_channels:
                for idx, ch in enumerate(node_channels):
                    name = None
                    if isinstance(ch, dict):
                        settings = ch.get("settings") or {}
                        name = settings.get("name") or ch.get("name")
                    else:
                        for attr in ("name", "settings"):
                            try:
                                value = getattr(ch, attr, None)
                            except Exception:
                                value = None
                            if isinstance(value, dict):
                                name = value.get("name") or name
                            elif isinstance(value, str):
                                name = value or name
                    channels.append({"index": idx, "name": name or f"Channel {idx}"})

        if not channels:
            channels = [{"index": i, "name": f"Channel {i}"} for i in range(8)]
        self.channels = channels

    def _parse_message(self, packet: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(packet, dict):
            return None
        decoded = packet.get("decoded") or {}
        text = None
        channel = None
        if isinstance(decoded, dict):
            text = decoded.get("text") or decoded.get("payload")
            channel = decoded.get("channel") or decoded.get("channelIndex")
        msg = {
            "ts": time.time(),
            "text": _to_text(text),
            "from": packet.get("fromId") or packet.get("from"),
            "to": packet.get("toId") or packet.get("to"),
            "channel": _safe_int(channel, 0),
            "rssi": _safe_int(packet.get("rxRssi")),
            "snr": _safe_float(packet.get("rxSnr")),
        }
        return msg

class MeshtasticReadNodeDialog(QtWidgets.QDialog):
    def __init__(self, manager: MeshtasticManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Read Meshtastic Node")
        self.setMinimumSize(420, 220)
        self.setModal(True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._manager = manager

        prompt = QtWidgets.QLabel("Read Meshtastic Node to enable")
        prompt.setWordWrap(True)

        self.port_combo = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("Refresh Ports")
        self.refresh_btn.clicked.connect(self._refresh_ports)
        port_row = QtWidgets.QHBoxLayout()
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(self.refresh_btn)

        self.baud_combo = QtWidgets.QComboBox()
        self.baud_combo.addItems(["115200", "230400", "460800", "921600"])
        self.baud_combo.setCurrentText(str(self._manager.baud or 115200))

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #b91c1c;")

        self.read_btn = QtWidgets.QPushButton("Read Node")
        self.cancel_btn = QtWidgets.QPushButton("Cancel")
        self.read_btn.clicked.connect(self._read_node)
        self.cancel_btn.clicked.connect(self.reject)

        button_row = QtWidgets.QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(self.read_btn)
        button_row.addWidget(self.cancel_btn)

        form = QtWidgets.QFormLayout()
        form.addRow("Serial Port", port_row)
        form.addRow("Link Speed", self.baud_combo)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(prompt)
        layout.addLayout(form)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        layout.addLayout(button_row)

        self._refresh_ports()
        if not self._manager.library_available():
            self.status_label.setText("Meshtastic library not installed. Install the 'meshtastic' package.")
            self.read_btn.setEnabled(False)

    def _refresh_ports(self) -> None:
        self.port_combo.clear()
        ports = funcs.list_serial_ports()
        for port in ports:
            self.port_combo.addItem(_port_label(port), port.get("device"))
        if not ports:
            self.port_combo.addItem("No serial ports detected", None)

    def _read_node(self) -> None:
        device = self.port_combo.currentData()
        if not device:
            self.status_label.setText("No valid serial port selected.")
            return
        baud = _safe_int(self.baud_combo.currentText(), 115200) or 115200
        ok = self._manager.connect(device, baud)
        if not ok:
            self.status_label.setText(self._manager.last_error or "Failed to read node.")
            return
        self.accept()


class MeshtasticConnectivityDialog(QtWidgets.QDialog):
    def __init__(self, manager: MeshtasticManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Meshtastic Connectivity")
        self.setMinimumSize(820, 620)
        self.setModal(True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._manager = manager

        self.status_label = QtWidgets.QLabel("--")
        self.node_label = QtWidgets.QLabel("--")
        self.port_label = QtWidgets.QLabel("--")
        self.link_label = QtWidgets.QLabel("--")
        self.hw_label = QtWidgets.QLabel("--")

        status_grid = QtWidgets.QGridLayout()
        status_grid.addWidget(QtWidgets.QLabel("Status"), 0, 0)
        status_grid.addWidget(self.status_label, 0, 1)
        status_grid.addWidget(QtWidgets.QLabel("Local Node"), 1, 0)
        status_grid.addWidget(self.node_label, 1, 1)
        status_grid.addWidget(QtWidgets.QLabel("Port / Speed"), 2, 0)
        status_grid.addWidget(self.port_label, 2, 1)
        status_grid.addWidget(QtWidgets.QLabel("Hardware"), 3, 0)
        status_grid.addWidget(self.hw_label, 3, 1)
        status_grid.addWidget(QtWidgets.QLabel("Mesh Link"), 4, 0)
        status_grid.addWidget(self.link_label, 4, 1)

        status_box = QtWidgets.QGroupBox("Node Status")
        status_box.setLayout(status_grid)

        self.channel_combo = QtWidgets.QComboBox()
        self.channel_combo.currentIndexChanged.connect(self._on_channel_changed)

        self.speed_combo = QtWidgets.QComboBox()
        self.speed_combo.addItems(["115200", "230400", "460800", "921600"])
        self.speed_combo.setCurrentText(str(self._manager.baud or 115200))
        self.speed_combo.currentTextChanged.connect(self._on_speed_changed)

        self.auto_handshake_chk = QtWidgets.QCheckBox("Auto handshake")
        self.auto_handshake_chk.setChecked(bool(self._manager.auto_handshake))
        self.auto_handshake_chk.stateChanged.connect(
            lambda state: self._manager.set_auto_handshake(state == QtCore.Qt.CheckState.Checked)
        )

        settings_form = QtWidgets.QFormLayout()
        settings_form.addRow("Channel", self.channel_combo)
        settings_form.addRow("Link Speed", self.speed_combo)
        settings_form.addRow("", self.auto_handshake_chk)

        settings_box = QtWidgets.QGroupBox("Settings")
        settings_box.setLayout(settings_form)

        self.enable_btn = QtWidgets.QPushButton("Enable Meshtastic")
        self.enable_btn.clicked.connect(self._toggle_enabled)
        self.handshake_btn = QtWidgets.QPushButton("Broadcast Handshake")
        self.handshake_btn.clicked.connect(lambda: self._manager.send_text(HANDSHAKE_TEXT))
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._manager.disconnect)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.enable_btn)
        btn_row.addWidget(self.handshake_btn)
        btn_row.addWidget(self.disconnect_btn)
        btn_row.addStretch(1)

        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("Live Meshtastic log will appear here.")

        log_box = QtWidgets.QGroupBox("Activity")
        log_layout = QtWidgets.QVBoxLayout(log_box)
        log_layout.addWidget(self.log_output, 1)
        mesh_note = QtWidgets.QLabel(
            "Mesh relay nodes store and forward messages so the network can deliver data beyond line of sight."
        )
        mesh_note.setWordWrap(True)
        mesh_note.setStyleSheet("color: #6b7280;")
        log_layout.addWidget(mesh_note)

        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.clicked.connect(self.close)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(status_box)
        layout.addWidget(settings_box)
        layout.addLayout(btn_row)
        layout.addWidget(log_box, 1)
        layout.addWidget(self.close_btn, alignment=QtCore.Qt.AlignmentFlag.AlignRight)

        self._manager.status_changed.connect(self._refresh_ui)
        self._manager.message_received.connect(self._append_message)

        self._refresh_ui()
        self._append_existing_messages()

    def _append_existing_messages(self) -> None:
        for msg in self._manager.messages[-50:]:
            self._append_message(msg)

    def _refresh_ui(self, *_args) -> None:
        info = self._manager.node_info or {}
        long_name = info.get("longName") or info.get("long_name") or "--"
        short_name = info.get("shortName") or info.get("short_name") or ""
        node_id = info.get("id") or info.get("nodeId") or ""
        node_text = long_name
        if short_name:
            node_text = f"{long_name} ({short_name})"
        if node_id:
            node_text = f"{node_text} [{node_id}]"

        status_text = "Connected" if self._manager.connected else "Disconnected"
        if self._manager.enabled:
            status_text += " / Enabled"
        self.status_label.setText(status_text)

        port = self._manager.port or "--"
        baud = self._manager.baud or "--"
        self.port_label.setText(f"{port} @ {baud}")
        self.node_label.setText(node_text)
        self.hw_label.setText(str(info.get("hwModel") or info.get("hw_model") or "--"))
        self.link_label.setText("Linked" if self._manager.peer_linked else "Waiting for peer")

        if not self._manager.connected:
            self.enable_btn.setText("Read Node")
        else:
            self.enable_btn.setText("Disable Meshtastic" if self._manager.enabled else "Enable Meshtastic")
        self.handshake_btn.setEnabled(self._manager.connected)
        self.disconnect_btn.setEnabled(self._manager.connected)
        self.channel_combo.setEnabled(self._manager.connected)
        self.speed_combo.setEnabled(self._manager.connected)

        self._refresh_channels()

        if self._manager.last_error:
            self._append_log(f"Error: {self._manager.last_error}")

    def _refresh_channels(self) -> None:
        channels = self._manager.channels or []
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        for ch in channels:
            self.channel_combo.addItem(ch.get("name", f"Channel {ch.get('index', 0)}"), ch.get("index", 0))
        # Keep selection if possible
        idx = self.channel_combo.findData(self._manager.selected_channel_index)
        if idx >= 0:
            self.channel_combo.setCurrentIndex(idx)
        self.channel_combo.blockSignals(False)

    def _on_channel_changed(self, idx: int) -> None:
        data = self.channel_combo.itemData(idx)
        self._manager.set_selected_channel(_safe_int(data, 0) or 0)

    def _on_speed_changed(self, text: str) -> None:
        baud = _safe_int(text, 115200) or 115200
        self._manager.baud = baud
        if self._manager.connected:
            self._append_log("Link speed updated; re-read node to apply.")

    def _prompt_read_node(self) -> bool:
        prompt = MeshtasticReadNodeDialog(self._manager, self)
        if prompt.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._append_log("Meshtastic node connected.")
            return True
        return False

    def _toggle_enabled(self) -> None:
        if not self._manager.connected:
            if not self._prompt_read_node():
                self._append_log("No Meshtastic node connected.")
                return
        if self._manager.enabled:
            self._manager.disable()
            self._append_log("Meshtastic disabled.")
        else:
            self._manager.enable()
            self._append_log("Meshtastic enabled. Waiting for peer acknowledgment...")

    def _append_message(self, msg: Dict[str, Any]) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime(msg.get("ts", time.time())))
        src = msg.get("from") or "unknown"
        channel = msg.get("channel", 0)
        text = msg.get("text") or ""
        line = f"[{ts}] ch{channel} {src}: {text}"
        self._append_log(line)

    def _append_log(self, line: str) -> None:
        self.log_output.appendPlainText(line)

class LiveNetworkDataViewer(QtWidgets.QDialog):
    def __init__(self, manager: MeshtasticManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Live Network Data Viewer")
        self.setMinimumSize(860, 520)
        self.setModal(True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._manager = manager

        self.table = QtWidgets.QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Time", "From", "To", "Channel", "RSSI", "Text"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        self.clear_btn = QtWidgets.QPushButton("Clear")
        self.clear_btn.clicked.connect(self._clear)
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.clicked.connect(self.close)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        btn_row.addWidget(self.clear_btn)
        btn_row.addWidget(self.close_btn)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self.table, 1)
        layout.addLayout(btn_row)

        self._manager.message_received.connect(self._on_message)
        self._load_existing()

    def _load_existing(self) -> None:
        for msg in self._manager.messages[-200:]:
            self._append_message(msg)

    def _clear(self) -> None:
        self.table.setRowCount(0)

    def _on_message(self, msg: Dict[str, Any]) -> None:
        self._append_message(msg)

    def _append_message(self, msg: Dict[str, Any]) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        ts = time.strftime("%H:%M:%S", time.localtime(msg.get("ts", time.time())))
        items = [
            ts,
            str(msg.get("from") or ""),
            str(msg.get("to") or ""),
            f"ch{msg.get('channel', 0)}",
            str(msg.get("rssi") if msg.get("rssi") is not None else "--"),
            str(msg.get("text") or ""),
        ]
        for col, value in enumerate(items):
            self.table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        self.table.scrollToBottom()


def plugin_entry(api: PinpointAPI) -> AddonPlugin:
    window = api.call("ui.get_main_window").get("window")
    manager = MeshtasticManager(parent=window)

    def _refresh_actions(*_args):
        api.call("addons.refresh_actions")

    manager.status_changed.connect(_refresh_actions)
    manager.link_changed.connect(_refresh_actions)

    def _open_connectivity(_api: PinpointAPI) -> None:
        if not manager.library_available():
            api.call(
                "ui.show_message",
                {
                    "title": "Meshtastic Unavailable",
                    "message": "Install the 'meshtastic' Python package to enable connectivity.",
                    "level": "warning",
                },
            )
            return
        if not manager.connected:
            prompt = MeshtasticReadNodeDialog(manager, window)
            if prompt.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
        dlg = MeshtasticConnectivityDialog(manager, window)
        dlg.exec()

    def _open_viewer(_api: PinpointAPI) -> None:
        if not (manager.enabled and manager.peer_linked):
            api.call(
                "ui.show_message",
                {
                    "title": "Meshtastic Offline",
                    "message": "Enable Meshtastic and wait for a peer node before viewing live network data.",
                    "level": "info",
                },
            )
            return
        dlg = LiveNetworkDataViewer(manager, window)
        dlg.exec()

    def _viewer_available(_api: PinpointAPI) -> bool:
        return bool(manager.enabled and manager.peer_linked)

    def _on_unload(_api: PinpointAPI) -> None:
        manager.shutdown()

    return AddonPlugin(
        id="meshtastic_connectivity",
        name="Meshtastic",
        version="1.0.0",
        description="Meshtastic connectivity and live network viewer.",
        menu=[
            AddonAction(
                id="meshtastic_connect",
                label="Meshtastic Connectivity...",
                handler=_open_connectivity,
            ),
            AddonAction(
                id="meshtastic_viewer",
                label="Live Network Data Viewer",
                handler=_open_viewer,
                enabled=_viewer_available,
            ),
        ],
        on_unload=_on_unload,
    )
