"""Local lifecycle bookkeeping and immutable stage-envelope helpers."""

from __future__ import annotations

from interfaces import PluginContext
from interfaces.commands import CommandRequest, StageInput, StageResult


class Lifecycle:
    id: str
    name: str

    def __init__(self) -> None:
        self._context: PluginContext | None = None

    def init(self, context: PluginContext) -> None:
        if self._context is not None:
            raise RuntimeError(f"Plugin is already initialized: {self.id}")
        self._context = context

    def destroy(self) -> None:
        self._context = None

    def require_active(self) -> None:
        if self._context is None:
            raise RuntimeError(f"Plugin is not loaded: {self.id}")


class StagePlugin(Lifecycle):
    def preflight(self, request: CommandRequest) -> None:
        """Check requirements without producing business outputs."""

    def execute(self, value: StageInput) -> StageResult:
        self.require_active()
        return self.run(value)

    def run(self, value: StageInput) -> StageResult:
        raise NotImplementedError


def request_of(value: StageInput) -> CommandRequest:
    return value.request if isinstance(value, StageResult) else value


def result_of(value: StageInput, *, request: CommandRequest | None = None,
              artifacts: dict | None = None, details: dict | None = None) -> StageResult:
    previous = value.artifacts if isinstance(value, StageResult) else {}
    return StageResult(request or request_of(value), {**previous, **(artifacts or {})}, details or {})
