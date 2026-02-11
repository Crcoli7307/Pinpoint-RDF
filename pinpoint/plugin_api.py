"""
PINPOINT Software Project
pinpoint/plugin_api.py
Copyright 2026 Crayton Litton. Public Domain.
MIT License
---
Defines the add-on API, including plugin metadata and menu action models.
Implements a lightweight handler registry and event bus for add-ons.
---

https://nexus.crayton.dev/
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
import threading

Payload = Dict[str, Any]
Handler = Callable[[Payload], Payload]
ActionHandler = Callable[["PinpointAPI"], None]
ActionPredicate = Callable[["PinpointAPI"], bool]


@dataclass
class AddonAction:
    """Menu action exposed by an add-on."""

    id: str
    label: str = ""
    handler: Optional[ActionHandler] = None
    enabled: Optional[ActionPredicate] = None
    checkable: bool = False
    checked: Optional[ActionPredicate] = None
    tooltip: Optional[str] = None
    separator: bool = False
    children: List["AddonAction"] = field(default_factory=list)


@dataclass
class AddonPlugin:
    """Add-on metadata and menu actions."""

    id: str
    name: str
    version: str = "0.0.0"
    description: str = ""
    menu: List[AddonAction] = field(default_factory=list)
    on_load: Optional[Callable[["PinpointAPI"], None]] = None
    on_unload: Optional[Callable[["PinpointAPI"], None]] = None


class PinpointAPI:
    """Internal API for add-ons. All handlers accept/return dict payloads."""

    def __init__(self, logger):
        self._handlers: Dict[str, Handler] = {}
        self._logger = logger
        self._context: Dict[str, Any] = {}
        self._event_handlers: Dict[str, List[tuple[int, Callable[[Payload], None]]]] = {}
        self._event_lock = threading.Lock()
        self._next_event_token = 1

    def register(self, name: str, handler: Handler) -> None:
        if not name or not callable(handler):
            raise ValueError("API handler requires a name and callable.")
        self._handlers[name] = handler

    def call(self, name: str, payload: Optional[Payload] = None) -> Payload:
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "Payload must be a dict."}
        handler = self._handlers.get(name)
        if not handler:
            return {"ok": False, "error": f"Unknown API call: {name}"}
        try:
            result = handler(payload)
            if result is None:
                result = {}
            if not isinstance(result, dict):
                result = {"result": result}
            result.setdefault("ok", True)
            return result
        except Exception as exc:
            self._logger.exception("API call failed: %s", name)
            return {"ok": False, "error": str(exc)}

    def set_context(self, **kwargs: Any) -> None:
        self._context.update(kwargs)

    def get_context(self, key: str, default: Any = None) -> Any:
        return self._context.get(key, default)

    def list_handlers(self) -> List[str]:
        return sorted(self._handlers.keys())

    def subscribe(self, event: str, handler: Callable[[Payload], None]) -> int:
        if not event or not callable(handler):
            raise ValueError("Event subscription requires a name and callable.")
        with self._event_lock:
            token = self._next_event_token
            self._next_event_token += 1
            self._event_handlers.setdefault(event, []).append((token, handler))
        return token

    def unsubscribe(self, token: int) -> bool:
        removed = False
        with self._event_lock:
            for name in list(self._event_handlers.keys()):
                handlers = self._event_handlers[name]
                new_handlers = [(t, h) for t, h in handlers if t != token]
                if len(new_handlers) != len(handlers):
                    removed = True
                    if new_handlers:
                        self._event_handlers[name] = new_handlers
                    else:
                        self._event_handlers.pop(name, None)
        return removed

    def emit(self, event: str, payload: Optional[Payload] = None) -> None:
        if not event:
            return
        if payload is None:
            payload = {}
        if not isinstance(payload, dict):
            payload = {"data": payload}
        with self._event_lock:
            handlers = list(self._event_handlers.get(event, []))
        for _token, handler in handlers:
            try:
                handler(payload)
            except Exception:
                self._logger.exception("Event handler failed: %s", event)
