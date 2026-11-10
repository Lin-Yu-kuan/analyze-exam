"""check: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import json
import re
from interfaces.commands import StageInput, StageResult
from interfaces.services import Storage
from plugins._shared.lifecycle import StagePlugin, request_of, result_of



class ReportCheckPlugin(StagePlugin):
    id = 'check'
    name = 'check'

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage


    def check_report_html(self, html_file: Path) -> dict[str, Any]:
        """檢驗 report.html 的結構完整性與資料健康度。"""
        html_file = html_file.resolve()
        if not html_file.is_file():
            raise FileNotFoundError(f"報告檔案不存在：{html_file}")
    
        file_size_bytes = html_file.stat().st_size
        if file_size_bytes < 10 * 1024:
            raise ValueError(f"報告檔案體積過小 ({file_size_bytes} bytes)，疑似不完整！")
    
        content = html_file.read_text(encoding="utf-8")
    
        # 1. 驗證 JSON 資料契約
        m = re.search(
            r'<script\s+id=["\']exam-report-data["\']\s+type=["\']application/json["\']\s*>(.*?)</script>',
            content,
            re.S,
        )
        if not m:
            raise ValueError('找不到 <script id="exam-report-data"> 資料區塊！')
    
        try:
            data = json.loads(m.group(1))
        except Exception as exc:
            raise ValueError(f'<script id="exam-report-data"> 無法解析為合法 JSON：{exc}') from exc
    
        study_subject = data.get("study_subject", "")
        target_count = data.get("target_unique_count", 0)
        all_count = data.get("all_unique_count", 0)
        topics = data.get("topics", [])
        questions = data.get("questions", [])
    
        if not study_subject:
            raise ValueError("資料中缺少 study_subject 備考科目名稱")
        if not topics:
            raise ValueError("資料中 topics 考點主題清單為空")
        if all_count != len(questions):
            raise ValueError("全題庫統計與已確認題目數不一致，請重新產生 summary")
        if target_count != sum(bool(q.get("target")) for q in questions):
            raise ValueError("目標考試統計與題目範圍不一致，請重新產生 summary")
    
        # 2. 驗證關鍵 UI DOM 元件
        dom_checks = {
            "kpi_container": 'id="kpi-container"' in content,
            "topic_bars": 'id="topic-bars-container"' in content and "bar-rank-badge" in content,
            "accordion": 'id="topics-accordion-container"' in content and "topic-accordion" in content,
            "questions_list": 'id="questions-list-container"' in content and "q-card" in content,
            "km_bridge": 'data-km-mode="full"' in content and "copyTopicKmPrompt" in content,
        }
        missing_dom = [k for k, v in dom_checks.items() if not v]
        if missing_dom:
            raise ValueError(f"報告中缺少關鍵 UI 元件：{', '.join(missing_dom)}")
    
        # 3. 檢查題庫根目錄同步複本
        repo_root = self.storage.find_repo_root(html_file.parent)
        sync_report_html = (repo_root / "report.html").is_file() if repo_root else False
        safe_subj = re.sub(r'[\\/*?:"<>|]', "_", str(study_subject).strip())
        named_report_html = (repo_root / f"{safe_subj}_考點分析報告.html").is_file() if repo_root else False

        # 4. 檢查主題手風琴內容健康度（防止被 knowledge-map 特訓指令惡意覆蓋或為空）
        corrupted_topics = []
        for t in topics:
            t_html = t.get("html") or ""
            if "knowledge-map-learning" in t_html or "特訓指令" in t_html:
                corrupted_topics.append(t.get("id") or "未知主題")
        if corrupted_topics:
            raise ValueError(f"主題手風琴內容異常（包含特訓指令/被後續章節誤覆蓋）：{', '.join(corrupted_topics)}")

        # 5. 檢查 HTML 表格結構健康度（防範欄位數不匹配、儲存格斷裂或殘留未解析 Markdown）
        md_table_blocks = re.findall(r'<table\b[^>]*class=["\'][^"\']*md-table[^"\']*["\'][^>]*>(.*?)</table>', content, re.S)
        for t_idx, tbl in enumerate(md_table_blocks):
            thead_m = re.search(r'<thead>(.*?)</thead>', tbl, re.S)
            th_count = len(re.findall(r'<th\b[^>]*>', thead_m.group(1))) if thead_m else 0
            if th_count > 0:
                tbody_m = re.search(r'<tbody>(.*?)</tbody>', tbl, re.S)
                tbody_html = tbody_m.group(1) if tbody_m else tbl
                rows = re.findall(r'<tr>(.*?)</tr>', tbody_html, re.S)
                for r_idx, row in enumerate(rows):
                    td_count = len(re.findall(r'<td\b[^>]*>', row))
                    if td_count > 0 and td_count != th_count:
                        raise ValueError(f"報告中第 {t_idx + 1} 個表格第 {r_idx + 1} 列欄位數異常（表頭 {th_count} 欄，實際 {td_count} 欄），疑似內容斷裂跑版！")
            if "**" in tbl:
                raise ValueError(f"報告中第 {t_idx + 1} 個表格內含有未解析的 Markdown 粗體標記 '**'，疑似解析異常！")
    
        return {
            "html_file": html_file,
            "file_size_kb": round(file_size_bytes / 1024, 1),
            "study_subject": study_subject,
            "target_unique_count": target_count,
            "all_unique_count": all_count,
            "topics_count": len(topics),
            "questions_count": len(questions),
            "repo_root": repo_root,
            "sync_report_html": sync_report_html,
            "named_report_html": named_report_html,
            "status": "PASS",
        }

    def run(self, value: StageInput) -> StageResult:
        r = request_of(value)
        return result_of(value, artifacts={"html": r.path}, details=self.check_report_html(r.path))
