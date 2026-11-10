"""Synchronous notification bus, without domain-specific event knowledge."""

from __future__ import annotations

from interfaces import Cleanup, EventHandler


class EventBus:
    def __init__(self) -> None:
        self._topics: dict[str, dict[object, EventHandler]] = {}

    @staticmethod
    def _validate_topic(topic: str) -> None:
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("Event topic must be a non-empty string")

    def subscribe(self, topic: str, handler: EventHandler) -> Cleanup:
        self._validate_topic(topic)
        if not callable(handler):
            raise TypeError("Event handler must be callable")
        token = object()
        self._topics.setdefault(topic, {})[token] = handler

        def cancel() -> None:
            handlers = self._topics.get(topic)
            if handlers is not None:
                handlers.pop(token, None)
                if not handlers:
                    self._topics.pop(topic, None)

        return cancel

    def publish(self, topic: str, payload: object) -> list[Exception]:
        self._validate_topic(topic)
        errors: list[Exception] = []
        # Newly subscribed handlers wait for the next publication. Cancelled
        # handlers are skipped even if they were present in this snapshot.
        handlers = tuple(self._topics.get(topic, {}).items())
        for token, handler in handlers:
            if token not in self._topics.get(topic, {}):
                continue
            try:
                handler(payload)
            except Exception as error:
                errors.append(error)
        return errors
