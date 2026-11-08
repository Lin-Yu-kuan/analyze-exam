"""Test-only bindings to initialized plugin algorithms for existing regression cases."""
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from interfaces.commands import CommandRequest
from scripts._bootstrap import Application, FACTORIES
from plugins._shared import analysis, files
from plugins.cli.formatting import format_check_report


def make_services():
    app = Application()
    app._load(tuple(FACTORIES), CommandRequest("inventory", manifest=Path("unused-test-manifest")))
    instances = app.instances
    storage = instances["workspace"]
    tool = SimpleNamespace(**{name: getattr(storage, name) for name in
        ("digest", "linked", "read_json", "write_new", "source_path", "select_sources", "verify_source", "run_dir")},
        inventory=instances["inventory"].inventory, extract=instances["extract"].extract,
        clean_images=instances["clean-images"].clean_images, safe_name=files.safe_name,
        portable_relative=files.portable_relative)
    compact = SimpleNamespace(prepare=instances["prepare"].prepare, packet=instances["packet"].packet,
        merge=instances["merge"].merge, summarize=instances["summary"].summarize,
        describe_work=instances["status"].describe_work, matches_target=analysis.matches_target,
        fingerprint=analysis.fingerprint)
    return SimpleNamespace(close=app.close, app=app, tool=tool, compact=compact, storage=storage,
                           renderer=instances["render"], report=instances["report-html"], check=instances["check"])
