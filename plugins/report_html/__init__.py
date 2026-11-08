"""report-html: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from typing import Any
import html
import json
import os
import re
import shutil
import tempfile
from interfaces.commands import StageInput, StageResult
from interfaces.services import Storage, Statistics
from plugins._shared.lifecycle import StagePlugin, request_of, result_of
from plugins._shared.analysis import matches_target

PUA_OPTION_MAP = {
    "\ue18c": "(A)",
    "\ue18d": "(B)",
    "\ue18e": "(C)",
    "\ue18f": "(D)",
    "\ue190": "(E)",
}

PUA_SUBNUM_MAP = {
    "\ue129": "（一）",
    "\ue12a": "（二）",
    "\ue12b": "（三）",
    "\ue12c": "（四）",
    "\ue12d": "（五）",
    "\ue12e": "（六）",
    "\ue12f": "（七）",
    "\ue130": "（八）",
    "\ue131": "（九）",
    "\ue132": "（十）",
}

CHINESE_NUMS = "一二三四五六七八九十"

class ReportHtmlPlugin(StagePlugin):
    id = 'report-html'
    name = 'report-html'

    def __init__(self, storage: Storage, statistics: Statistics) -> None:
        super().__init__()
        self.storage = storage
        self.statistics = statistics


    def clean_moex_pua(self, text: str | None) -> str:
        """將考選部 PUA 造字（選項符號與子題號）轉為標準可讀文字，並修正 pypdf 前置數字錯位。"""
        if not text:
            return ""
        # 修正 pypdf 將選項 A 開頭的數字排到 \ue18c 前方的現象（例如 "40\ue18c 日" -> "\ue18c 40 日"）
        cleaned = re.sub(r"(?m)^[ \t]*([0-9][0-9,.\s/%～~－-]*)\s*\ue18c", "\ue18c \\1", text)
        cleaned = re.sub(r"([？：。])\s*([0-9][0-9,.\s/%～~－-]*)\s*\ue18c", "\\1 \ue18c \\2", cleaned)
        for k, v in PUA_OPTION_MAP.items():
            cleaned = cleaned.replace(k, f" {v} ")
        for k, v in PUA_SUBNUM_MAP.items():
            cleaned = cleaned.replace(k, v)
        # 清除其他殘留 PUA 控制字元（U+E000..U+F8FF）
        cleaned = re.sub(r"[\ue000-\uf8ff]", "", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        return cleaned.strip()

    def repair_latex_escapes(self, text: str | None) -> str:
        """自動自癒被 Python escape 污染（\\x08, \\x09, \\x0b, \\x0c 或 \\r 斷行）的 LaTeX 公式與特殊符號。"""
        if not text:
            return ""
        fixed = text
    
        # 1. 優先處理複合損毀：Outer Join 區塊（跨行與控制字元拼湊）
        fixed = re.sub(
            r"Left/Right/Full Outer Join[\s\S]*?circ[\s\S]*?\$?\s*）",
            lambda m: "Left/Right/Full Outer Join（$\\mathbin{⟕}$、$\\mathbin{⟖}$、$\\mathbin{⟗}$，亦記為 $\\bowtie$、$\\ltimes$、$\\rtimes$）",
            fixed,
        )
        fixed = re.sub(
            r"\\\s*\\?v?center\{\\hbox\{[^}]*bowtie[^}]*\}\}\\mkern-?14mu\s*\\?r?aisebox\{[^}]*\}\{[^}]*circ[^}]*\}\\\s*",
            lambda m: "\\mathbin{⟗}",
            fixed,
        )
    
        # 2. 控制字元首字母補全（Python escape 吞噬了 TeX 指令首字母）
        # \\x08 (ASCII 8, \\b) 吞噬了 'b' -> \\b{word}
        fixed = re.sub(r"\x08([a-zA-Z]+)", lambda m: "\\b" + m.group(1), fixed)
        # \\x0c (ASCII 12, \\f) 吞噬了 'f' -> \\f{word}
        fixed = re.sub(r"\x0c([a-zA-Z]+)", lambda m: "\\f" + m.group(1), fixed)
        # \\x0b (ASCII 11, \\v) 吞噬了 'v' -> \\v{word}
        fixed = re.sub(r"\x0b([a-zA-Z]+)", lambda m: "\\v" + m.group(1), fixed)
        # \\t (ASCII 9, \\t) 吞噬了 't' -> 針對特定 TeX 關鍵字補全 \\t{word}（容錯包含 tab 後殘留空白）
        fixed = re.sub(r"\t\s*(ext|imes|heta|woheadrightarrow|o\b|au\b|ilde|op\b)", lambda m: "\\t" + m.group(1), fixed)
    
        # 3. \\r (ASCII 13) 吞噬了 'r' 並造成換行
        fixed = re.sub(r"\$\s*(?:\r?\n)[ \t]*ightarrow\$", lambda m: "$\\rightarrow$", fixed)
        fixed = re.sub(r"(?<!\\)\bightarrow\b", lambda m: "rightarrow", fixed)
        # 修復以 \\r 斷行的 \\rho
        fixed = re.sub(r"\$\s*(?:\r?\n)[ \t]*ho\b", lambda m: "$\\rho", fixed)
        fixed = re.sub(r"(?<=\$)ho\b", lambda m: "\\rho", fixed)
        # 修復 \\raisebox
        fixed = re.sub(r"(?:\r?\n)[ \t]*aisebox\b", lambda m: " \\raisebox", fixed)
        fixed = re.sub(r"\baisebox\b", lambda m: "raisebox", fixed)
        # 修復 \\rceil（例如 \\lceil M/2 \\n ceil$）
        fixed = re.sub(r"(?:\r?\n)[ \t]*ceil\$", lambda m: " \\rceil$", fixed)
        fixed = re.sub(r"(?<=\s)ceil\$", lambda m: "\\rceil$", fixed)
    
        # 4. 接回因公式換行斷裂的行（同一清單項目或段落內奇數個 $ 斷行修復）
        lines = fixed.split("\n")
        merged_lines: list[str] = []
        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            while (line.count("$") % 2 != 0) and i + 1 < n:
                next_line = lines[i + 1].strip()
                # 若下一行是標題或分隔線則中斷避免越界
                if not next_line or next_line.startswith(("#", "---")):
                    break
                # 如果下一行是獨立清單符號開頭則中斷
                if re.match(r"^([-*+]|\d+\.)\s+", next_line) and not line.strip().endswith("$"):
                    break
                line = line + " " + next_line
                i += 1
            merged_lines.append(line)
            i += 1
        return "\n".join(merged_lines)

    def _strip_page_header(self, page_text: str, is_first_page: bool) -> str:
        """移除試卷每頁頂端重複之『代號：... 頁次：...』表頭，方便跨頁與單頁切題。"""
        text = page_text.replace("\r\n", "\n").replace("\r", "\n")
        if not is_first_page:
            text = re.sub(
                r"^\s*代號：\s*[^\n]+(?:\n\s*\d+\s*)*\n\s*頁次：\s*\d+\s*[－\-—~]\s*\d+\s*\n?",
                "",
                text,
            )
        return text

    def _split_pua_options(self, raw_block: str) -> tuple[str, list[str]]:
        """從含有考選部 PUA 選項符號（或已轉為 (A)-(D)）之原始區塊切出題幹與四個選項。"""
        block = re.sub(r"(?m)^[ \t]*([0-9][0-9,.\s/%～~－-]*)\s*\ue18c", "\ue18c \\1", raw_block)
        block = re.sub(r"([？：。])\s*([0-9][0-9,.\s/%～~－-]*)\s*\ue18c", "\\1 \ue18c \\2", block)
        if "\ue18c" in block and "\ue18d" in block and "\ue18e" in block and "\ue18f" in block:
            parts_a = block.split("\ue18c", 1)
            stem = parts_a[0]
            rest = parts_a[1]
            parts_b = rest.split("\ue18d", 1)
            opt_a = parts_b[0]
            parts_c = parts_b[1].split("\ue18e", 1)
            opt_b = parts_c[0]
            parts_d = parts_c[1].split("\ue18f", 1)
            opt_c = parts_d[0]
            opt_d = parts_d[1]
            return (
                self.clean_moex_pua(stem.replace("\n", " ")),
                [self.clean_moex_pua(o.replace("\n", " ")) for o in (opt_a, opt_b, opt_c, opt_d)],
            )
    
        # 若無 PUA 符號（例如部分身障特考 PDF CMap 未映射 \ue18c），利用 pypdf 段落尾空白規則先合併自動換行
        unwrapped = re.sub(r"(?<! )\n", "", block.strip())
        lines = [ln.strip() for ln in unwrapped.split("\n") if ln.strip()]
        if len(lines) >= 5:
            stem = self.clean_moex_pua(" ".join(lines[:-4]))
            opt_lines = lines[-4:]
            return stem, [self.clean_moex_pua(o) for o in opt_lines]
    
        lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
        stem_idx = -1
        for i, ln in enumerate(lines):
            if ln.endswith("？") or ln.endswith("?") or ln.endswith("：") or ln.endswith(":"):
                stem_idx = i
                break
        if stem_idx != -1 and stem_idx + 1 < len(lines):
            stem = self.clean_moex_pua(" ".join(lines[: stem_idx + 1]))
            opt_lines = lines[stem_idx + 1 :]
            if len(opt_lines) == 4:
                return stem, [self.clean_moex_pua(o) for o in opt_lines]
            full_text = self.clean_moex_pua(" ".join(lines))
            return full_text, []
    
        return self.clean_moex_pua(block.replace("\n", " ")), []

    def parse_extraction_questions(self, extraction: dict | None) -> dict[tuple[str, str], dict[str, Any]]:
        """從 extraction.json 解析每份試卷的完整題幹與選項，回傳 (sha256, q_num) -> {stem, options}。"""
        if not extraction or not isinstance(extraction.get("documents"), list):
            return {}
        lookup: dict[tuple[str, str], dict[str, Any]] = {}
        for doc in extraction["documents"]:
            sha = doc.get("sha256")
            pages = doc.get("pages") or []
            if not sha or not pages:
                continue
            sorted_pages = sorted(pages, key=lambda p: p.get("page", 0))
            cleaned_pages = [
                self._strip_page_header(p.get("text") or "", idx == 0)
                for idx, p in enumerate(sorted_pages)
            ]
            full_doc_text = "\n".join(cleaned_pages)
    
            # 1. 切割申論題（一、二、三、四...）
            essay_zone = full_doc_text
            mcq_split = re.split(r"(?m)^\s*乙、\s*測驗(?:式試)?題部分", full_doc_text, maxsplit=1)
            if len(mcq_split) == 2:
                essay_zone, mcq_zone = mcq_split[0], mcq_split[1]
            else:
                mcq_zone = full_doc_text
    
            # 移除首頁考卷表頭（頁次：... 之前）
            essay_body = re.sub(r"^.*?\n\s*頁次：\s*\d+\s*[－\-—~]\s*\d+\s*\n", "", essay_zone, count=1, flags=re.S)
            essay_matches = list(
                re.finditer(r"(?m)^\s*([一二三四五六七八九十]+)\s*[、.．]\s*", essay_body)
            )
            for i, m in enumerate(essay_matches):
                q_num = m.group(1)
                start = m.end()
                end = essay_matches[i + 1].start() if i + 1 < len(essay_matches) else len(essay_body)
                raw_q = essay_body[start:end].strip()
                # 移除尾端可能夾帶的頁尾注意事項
                raw_q = re.sub(r"\n\s*※注意：.*$", "", raw_q, flags=re.S).strip()
                stem = self.clean_moex_pua(raw_q.replace("\n", ""))
                if stem:
                    lookup[(sha, q_num)] = {"stem": stem, "options": []}
    
            # 2. 切割測驗題（1..50）
            mcq_body = re.sub(r"^.*?\n\s*頁次：\s*\d+\s*[－\-—~]\s*\d+\s*\n", "", mcq_zone, count=1, flags=re.S)
            # 依序尋找題號 1, 2, 3... 避免選項開頭數字誤判為題號
            pos = 0
            q_positions: list[tuple[int, int, int]] = []
            for expected_q in range(1, 61):
                pattern = re.compile(
                    rf"(?m)^[ \t]*{expected_q}[ \t]+(?![\ue18c-\ue18f\d])([^\n]+)"
                )
                m = pattern.search(mcq_body, pos)
                if not m:
                    if expected_q == 1:
                        break
                    continue
                q_positions.append((expected_q, m.start(), m.start(1)))
                pos = m.end()
    
            for i, (q_int, _, text_start) in enumerate(q_positions):
                text_end = q_positions[i + 1][1] if i + 1 < len(q_positions) else len(mcq_body)
                raw_block = mcq_body[text_start:text_end].strip()
                stem, opts = self._split_pua_options(raw_block)
                lookup[(sha, str(q_int))] = {"stem": stem, "options": opts}
    
        return lookup

    def parse_report_sections(self, report_md: str, topic_ids: list[str]) -> dict[str, Any]:
        """無損拆解 report.md 章節，並將第 2 節各主題解析自動對應至 topic_id。"""
        text = self.repair_latex_escapes(report_md).replace("\r\n", "\n").replace("\r", "\n")
        topic_details: dict[str, dict[str, Any]] = {}
    
        # 依 ### 標題切分，尋找包含 topic_id 的段落
        h3_splits = re.split(r"(?m)^###\s+(.+)$", text)
        for idx in range(1, len(h3_splits), 2):
            heading = h3_splits[idx].strip()
            body = h3_splits[idx + 1].strip()
            # 截斷到下一個 ## 二級標題之前
            body = re.split(r"(?m)^##\s+", body, maxsplit=1)[0].strip()
            body = re.sub(r"\n---\s*$", "", body).strip()
    
            matched_tid = None
            for tid in topic_ids:
                if re.search(rf"\b{re.escape(tid)}\b", heading):
                    matched_tid = tid
                    break
            if matched_tid:
                # 萃取關鍵欄位供知識地圖橋接器與卡片標籤使用（若無則不強求，保持彈性）
                laws = ""
                subtopics = ""
                patterns = ""
                m_law = re.search(r"-\s*\*\*(?:核心法規|核心理論|底層核心)[^*]*\*\*[:：]\s*(.+)", body)
                if m_law:
                    laws = m_law.group(1).strip()
                m_sub = re.search(r"-\s*\*\*(?:涵蓋子主題|次領域核心子維度|子維度|必考關鍵詞|核心概念)[^*]*\*\*[:：]\s*(.+)", body)
                if m_sub:
                    subtopics = m_sub.group(1).strip()
                m_pat = re.search(r"-\s*\*\*(?:常見命題模式|經典考法與命題型態|經典考法|命題型態|常見考法)[^*]*\*\*[:：]\s*(.+)", body)
                if m_pat:
                    patterns = m_pat.group(1).strip()
    
                topic_details[matched_tid] = {
                    "heading": heading,
                    "markdown": body,
                    "html": self.render_markdown_block(body),
                    "laws": laws,
                    "subtopics": subtopics,
                    "patterns": patterns,
                }
    
        # 將整份 report.md 依 ## 二級標題切分為導覽區塊
        h2_splits = re.split(r"(?m)^##\s+(.+)$", text)
        intro_md = h2_splits[0].strip()
        sections = []
        if intro_md:
            sections.append({
                "id": "sec-intro",
                "title": "報告摘要與範圍狀態",
                "html": self.render_markdown_block(intro_md),
            })
        for idx in range(1, len(h2_splits), 2):
            sec_title = h2_splits[idx].strip()
            sec_body = h2_splits[idx + 1].strip()
            sec_id = f"sec-{idx // 2 + 1}"
            sections.append({
                "id": sec_id,
                "title": sec_title,
                "html": self.render_markdown_block(f"## {sec_title}\n\n{sec_body}"),
            })
    
        return {
            "topic_details": topic_details,
            "sections": sections,
        }

    def _render_inline(self, text: str) -> str:
        """渲染行內 Markdown 語法（連結、行內程式碼、粗斜體、法條/釋字膠囊高亮）。"""
        placeholders: list[str] = []
    
        def save_ph(html_frag: str) -> str:
            idx = len(placeholders)
            placeholders.append(html_frag)
            return f"\x00PH{idx}\x00"
    
        # 1. 行內程式碼 `...`
        def repl_code(m: re.Match) -> str:
            code_txt = html.escape(m.group(1), quote=False)
            return save_ph(f"<code>{code_txt}</code>")
    
        text = re.sub(r"`([^`\n]+)`", repl_code, text)
    
        # 2. 數學公式 $$...$$ 與 $...$（優先保護，防止被底線或星號破壞）
        def repl_display_math(m: re.Match) -> str:
            raw_math = html.escape(m.group(1).strip(), quote=False)
            return save_ph(f'<span class="katex-math-display">$${raw_math}$$</span>')
    
        def repl_inline_math(m: re.Match) -> str:
            raw_math = html.escape(m.group(1).strip(), quote=False)
            return save_ph(f'<span class="katex-math-inline">${raw_math}$</span>')
    
        text = re.sub(r"\$\$([^\$\n]+?)\$\$", repl_display_math, text)
        text = re.sub(r"(?<!\\)\$([^\$\n]+?)(?<!\\)\$", repl_inline_math, text)
    
        # 3. Markdown 連結 [label](url)
        def repl_link(m: re.Match) -> str:
            raw_label = m.group(1)
            raw_url = m.group(2).strip()
            # 移除 label 內可能包裹的 `\x00PH...\x00` 並還原
            for i, ph in enumerate(placeholders):
                raw_label = raw_label.replace(f"\x00PH{i}\x00", ph)
            safe_url = html.escape(raw_url, quote=True)
            return save_ph(f'<a href="{safe_url}" target="_blank" rel="noopener">{raw_label}</a>')
    
        text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", repl_link, text)
    
        # 3. 轉義一般文字 HTML
        escaped = html.escape(self.clean_moex_pua(text), quote=False)
    
        # 4. 粗體與斜體
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"\*(.+?)\*", r"<em>\1</em>", escaped)
    
        # 5. 法規與釋字視覺強化膠囊（非侵入式）
        escaped = re.sub(
            r"(司法院釋字第\s*\d+(?:、\d+)*\s*號(?:解釋)?|\d+\s*年?憲判字第\s*\d+\s*號(?:判決)?)",
            r'<span class="law-pill interp-pill">\1</span>',
            escaped,
        )
    
        # 還原 placeholders
        for i, ph in enumerate(placeholders):
            escaped = escaped.replace(f"\x00PH{i}\x00", ph)
        return escaped

    def render_markdown_block(self, md_text: str) -> str:
        """純標準庫通用 Markdown 渲染器（支援 GitHub Alerts、管線表格、多層清單、標題、分隔線，具備 LaTeX 破損自癒能力）。"""
        sanitized_text = self.repair_latex_escapes(md_text)
        lines = sanitized_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        out: list[str] = []
        i = 0
        n = len(lines)
    
        while i < n:
            line = lines[i]
            stripped = line.strip()
    
            if not stripped:
                i += 1
                continue
    
            # 分隔線
            if re.match(r"^-{3,}\s*$", stripped):
                out.append("<hr>")
                i += 1
                continue
    
            # 標題
            m_h = re.match(r"^(#{1,4})\s+(.+)$", stripped)
            if m_h:
                level = len(m_h.group(1))
                title_html = self._render_inline(m_h.group(2).strip())
                out.append(f"<h{level}>{title_html}</h{level}>")
                i += 1
                continue
    
            # GitHub Alerts 或 Blockquote
            if stripped.startswith(">"):
                bq_lines: list[str] = []
                while i < n and lines[i].strip().startswith(">"):
                    bq_lines.append(re.sub(r"^>\s?", "", lines[i].strip()))
                    i += 1
                first_line = bq_lines[0].strip() if bq_lines else ""
                m_alert = re.match(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$", first_line, re.I)
                if m_alert:
                    kind = m_alert.group(1).lower()
                    rest_first = m_alert.group(2).strip()
                    body_lines = ([rest_first] if rest_first else []) + bq_lines[1:]
                    body_html = self.render_markdown_block("\n".join(body_lines))
                    labels = {
                        "note": "📌 補充說明（NOTE）",
                        "tip": "💡 實戰技巧（TIP）",
                        "important": "⚡ 關鍵重點（IMPORTANT）",
                        "warning": "⚠️ 注意事項（WARNING）",
                        "caution": "🚨 特別警示（CAUTION）",
                    }
                    out.append(
                        f'<div class="md-alert md-alert-{kind}">'
                        f'<div class="md-alert-title">{labels.get(kind, kind.upper())}</div>'
                        f'<div class="md-alert-body">{body_html}</div></div>'
                    )
                else:
                    body_html = self.render_markdown_block("\n".join(bq_lines))
                    out.append(f"<blockquote>{body_html}</blockquote>")
                continue
    
            # 管線表格
            if "|" in stripped and i + 1 < n and re.match(r"^\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1].strip()):
                header_cells = [c.strip() for c in stripped.strip("|").split("|")]
                align_cells = [c.strip() for c in lines[i + 1].strip().strip("|").split("|")]
                aligns = []
                for ac in align_cells:
                    if ac.startswith(":") and ac.endswith(":"):
                        aligns.append("center")
                    elif ac.endswith(":"):
                        aligns.append("right")
                    else:
                        aligns.append("left")
                i += 2
                rows_html = []
                while i < n and "|" in lines[i].strip() and not lines[i].strip().startswith("#"):
                    row_cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    tds = []
                    for col_idx, cell in enumerate(row_cells):
                        al = aligns[col_idx] if col_idx < len(aligns) else "left"
                        tds.append(f'<td style="text-align:{al}">{self._render_inline(cell)}</td>')
                    rows_html.append("<tr>" + "".join(tds) + "</tr>")
                    i += 1
                ths = []
                for col_idx, hcell in enumerate(header_cells):
                    al = aligns[col_idx] if col_idx < len(aligns) else "left"
                    ths.append(f'<th style="text-align:{al}">{self._render_inline(hcell)}</th>')
                out.append(
                    '<div class="table-wrap"><table class="md-table">'
                    f"<thead><tr>{''.join(ths)}</tr></thead>"
                    f"<tbody>{''.join(rows_html)}</tbody></table></div>"
                )
                continue
    
            # 清單（無序或有序）
            if re.match(r"^(\s*)([-*+]|\d+\.)\s+", line):
                list_items: list[str] = []
                is_ordered = bool(re.match(r"^\s*\d+\.\s+", line))
                tag = "ol" if is_ordered else "ul"
                while i < n and re.match(r"^(\s*)([-*+]|\d+\.)\s+", lines[i]):
                    item_text = re.sub(r"^(\s*)([-*+]|\d+\.)\s+", "", lines[i])
                    i += 1
                    sub_lines: list[str] = []
                    while i < n and lines[i].strip() and (
                        lines[i].startswith("  ") or lines[i].startswith("\t")
                    ) and not re.match(r"^([-*+]|\d+\.)\s+", lines[i].strip()):
                        sub_lines.append(lines[i][2:] if lines[i].startswith("  ") else lines[i][1:])
                        i += 1
                    item_html = self._render_inline(item_text.strip())
                    if sub_lines:
                        item_html += self.render_markdown_block("\n".join(sub_lines))
                    list_items.append(f"<li>{item_html}</li>")
                out.append(f"<{tag}>{''.join(list_items)}</{tag}>")
                continue
    
            # 一般段落
            para_lines = [stripped]
            i += 1
            while (
                i < n
                and lines[i].strip()
                and not lines[i].strip().startswith(("#", ">", "---"))
                and not re.match(r"^(\s*)([-*+]|\d+\.)\s+", lines[i])
                and not ("|" in lines[i] and i + 1 < n and re.match(r"^\|?[\s:|-]+\|[\s:|-]*$", lines[i + 1].strip()))
            ):
                para_lines.append(lines[i].strip())
                i += 1
            out.append(f"<p>{self._render_inline(' '.join(para_lines))}</p>")
    
        return "\n".join(out)

    def _to_rel_or_file_uri(self, target_path: str | None, run_dir: Path) -> str:
        """優先將檔案轉為相對於 run_dir 之相對路徑，若跨磁碟無法計算則轉為 file:/// URI。"""
        if not target_path:
            return ""
        p = Path(target_path)
        try:
            rel = os.path.relpath(p.resolve(), run_dir.resolve())
            return str(rel).replace("\\", "/")
        except (ValueError, OSError):
            raw = str(p).replace("\\", "/")
            if not raw.startswith("file:///"):
                return "file:///" + raw.lstrip("/")
            return raw

    def build_compact_payload(self, 
        run_dir: Path,
        report_path: Path,
        report_md: str,
        summary_target: dict,
        summary_all: dict,
        index_data: dict,
        extraction_data: dict | None,
    ) -> dict[str, Any]:
        """將題庫、雙軌統計、Markdown 章節與知識地圖橋接藍圖組裝並進行字典去重壓縮。"""
        target_cfg = (
            summary_target.get("target_exam")
            if (summary_target and summary_target.get("target_exam") is not None)
            else index_data.get("target_exam")
        )
        topics_meta = index_data.get("topics") or summary_target.get("topics") or []
        topic_ids = [t["topic_id"] for t in topics_meta]
        parsed_md = self.parse_report_sections(report_md, topic_ids)
        ext_lookup = self.parse_extraction_questions(extraction_data)
    
        # 建立來源試卷字典表（避免 1,772 題重複儲存長路徑與 metadata）
        sources_list: list[dict[str, Any]] = []
        source_idx_map: dict[str, int] = {}
    
        def get_source_idx(ref: dict[str, Any]) -> int:
            sha = ref.get("sha256") or ref.get("path") or ""
            if sha in source_idx_map:
                return source_idx_map[sha]
            idx = len(sources_list)
            source_idx_map[sha] = idx
            pdf_uri = self._to_rel_or_file_uri(ref.get("path"), run_dir)
            sources_list.append({
                "year": ref.get("year"),
                "exam": ref.get("exam_name") or "未知考試",
                "subj": ref.get("official_subject") or "未知科目",
                "cat": ref.get("category") or "",
                "pdf": pdf_uri,
                "is_target": bool(matches_target(ref, target_cfg)),
            })
            return idx
    
        # 統計各主題在目標考試與全題庫的申論/測驗拆解
        topic_stats: dict[str, dict[str, int]] = {
            tid: {
                "target_total": 0,
                "target_essay": 0,
                "target_mcq": 0,
                "all_total": 0,
                "all_essay": 0,
                "all_mcq": 0,
            }
            for tid in topic_ids
        }
    
        compact_questions: list[dict[str, Any]] = []
        countable_questions = [
            q for q in index_data.get("questions", [])
            if q.get("countable", True)
            and q.get("analysis_status") == "completed"
            and q.get("relevance") in {"relevant", "cross_domain"}
        ]
    
        for q in countable_questions:
            qid = q["question_id"]
            sha = q.get("content_sha256") or ""
            qnum = str(q.get("question_number") or "")
            refs = q.get("source_refs") or []
            src_indices = [get_source_idx(r) for r in refs]
            is_target_q = any(matches_target(r, target_cfg) for r in refs)
            # 還原完整題幹與選項
            ext_item = ext_lookup.get((sha, qnum))
            if ext_item and ext_item.get("stem"):
                stem = ext_item["stem"]
                options = ext_item.get("options") or []
            else:
                raw_ev = q.get("evidence_excerpt") or q.get("summary") or ""
                # 移除開頭重複題號
                raw_ev = re.sub(rf"^\s*{re.escape(qnum)}\s*[、.．\s]\s*", "", raw_ev)
                stem, options = self._split_pua_options(raw_ev)
                if not stem:
                    stem = self.clean_moex_pua(q.get("summary") or "")
    
            # 題型判定（支援智慧推斷，避免上游 unknown 導致前端題型統計與 KPI 卡片為 0）
            question_type = q.get("question_type", "unknown")
            if question_type not in {"essay", "mcq"}:
                question_type = self._infer_question_type(q, stem, options)
            is_essay = question_type == "essay"
    
            tids = list(dict.fromkeys(q.get("topic_ids") or []))
            for tid in tids:
                if tid in topic_stats:
                    topic_stats[tid]["all_total"] += 1
                    if is_essay:
                        topic_stats[tid]["all_essay"] += 1
                    elif question_type == "mcq":
                        topic_stats[tid]["all_mcq"] += 1
                    if is_target_q:
                        topic_stats[tid]["target_total"] += 1
                        if is_essay:
                            topic_stats[tid]["target_essay"] += 1
                        elif question_type == "mcq":
                            topic_stats[tid]["target_mcq"] += 1
    
            # 處理答案與更正答案
            ans_refs = q.get("answer_refs") or []
            official_ans = ""
            corrected_ans = ""
            ans_basis = ""
            for ar in ans_refs:
                role = ar.get("role")
                if role == "corrected_answer":
                    corrected_ans = str(ar.get("answer") or "")
                    ans_basis = str(ar.get("basis") or ans_basis)
                elif role == "official_answer":
                    official_ans = str(ar.get("answer") or "")
                    if not ans_basis:
                        ans_basis = str(ar.get("basis") or "")
            final_ans = corrected_ans or official_ans
    
            # 處理試卷圖片相對路徑
            imgs = [
                self._to_rel_or_file_uri(im.get("image_path"), run_dir)
                for im in (q.get("image_evidence") or [])
                if im.get("image_path")
            ]
    
            compact_questions.append({
                "id": qid,
                "q": qnum,
                "p": q.get("pages") or [1],
                "type": question_type,
                "score": q.get("score"),
                "rel": "c" if q.get("relevance") == "cross_domain" else "r",
                "why": self.clean_moex_pua(q.get("reason") or ""),
                "t": tids,
                "s": src_indices[0] if src_indices else 0,
                "target": is_target_q,
                "stem": stem,
                "quiz_ready": bool(q.get("quiz_verified") is True and stem and options and final_ans and question_type == "mcq"),
                "opts": options,
                "ans": final_ans,
                "orig_ans": official_ans if corrected_ans and corrected_ans != official_ans else "",
                "basis": ans_basis,
                "imgs": imgs,
                "dups": q.get("duplicate_candidates") or [],
            })
    
        # 零命中是有效結果，不得擴張使用者指定的考試範圍。
    
        # 組裝 11 大主題完整結構（按目標考試題數降序排列）
        enriched_topics: list[dict[str, Any]] = []
        for t in topics_meta:
            tid = t["topic_id"]
            t_label = t.get("label") or tid
            st = topic_stats.get(tid, {})
            md_info = parsed_md["topic_details"].get(tid)
    
            if not md_info or not md_info.get("html"):
                self._warn(f"主題 '{tid}' ({t_label}) 在 report.md 中缺少 '### {tid}' 深度解析區塊！已自動啟動智慧真題導讀補償。")
                # 智慧真題補償機制：動態自題目索引聚合代表題與分佈
                topic_qs = [q for q in countable_questions if tid in q.get("topic_ids", [])]
                sample_lines = []
                for q in topic_qs[:3]:
                    sref = q.get("source_refs", [{}])[0]
                    yr = sref.get("year", "")
                    ex_name = sref.get("exam_name", "")
                    q_num = q.get("question_number", "")
                    q_sum = q.get("summary") or q.get("evidence_excerpt") or "歷屆考點真題演練"
                    q_sum = q_sum.replace("\n", " ").strip()
                    if len(q_sum) > 80:
                        q_sum = q_sum[:80] + "..."
                    prefix = f"{yr} 年 {ex_name} 第 {q_num} 題" if yr or ex_name else f"第 {q_num} 題"
                    sample_lines.append(f">   - **{prefix}**：{q_sum}")
    
                samples_md = "\n".join(sample_lines) if sample_lines else ">   - 尚無代表題摘要"
                subtopics_val = (md_info.get("subtopics") if md_info else "") or f"{t_label} 核心概念與演算法操作"
                patterns_val = (md_info.get("patterns") if md_info else "") or f"申論題 ({st.get('all_essay', 0)} 題) / 測驗題 ({st.get('all_mcq', 0)} 題)"
    
                fallback_md = (
                    f"> [!NOTE]\n"
                    f"> **💡 智慧考情導讀（自動聚合自歷屆真題庫）**\n"
                    f">\n"
                    f"> - **題型分佈**：目標考試命中 **{st.get('target_total', 0)}** 題（申論 {st.get('target_essay', 0)} 題 / 測驗 {st.get('target_mcq', 0)} 題），全題庫累積 **{st.get('all_total', 0)}** 題。\n"
                    f"> - **歷屆代表真題**：\n"
                    f"{samples_md}\n"
                    f">\n"
                    f"> - **限制**：此區僅彙整已確認題目，不代表未來命題機率；尚未提供教學解析。"
                )
                html_val = self.render_markdown_block(fallback_md)
                laws_val = md_info.get("laws", "") if md_info else ""
            else:
                laws_val = md_info.get("laws", "")
                subtopics_val = md_info.get("subtopics", "")
                patterns_val = md_info.get("patterns", "")
                html_val = md_info.get("html", "")
    
            enriched_topics.append({
                "id": tid,
                "label": t_label,
                "target_total": st.get("target_total", 0),
                "target_essay": st.get("target_essay", 0),
                "target_mcq": st.get("target_mcq", 0),
                "all_total": st.get("all_total", 0),
                "all_essay": st.get("all_essay", 0),
                "all_mcq": st.get("all_mcq", 0),
                "laws": laws_val,
                "subtopics": subtopics_val,
                "patterns": patterns_val,
                "html": html_val,
            })
        enriched_topics.sort(key=lambda x: (-x["target_total"], -x["all_total"], x["id"]))
    
        # 自動劃分 M1 / M2 / M3 知識地圖建議模組（依目標題數累進占比：前 ~65% 為 M1，~65-90% 為 M2，其餘為 M3）
        total_target_hits = sum(t["target_total"] for t in enriched_topics) or 1
        cum_hits = 0
        for idx, t in enumerate(enriched_topics):
            ratio = cum_hits / total_target_hits
            if idx < 4 or ratio < 0.62:
                t["km_module"] = "M1"
                t["km_module_name"] = "M1 題庫主題第一組"
            elif idx < 8 or ratio < 0.88:
                t["km_module"] = "M2"
                t["km_module_name"] = "M2 題庫主題第二組"
            else:
                t["km_module"] = "M3"
                t["km_module_name"] = "M3 題庫主題第三組"
            cum_hits += t["target_total"]
    
        report_uri_for_prompt = str(report_path.resolve()).replace("\\", "/")
        return {
            "study_subject": index_data.get("study_subject") or summary_target.get("study_subject") or "未命名科目",
            "target_exam": target_cfg,
            "status": index_data.get("status") or summary_target.get("status") or "unknown",
            "coverage": summary_target.get("coverage") or index_data.get("coverage") or {},
            "target_unique_count": summary_target.get("unique_related_questions", 0),
            "all_unique_count": summary_all.get("unique_related_questions", len(compact_questions)),
            "answer_pair_count": summary_all.get("answer_pair_count", 0),
            "target_answer_pair_count": summary_target.get("answer_pair_count", 0),
            "duplicate_count": summary_all.get("duplicate_candidate_count", 0),
            "target_duplicate_count": summary_target.get("duplicate_candidate_count", 0),
            "report_path": report_uri_for_prompt,
            "topics": enriched_topics,
            "sources": sources_list,
            "questions": compact_questions,
            "report_sections": parsed_md["sections"],
            "by_year_target": summary_target.get("by_year_exam") or [],
            "by_year_all": summary_all.get("by_year_exam") or [],
        }

    def _infer_question_type(self, q: dict[str, Any], stem: str, options: list[str]) -> str:
        """智慧推斷試題類型 (essay / mcq / unknown)。

    防止上游分析管線遺漏 question_type 時導致整份 HTML 報告
    題型統計顯示為 0 題申論 (0.0%) / 0 題測驗 (0.0%)。
    """
        raw_type = q.get("question_type") or q.get("type")
        if raw_type in {"essay", "mcq"}:
            return raw_type
    
        # 1. 明確選項結構：有 2 個以上選項必為單選/多選測驗題
        if options and len(options) >= 2:
            return "mcq"
    
        # 2. 題幹內嵌選項特徵：如 (A)...(B)... 或 [A]...[B]... 或 ①...②...
        if re.search(r'[\(（][A-Da-d1-4][\)）]|[\u2460-\u2463]', stem):
            score = q.get("score")
            if not score or score < 5:
                return "mcq"
    
        # 3. 申論題特徵：
        # (a) 具有申論級配分（通常單題 5~50 分）
        score = q.get("score")
        if score is not None and score >= 5:
            return "essay"
        if re.search(r'[（\(]\s*\d+\s*分\s*[）\)]', stem):
            return "essay"
    
        # (b) 題號為中文大寫國字（一、二、三、四、五、六、七、八、九、十）
        qnum = str(q.get("question_number") or q.get("q") or "").strip()
        if re.match(r'^[一二三四五六七八九十]+', qnum):
            return "essay"
    
        # (c) 題幹長度充足且包含典型申論指令關鍵字
        if len(stem) >= 20 and any(w in stem for w in [
            "說明", "試問", "請分析", "何謂", "繪圖", "詳述", "試述", "如何",
            "優缺點", "比較", "差異", "列舉", "試依", "請說明", "請評估", "試規劃"
        ]):
            return "essay"
    
        return "unknown"

    def build_report_html(self, 
        run_dir: Path,
        report_path: Path | None = None,
        summary_path: Path | None = None,
        summary_all_path: Path | None = None,
        index_path: Path | None = None,
        extraction_path: Path | None = None,
        output_path: Path | None = None,
        sync_root: bool = True,
    ) -> Path:
        """從 run_dir（或指定檔案路徑）讀取分析成果並原子寫出單一自包含 report.html。"""
        run_dir = run_dir.resolve()
        report_file = (report_path or (run_dir / "report.md")).resolve()
        summary_file = (summary_path or (run_dir / "summary.json")).resolve()
        summary_all_file = (summary_all_path or (run_dir / "summary-all.json")).resolve()
        index_file = (index_path or (run_dir / "question-index.json")).resolve()
        extraction_file = (extraction_path or (run_dir / "extraction.json")).resolve()
        out_file = (output_path or (run_dir / "report.html")).resolve()
    
        if not report_file.is_file():
            raise ValueError(f"找不到報告檔案：{report_file}")
        if not index_file.is_file():
            raise ValueError(f"找不到題目索引檔案：{index_file}")
    
        report_md = self.repair_latex_escapes(report_file.read_text(encoding="utf-8"))
        index_data = self.storage.read_json(index_file)
        summary_target = self.storage.read_json(summary_file) if summary_file.is_file() else self.statistics.summarize(index_data, examples=2)
        summary_all = (
            self.storage.read_json(summary_all_file)
            if summary_all_file.is_file()
            else self.statistics.summarize(index_data, examples=2, target=None)
        )
        extraction_data = self.storage.read_json(extraction_file) if extraction_file.is_file() else None
    
        payload = self.build_compact_payload(
            run_dir=run_dir,
            report_path=report_file,
            report_md=report_md,
            summary_target=summary_target,
            summary_all=summary_all,
            index_data=index_data,
            extraction_data=extraction_data,
        )
    
        # 轉為緊湊 JSON 並防止 </script> 注入截斷
        json_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
        safe_title = html.escape(str(payload["study_subject"]), quote=False)
        final_html = self._template().replace("{TITLE}", safe_title).replace("{JSON_PAYLOAD}", json_str)
    
        out_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".report_html_", suffix=".tmp", dir=str(out_file.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(final_html)
            os.replace(tmp_name, out_file)
        except Exception:
            if os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass
            raise
    
        # 自動同步一份至題庫根目錄（雙軌保證：通用 report.* 與具名 <備考科目>_考點分析報告.*）
        if sync_root:
            repo_root = self.storage.find_repo_root(run_dir)
            if repo_root and repo_root.is_dir():
                try:
                    # 1. 複製標準入口
                    shutil.copy2(out_file, repo_root / "report.html")
                    shutil.copy2(report_file, repo_root / "report.md")
    
                    # 2. 複製具名入口（防止同一題庫不同備考科目互相覆蓋）
                    safe_subj = re.sub(r'[\\/*?:"<>|]', '_', str(payload.get("study_subject", "")).strip())
                    if safe_subj:
                        shutil.copy2(out_file, repo_root / f"{safe_subj}_考點分析報告.html")
                        shutil.copy2(report_file, repo_root / f"{safe_subj}_考點分析報告.md")
                except OSError as exc:
                    self._warn(f"同步報告至題庫根目錄時略過：{exc}")
    
        return out_file

    def _template(self) -> str:
        return Path(__file__).with_name("template.html").read_text(encoding="utf-8")

    def _warn(self, message: str) -> None:
        # Advisory notifications never control the report's data flow.
        if self._context is not None:
            self._context.publish("report.warning", message)

    def run(self, value: StageInput) -> StageResult:
        r = request_of(value)
        run_dir = r.run_dir or (r.report.resolve().parent if r.report else r.index.resolve().parent)
        output = self.build_report_html(run_dir, report_path=r.report, summary_path=r.summary,
                                       summary_all_path=r.summary_all, index_path=r.index, extraction_path=r.extraction,
                                       output_path=r.output, sync_root=not r.no_sync_root)
        return result_of(value, request=replace(r, path=output), artifacts={"html": output})
