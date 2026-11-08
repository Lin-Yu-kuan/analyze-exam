"""測試 analyze-exam-subject 的 -revise 快速重新生成與健檢模式。"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"





from plugin_support import make_services, format_check_report
services = make_services()
unittest.addModuleCleanup(services.close)
tool = services.tool
compact = services.compact
renderer = services.renderer
build_report_html = services.report.build_report_html
check_report_html = services.check.check_report_html
resolve_run_dir = services.storage.resolve_run_dir


class ReviseModeTests(unittest.TestCase):
    def _create_mock_run_dir(self, parent_dir: Path, run_name: str, subject: str = "資料庫應用") -> Path:
        run_dir = parent_dir / run_name
        run_dir.mkdir(parents=True, exist_ok=True)
        sha = "d" * 64
        index_data = {
            "schema_version": 3,
            "protocol": 2,
            "study_subject": subject,
            "classification_version": "1",
            "status": "complete",
            "coverage": {"selected_files": 1, "processed_files": 1, "missing_pages": 0},
            "topics": [
                {"topic_id": "SQL_DDL", "label": "SQL 語法與 DDL/DML"},
            ],
            "questions": [
                {
                    "question_id": "Q1",
                    "content_sha256": sha,
                    "pages": [1],
                    "question_number": "1",
                    "countable": True,
                    "relevance": "relevant",
                    "topic_ids": ["SQL_DDL"],
                    "reason": "",
                    "summary": "題目摘要",
                    "source_refs": [
                        {
                            "path": "test.pdf",
                            "sha256": sha,
                            "official_subject": subject,
                            "role": "question",
                            "year": 114,
                            "exam_name": "高考三級",
                            "category": "資訊處理",
                        }
                    ],
                    "score": 25,
                    "evidence_excerpt": "1 請寫出 SQL 查詢敘述。",
                    "visual_status": "checked",
                    "analysis_status": "completed",
                    "duplicate_candidates": [],
                }
            ],
        }
        (run_dir / "question-index.json").write_text(json.dumps(index_data, ensure_ascii=False), encoding="utf-8")
        (run_dir / "report.md").write_text(
            f"# {subject}分析報告\n\n## 2. 考點地圖\n\n### 2.1 `SQL_DDL`：SQL 語法與 DDL/DML\n- **涵蓋子主題**：SELECT, JOIN\n",
            encoding="utf-8",
        )
        return run_dir

    def test_resolve_run_dir_from_various_depths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "題庫庫房"
            analysis_root = repo_root / "_analysis"
            subj_dir = analysis_root / "資料庫應用"

            old_run = self._create_mock_run_dir(subj_dir, "run_old")
            time.sleep(0.05)
            new_run = self._create_mock_run_dir(subj_dir, "run_new")

            # 1. 傳入 repo_root -> 自動穿透到最新 new_run
            self.assertEqual(resolve_run_dir(repo_root), new_run)

            # 2. 傳入 _analysis 根目錄 -> 自動定位 new_run
            self.assertEqual(resolve_run_dir(analysis_root), new_run)

            # 3. 傳入科目目錄 -> 自動定位 new_run
            self.assertEqual(resolve_run_dir(subj_dir), new_run)

            # 4. 直接傳入 old_run -> 尊重明確指定的 old_run
            self.assertEqual(resolve_run_dir(old_run), old_run)

    def test_check_report_html_and_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "題庫庫房"
            analysis_root = repo_root / "_analysis"
            run_dir = self._create_mock_run_dir(analysis_root / "資料庫應用", "run1")

            out_html = build_report_html(run_dir, sync_root=True)
            info = check_report_html(out_html)

            self.assertEqual(info["status"], "PASS")
            self.assertEqual(info["study_subject"], "資料庫應用")
            self.assertEqual(info["topics_count"], 1)
            self.assertEqual(info["questions_count"], 1)
            self.assertTrue(info["sync_report_html"])
            self.assertTrue(info["named_report_html"])

            card = format_check_report(info)
            self.assertIn("✅ 考點分析報告重新生成與健康體檢合格！", card)
            self.assertIn("資料庫應用", card)

    def test_cli_revise_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "題庫庫房"
            analysis_root = repo_root / "_analysis"
            run_dir = self._create_mock_run_dir(analysis_root / "資料庫應用", "run1")

            # 測試 CLI --revise 傳入 _analysis 路徑
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / 'skill.py'),
                    'revise',
                    str(analysis_root),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("自動鎖定最新分析成果目錄", proc.stdout)
            self.assertIn("健康體檢合格", proc.stdout)
            self.assertTrue((run_dir / "report.html").is_file())
            self.assertTrue((repo_root / "資料庫應用_考點分析報告.html").is_file())
