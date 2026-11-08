"""New entry point, segmented pipelines and pre-refactor output parity."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from plugin_support import ROOT
from interfaces.commands import CommandRequest
from plugins.pipeline import PipelineStepError
from scripts._bootstrap import Application, ExecutionCleanupError

ENTRY = ROOT / "scripts" / "skill.py"
BASELINE = ROOT / "tests" / "fixtures" / "plugin_migration.json"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

        def restore(value):
            if isinstance(value, dict): return {restore(k): restore(v) for k, v in value.items()}
            if isinstance(value, list): return [restore(v) for v in value]
            if isinstance(value, str): return value.replace("{ROOT}", str(self.root))
            return value

        self.data = restore(self.baseline)
        (self.root / "來源.pdf").write_bytes(b"source fixture; rendering is tested separately")
        (self.root / "page.png").write_bytes(b"image fixture; integrity only")
        for key, name in (("inventory", "inventory.json"), ("selection", "selection.json"),
                          ("renders", "render-index.json"), ("review", "visual-review.json")):
            self.write(name, self.data[key])
        self.write("topics.json", {"topics": self.data["topics"]})
        self.write("results.json", {"results": self.data["results"]})
        (self.root / "report.md").write_text(self.data["report_md"], encoding="utf-8")

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def normalize(self, value):
        if isinstance(value, dict): return {self.normalize(k): self.normalize(v) for k, v in value.items()}
        if isinstance(value, list): return [self.normalize(v) for v in value]
        if isinstance(value, str): return value.replace(str(self.root), "{ROOT}").replace(self.root.as_posix(), "{ROOT}")
        return value

    def run_command(self, request):
        with Application() as app:
            return app.execute(request)

    def prepare(self):
        self.run_command(CommandRequest("prepare", inventory=self.root / "inventory.json", selection=self.root / "selection.json",
                         render_index=self.root / "render-index.json", topics=self.root / "topics.json",
                         output=self.root / "work.json", batch_size=1))

    def finalize_request(self):
        return CommandRequest("finalize", run_dir=self.root, results=(self.root / "results.json",), require_complete=True)

    def test_index_summary_payload_and_template_match_old_version(self):
        self.prepare()
        result = self.run_command(self.finalize_request())
        self.assertEqual(set(result.artifacts), {"index", "summary"})
        for key, name in (("expected_index", "question-index.json"), ("expected_summary", "summary.json")):
            self.assertEqual(self.normalize(json.loads((self.root / name).read_text(encoding="utf-8"))), self.baseline[key])
        self.run_command(CommandRequest("revise", path=self.root, no_sync_root=True))
        content = (self.root / "report.html").read_text(encoding="utf-8")
        payload = re.search(r'<script id="exam-report-data" type="application/json">(.*?)</script>', content, re.S)
        self.assertIsNotNone(payload)
        self.assertEqual(self.normalize(json.loads(payload.group(1))), self.baseline["expected_payload"])
        template = (ROOT / "plugins" / "report_html" / "template.html").read_text(encoding="utf-8")
        self.assertEqual(hashlib.sha256(template.encode()).hexdigest(), self.baseline["template_sha256"])

    def test_scan_from_other_directory_and_read_only_sources(self):
        before = (self.root / "來源.pdf").read_bytes()
        result = subprocess.run([sys.executable, str(ENTRY), "scan", str(self.root), "--subject", "資料結構"],
                                cwd=self.temp.name, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        outputs = json.loads(result.stdout)
        inventory = json.loads(Path(outputs["inventory"]).read_text(encoding="utf-8"))
        self.assertEqual(inventory["files"][0]["relative_path"], "來源.pdf")
        self.assertEqual((self.root / "來源.pdf").read_bytes(), before)
        self.assertTrue(Path(outputs["run_dir"]).is_relative_to(self.root / "_analysis"))

    def test_finalize_cli_and_target_override(self):
        self.prepare()
        result = subprocess.run([sys.executable, str(ENTRY), "finalize", "--run-dir", str(self.root),
                                 "--results", str(self.root / "results.json"), "--target", "不存在的考試", "--require-complete"],
                                cwd=self.temp.name, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(Path(json.loads(result.stdout)["summary"]).read_text(encoding="utf-8"))
        self.assertEqual(summary["unique_related_questions"], 0)

    def test_finalize_preflights_all_outputs_before_writing(self):
        self.prepare()
        self.write("summary.json", {"keep": True})
        with self.assertRaises(ValueError):
            self.run_command(self.finalize_request())
        self.assertFalse((self.root / "question-index.json").exists())
        self.assertEqual(json.loads((self.root / "summary.json").read_text()), {"keep": True})

    def test_finalize_partial_is_not_published_when_complete_required(self):
        self.prepare()
        self.write("visual-review.json", {"schema_version": 1, "host_vision": {"available": False}, "pages": []})
        with self.assertRaises(PipelineStepError):
            self.run_command(self.finalize_request())
        self.assertFalse((self.root / "question-index.json").exists())
        self.assertFalse((self.root / "summary.json").exists())

    def test_invalid_summary_options_fail_before_merge_writes(self):
        self.prepare()
        with self.assertRaises(ValueError):
            self.run_command(replace(self.finalize_request(), examples=6))
        self.assertFalse((self.root / "question-index.json").exists())

    def test_unrelated_disabled_plugin_does_not_block_inventory(self):
        with Application(disabled=frozenset({"render", "report-html", "manifest"})) as app:
            app.execute(CommandRequest("inventory", root=self.root, output=self.root / "inventory-new.json"))
            self.assertEqual(set(app.instances), {"pipeline", "workspace", "inventory"})

    def test_required_disabled_plugin_stops_scan_before_creating_run(self):
        with Application(disabled=frozenset({"inventory"})) as app:
            with self.assertRaisesRegex(ValueError, "inventory"):
                app.execute(CommandRequest("scan", root=self.root, subject="資料結構"))
        self.assertFalse((self.root / "_analysis").exists())

    def test_primary_error_survives_cleanup_failure(self):
        app = Application()
        primary, cleanup = ValueError("primary failure"), RuntimeError("cleanup failure")
        with patch.object(app, "close", side_effect=cleanup):
            with self.assertRaises(ExecutionCleanupError) as caught:
                with app:
                    raise primary
        self.assertIs(caught.exception.primary, primary)
        self.assertIs(caught.exception.cleanup, cleanup)
        self.assertIs(caught.exception.__cause__, primary)

    def test_successful_merge_remains_when_summary_step_fails(self):
        self.prepare()
        with Application() as app:
            app._load(("merge", "summary"), self.finalize_request())
            with patch.object(app.instances["summary"], "summarize", side_effect=ValueError("summary failed")):
                with self.assertRaises(PipelineStepError):
                    app.execute(self.finalize_request())
        self.assertTrue((self.root / "question-index.json").is_file())
        self.assertFalse((self.root / "summary.json").exists())
        self.run_command(CommandRequest("summary", index=self.root / "question-index.json", output=self.root / "summary.json"))
        self.assertTrue((self.root / "summary.json").is_file())

    def test_plugin_imports_are_lazy_and_leave_process_settings_unchanged(self):
        code = """
import sys, pkgutil, importlib, plugins
before = (list(sys.path), sys.stdout, sys.stderr, sys.stdout.encoding, sys.stderr.encoding)
for module in pkgutil.walk_packages(plugins.__path__, plugins.__name__ + '.'):
    importlib.import_module(module.name)
assert before == (list(sys.path), sys.stdout, sys.stderr, sys.stdout.encoding, sys.stderr.encoding)
assert not {'pypdf', 'pypdfium2', 'PIL'} & set(sys.modules)
"""
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_preexisting_schema_three_cache_is_reused(self):
        self.write("old-index.json", self.data["expected_index"])
        result = self.run_command(CommandRequest("prepare", inventory=self.root / "inventory.json",
            selection=self.root / "selection.json", render_index=self.root / "render-index.json",
            topics=self.root / "topics.json", prior=self.root / "old-index.json", output=self.root / "work.json"))
        self.assertEqual(result.details["pending_batches"], [])
        self.assertEqual(result.details["reused_sources"], 1)

    def test_report_fallback_uses_injected_statistics(self):
        self.write("question-index.json", self.data["expected_index"])
        request = CommandRequest("report-html", run_dir=self.root, no_sync_root=True)
        with Application() as app:
            app._load(("report-html",), request)
            statistics = app.instances["summary"]
            with patch.object(statistics, "summarize", wraps=statistics.summarize) as summarize:
                app.execute(request)
            self.assertEqual(summarize.call_count, 2)
        self.assertTrue((self.root / "report.html").is_file())

    def test_missing_render_dependency_fails_before_creating_output(self):
        import builtins
        original = builtins.__import__

        def blocked(name, *args, **kwargs):
            if name == "pypdfium2":
                raise ModuleNotFoundError("synthetically unavailable")
            return original(name, *args, **kwargs)

        output = self.root / "new-render.json"
        with patch("builtins.__import__", side_effect=blocked):
            with self.assertRaisesRegex(ValueError, "pypdfium2"):
                self.run_command(CommandRequest("render", inventory=self.root / "inventory.json", pages=(1,), output=output))
        self.assertFalse(output.exists())
        self.assertFalse((self.root / "render-images").exists())


if __name__ == "__main__":
    unittest.main()
