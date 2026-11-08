"""The command-line interface is a plugin; it owns parsing and presentation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from interfaces import PluginContext
from interfaces.commands import CommandRequest, StageResult
from plugins._shared.lifecycle import Lifecycle
from .formatting import format_check_report


class CliPlugin(Lifecycle):
    id = "cli"
    name = "Command line"

    def init(self, context: PluginContext) -> None:
        super().init(context)
        context.subscribe("report.warning", self._warning)

    def _warning(self, message: object) -> None:
        text = f"[WARNING] {message}"
        encoding = getattr(sys.stderr, "encoding", None)
        if encoding:
            text = text.encode(encoding, errors="backslashreplace").decode(encoding)
        print(text, file=sys.stderr)

    def execute(self, argv: list[str]) -> CommandRequest:
        self.require_active()
        parser = argparse.ArgumentParser(description="備考科目分析插件管線（宿主 AI 判讀、本機處理）")
        sub = parser.add_subparsers(dest="command", required=True)
        for command in ("run-dir", "scan"):
            item = sub.add_parser(command)
            item.add_argument("root", type=Path)
            item.add_argument("--subject", required=True)
            item.add_argument("--output-root", type=Path)
            if command == "scan":
                item.add_argument("--manifest", type=Path)
        item = sub.add_parser("inventory")
        item.add_argument("root", type=Path)
        item.add_argument("--manifest", type=Path)
        item.add_argument("--output", required=True, type=Path)
        for command in ("extract", "render"):
            item = sub.add_parser(command)
            item.add_argument("--inventory", required=True, type=Path)
            item.add_argument("--selection", type=Path)
            item.add_argument("--pages", help="一基頁碼，逗號分隔；未選來源時只允許 1")
            item.add_argument("--resume", type=Path)
            item.add_argument("--output", required=True, type=Path)
            if command == "render":
                item.add_argument("--dpi", type=float, default=150.0)
        item = sub.add_parser("prepare")
        for name in ("inventory", "selection", "render-index", "topics"):
            item.add_argument("--" + name, required=True, type=Path)
        item.add_argument("--prior", type=Path)
        item.add_argument("--no-cache", action="store_true")
        item.add_argument("--batch-size", type=int, default=5)
        item.add_argument("--output", required=True, type=Path)
        item = sub.add_parser("packet")
        item.add_argument("--work", required=True, type=Path)
        item.add_argument("--batch-id", required=True)
        item.add_argument("--context", type=int, default=0)
        item.add_argument("--output", required=True, type=Path)
        item = sub.add_parser("status")
        item.add_argument("--work", required=True, type=Path)
        for command in ("merge", "finalize"):
            item = sub.add_parser(command)
            if command == "finalize":
                item.add_argument("--run-dir", required=True, type=Path)
                item.add_argument("--work", type=Path)
                item.add_argument("--target")
                item.add_argument("--examples", type=int, default=2)
            else:
                item.add_argument("--work", required=True, type=Path)
                item.add_argument("--output", required=True, type=Path)
            item.add_argument("--results", nargs="*", type=Path, default=[])
            item.add_argument("--visual-review", type=Path)
            item.add_argument("--extraction", type=Path)
            item.add_argument("--fast-verify", action="store_true")
            item.add_argument("--require-complete", action="store_true")
        item = sub.add_parser("summary")
        item.add_argument("--index", required=True, type=Path)
        item.add_argument("--examples", type=int, default=2)
        item.add_argument("--target")
        item.add_argument("--output", required=True, type=Path)
        item = sub.add_parser("report-html")
        for name in ("run-dir", "report", "summary", "summary-all", "index", "extraction", "output"):
            item.add_argument("--" + name, type=Path)
        item.add_argument("--no-sync-root", action="store_true")
        item = sub.add_parser("revise")
        item.add_argument("path", type=Path)
        item.add_argument("--output", type=Path)
        item.add_argument("--no-sync-root", action="store_true")
        for command in ("check", "clean-images"):
            sub.add_parser(command).add_argument("path", type=Path)
        values = vars(parser.parse_args(argv))
        if "pages" in values and values["pages"] is not None:
            try:
                values["pages"] = tuple(sorted({int(p) for p in values["pages"].split(",")}))
            except ValueError:
                parser.error("--pages 必須是以逗號分隔的整數")
        if "results" in values:
            values["results"] = tuple(values["results"])
        if values["command"] == "report-html" and not any(values.get(k) for k in ("run_dir", "report", "index")):
            parser.error("report-html 需要 --run-dir、--report 或 --index")
        return CommandRequest(**values)

    def present(self, result: StageResult) -> None:
        self.require_active()
        command = result.request.command
        if command == "run-dir":
            print(result.artifacts["run_dir"])
        elif command in {"revise", "check"}:
            if command == "revise":
                print(f"🔍 自動鎖定最新分析成果目錄：{result.request.run_dir}")
            print(format_check_report(result.details))
        elif command in {"scan", "finalize"}:
            print(json.dumps({**{key: str(path) for key, path in result.artifacts.items()}, **result.details}, ensure_ascii=False, indent=2))
        elif command == "status":
            print(json.dumps(result.details, ensure_ascii=False, indent=2))
        elif command == "clean-images":
            data = result.details
            print(f"已清理 {len(data['removed_dirs'])} 個圖片目錄，共刪除 {data['freed_files']} 個暫存圖片，釋放 {data['freed_bytes'] / (1024 * 1024):.2f} MB 空間。")
        else:
            for path in result.artifacts.values():
                print(f"已輸出：{path}")
            if command == "prepare":
                print(json.dumps(result.details, ensure_ascii=False))

    def report_error(self, error: Exception) -> None:
        print(f"無法完成：{error}", file=sys.stderr)
