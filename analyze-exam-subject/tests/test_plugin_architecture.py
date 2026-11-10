"""Behavioral contracts for the independent microkernel scaffold."""

from __future__ import annotations

import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import EventBus, LifecycleError, PluginManager, ShutdownError
from interfaces import PipelineRequest
from plugins.dummy import DummyPlugin
from plugins.pipeline import PipelinePlugin, PipelineStepError


def fail(error):
    raise error


class Probe:
    name = "Lifecycle probe"

    def __init__(self, plugin_id="probe", log=None):
        self.id = plugin_id
        self.log = log if log is not None else []
        self.context = None
        self.init_error = None
        self.destroy_error = None
        self.cleanup_error = None
        self.seen = []

    def init(self, context):
        self.context = context
        self.log.append("init:" + self.id)
        context.subscribe("test", self.seen.append)
        context.add_cleanup(lambda: self.log.append("cleanup:first:" + self.id))
        if self.cleanup_error is not None:
            context.add_cleanup(lambda: fail(self.cleanup_error))
        context.add_cleanup(lambda: self.log.append("cleanup:last:" + self.id))
        if self.init_error is not None:
            raise self.init_error

    def destroy(self):
        self.log.append("destroy:" + self.id)
        if self.destroy_error is not None:
            raise self.destroy_error


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.manager = PluginManager(self.bus)

    def tearDown(self):
        self.manager.shutdown()

    def load(self, plugin):
        self.manager.register(plugin)
        self.manager.load(plugin.id)
        return plugin

    def test_registration_validation_and_no_initialization(self):
        for field in ("id", "name"):
            for value in (None, "", "  ", 7):
                with self.subTest(field=field, value=value):
                    plugin = Probe()
                    setattr(plugin, field, value)
                    with self.assertRaises(ValueError):
                        self.manager.register(plugin)
        for method in ("init", "destroy"):
            plugin = Probe()
            setattr(plugin, method, None)
            with self.assertRaises(TypeError):
                self.manager.register(plugin)
        plugin = Probe()
        self.manager.register(plugin)
        self.assertEqual(plugin.log, [])
        with self.assertRaises(ValueError):
            self.manager.register(Probe())
        self.manager.unload(plugin.id)
        self.assertEqual(plugin.log, [])

    def test_duplicate_load_and_missing_load(self):
        plugin = self.load(Probe())
        with self.assertRaises(RuntimeError):
            self.manager.load(plugin.id)
        with self.assertRaises(KeyError):
            self.manager.load("missing")
        self.assertEqual(plugin.log, ["init:probe"])

    def test_init_failure_preserves_all_errors_and_removes_instance(self):
        plugin = Probe()
        plugin.init_error = ValueError("init failed")
        plugin.destroy_error = RuntimeError("destroy failed")
        plugin.cleanup_error = OSError("cleanup failed")
        self.manager.register(plugin)
        with self.assertRaises(LifecycleError) as caught:
            self.manager.load(plugin.id)
        self.assertEqual(caught.exception.errors,
                         (plugin.init_error, plugin.destroy_error, plugin.cleanup_error))
        self.assertIs(caught.exception.__cause__, plugin.init_error)
        self.assertEqual(caught.exception.phase, "init")
        self.assertEqual(plugin.log, ["init:probe", "destroy:probe",
                                     "cleanup:last:probe", "cleanup:first:probe"])
        self.bus.publish("test", "after failure")
        self.assertEqual(plugin.seen, [])
        with self.assertRaises(RuntimeError):
            plugin.context.publish("test", None)
        replacement = self.load(Probe())
        self.bus.publish("test", "replacement")
        self.assertEqual(replacement.seen, ["replacement"])

    def test_unload_revokes_before_destroy_and_is_idempotent(self):
        plugin = self.load(Probe())
        context = plugin.context
        cancel = context.subscribe("extra", plugin.seen.append)
        original_destroy = plugin.destroy

        def destroy():
            for operation in (lambda: context.publish("test", None),
                              lambda: context.subscribe("test", plugin.seen.append),
                              lambda: context.add_cleanup(lambda: None)):
                with self.assertRaises(RuntimeError):
                    operation()
            self.bus.publish("test", "during destroy")
            original_destroy()

        plugin.destroy = destroy
        self.manager.unload(plugin.id)
        self.manager.unload(plugin.id)
        cancel()
        cancel()
        self.bus.publish("extra", "after unload")
        self.assertEqual(plugin.seen, [])
        self.assertEqual(plugin.log.count("destroy:probe"), 1)

    def test_unload_failure_cleans_up_and_keeps_other_plugin_alive(self):
        bad = Probe("bad")
        bad.destroy_error = RuntimeError("destroy failed")
        bad.cleanup_error = RuntimeError("cleanup failed")
        self.load(bad)
        good = self.load(Probe("good"))
        with self.assertRaises(LifecycleError) as caught:
            self.manager.unload("bad")
        self.assertEqual(caught.exception.errors, (bad.destroy_error, bad.cleanup_error))
        self.assertEqual(bad.log[-2:], ["cleanup:last:bad", "cleanup:first:bad"])
        self.assertEqual(self.bus.publish("test", 42), [])
        self.assertEqual(bad.seen, [])
        self.assertEqual(good.seen, [42])
        self.manager.unload("bad")
        self.load(Probe("bad"))

    def test_shutdown_uses_reverse_load_order_and_collects_failures(self):
        log = []
        first, second = Probe("first", log), Probe("second", log)
        # Deliberately register and load in different orders.
        self.manager.register(second)
        self.manager.register(first)
        self.manager.register(Probe("never-loaded", log))
        first.destroy_error = ValueError("first failed")
        second.cleanup_error = ValueError("second failed")
        self.manager.load(first.id)
        self.manager.load(second.id)
        with self.assertRaises(ShutdownError) as caught:
            self.manager.shutdown()
        self.assertEqual([e.plugin_id for e in caught.exception.errors], ["second", "first"])
        self.assertEqual(log, ["init:first", "init:second", "destroy:second",
                               "cleanup:last:second", "cleanup:first:second",
                               "destroy:first", "cleanup:last:first", "cleanup:first:first"])
        self.manager.shutdown()
        self.manager.register(Probe("never-loaded"))

    def test_reentrant_lifecycle_call_fails_without_corrupting_registration(self):
        plugin = Probe()
        plugin.init = lambda context: self.manager.unload(plugin.id)
        self.manager.register(plugin)
        with self.assertRaises(LifecycleError) as caught:
            self.manager.load(plugin.id)
        self.assertIn("Reentrant", str(caught.exception.errors[0]))
        self.load(Probe())


class EventTests(unittest.TestCase):
    def test_delivery_order_errors_and_repeated_cancel(self):
        bus, seen = EventBus(), []
        failure = ValueError("subscriber failed")
        bus.subscribe("event", lambda value: seen.append(("first", value)))
        cancel = bus.subscribe("event", lambda value: fail(failure))
        bus.subscribe("event", lambda value: seen.append(("last", value)))
        payload = object()
        self.assertEqual(bus.publish("event", payload), [failure])
        self.assertEqual(seen, [("first", payload), ("last", payload)])
        cancel()
        cancel()
        self.assertEqual(bus.publish("event", payload), [])
        self.assertEqual(bus.publish("unknown", payload), [])

    def test_subscription_changes_during_delivery(self):
        bus, seen = EventBus(), []

        def first(value):
            seen.append("first")
            cancel_second()
            cancel_first()
            bus.subscribe("event", lambda value: seen.append("new"))

        cancel_first = bus.subscribe("event", first)
        cancel_second = bus.subscribe("event", lambda value: seen.append("second"))
        bus.publish("event", None)
        self.assertEqual(seen, ["first"])
        bus.publish("event", None)
        self.assertEqual(seen, ["first", "new"])

    def test_validation_and_independent_buses(self):
        first, second = EventBus(), EventBus()
        seen = []
        first.subscribe("topic", seen.append)
        second.publish("topic", 1)
        self.assertEqual(seen, [])
        for topic in ("", "  ", None):
            with self.assertRaises(ValueError):
                first.publish(topic, None)
            with self.assertRaises(ValueError):
                first.subscribe(topic, seen.append)
        with self.assertRaises(TypeError):
            first.subscribe("topic", None)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.manager = PluginManager()
        self.runner = PipelinePlugin()
        self.manager.register(self.runner)
        self.manager.load(self.runner.id)

    def tearDown(self):
        self.manager.shutdown()

    def step(self, plugin_id, execute):
        plugin = Probe(plugin_id)
        plugin.execute = execute
        self.manager.register(plugin)
        self.manager.load(plugin.id)
        return plugin

    def test_order_and_result_passing(self):
        seen = []

        def increment(value):
            seen.append(("increment", value))
            return value + 1

        def double(value):
            seen.append(("double", value))
            return value * 2

        steps = (self.step("increment", increment), self.step("double", double))
        self.assertEqual(self.runner.execute(PipelineRequest(steps, 3)), 8)
        self.assertEqual(seen, [("increment", 3), ("double", 4)])

    def test_empty_pipeline_and_unloaded_runner(self):
        value = object()
        self.assertIs(self.runner.execute(PipelineRequest((), value)), value)
        self.manager.unload(self.runner.id)
        with self.assertRaises(RuntimeError):
            self.runner.execute(PipelineRequest((), value))

    def test_step_failure_stops_pipeline_preserves_cause_and_allows_next_run(self):
        seen, failure = [], ValueError("step failed")
        steps = (self.step("ok", lambda value: value + 1),
                 self.step("bad", lambda value: fail(failure)),
                 self.step("later", seen.append))
        with self.assertRaises(PipelineStepError) as caught:
            self.runner.execute(PipelineRequest(steps, 0))
        self.assertEqual(caught.exception.step_index, 1)
        self.assertEqual(caught.exception.plugin_id, "bad")
        self.assertIs(caught.exception.cause, failure)
        self.assertIs(caught.exception.__cause__, failure)
        self.assertEqual(seen, [])
        self.assertEqual(self.runner.execute(PipelineRequest((), 5)), 5)

    def test_reentrant_execution_is_rejected(self):
        step = self.step("recursive", lambda value: self.runner.execute(PipelineRequest((), value)))
        with self.assertRaises(PipelineStepError) as caught:
            self.runner.execute(PipelineRequest((step,), 1))
        self.assertIn("already in progress", str(caught.exception.cause))


class IntegrationTests(unittest.TestCase):
    def test_dummy_unload_reload_and_no_subscription_accumulation(self):
        bus, output = EventBus(), io.StringIO()
        manager = PluginManager(bus)
        old, other, fresh = DummyPlugin("a"), DummyPlugin("b"), DummyPlugin("a")
        with redirect_stdout(output):
            try:
                for plugin in (old, other):
                    manager.register(plugin)
                    manager.load(plugin.id)
                payload = object()
                self.assertIs(old.execute(payload), payload)
                bus.publish("demo.message", "before")
                manager.unload("a")
                with self.assertRaises(RuntimeError):
                    old.execute(payload)
                manager.register(fresh)
                manager.load(fresh.id)
                bus.publish("demo.message", "after")
                self.assertEqual((old.event_count, other.event_count, fresh.event_count), (1, 2, 1))
            finally:
                manager.shutdown()
            bus.publish("demo.message", "shutdown")
        self.assertNotIn("shutdown", output.getvalue())
        self.assertIn("[a] Hello from Dummy Plugin", output.getvalue())

    def test_dummy_pipeline_lifecycle(self):
        bus, output = EventBus(), io.StringIO()
        manager = PluginManager(bus)
        runner, first, second = PipelinePlugin(), DummyPlugin("dummy.first"), DummyPlugin("dummy.second")
        with redirect_stdout(output):
            try:
                for plugin in (runner, first, second):
                    manager.register(plugin)
                    manager.load(plugin.id)
                value = object()
                self.assertIs(runner.execute(PipelineRequest((first, second), value)), value)
                self.assertEqual(bus.publish("demo.message", "before unload"), [])
                manager.unload(first.id)
                self.assertEqual(bus.publish("demo.message", "after unload"), [])
            finally:
                manager.shutdown()
        self.assertNotIn("[dummy.first] event: after unload", output.getvalue())
        self.assertIn("[dummy.second] event: after unload", output.getvalue())

    def test_imports_have_no_output_or_demo_side_effects(self):
        result = subprocess.run([sys.executable, "-c",
                                 "import core, interfaces, plugins.dummy, plugins.pipeline, scripts.skill"],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_dependency_boundaries(self):
        # Concrete plugin dependencies are resolved only by the composition root.
        for layer in ("core", "interfaces", "plugins"):
            allowed = set(sys.stdlib_module_names) | {"interfaces", layer}
            for path in (ROOT / layer).rglob("*.py"):
                owner = path.relative_to(ROOT).parts[1] if layer == "plugins" else None
                optional = {"extract": {"pypdf"}, "render": {"PIL", "pypdfium2"}}.get(owner, set())
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        if node.level:
                            self.assertEqual(node.level, 1, f"Cross-plugin relative import: {path}")
                            continue
                        modules = [node.module or ""]
                    else:
                        continue
                    for module in modules:
                        root = module.split(".")[0]
                        self.assertIn(root, allowed | optional, f"{path}: {module}")
                        if root == "plugins":
                            self.assertIn(module.split(".")[1], {owner, "_shared"}, f"{path}: {module}")
                        if root in optional:
                            self.assertNotIn(node, tree.body, f"Eager PDF import: {path}")


if __name__ == "__main__":
    unittest.main()
