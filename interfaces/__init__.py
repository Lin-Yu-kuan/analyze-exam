"""Public contracts shared by the microkernel and its plugins."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, TypeVar

Cleanup = Callable[[], None]
EventHandler = Callable[[object], None]
Input = TypeVar("Input", contravariant=True)
Output = TypeVar("Output", covariant=True)


class PluginContext(Protocol):
    """Owner-scoped services; event payloads must be treated as read-only."""

    def subscribe(self, topic: str, handler: EventHandler) -> Cleanup: ...

    def publish(self, topic: str, payload: object) -> list[Exception]: ...

    def add_cleanup(self, callback: Cleanup) -> None: ...


class Plugin(Protocol):
    """IDs and names remain stable throughout registration and loading."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    def init(self, context: PluginContext) -> None: ...

    def destroy(self) -> None:
        """Release resources, including after a partially failed init."""
        ...


class ExecutablePlugin(Plugin, Protocol[Input, Output]):
    def execute(self, value: Input) -> Output: ...


@dataclass(frozen=True)
class PipelineRequest:
    """The composition root supplies loaded, type-compatible steps."""

    steps: tuple[ExecutablePlugin[Any, Any], ...]
    initial_input: Any
