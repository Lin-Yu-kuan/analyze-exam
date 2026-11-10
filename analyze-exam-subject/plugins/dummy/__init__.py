"""Minimal pass-through plugin for lifecycle and event demonstrations."""

from __future__ import annotations

from interfaces import PluginContext


class DummyPlugin:
    name = "Dummy"

    def __init__(self, plugin_id: str = "dummy") -> None:
        self.id = plugin_id
        self.event_count = 0
        self._context: PluginContext | None = None

    def init(self, context: PluginContext) -> None:
        if self._context is not None:
            raise RuntimeError("Dummy is already initialized")
        self._context = context
        context.subscribe("demo.message", self._on_message)

    def _on_message(self, payload: object) -> None:
        self.event_count += 1
        print(f"[{self.id}] event: {payload}")

    def execute(self, value: object) -> object:
        if self._context is None:
            raise RuntimeError("Dummy is not loaded")
        print(f"[{self.id}] Hello from Dummy Plugin")
        return value

    def destroy(self) -> None:
        self._context = None
