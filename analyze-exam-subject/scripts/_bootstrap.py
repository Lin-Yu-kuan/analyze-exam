"""Composition root: explicit factories and pipeline wiring, no domain algorithms."""

from __future__ import annotations

from importlib import import_module
from typing import Any

from core import EventBus, PluginManager
from interfaces import PipelineRequest
from interfaces.commands import CommandRequest, StageResult

# The only place allowed to resolve concrete plugin implementations.
# Dependency names double as constructor parameter names.
FACTORIES = {
    "cli": ("plugins.cli", "CliPlugin", {}),
    "pipeline": ("plugins.pipeline", "PipelinePlugin", {}),
    "workspace": ("plugins.workspace", "WorkspacePlugin", {}),
    "manifest": ("plugins.manifest_sqlite", "ManifestPlugin", {}),
    "inventory": ("plugins.inventory", "InventoryPlugin", {"storage": "workspace"}),
    "extract": ("plugins.extract", "ExtractPlugin", {"storage": "workspace"}),
    "render": ("plugins.render", "RenderPlugin", {"storage": "workspace"}),
    "prepare": ("plugins.prepare", "PreparePlugin", {"storage": "workspace"}),
    "packet": ("plugins.packet", "PacketPlugin", {"storage": "workspace"}),
    "status": ("plugins.status", "StatusPlugin", {"storage": "workspace"}),
    "merge": ("plugins.merge", "MergePlugin", {"storage": "workspace"}),
    "summary": ("plugins.summary", "SummaryPlugin", {"storage": "workspace"}),
    "report-html": ("plugins.report_html", "ReportHtmlPlugin", {"storage": "workspace", "statistics": "summary"}),
    "check": ("plugins.report_check", "ReportCheckPlugin", {"storage": "workspace"}),
    "clean-images": ("plugins.clean_images", "CleanImagesPlugin", {"storage": "workspace"}),
}
PIPELINES = {name: (name,) for name in FACTORIES if name not in {"cli", "pipeline", "manifest", "workspace"}}
PIPELINES.update({"run-dir": ("workspace",), "scan": ("workspace", "inventory"),
                  "finalize": ("merge", "summary"), "revise": ("workspace", "report-html", "check")})


class ExecutionCleanupError(RuntimeError):
    def __init__(self, primary: Exception, cleanup: Exception) -> None:
        self.primary = primary
        self.cleanup = cleanup
        super().__init__(f"{primary}\n另外，插件清理失敗：{cleanup}")


class Application:
    """One explicitly scoped invocation, including dependencies and cleanup."""

    def __init__(self, disabled: frozenset[str] = frozenset()) -> None:
        self.manager = PluginManager(EventBus())
        self.instances: dict[str, Any] = {}
        self.disabled = disabled

    def _load(self, identifiers: tuple[str, ...], request: CommandRequest | None = None) -> None:
        order: list[str] = []
        dependencies: dict[str, dict[str, str]] = {}

        def visit(identifier: str) -> None:
            if identifier in self.instances or identifier in order:
                return
            if identifier in self.disabled or identifier not in FACTORIES:
                raise ValueError(f"必要插件不可用：{identifier}")
            deps = dict(FACTORIES[identifier][2])
            if identifier == "inventory" and request is not None and request.manifest:
                deps["manifest"] = "manifest"
            dependencies[identifier] = deps
            for dependency in deps.values():
                visit(dependency)
            order.append(identifier)

        for identifier in identifiers:
            visit(identifier)
        # Resolve all required implementations before initializing anything new.
        classes = {identifier: getattr(import_module(FACTORIES[identifier][0]), FACTORIES[identifier][1]) for identifier in order}
        for identifier in order:
            plugin = classes[identifier](**{key: self.instances[value] for key, value in dependencies[identifier].items()})
            self.manager.register(plugin)
            self.manager.load(plugin.id)
            self.instances[identifier] = plugin

    def execute(self, request: CommandRequest) -> StageResult:
        steps = PIPELINES[request.command]
        self._load(("pipeline", *steps), request)
        for plugin in self.instances.values():
            preflight = getattr(plugin, "preflight", None)
            if preflight is not None:
                preflight(request)
        return self.instances["pipeline"].execute(PipelineRequest(tuple(self.instances[name] for name in steps), request))

    def close(self) -> None:
        try:
            self.manager.shutdown()
        finally:
            self.instances.clear()

    def __enter__(self) -> Application:
        return self

    def __exit__(self, error_type, primary, traceback) -> bool:
        try:
            self.close()
        except Exception as cleanup:
            if isinstance(primary, Exception):
                raise ExecutionCleanupError(primary, cleanup) from primary
            if primary is None:
                raise
            # Preserve process-control exceptions, while retaining cleanup evidence.
            primary.cleanup_error = cleanup
        return False


def main(argv: list[str]) -> int:
    cli = None
    request = None
    try:
        with Application() as app:
            app._load(("cli",))
            cli = app.instances["cli"]
            request = cli.execute(argv)
            result = app.execute(request)
            cli.present(result)
        return 0
    except Exception as error:
        if cli is not None:
            cli.report_error(error)
        else:
            import sys
            print(f"無法啟動：{error}", file=sys.stderr)
        return 1 if request is not None and request.command == "check" else 2
