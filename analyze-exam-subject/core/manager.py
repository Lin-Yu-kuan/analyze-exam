"""Registration, lifecycle state and owner-scoped resource cleanup only."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from interfaces import Cleanup, EventHandler, Plugin

from .events import EventBus


class LifecycleError(RuntimeError):
    """Retains all original failures without requiring Python 3.11 groups."""

    def __init__(self, plugin_id: str, phase: str, errors: list[Exception]) -> None:
        self.plugin_id = plugin_id
        self.phase = phase
        self.errors = tuple(errors)
        super().__init__(f"Plugin {plugin_id!r} {phase} failed: "
                         + "; ".join(str(error) for error in errors))


class ShutdownError(RuntimeError):
    def __init__(self, errors: list[LifecycleError]) -> None:
        self.errors = tuple(errors)
        super().__init__("Plugin shutdown failed: "
                         + "; ".join(str(error) for error in errors))


class _Context:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._active = True
        self._subscriptions: dict[object, Cleanup] = {}
        self._cleanups: list[Cleanup] = []

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("Plugin context has been revoked")

    def subscribe(self, topic: str, handler: EventHandler) -> Cleanup:
        self._require_active()
        token = object()
        self._subscriptions[token] = self._bus.subscribe(topic, handler)

        def cancel() -> None:
            unsubscribe = self._subscriptions.pop(token, None)
            if unsubscribe is not None:
                unsubscribe()

        return cancel

    def publish(self, topic: str, payload: object) -> list[Exception]:
        self._require_active()
        return self._bus.publish(topic, payload)

    def add_cleanup(self, callback: Cleanup) -> None:
        self._require_active()
        if not callable(callback):
            raise TypeError("Cleanup must be callable")
        self._cleanups.append(callback)

    def _revoke(self) -> None:
        self._active = False
        for cancel in self._subscriptions.values():
            cancel()
        self._subscriptions.clear()

    def _cleanup(self) -> list[Exception]:
        errors: list[Exception] = []
        while self._cleanups:
            callback = self._cleanups.pop()
            try:
                callback()
            except Exception as error:
                errors.append(error)
        return errors


@dataclass
class _Registration:
    plugin: Plugin
    context: _Context | None = None
    loaded: bool = False


class PluginManager:
    """Single-threaded lifecycle manager; execution belongs to plugins.

    The caller must finish execution before unloading. Lifecycle calls may not
    reenter the manager from init, destroy or cleanup callbacks.
    """

    def __init__(self, bus: EventBus | None = None) -> None:
        self._bus = bus if bus is not None else EventBus()
        self._plugins: dict[str, _Registration] = {}
        self._load_order: list[str] = []
        self._busy = False

    @contextmanager
    def _operation(self) -> Iterator[None]:
        if self._busy:
            raise RuntimeError("Reentrant plugin lifecycle operation")
        self._busy = True
        try:
            yield
        finally:
            self._busy = False

    def register(self, plugin: Plugin) -> None:
        with self._operation():
            for field in ("id", "name"):
                value = getattr(plugin, field, None)
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"Plugin {field} must be a non-empty string")
            for method in ("init", "destroy"):
                if not callable(getattr(plugin, method, None)):
                    raise TypeError(f"Plugin must implement {method}()")
            if plugin.id in self._plugins:
                raise ValueError(f"Duplicate plugin ID: {plugin.id}")
            if any(entry.plugin is plugin for entry in self._plugins.values()):
                raise ValueError("Plugin instance is already registered")
            self._plugins[plugin.id] = _Registration(plugin)

    def load(self, plugin_id: str) -> None:
        with self._operation():
            entry = self._plugins[plugin_id]
            if entry.loaded:
                raise RuntimeError(f"Plugin is already loaded: {plugin_id}")
            entry.context = _Context(self._bus)
            try:
                if entry.plugin.id != plugin_id:
                    raise ValueError("Plugin ID changed after registration")
                entry.plugin.init(entry.context)
            except Exception as error:
                errors = [error, *self._dispose(entry)]
                del self._plugins[plugin_id]
                raise LifecycleError(plugin_id, "init", errors) from error
            entry.loaded = True
            self._load_order.append(plugin_id)

    @staticmethod
    def _dispose(entry: _Registration) -> list[Exception]:
        errors: list[Exception] = []
        context = entry.context
        if context is not None:
            context._revoke()
            try:
                entry.plugin.destroy()
            except Exception as error:
                errors.append(error)
            finally:
                errors.extend(context._cleanup())
                entry.context = None
                entry.loaded = False
        return errors

    def _unload(self, plugin_id: str) -> None:
        entry = self._plugins.get(plugin_id)
        if entry is None:
            return
        errors = self._dispose(entry)
        del self._plugins[plugin_id]
        if plugin_id in self._load_order:
            self._load_order.remove(plugin_id)
        if errors:
            raise LifecycleError(plugin_id, "unload", errors) from errors[0]

    def unload(self, plugin_id: str) -> None:
        with self._operation():
            self._unload(plugin_id)

    def shutdown(self) -> None:
        with self._operation():
            errors: list[LifecycleError] = []
            for plugin_id in reversed(tuple(self._load_order)):
                try:
                    self._unload(plugin_id)
                except LifecycleError as error:
                    errors.append(error)
            # Registered but never initialized instances own no managed resources.
            self._plugins.clear()
            if errors:
                raise ShutdownError(errors) from errors[0]
