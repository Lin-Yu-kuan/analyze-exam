"""Sequential composition is a plugin responsibility, not a core service."""

from __future__ import annotations

from typing import Any

from interfaces import PipelineRequest, PluginContext


class PipelineStepError(RuntimeError):
    def __init__(self, step_index: int, plugin_id: str, cause: Exception) -> None:
        self.step_index = step_index  # zero-based; display is one-based
        self.plugin_id = plugin_id
        self.cause = cause
        super().__init__(f"Pipeline step {step_index + 1} ({plugin_id}) failed: {cause}")


class PipelinePlugin:
    name = "Sequential Pipeline"

    def __init__(self, plugin_id: str = "pipeline") -> None:
        self.id = plugin_id
        self._context: PluginContext | None = None
        self._executing = False

    def init(self, context: PluginContext) -> None:
        if self._context is not None:
            raise RuntimeError("Pipeline is already initialized")
        self._context = context

    def execute(self, value: PipelineRequest) -> Any:
        if self._context is None:
            raise RuntimeError("Pipeline is not loaded")
        if self._executing:
            raise RuntimeError("Pipeline execution is already in progress")
        self._executing = True
        try:
            result = value.initial_input
            for index, step in enumerate(value.steps):
                try:
                    result = step.execute(result)
                except Exception as error:
                    raise PipelineStepError(index, step.id, error) from error
            return result
        finally:
            self._executing = False

    def destroy(self) -> None:
        if self._executing:
            raise RuntimeError("Finish pipeline execution before unloading")
        self._context = None
