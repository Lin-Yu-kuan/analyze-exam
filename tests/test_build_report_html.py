"""測試互動式 HTML 報告插件。"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
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
clean_moex_pua = services.report.clean_moex_pua
parse_extraction_questions = services.report.parse_extraction_questions
repair_latex_escapes = services.report.repair_latex_escapes


class BuildReportHtmlTests(unittest.TestCase):
    def test_clean_moex_pua_and_leading_number_fix(self) -> None:
        raw = "28 依規定會期不得超過幾日？\n40\ue18c 日 \ue18d50 日 \ue18e60 日 \ue18f70 日 \ue129子題一"
        cleaned = clean_moex_pua(raw)
        self.assertNotRegex(cleaned, r"[\ue000-\uf8ff]")
        self.assertIn("(A) 40 日", cleaned)
        self.assertIn("(B) 50 日", cleaned)
        self.assertIn("(C) 60 日", cleaned)
        self.assertIn("(D) 70 日", cleaned)
        self.assertIn("（一）子題一", cleaned)

    def test_parse_extraction_questions_recovers_full_options(self) -> None:
        sha = "a" * 64
        extraction = {
            "schema_version": 1,
            "documents": [
                {
                    "sha256": sha,
                    "status": "extracted",
                    "pages": [
                        {
                            "page": 1,
                            "text": (
                                "114年公務人員普通考試試題\n代號：40120\n頁次：2－1\n"
                                "甲、申論題部分：（50 分）\n"
                                "一、何謂地方自治團體之組織自主權？請詳述之。（25 分）\n"
                                "乙、測驗題部分：（50 分）\n"
                                "1 縣議會在會期中對於縣政府移送之覆議案，應於送達後幾日內作成決議？\n"
                                "3\ue18c 日 \ue18d7 日 \ue18e15 日 \ue18f30 日\n"
                            ),
                            "status": "text_extracted",
                        },
                        {
                            "page": 2,
                            "text": (
                                "代號：40120\n頁次：2－2\n"
                                "2 關於撤回縣議會議長罷免提案之敘述，下列何者正確？ \n"
                                "經簽署議員三分之二以上同意，始得撤回 \n"
                                "經簽署議員三分之二以上同意，且主席徵詢全體出席議員無異議後，始\n得撤回 \n"
                                "經簽署議員全體同意，始得撤回 \n"
                                "經簽署議員全體同意，且主席徵詢全體出席議員無異議後，始得撤回 \n"
                            ),
                            "status": "text_extracted",
                        },
                    ],
                }
            ],
        }
        lookup = parse_extraction_questions(extraction)
        self.assertIn((sha, "一"), lookup)
        self.assertIn("何謂地方自治團體之組織自主權", lookup[(sha, "一")]["stem"])
        self.assertEqual(lookup[(sha, "一")]["options"], [])

        self.assertIn((sha, "1"), lookup)
        self.assertEqual(lookup[(sha, "1")]["options"], ["3 日", "7 日", "15 日", "30 日"])

        self.assertIn((sha, "2"), lookup)
        self.assertEqual(len(lookup[(sha, "2")]["options"]), 4)
        self.assertEqual(
            lookup[(sha, "2")]["options"][1],
            "經簽署議員三分之二以上同意，且主席徵詢全體出席議員無異議後，始得撤回",
        )

    def test_build_report_html_end_to_end_and_script_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            sha = "b" * 64
            index_data = {
                "schema_version": 3,
                "protocol": 2,
                "study_subject": "地方政府與政治",
                "classification_version": "1",
                "topics": [
                    {"topic_id": "LEGIS_REL", "label": "地方立法與府會關係"},
                    {"topic_id": "FINANCE", "label": "地方財政與稅課"},
                ],
                "mode": "compact",
                "target_exam": {"category": "一般民政"},
                "status": "complete",
                "coverage": {
                    "selected_files": 2,
                    "processed_files": 2,
                    "failed_files": 0,
                    "selected_pages": 2,
                    "visually_checked_pages": 2,
                },
                "questions": [
                    {
                        "question_id": "Sbbbb-P1-Q1",
                        "content_sha256": sha,
                        "pages": [1],
                        "question_number": "1",
                        "countable": True,
                        "relevance": "relevant",
                        "topic_ids": ["LEGIS_REL"],
                        "reason": "",
                        "summary": "測試含有 </script> 標籤的題目摘要",
                        "source_refs": [
                            {
                                "path": str(run_dir / "exam1.pdf"),
                                "sha256": sha,
                                "official_subject": "地方自治概要",
                                "role": "question",
                                "year": 114,
                                "exam_name": "普通考試",
                                "category": "一般民政",
                            }
                        ],
                        "answer_refs": [
                            {
                                "role": "official_answer",
                                "answer": "C",
                                "basis": "標準答案",
                            }
                        ],
                        "score": 2,
                        "evidence_excerpt": "1 測試含有 </script> 標籤的題目？ \ue18c選項A \ue18d選項B \ue18e選項C \ue18f選項D",
                        "visual_status": "checked",
                        "analysis_status": "completed",
                        "image_evidence": [
                            {
                                "source_sha256": sha,
                                "page": 1,
                                "image_path": str(run_dir / "render-images" / "page1.png"),
                                "image_sha256": "c" * 64,
                            }
                        ],
                        "duplicate_candidates": [],
                    }
                ],
            }
            (run_dir / "question-index.json").write_text(
                json.dumps(index_data, ensure_ascii=False), encoding="utf-8"
            )
            (run_dir / "report.md").write_text(
                "# 地方政府與政治分析報告\n\n"
                "> [!IMPORTANT]\n> 狀態：`complete`\n\n"
                "## 2. 考點地圖\n\n"
                "### 2.1 `LEGIS_REL`：地方立法與府會關係\n"
                "- **核心法規與大法官解釋**：《地方制度法》第 39 條、司法院釋字第 498 號解釋\n"
                "- **涵蓋子主題**：覆議制度、定期會會期\n",
                encoding="utf-8",
            )

            out_html = build_report_html(run_dir)
            self.assertTrue(out_html.is_file())
            content = out_html.read_text(encoding="utf-8")
            # 確保內嵌 JSON 中的 </script> 已被轉義，不會提前截斷 script 標籤
            m = re.search(
                r'<script id="exam-report-data" type="application/json">(.*?)</script>',
                content,
                re.S,
            )
            self.assertIsNotNone(m)
            payload = json.loads(m.group(1))
            self.assertEqual(payload["study_subject"], "地方政府與政治")
            self.assertEqual(payload["target_unique_count"], 1)
            self.assertEqual(payload["questions"][0]["opts"], ["選項A", "選項B", "選項C", "選項D"])
            self.assertEqual(payload["questions"][0]["imgs"], ["render-images/page1.png"])
            self.assertEqual(payload["topics"][0]["km_module"], "M1")

            # 驗證知識地圖橋接專區之模式按鈕與勾選互動架構
            self.assertIn('data-km-mode="full"', content)
            self.assertIn('data-km-mode="m1_only"', content)
            self.assertIn('data-km-mode="m2_only"', content)
            self.assertIn('data-km-mode="m3_only"', content)
            self.assertIn('data-km-mode="essay_focus"', content)
            self.assertIn('data-km-mode="cart"', content)
            self.assertIn('selectKmMode', content)
            self.assertIn('toggleKmTopicSingle', content)
            self.assertIn('toggleKmModuleTopics', content)

            # 驗證知識地圖介接指引與一鍵安裝快捷架構
            self.assertIn('km-guide-card', content)
            self.assertIn('toggleKmGuide', content)
            self.assertIn('copyKmInstallCmd', content)
            self.assertIn('copyKmInstallCmd(\'ps\')', content)
            self.assertIn('copyKmInstallCmd(\'sh\')', content)
            self.assertIn('copyKmInstallCmd(\'nlp\')', content)

            # 驗證 PDF 動態路徑導向與按鈕文字簡化
            self.assertIn('getEffectivePdfUrl', content)
            self.assertIn('📄 開啟原卷 PDF</a>', content)
            self.assertIn('#page=', content)
            self.assertIn("p.replace(/\\.html$/i, '.md')", content)
            self.assertNotIn('pageLabel', content)

            # 驗證長條圖與手風琴標題視覺降噪（移除英文 Topic ID，採用 bar-rank-badge）
            self.assertIn('bar-rank-badge', content)
            self.assertNotIn('<span class="bar-code">#${idx + 1}', content)

            # 驗證 CLI 執行
            cli_out = run_dir / "custom-report.html"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / 'skill.py'), "report-html",
                    "--run-dir",
                    str(run_dir),
                    "--output",
                    str(cli_out),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(cli_out.is_file())

    def test_sync_root_and_smart_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "題庫"
            run_dir = repo_root / "_analysis" / "地方自治" / "run1"
            run_dir.mkdir(parents=True)

            (run_dir / "report.md").write_text("# 報告\n", encoding="utf-8")
            (run_dir / "question-index.json").write_text(json.dumps({
                "schema_version": 3,
                "protocol": 2,
                "study_subject": "地方自治",
                "classification_version": "1",
                "status": "complete",
                "coverage": {"selected_files": 1, "processed_files": 1, "missing_pages": 0},
                "sources": [],
                "topics": [{"topic_id": "T1", "label": "主題一"}],
                "questions": [],
            }, ensure_ascii=False), encoding="utf-8")

            out_html = build_report_html(run_dir=run_dir, sync_root=True)
            self.assertTrue(out_html.is_file())

            # 驗證題庫根目錄同步了標準與具名檔案
            self.assertTrue((repo_root / "report.html").is_file())
            self.assertTrue((repo_root / "report.md").is_file())
            self.assertTrue((repo_root / "地方自治_考點分析報告.html").is_file())
            self.assertTrue((repo_root / "地方自治_考點分析報告.md").is_file())

    def test_topic_details_fallback_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "report.md").write_text("# 報告\n\n## 2. 考點地圖\n\n僅有總表而無子章節\n", encoding="utf-8")
            (run_dir / "question-index.json").write_text(json.dumps({
                "schema_version": 3,
                "protocol": 2,
                "study_subject": "資料結構",
                "classification_version": "1",
                "status": "complete",
                "coverage": {"selected_files": 1, "processed_files": 1},
                "topics": [{"topic_id": "TREE", "label": "樹與走訪"}],
                "questions": [
                    {
                        "question_id": "Q1",
                        "content_sha256": "x" * 64,
                        "pages": [1],
                        "question_number": "1",
                        "countable": True,
                        "relevance": "relevant",
                        "topic_ids": ["TREE"],
                        "summary": "前序走訪與後序走訪推導原始二元樹",
                        "analysis_status": "completed",
                        "source_refs": [{"year": 112, "exam_name": "高考三級"}],
                    }
                ],
            }, ensure_ascii=False), encoding="utf-8")

            out_html = build_report_html(run_dir=run_dir, sync_root=False)
            self.assertTrue(out_html.is_file())
            content = out_html.read_text(encoding="utf-8")

            # 確保內嵌 JSON 資料庫中的主題 html 成功啟動智慧補償，且不包含陽春佔位符號
            m = re.search(
                r'<script id="exam-report-data" type="application/json">(.*?)</script>',
                content,
                re.S,
            )
            self.assertIsNotNone(m)
            payload = json.loads(m.group(1))
            topic = payload["topics"][0]
            self.assertIn("智慧考情導讀", topic["html"])
            self.assertIn("前序走訪與後序走訪推導原始二元樹", topic["html"])
            self.assertNotIn("請點擊右上角", topic["html"])

    def test_visual_notes_ui_and_math_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "report.md").write_text(
                "# 演算法與資料結構分析報告\n\n"
                "## 2. 考點地圖\n\n"
                "### CMPLX 演算法複雜度\n"
                "- **涵蓋子主題**：時間複雜度漸近符號（$O, \\Omega, \\Theta$）、遞迴式 $T(n) = aT(n/b) + f(n)$\n"
                "- **常見命題模式**：主定理求解\n",
                encoding="utf-8",
            )
            (run_dir / "question-index.json").write_text(json.dumps({
                "schema_version": 3,
                "protocol": 2,
                "study_subject": "演算法",
                "classification_version": "1",
                "status": "complete",
                "coverage": {"selected_files": 1, "processed_files": 1},
                "topics": [{"topic_id": "CMPLX", "label": "演算法複雜度"}],
                "questions": [],
            }, ensure_ascii=False), encoding="utf-8")

            out_html = build_report_html(run_dir=run_dir, sync_root=False)
            self.assertTrue(out_html.is_file())
            content = out_html.read_text(encoding="utf-8")

            # 驗證便條紙 #1, #2, #3：文案精確修正
            self.assertIn("<span>🗂️ 主題考點</span>", content)
            self.assertNotIn("主題考點手風琴", content)
            self.assertIn("<span>📊 總覽與配分</span>", content)
            self.assertNotIn("總覽與配分雷達", content)
            self.assertIn("<span>🧭 知識地圖學習銜接</span>", content)
            self.assertNotIn("知識地圖學習橋接", content)

            # 驗證便條紙 #4：KaTeX CDN 引入與數學公式排版保護標籤
            self.assertIn("katex@0.16.9/dist/katex.min.css", content)
            m_data = re.search(
                r'<script id="exam-report-data" type="application/json">(.*?)</script>',
                content,
                re.S,
            )
            self.assertIsNotNone(m_data)
            payload = json.loads(m_data.group(1))
            topic_html = payload["topics"][0]["html"]
            self.assertIn("katex-math-inline", topic_html)
            self.assertIn("$O, \\Omega, \\Theta$", topic_html)

            # 驗證題庫檢索與刷題器（view-explorer）具備動態 KaTeX 渲染鉤子
            self.assertIn("renderMath(qListContainer);", content)

    def test_repair_latex_escapes_heals_corrupted_math(self) -> None:
        raw_corrupted = (
            "- SQL 順序：`FROM` $\n"
            "ightarrow$ `WHERE` $\n"
            "ightarrow$ `SELECT`。\n"
            "- 衍生運算子（Natural Join $\x08owtie$、Theta Join $\x08owtie_\theta$、Division $\\div$、Left/Right/Full Outer Join $\n"
            "times, \\ltimes, \\ \x0bcenter{\\hbox{$\x08owtie$}}\\mkern-14mu\n"
            "aisebox{0.1ex}{\x0cootnotesize $\\circ$}\\ $）\n"
            "- 關聯演算：$\\sigma_{\t ext{Age} \\ge 20}(R)$\n"
            "- 節點限制：內部節點需有 $\\lceil M/2 \n"
            "ceil$ 個。\n"
        )
        repaired = repair_latex_escapes(raw_corrupted)
        self.assertNotIn("\x08", repaired)
        self.assertNotIn("\x0b", repaired)
        self.assertNotIn("\x0c", repaired)
        self.assertNotIn("times, \\ltimes", repaired)
        self.assertIn(r"\rightarrow", repaired)
        self.assertIn(r"\bowtie", repaired)
        self.assertIn(r"\text", repaired)
        self.assertIn(r"\lceil M/2  \rceil$", repaired)
        self.assertIn(r"\mathbin{⟗}", repaired)

    def test_learning_status_and_custom_tags_ui(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "report.md").write_text("# 報告\n\n## 2. 考點地圖\n\n### T1 題目\n- **涵蓋子主題**：子主題1\n", encoding="utf-8")
            (run_dir / "question-index.json").write_text(json.dumps({
                "schema_version": 3,
                "protocol": 2,
                "study_subject": "地方政府與政治",
                "classification_version": "1",
                "status": "complete",
                "coverage": {"selected_files": 1, "processed_files": 1},
                "topics": [{"topic_id": "T1", "label": "主題一"}],
                "questions": [{
                    "question_id": "q1",
                    "content_sha256": "x" * 64,
                    "pages": [1],
                    "question_number": "1",
                    "countable": True,
                    "relevance": "relevant",
                    "topic_ids": ["T1"],
                    "source_id": "src1",
                    "question_type": "mcq",
                    "stem": "測試題幹",
                    "options": ["A", "B", "C", "D"],
                    "official_answer": "A",
                }],
                "sources": {
                    "src1": {"exam": "高考", "subj": "地方自治", "year": 112, "pdf": "test.pdf", "is_target_exam": True}
                }
            }, ensure_ascii=False), encoding="utf-8")

            out_html = build_report_html(run_dir=run_dir, sync_root=False)
            content = out_html.read_text(encoding="utf-8")

            # 驗證學習狀態與自訂標籤相關元件存在
            self.assertIn('class="status-select st-', content)
            self.assertIn('openTagModal', content)
            self.assertIn('q-tags-bar', content)
            self.assertIn('id="f-status"', content)
            self.assertIn('id="f-tag"', content)
            self.assertIn('id="study-progress-pill"', content)
            self.assertIn('id="tag-modal"', content)
            self.assertIn('id="backup-modal"', content)
            self.assertIn('🎯 模式 F：待學與弱點微課程', content)
            self.assertIn('exportUserDataJson', content)
            self.assertIn('importUserDataJson', content)


if __name__ == "__main__":
    unittest.main()
