"""Add-on discovery, loading, and menu wiring."""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Dict, List, Optional, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets

from .plugin_api import AddonAction, AddonPlugin, PinpointAPI


class AddonManager(QtCore.QObject):
    def __init__(
        self,
        api: PinpointAPI,
        addons_dir: str,
        menu: QtWidgets.QMenu,
        logger,
        parent=None,
    ):
        super().__init__(parent)
        self.api = api
        self.addons_dir = addons_dir
        self.menu = menu
        self.logger = logger
        self._plugins: Dict[str, AddonPlugin] = {}
        self._module_names: Dict[str, str] = {}
        self._action_bindings: List[Tuple[QtGui.QAction, AddonAction]] = []
        self._watch_timer = QtCore.QTimer(self)
        self._watch_timer.timeout.connect(self._maybe_reload)
        self._last_signature: Optional[Tuple[Tuple[str, float, int], ...]] = None

    def start_watch(self, interval_ms: int = 2000) -> None:
        self._watch_timer.setInterval(max(500, int(interval_ms)))
        if not self._watch_timer.isActive():
            self._watch_timer.start()

    def stop_watch(self) -> None:
        if self._watch_timer.isActive():
            self._watch_timer.stop()

    def shutdown(self) -> None:
        self.stop_watch()
        self._unload_all()

    def reload(self) -> None:
        self.load_all()

    def load_all(self) -> None:
        self._unload_all()
        if self.menu:
            self.menu.clear()

        plugin_paths = self._discover_plugin_files()
        errors: List[str] = []

        for path in plugin_paths:
            try:
                plugin = self._load_plugin(path)
                if plugin:
                    if plugin.id in self._plugins:
                        self.logger.warning("Duplicate add-on id '%s' ignored.", plugin.id)
                        continue
                    self._plugins[plugin.id] = plugin
                    self._build_plugin_menu(plugin)
            except Exception as exc:  # pragma: no cover
                self.logger.exception("Failed to load add-on: %s", path)
                errors.append(str(exc))

        if not self._plugins:
            placeholder = QtGui.QAction("No add-ons found", self.menu)
            placeholder.setEnabled(False)
            self.menu.addAction(placeholder)

        if errors:
            err_action = QtGui.QAction("Add-on load errors (see log)", self.menu)
            err_action.setEnabled(False)
            self.menu.addAction(err_action)

        if self.menu:
            self.menu.addSeparator()
            reload_action = QtGui.QAction("Reload Add-ons", self.menu)
            reload_action.triggered.connect(self.reload)
            self.menu.addAction(reload_action)

        self.refresh_enabled_states()
        self._last_signature = self._signature()

    def refresh_enabled_states(self) -> None:
        for action, definition in list(self._action_bindings):
            if definition.enabled:
                try:
                    action.setEnabled(bool(definition.enabled(self.api)))
                except Exception:
                    self.logger.debug("Failed to update enabled state for %s", definition.id, exc_info=True)
            if definition.checkable and definition.checked:
                try:
                    action.setChecked(bool(definition.checked(self.api)))
                except Exception:
                    self.logger.debug("Failed to update checked state for %s", definition.id, exc_info=True)

    def _maybe_reload(self) -> None:
        sig = self._signature()
        if self._last_signature is None:
            self._last_signature = sig
            return
        if sig != self._last_signature:
            self.logger.info("Add-on directory changed; reloading add-ons.")
            self.load_all()

    def _signature(self) -> Tuple[Tuple[str, float, int], ...]:
        entries: List[Tuple[str, float, int]] = []
        for path in self._discover_plugin_files():
            try:
                stat = os.stat(path)
            except OSError:
                continue
            entries.append((path, stat.st_mtime, stat.st_size))
        return tuple(sorted(entries))

    def _discover_plugin_files(self) -> List[str]:
        files: List[str] = []
        if not self.addons_dir or not os.path.isdir(self.addons_dir):
            return files
        for entry in os.scandir(self.addons_dir):
            if entry.name.startswith((".", "_")):
                continue
            if entry.is_file() and entry.name.endswith(".py"):
                if entry.name == "__init__.py":
                    continue
                files.append(entry.path)
            elif entry.is_dir():
                plugin_path = os.path.join(entry.path, "plugin.py")
                if os.path.isfile(plugin_path):
                    files.append(plugin_path)
                    continue
                init_path = os.path.join(entry.path, "__init__.py")
                if os.path.isfile(init_path):
                    files.append(init_path)
        return files

    def _load_plugin(self, path: str) -> Optional[AddonPlugin]:
        module_name = self._module_name_for_path(path)
        module = self._load_module(module_name, path)
        entry = getattr(module, "plugin_entry", None)
        if entry is None or not callable(entry):
            self.logger.warning("Add-on %s has no plugin_entry(api) function.", path)
            return None

        plugin = entry(self.api)
        if not isinstance(plugin, AddonPlugin):
            self.logger.warning("Add-on %s returned invalid plugin object.", path)
            return None

        if not plugin.id:
            plugin.id = os.path.splitext(os.path.basename(path))[0]

        if plugin.on_load:
            try:
                plugin.on_load(self.api)
            except Exception:
                self.logger.exception("Add-on on_load failed: %s", plugin.id)

        self._module_names[plugin.id] = module_name
        return plugin

    def _load_module(self, module_name: str, path: str):
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to load add-on module: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _module_name_for_path(self, path: str) -> str:
        base = os.path.splitext(os.path.basename(path))[0]
        token = abs(hash(path)) % 1_000_000
        return f"pinpoint_addons.{base}_{token}"

    def _build_plugin_menu(self, plugin: AddonPlugin) -> None:
        submenu = self.menu.addMenu(plugin.name)
        if plugin.description:
            submenu.setToolTip(plugin.description)
            submenu.setToolTipsVisible(True)

        for action in plugin.menu:
            self._add_action(submenu, action)

    def _add_action(self, menu: QtWidgets.QMenu, definition: AddonAction) -> None:
        if definition.separator:
            menu.addSeparator()
            return
        if definition.children:
            label = definition.label or definition.id
            submenu = menu.addMenu(label)
            for child in definition.children:
                self._add_action(submenu, child)
            return

        label = definition.label or definition.id
        action = QtGui.QAction(label, menu)
        if definition.tooltip:
            action.setToolTip(definition.tooltip)
        if definition.checkable:
            action.setCheckable(True)
        if definition.handler:
            action.triggered.connect(
                lambda _checked=False, h=definition.handler, aid=definition.id: self._safe_invoke(h, aid)
            )
        else:
            action.setEnabled(False)
        menu.addAction(action)
        self._action_bindings.append((action, definition))

    def _unload_all(self) -> None:
        for plugin in list(self._plugins.values()):
            if plugin.on_unload:
                try:
                    plugin.on_unload(self.api)
                except Exception:
                    self.logger.exception("Add-on on_unload failed: %s", plugin.id)
        self._plugins.clear()

        for module_name in list(self._module_names.values()):
            sys.modules.pop(module_name, None)
        self._module_names.clear()
        self._action_bindings.clear()

    def _safe_invoke(self, handler, action_id: str) -> None:
        try:
            handler(self.api)
        except Exception:
            self.logger.exception("Add-on action failed: %s", action_id)
