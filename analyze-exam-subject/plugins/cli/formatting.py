"""Console presentation owned by the CLI plugin."""
from typing import Any
import re

def format_check_report(info: dict[str, Any]) -> str:
    """將 check_report_html 結果格式化為易讀之文字卡片。"""
    lines = [
        "============================================================",
        "✅ 考點分析報告重新生成與健康體檢合格！",
        f"📄 報告位置：{info['html_file']} ({info['file_size_kb']} KB)",
        f"🎯 備考科目：{info['study_subject']}",
        f"📊 考情數據：目標去重 {info['target_unique_count']} 題｜全題庫 {info['all_unique_count']} 題",
        f"🗂️ 考點主題：{info['topics_count']} 個主題",
        f"🔍 題庫卡片：{info['questions_count']} 道題目完整注入",
        "🎨 前端元件：雙 KPI 看板、排行榜、主題手風琴、刷題器全數就緒",
    ]
    if info.get("repo_root"):
        lines.append(f"📁 題庫根目錄：{info['repo_root']}")
        if info.get("named_report_html"):
            safe_subj = re.sub(r'[\\/*?:"<>|]', "_", str(info["study_subject"]).strip())
            lines.append(f"🔄 具名同步檔：{info['repo_root'] / f'{safe_subj}_考點分析報告.html'}")
    lines.append("============================================================")
    return "\n".join(lines)
