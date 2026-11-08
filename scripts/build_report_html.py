#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""將 analyze-exam-subject 之 report.md 與 JSON 索引確定性編譯為單一自包含互動式網頁 report.html；不呼叫模型。"""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any

# 確保在 Windows 終端下以 UTF-8 輸出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from compact_analysis import matches_target, summarize  # noqa: E402
from exam_files import read_json  # noqa: E402

# 考選部試題 PDF 常見私用區字碼（PUA）對照表
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


def clean_moex_pua(text: str | None) -> str:
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


def repair_latex_escapes(text: str | None) -> str:
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


def _strip_page_header(page_text: str, is_first_page: bool) -> str:
    """移除試卷每頁頂端重複之『代號：... 頁次：...』表頭，方便跨頁與單頁切題。"""
    text = page_text.replace("\r\n", "\n").replace("\r", "\n")
    if not is_first_page:
        text = re.sub(
            r"^\s*代號：\s*[^\n]+(?:\n\s*\d+\s*)*\n\s*頁次：\s*\d+\s*[－\-—~]\s*\d+\s*\n?",
            "",
            text,
        )
    return text


def _split_pua_options(raw_block: str) -> tuple[str, list[str]]:
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
            clean_moex_pua(stem.replace("\n", " ")),
            [clean_moex_pua(o.replace("\n", " ")) for o in (opt_a, opt_b, opt_c, opt_d)],
        )

    # 若無 PUA 符號（例如部分身障特考 PDF CMap 未映射 \ue18c），利用 pypdf 段落尾空白規則先合併自動換行
    unwrapped = re.sub(r"(?<! )\n", "", block.strip())
    lines = [ln.strip() for ln in unwrapped.split("\n") if ln.strip()]
    if len(lines) >= 5:
        stem = clean_moex_pua(" ".join(lines[:-4]))
        opt_lines = lines[-4:]
        return stem, [clean_moex_pua(o) for o in opt_lines]

    lines = [ln.strip() for ln in block.strip().split("\n") if ln.strip()]
    stem_idx = -1
    for i, ln in enumerate(lines):
        if ln.endswith("？") or ln.endswith("?") or ln.endswith("：") or ln.endswith(":"):
            stem_idx = i
            break
    if stem_idx != -1 and stem_idx + 1 < len(lines):
        stem = clean_moex_pua(" ".join(lines[: stem_idx + 1]))
        opt_lines = lines[stem_idx + 1 :]
        if len(opt_lines) == 4:
            return stem, [clean_moex_pua(o) for o in opt_lines]
        full_text = clean_moex_pua(" ".join(lines))
        return full_text, []

    return clean_moex_pua(block.replace("\n", " ")), []


def parse_extraction_questions(extraction: dict | None) -> dict[tuple[str, str], dict[str, Any]]:
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
            _strip_page_header(p.get("text") or "", idx == 0)
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
            stem = clean_moex_pua(raw_q.replace("\n", ""))
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
            stem, opts = _split_pua_options(raw_block)
            lookup[(sha, str(q_int))] = {"stem": stem, "options": opts}

    return lookup


def parse_report_sections(report_md: str, topic_ids: list[str]) -> dict[str, Any]:
    """無損拆解 report.md 章節，並將第 2 節各主題解析自動對應至 topic_id。"""
    text = repair_latex_escapes(report_md).replace("\r\n", "\n").replace("\r", "\n")
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
            m_law = re.search(r"-\s*\*\*核心法規[^*]*\*\*：\s*(.+)", body)
            if m_law:
                laws = m_law.group(1).strip()
            m_sub = re.search(r"-\s*\*\*涵蓋子主題[^*]*\*\*：\s*(.+)", body)
            if m_sub:
                subtopics = m_sub.group(1).strip()
            m_pat = re.search(r"-\s*\*\*常見命題模式[^*]*\*\*：\s*(.+)", body)
            if m_pat:
                patterns = m_pat.group(1).strip()

            topic_details[matched_tid] = {
                "heading": heading,
                "markdown": body,
                "html": render_markdown_block(body),
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
            "html": render_markdown_block(intro_md),
        })
    for idx in range(1, len(h2_splits), 2):
        sec_title = h2_splits[idx].strip()
        sec_body = h2_splits[idx + 1].strip()
        sec_id = f"sec-{idx // 2 + 1}"
        sections.append({
            "id": sec_id,
            "title": sec_title,
            "html": render_markdown_block(f"## {sec_title}\n\n{sec_body}"),
        })

    return {
        "topic_details": topic_details,
        "sections": sections,
    }


def _render_inline(text: str) -> str:
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
    escaped = html.escape(clean_moex_pua(text), quote=False)

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


def render_markdown_block(md_text: str) -> str:
    """純標準庫通用 Markdown 渲染器（支援 GitHub Alerts、管線表格、多層清單、標題、分隔線，具備 LaTeX 破損自癒能力）。"""
    sanitized_text = repair_latex_escapes(md_text)
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
            title_html = _render_inline(m_h.group(2).strip())
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
                body_html = render_markdown_block("\n".join(body_lines))
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
                body_html = render_markdown_block("\n".join(bq_lines))
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
                    tds.append(f'<td style="text-align:{al}">{_render_inline(cell)}</td>')
                rows_html.append("<tr>" + "".join(tds) + "</tr>")
                i += 1
            ths = []
            for col_idx, hcell in enumerate(header_cells):
                al = aligns[col_idx] if col_idx < len(aligns) else "left"
                ths.append(f'<th style="text-align:{al}">{_render_inline(hcell)}</th>')
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
                item_html = _render_inline(item_text.strip())
                if sub_lines:
                    item_html += render_markdown_block("\n".join(sub_lines))
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
        out.append(f"<p>{_render_inline(' '.join(para_lines))}</p>")

    return "\n".join(out)


def _to_rel_or_file_uri(target_path: str | None, run_dir: Path) -> str:
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


def build_compact_payload(
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
    parsed_md = parse_report_sections(report_md, topic_ids)
    ext_lookup = parse_extraction_questions(extraction_data)

    # 建立來源試卷字典表（避免 1,772 題重複儲存長路徑與 metadata）
    sources_list: list[dict[str, Any]] = []
    source_idx_map: dict[str, int] = {}

    def get_source_idx(ref: dict[str, Any]) -> int:
        sha = ref.get("sha256") or ref.get("path") or ""
        if sha in source_idx_map:
            return source_idx_map[sha]
        idx = len(sources_list)
        source_idx_map[sha] = idx
        pdf_uri = _to_rel_or_file_uri(ref.get("path"), run_dir)
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
            stem, options = _split_pua_options(raw_ev)
            if not stem:
                stem = clean_moex_pua(q.get("summary") or "")

        # 題型判定（支援智慧推斷，避免上游 unknown 導致前端題型統計與 KPI 卡片為 0）
        question_type = q.get("question_type", "unknown")
        if question_type not in {"essay", "mcq"}:
            question_type = _infer_question_type(q, stem, options)
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
            _to_rel_or_file_uri(im.get("image_path"), run_dir)
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
            "why": clean_moex_pua(q.get("reason") or ""),
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
            print(f"⚠️ [WARNING] 主題 '{tid}' ({t_label}) 在 report.md 中缺少 '### {tid}' 深度解析區塊！已自動啟動智慧真題導讀補償。")
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
            html_val = render_markdown_block(fallback_md)
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


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{TITLE} — 國考考古題深度分析與互動刷題系統</title>
<style>
:root {
    --bg-main: #f8fafc;
    --bg-card: #ffffff;
    --bg-muted: #f1f5f9;
    --text-primary: #0f172a;
    --text-secondary: #475569;
    --text-muted: #64748b;
    --border: #e2e8f0;
    --accent: #2563eb;
    --accent-hover: #1d4ed8;
    --accent-light: #dbeafe;
    --purple: #7c3aed;
    --purple-light: #ede9fe;
    --emerald: #059669;
    --emerald-light: #d1fae5;
    --amber: #d97706;
    --amber-light: #fef3c7;
    --rose: #e11d48;
    --rose-light: #ffe4e6;
    --code-bg: #1e293b;
    --code-color: #f8fafc;
    --sidebar-w: 280px;
    --shadow-sm: 0 1px 3px rgba(15, 23, 42, 0.06);
    --shadow-md: 0 4px 12px rgba(15, 23, 42, 0.08);
}

[data-theme="dark"] {
    --bg-main: #0b0f19;
    --bg-card: #131b2e;
    --bg-muted: #1e293b;
    --text-primary: #f1f5f9;
    --text-secondary: #94a3b8;
    --text-muted: #64748b;
    --border: #1e293b;
    --accent: #3b82f6;
    --accent-hover: #60a5fa;
    --accent-light: #1e3a8a;
    --purple: #a78bfa;
    --purple-light: #2e1065;
    --emerald: #34d399;
    --emerald-light: #064e3b;
    --amber: #fbbf24;
    --amber-light: #451a03;
    --rose: #fb7185;
    --rose-light: #4c0519;
    --code-bg: #030712;
    --code-color: #e2e8f0;
    --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.35);
    --shadow-md: 0 4px 14px rgba(0, 0, 0, 0.5);
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft JhengHei", "PingFang TC", Roboto, sans-serif;
    background: var(--bg-main);
    color: var(--text-primary);
    line-height: 1.68;
    display: flex;
    min-height: 100vh;
}

#reading-progress {
    position: fixed;
    top: 0;
    left: 0;
    height: 3px;
    background: linear-gradient(90deg, #2563eb, #7c3aed, #059669);
    width: 0%;
    z-index: 9999;
    transition: width 0.1s;
}

/* 側邊欄（與 knowledge-map-learning/build_notes.py 視覺同源） */
#sidebar {
    width: var(--sidebar-w);
    background: var(--bg-card);
    border-right: 1px solid var(--border);
    position: fixed;
    top: 0;
    bottom: 0;
    left: 0;
    display: flex;
    flex-direction: column;
    z-index: 100;
}

.sidebar-header {
    padding: 16px 18px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
}

.sidebar-header h2 {
    font-size: 0.98rem;
    font-weight: 700;
    color: var(--accent);
    line-height: 1.35;
}

.icon-btn {
    background: var(--bg-muted);
    border: 1px solid var(--border);
    color: var(--text-primary);
    padding: 6px 10px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.85rem;
    transition: all 0.15s;
}
.icon-btn:hover { border-color: var(--accent); color: var(--accent); }

.scope-switcher {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-muted);
}
.scope-label {
    font-size: 0.75rem;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 6px;
    display: block;
}
.scope-toggle-group {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
}
.scope-btn {
    padding: 7px 8px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text-secondary);
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
    text-align: center;
    transition: all 0.15s;
}
.scope-btn.active {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
}

.nav-tree {
    flex: 1;
    overflow-y: auto;
    padding: 12px 14px;
}
.nav-group-title {
    font-size: 0.73rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-muted);
    margin: 12px 8px 6px;
    font-weight: 700;
}
.nav-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 9px 12px;
    margin-bottom: 4px;
    border-radius: 8px;
    color: var(--text-secondary);
    text-decoration: none;
    font-size: 0.88rem;
    cursor: pointer;
    border: none;
    background: transparent;
    width: 100%;
    text-align: left;
    transition: all 0.15s;
}
.nav-item:hover, .nav-item.active {
    background: var(--accent-light);
    color: var(--accent);
    font-weight: 600;
}
.nav-badge {
    font-size: 0.74rem;
    padding: 1px 7px;
    border-radius: 999px;
    background: var(--bg-muted);
    color: var(--text-secondary);
    font-weight: 600;
}
.nav-item.active .nav-badge {
    background: var(--accent);
    color: #fff;
}

.sidebar-footer {
    padding: 12px 16px;
    border-top: 1px solid var(--border);
    background: var(--bg-card);
}
.km-quick-btn {
    width: 100%;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--purple);
    background: var(--purple-light);
    color: var(--purple);
    font-weight: 700;
    font-size: 0.84rem;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    transition: all 0.2s;
}
.km-quick-btn:hover {
    background: var(--purple);
    color: #fff;
}

/* 主內容區 */
#main-content {
    margin-left: var(--sidebar-w);
    flex: 1;
    padding: 32px 44px 80px;
    max-width: 1260px;
}

.page-header {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 24px 28px;
    margin-bottom: 24px;
    box-shadow: var(--shadow-sm);
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 20px;
    flex-wrap: wrap;
}
.page-title {
    font-size: 1.65rem;
    font-weight: 800;
    color: var(--text-primary);
    margin-bottom: 6px;
}
.page-subtitle {
    font-size: 0.92rem;
    color: var(--text-secondary);
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
}
.status-pill {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
}
.status-complete { background: var(--emerald-light); color: var(--emerald); }
.status-partial { background: var(--amber-light); color: var(--amber); }

/* KPI 指標卡片列（雙核心指標：題量與題型） */
.kpi-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 16px;
    margin-bottom: 26px;
}
.kpi-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px 20px;
    box-shadow: var(--shadow-sm);
    display: flex;
    flex-direction: column;
    justify-content: space-between;
    min-height: 128px;
    transition: transform 0.2s ease, box-shadow 0.2s ease, border-color 0.2s ease;
}
.kpi-card:hover {
    box-shadow: var(--shadow-md);
    border-color: rgba(37, 99, 235, 0.35);
}
.kpi-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
}
.kpi-label {
    font-size: 0.82rem;
    color: var(--text-secondary);
    font-weight: 700;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    letter-spacing: -0.01em;
}
.kpi-badge {
    font-size: 0.7rem;
    font-weight: 700;
    padding: 2px 7px;
    border-radius: 999px;
    line-height: 1.3;
    white-space: nowrap;
}
.badge-blue { background: var(--accent-light); color: var(--accent); }
.badge-purple { background: var(--purple-light); color: var(--purple); }
.badge-emerald { background: var(--emerald-light); color: var(--emerald); }
.badge-amber { background: var(--amber-light); color: var(--amber); }

.kpi-card-body {
    display: flex;
    align-items: baseline;
    gap: 6px;
    margin: 4px 0 6px 0;
}
.kpi-value {
    font-size: 1.85rem;
    font-weight: 800;
    color: var(--accent);
    line-height: 1.1;
    letter-spacing: -0.02em;
}
.kpi-unit {
    font-size: 0.88rem;
    font-weight: 600;
    color: var(--text-muted);
}
.kpi-sub {
    font-size: 0.77rem;
    color: var(--text-muted);
    line-height: 1.45;
    margin-top: auto;
}
.kpi-sub strong {
    color: var(--text-primary);
    font-weight: 700;
}

/* 雙指標卡片 (卡片 2) */
.kpi-split {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 4px 0 6px 0;
}
.kpi-split-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
}
.kpi-split-label {
    font-size: 0.74rem;
    font-weight: 600;
    color: var(--text-secondary);
    display: inline-flex;
    align-items: center;
    gap: 4px;
}
.kpi-split-val {
    font-size: 1.35rem;
    font-weight: 800;
    line-height: 1.1;
    display: inline-flex;
    align-items: baseline;
    gap: 3px;
}
.kpi-split-val.purple { color: var(--purple); }
.kpi-split-val.blue { color: var(--accent); }
.kpi-split-pct {
    font-size: 0.72rem;
    font-weight: 600;
    color: var(--text-muted);
}
.kpi-split-divider {
    width: 1px;
    height: 32px;
    background: var(--border);
}
.kpi-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    display: inline-block;
}
.kpi-dot.purple { background: var(--purple); }
.kpi-dot.blue { background: var(--accent); }

/* 微比例長條圖 (Micro Ratio Bar) */
.kpi-ratio-bar {
    width: 100%;
    height: 6px;
    background: var(--bg-muted);
    border-radius: 999px;
    overflow: hidden;
    display: flex;
    margin: 6px 0 8px 0;
}
.ratio-segment {
    height: 100%;
    transition: width 0.3s ease;
}
.ratio-segment.purple { background: var(--purple); }
.ratio-segment.blue { background: var(--accent); }

/* 視圖切換區 */
.view-panel { display: none; }
.view-panel.active { display: block; }

.section-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 24px 28px;
    margin-bottom: 24px;
    box-shadow: var(--shadow-sm);
}
.section-card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 18px;
    flex-wrap: wrap;
    gap: 12px;
}
.section-card-title {
    font-size: 1.2rem;
    font-weight: 700;
    color: var(--text-primary);
}

/* 橫向長條圖 */
.bar-row {
    margin-bottom: 14px;
    padding: 10px 12px;
    border-radius: 10px;
    background: var(--bg-main);
    border: 1px solid transparent;
    cursor: pointer;
    transition: all 0.15s;
}
.bar-row:hover {
    border-color: var(--accent);
    transform: translateX(2px);
}
.bar-top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 6px;
    gap: 12px;
}
.bar-title {
    font-size: 0.9rem;
    font-weight: 600;
    color: var(--text-primary);
}
.bar-rank-badge {
    display: inline-block;
    font-size: 0.78rem;
    font-weight: 700;
    padding: 1px 7px;
    border-radius: 999px;
    background: var(--accent-light);
    color: var(--accent);
    margin-right: 6px;
}
.bar-code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.76rem;
    padding: 2px 6px;
    border-radius: 4px;
    background: var(--accent-light);
    color: var(--accent);
    margin-right: 6px;
}
.bar-meta {
    font-size: 0.82rem;
    color: var(--text-secondary);
    white-space: nowrap;
}
.bar-track {
    height: 10px;
    background: var(--border);
    border-radius: 999px;
    overflow: hidden;
    display: flex;
}
.bar-fill-essay { background: var(--purple); height: 100%; transition: width 0.3s; }
.bar-fill-mcq { background: var(--accent); height: 100%; transition: width 0.3s; }

/* 11 大主題手風琴卡片 */
.topic-accordion {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    margin-bottom: 14px;
    overflow: hidden;
    box-shadow: var(--shadow-sm);
}
.topic-acc-header {
    padding: 16px 20px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    cursor: pointer;
    gap: 16px;
    user-select: none;
    transition: background 0.15s;
}
.topic-acc-header:hover { background: var(--bg-muted); }
.topic-acc-left {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
}
.rank-circle {
    width: 30px;
    height: 30px;
    border-radius: 8px;
    background: var(--accent-light);
    color: var(--accent);
    font-weight: 800;
    font-size: 0.85rem;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
}
.km-mod-tag {
    font-size: 0.73rem;
    font-weight: 700;
    padding: 2px 8px;
    border-radius: 999px;
    background: var(--purple-light);
    color: var(--purple);
    white-space: nowrap;
}
.topic-acc-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-shrink: 0;
}
.btn-sm {
    padding: 5px 11px;
    border-radius: 6px;
    font-size: 0.78rem;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text-primary);
    transition: all 0.15s;
}
.btn-sm:hover { border-color: var(--accent); color: var(--accent); }
.btn-km {
    border-color: var(--purple);
    background: var(--purple-light);
    color: var(--purple);
}
.btn-km:hover { background: var(--purple); color: #fff; }
.topic-acc-body {
    display: none;
    padding: 20px 24px;
    border-top: 1px solid var(--border);
    background: var(--bg-main);
}
.topic-accordion.open .topic-acc-body { display: block; }

/* 題庫檢索與刷題器 */
.filter-bar {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 20px;
    margin-bottom: 18px;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
    gap: 12px;
    box-shadow: var(--shadow-sm);
}
.filter-group label {
    display: block;
    font-size: 0.76rem;
    font-weight: 700;
    color: var(--text-secondary);
    margin-bottom: 4px;
}
.filter-group select, .filter-group input {
    width: 100%;
    padding: 8px 10px;
    border-radius: 7px;
    border: 1px solid var(--border);
    background: var(--bg-main);
    color: var(--text-primary);
    font-size: 0.86rem;
    outline: none;
}
.filter-group select:focus, .filter-group input:focus {
    border-color: var(--accent);
}
.quiz-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 14px;
    flex-wrap: wrap;
    gap: 10px;
}

.q-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 18px 22px;
    margin-bottom: 14px;
    box-shadow: var(--shadow-sm);
    transition: border-color 0.15s;
}
.q-card:hover { border-color: var(--accent); }
.q-card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 10px;
    margin-bottom: 10px;
    flex-wrap: wrap;
}
.q-badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
}
.q-badge {
    font-size: 0.75rem;
    padding: 2px 8px;
    border-radius: 6px;
    font-weight: 600;
    background: var(--bg-muted);
    color: var(--text-secondary);
}
.q-badge-target { background: var(--accent-light); color: var(--accent); }
.q-badge-essay { background: var(--purple-light); color: var(--purple); }
.q-badge-mcq { background: var(--emerald-light); color: var(--emerald); }
.q-badge-dup { background: var(--amber-light); color: var(--amber); }

.q-stem {
    font-size: 1.01rem;
    font-weight: 600;
    color: var(--text-primary);
    margin-bottom: 12px;
    line-height: 1.65;
}
.q-options {
    display: grid;
    gap: 8px;
    margin-bottom: 12px;
}
.q-opt-btn {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 9px 13px;
    border-radius: 8px;
    border: 1px solid var(--border);
    background: var(--bg-main);
    color: var(--text-primary);
    text-align: left;
    font-size: 0.93rem;
    cursor: pointer;
    transition: all 0.15s;
}
.q-opt-btn:hover { border-color: var(--accent); }
.q-opt-btn.correct {
    border-color: var(--emerald);
    background: var(--emerald-light);
    font-weight: 700;
}
.q-opt-btn.wrong {
    border-color: var(--rose);
    background: var(--rose-light);
}
.q-opt-letter {
    font-weight: 800;
    color: var(--accent);
    min-width: 24px;
}
.q-footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding-top: 10px;
    border-top: 1px dashed var(--border);
    flex-wrap: wrap;
    gap: 8px;
    font-size: 0.82rem;
    color: var(--text-secondary);
}
.ans-box {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--emerald-light);
    color: var(--emerald);
    font-weight: 700;
}
.ans-hidden {
    background: var(--bg-muted);
    color: var(--text-secondary);
    cursor: pointer;
}

/* Markdown 報告樣式 */
.md-content h2 {
    font-size: 1.38rem;
    margin: 28px 0 14px;
    padding-bottom: 8px;
    border-bottom: 2px solid var(--border);
    color: var(--accent);
}
.md-content h3 {
    font-size: 1.14rem;
    margin: 22px 0 10px;
    color: var(--text-primary);
}
.md-content h4 { font-size: 1rem; margin: 16px 0 8px; }
.md-content p, .md-content li { margin-bottom: 8px; font-size: 0.96rem; }
.md-content ul, .md-content ol { padding-left: 22px; margin-bottom: 14px; }
.md-content code {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    background: var(--bg-muted);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.86em;
    color: var(--accent);
}
.md-content a { color: var(--accent); text-decoration: none; font-weight: 600; }
.md-content a:hover { text-decoration: underline; }
.md-content hr { border: none; border-top: 1px solid var(--border); margin: 24px 0; }
.law-pill {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 5px;
    font-size: 0.85em;
    font-weight: 600;
    background: var(--amber-light);
    color: var(--amber);
}
.md-alert {
    border-left: 4px solid var(--accent);
    background: var(--bg-muted);
    padding: 14px 18px;
    border-radius: 0 10px 10px 0;
    margin: 14px 0;
}
.md-alert-important { border-left-color: var(--purple); }
.md-alert-warning { border-left-color: var(--amber); }
.md-alert-caution { border-left-color: var(--rose); }
.md-alert-title { font-weight: 700; font-size: 0.88rem; margin-bottom: 6px; }
.table-wrap { overflow-x: auto; margin: 14px 0; }
.md-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.88rem;
}
.md-table th, .md-table td {
    border: 1px solid var(--border);
    padding: 9px 12px;
}
.md-table th { background: var(--bg-muted); font-weight: 700; }

/* 知識地圖橋接專區 */
.km-hero {
    background: linear-gradient(135deg, var(--accent-light), var(--purple-light));
    border: 1px solid var(--purple);
    border-radius: 14px;
    padding: 24px 28px;
    margin-bottom: 24px;
}
/* 知識地圖介接指引卡片 */
.km-guide-card {
    background: var(--bg-card);
    border: 1px solid rgba(124, 58, 237, 0.25);
    border-radius: 10px;
    margin: 14px 0 18px 0;
    overflow: hidden;
    box-shadow: 0 2px 6px rgba(124, 58, 237, 0.06);
}
.km-guide-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 16px;
    background: var(--purple-light);
    cursor: pointer;
    user-select: none;
    border-bottom: 1px solid rgba(124, 58, 237, 0.15);
}
.km-guide-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.92rem;
    color: var(--purple);
    font-weight: 700;
}
.km-guide-badge {
    background: var(--purple);
    color: #fff;
    font-size: 0.72rem;
    padding: 2px 7px;
    border-radius: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
}
.km-guide-toggle-btn {
    font-size: 0.78rem;
    color: var(--purple);
    background: transparent;
    border: 1px solid rgba(124, 58, 237, 0.3);
    border-radius: 6px;
    padding: 3px 8px;
    cursor: pointer;
}
.km-guide-body {
    padding: 16px 18px;
}
.km-steps-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
    margin-bottom: 14px;
}
.km-step-item {
    display: flex;
    gap: 10px;
    background: var(--bg-muted);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 12px;
}
.km-step-num {
    flex-shrink: 0;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: var(--purple);
    color: #fff;
    font-size: 0.8rem;
    font-weight: 800;
    display: flex;
    align-items: center;
    justify-content: center;
}
.km-step-content {
    display: flex;
    flex-direction: column;
    gap: 2px;
}
.km-step-title {
    font-size: 0.86rem;
    font-weight: 700;
    color: var(--text-primary);
}
.km-step-desc {
    font-size: 0.78rem;
    color: var(--text-secondary);
    line-height: 1.4;
}
.km-install-box {
    background: rgba(124, 58, 237, 0.04);
    border: 1px dashed rgba(124, 58, 237, 0.35);
    border-radius: 8px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
}
.km-install-title {
    font-size: 0.84rem;
    font-weight: 700;
    color: var(--purple);
    display: flex;
    align-items: center;
    gap: 6px;
}
.km-install-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}
.km-install-btn {
    background: var(--bg-card);
    border: 1px solid rgba(124, 58, 237, 0.4);
    color: var(--purple);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 0.8rem;
    font-weight: 600;
    cursor: pointer;
    transition: all 0.15s;
    display: inline-flex;
    align-items: center;
    gap: 5px;
}
.km-install-btn:hover {
    background: var(--purple);
    color: #fff;
    border-color: var(--purple);
}
.km-mode-selector {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 12px;
}
.km-mode-btn {
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 0.86rem;
    font-weight: 700;
    cursor: pointer;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text-primary);
    transition: all 0.15s;
    display: inline-flex;
    align-items: center;
    gap: 6px;
}
.km-mode-btn:hover {
    border-color: var(--purple);
    color: var(--purple);
}
.km-mode-btn.active {
    background: var(--purple);
    color: #fff;
    border-color: var(--purple);
    box-shadow: var(--shadow-sm);
}
.km-module-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
    gap: 16px;
    margin: 18px 0;
}
.km-mod-card {
    background: var(--bg-card);
    border: 2px solid var(--border);
    border-radius: 12px;
    padding: 18px 20px;
    transition: all 0.2s;
}
.km-mod-card.has-selected {
    border-color: var(--purple);
    background: var(--bg-card);
}
.km-mod-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
}
.km-mod-title {
    font-size: 1.02rem;
    color: var(--purple);
    font-weight: 800;
}
.km-topic-list {
    list-style: none;
    padding: 0;
    margin: 0;
    font-size: 0.88rem;
}
.km-topic-item {
    margin-bottom: 8px;
    padding: 8px 10px;
    border-radius: 8px;
    border: 1px solid transparent;
    transition: all 0.15s;
    background: var(--bg-main);
}
.km-topic-item:hover {
    border-color: var(--purple);
}
.km-topic-item.selected {
    background: var(--purple-light);
    border-color: var(--purple);
}
.km-topic-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
}
.km-topic-cb {
    margin-top: 3px;
    cursor: pointer;
    accent-color: var(--purple);
    width: 16px;
    height: 16px;
}
.prompt-preview-box {
    width: 100%;
    min-height: 220px;
    padding: 14px;
    border-radius: 10px;
    border: 1px solid var(--border);
    background: var(--code-bg);
    color: var(--code-color);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 0.84rem;
    line-height: 1.55;
    resize: vertical;
}

/* 右下角浮動待學清單（Study Cart） */
#study-cart-fab {
    position: fixed;
    right: 24px;
    bottom: 24px;
    background: var(--purple);
    color: #fff;
    padding: 12px 18px;
    border-radius: 999px;
    font-weight: 700;
    font-size: 0.9rem;
    box-shadow: var(--shadow-md);
    cursor: pointer;
    border: none;
    z-index: 900;
    display: none;
    align-items: center;
    gap: 8px;
}
#study-cart-fab.visible { display: flex; }

/* 學習狀態選擇器與自訂標籤 */
.status-select {
    padding: 4px 8px;
    border-radius: 7px;
    font-size: 0.8rem;
    font-weight: 700;
    border: 1px solid var(--border);
    background: var(--bg-main);
    color: var(--text-primary);
    cursor: pointer;
    outline: none;
    transition: all 0.15s;
}
.status-select.st-todo {
    border-color: var(--purple);
    background: var(--purple-light);
    color: var(--purple);
}
.status-select.st-review {
    border-color: var(--amber);
    background: var(--amber-light);
    color: var(--amber);
}
.status-select.st-done {
    border-color: var(--emerald);
    background: var(--emerald-light);
    color: var(--emerald);
}
.status-select.st-none {
    color: var(--text-secondary);
}

.q-tags-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 6px 0 10px 0;
}
.q-tag-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    background: rgba(37, 99, 235, 0.08);
    color: var(--accent);
    border: 1px solid rgba(37, 99, 235, 0.25);
    border-radius: 999px;
    padding: 2px 8px;
    font-size: 0.76rem;
    font-weight: 600;
    transition: all 0.15s;
}
.q-tag-chip .tag-name {
    cursor: pointer;
}
.q-tag-chip .tag-name:hover {
    text-decoration: underline;
}
.q-tag-chip .tag-del {
    cursor: pointer;
    font-weight: 800;
    color: var(--text-muted);
    padding: 0 2px;
    border-radius: 50%;
    font-size: 0.82rem;
    line-height: 1;
}
.q-tag-chip .tag-del:hover {
    color: var(--rose);
}

/* 自訂彈窗（標籤管理、備份） */
.custom-modal {
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.72);
    backdrop-filter: blur(2px);
    z-index: 2100;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 20px;
}
.custom-modal.open { display: flex; }
.custom-modal-content {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    max-width: 520px;
    width: 100%;
    box-shadow: var(--shadow-lg);
    overflow: hidden;
    animation: modalPop 0.15s ease-out;
}
@keyframes modalPop {
    from { opacity: 0; transform: scale(0.96); }
    to { opacity: 1; transform: scale(1); }
}
.custom-modal-header {
    padding: 12px 18px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-muted);
    display: flex;
    justify-content: space-between;
    align-items: center;
}
.custom-modal-body {
    padding: 18px 20px;
}
.tag-pool-item {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 6px 3px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 600;
    border: 1px solid var(--border);
    background: var(--bg-card);
    color: var(--text-primary);
    transition: all 0.15s;
    user-select: none;
}
.tag-pool-item:hover {
    border-color: var(--accent);
}
.tag-pool-item.active {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
}
.tag-pool-item .tag-pool-toggle {
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    gap: 4px;
}
.tag-pool-item .tag-pool-cnt {
    font-size: 0.7rem;
    opacity: 0.75;
    font-weight: normal;
}
.tag-pool-item .tag-pool-del {
    cursor: pointer;
    padding: 0 4px;
    margin-left: 2px;
    border-radius: 50%;
    font-size: 0.85rem;
    line-height: 1;
    color: var(--text-muted);
    opacity: 0.7;
    transition: all 0.15s;
}
.tag-pool-item.active .tag-pool-del {
    color: #fff;
}
.tag-pool-item .tag-pool-del:hover {
    opacity: 1;
    color: var(--rose);
    background: rgba(244, 63, 94, 0.15);
}
.tag-pool-item.active .tag-pool-del:hover {
    color: #fff;
    background: rgba(0, 0, 0, 0.25);
}
.study-progress-pill {
    font-size: 0.8rem;
    font-weight: 700;
    padding: 4px 10px;
    border-radius: 6px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    color: var(--text-primary);
    display: inline-flex;
    align-items: center;
    gap: 6px;
}

/* Lightbox 圖片預覽彈窗 */
#lightbox-modal {
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.82);
    z-index: 2000;
    display: none;
    align-items: center;
    justify-content: center;
    padding: 24px;
}
#lightbox-modal.open { display: flex; }
.lightbox-content {
    background: var(--bg-card);
    border-radius: 12px;
    max-width: 92vw;
    max-height: 92vh;
    display: flex;
    flex-direction: column;
    overflow: hidden;
}
.lightbox-bar {
    padding: 12px 18px;
    border-bottom: 1px solid var(--border);
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
}
.lightbox-img-wrap {
    overflow: auto;
    padding: 16px;
    text-align: center;
    background: #334155;
}
.lightbox-img-wrap img {
    max-width: 100%;
    height: auto;
    border-radius: 4px;
    background: #fff;
}

/* Toast 通知 */
#toast {
    position: fixed;
    bottom: 28px;
    left: 50%;
    transform: translateX(-50%);
    background: #0f172a;
    color: #fff;
    padding: 10px 20px;
    border-radius: 999px;
    font-size: 0.88rem;
    font-weight: 600;
    z-index: 3000;
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s;
}
#toast.show { opacity: 1; }

@media (max-width: 900px) {
    #sidebar { position: static; width: 100%; height: auto; border-right: none; border-bottom: 1px solid var(--border); }
    body { flex-direction: column; }
    #main-content { margin-left: 0; padding: 20px 16px; }
}
@media print {
    #sidebar, #reading-progress, #study-cart-fab, .filter-bar { display: none !important; }
    #main-content { margin-left: 0; max-width: 100%; padding: 0; }
    .view-panel { display: block !important; }
    .topic-acc-body { display: block !important; }
}

/* 數學公式樣式（KaTeX 與離線優雅降級） */
.katex-math-inline, .katex-math-display {
    font-family: KaTeX_Math, 'Cambria Math', 'Times New Roman', serif;
}
.katex-math-inline {
    display: inline-block;
    padding: 0 2px;
}
.katex-math-display {
    display: block;
    margin: 8px 0;
    text-align: center;
}
</style>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
</head>
<body>
<div id="reading-progress"></div>

<aside id="sidebar">
    <div class="sidebar-header">
        <h2 id="sidebar-subj-title">📊 國考考點分析系統</h2>
        <button class="icon-btn" id="theme-toggle-btn" title="切換深淺色主題">🌓</button>
    </div>

    <div class="scope-switcher">
        <span class="scope-label">統計與圖表視角切換：</span>
        <div class="scope-toggle-group">
            <button class="scope-btn active" id="scope-target-btn" onclick="setScope('target')">🎯 目標考試</button>
            <button class="scope-btn" id="scope-all-btn" onclick="setScope('all')">📚 全題庫</button>
        </div>
    </div>

    <nav class="nav-tree">
        <div class="nav-group-title">核心視圖</div>
        <button class="nav-item active" data-view="view-dashboard" onclick="switchView('view-dashboard')">
            <span>📊 總覽與配分</span>
            <span class="nav-badge" id="nav-badge-total">0</span>
        </button>
        <button class="nav-item" data-view="view-topics" onclick="switchView('view-topics')">
            <span>🗂️ 主題考點</span>
            <span class="nav-badge" id="nav-badge-topics">0</span>
        </button>
        <button class="nav-item" data-view="view-explorer" onclick="switchView('view-explorer')">
            <span>🔍 題庫檢索與刷題器</span>
            <span class="nav-badge" id="nav-badge-qbank">0</span>
        </button>
        <button class="nav-item" data-view="view-report" onclick="switchView('view-report')">
            <span>📖 完整報告與備考策略</span>
        </button>
        <button class="nav-item" data-view="view-km" onclick="switchView('view-km')">
            <span>🧭 知識地圖學習銜接</span>
            <span class="nav-badge" style="background:var(--purple-light);color:var(--purple)">M1~M3</span>
        </button>
    </nav>

    <div class="sidebar-footer">
        <button class="km-quick-btn" onclick="copyFullKmPrompt()">
            🚀 一鍵複製 `/knowledge-map-learning` 全科啟動指令
        </button>
    </div>
</aside>

<main id="main-content">
    <header class="page-header">
        <div>
            <h1 class="page-title" id="main-title">國考考古題深度分析報告</h1>
            <div class="page-subtitle">
                <span class="status-pill status-complete" id="status-badge">✓ COMPLETE</span>
                <span id="target-exam-desc">目標考試載入中…</span>
            </div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn-sm btn-km" onclick="switchView('view-km')">🧭 對接 `/knowledge-map-learning`</button>
            <button class="btn-sm" onclick="switchView('view-explorer')">🔍 進入刷題練習器</button>
        </div>
    </header>

    <!-- KPI 指標列 -->
    <section class="kpi-grid" id="kpi-container"></section>

    <!-- 視圖 1：總覽與配分 -->
    <section class="view-panel active" id="view-dashboard">
        <div class="section-card">
            <div class="section-card-header">
                <div>
                    <h2 class="section-card-title" id="chart-section-title">📈 各主題命題熱度排行（點擊任一主題可展開詳情或篩選題目）</h2>
                    <p style="font-size:0.83rem;color:var(--text-secondary);margin-top:2px">
                        紫色代表「申論題」、藍色代表「單選測驗題」。可從左側切換「🎯 目標考試」與「📚 全題庫」視角。
                    </p>
                </div>
            </div>
            <div id="topic-bars-container"></div>
        </div>

        <div class="section-card">
            <div class="section-card-header">
                <h2 class="section-card-title">📅 年度與考試試卷分層統計</h2>
            </div>
            <div class="table-wrap" id="year-table-container"></div>
        </div>
    </section>

    <!-- 視圖 2：核心考點主題卡片 -->
    <section class="view-panel" id="view-topics">
        <div class="section-card" style="padding:18px 24px;margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
                <div>
                    <h2 class="section-card-title">🗂️ 核心考點主題全覽（依目標考試重要性排序）</h2>
                    <p style="font-size:0.84rem;color:var(--text-secondary)">
                        每個主題均已標註對應之 <code>/knowledge-map-learning</code> 建議模組（M1～M3），可單獨複製單一主題深潛指令或一鍵檢視該主題全部歷屆真題。
                    </p>
                </div>
                <div style="display:flex;gap:8px">
                    <button class="btn-sm" onclick="toggleAllTopics(true)">全部展開</button>
                    <button class="btn-sm" onclick="toggleAllTopics(false)">全部摺疊</button>
                </div>
            </div>
        </div>
        <div id="topics-accordion-container"></div>
    </section>

    <!-- 視圖 3：題庫檢索與刷題器 -->
    <section class="view-panel" id="view-explorer">
        <div class="filter-bar">
            <div class="filter-group">
                <label>考試範圍</label>
                <select id="f-scope" onchange="applyQuestionFilters(1)">
                    <option value="all">📚 全題庫所有試卷</option>
                    <option value="target">🎯 僅目標考試</option>
                    <option value="dup">🔁 跨卷重複／共卷熱門題</option>
                    <option value="corrected">⚖️ 考選部更正答案／送分題</option>
                    <option value="cart">📌 已加入待學清單的題目</option>
                </select>
            </div>
            <div class="filter-group">
                <label>考點主題</label>
                <select id="f-topic" onchange="applyQuestionFilters(1)">
                    <option value="">全部主題</option>
                </select>
            </div>
            <div class="filter-group">
                <label>題型</label>
                <select id="f-type" onchange="applyQuestionFilters(1)">
                    <option value="">全部題型（申論＋測驗）</option>
                    <option value="mcq">單選測驗題</option>
                    <option value="essay">申論題</option>
                </select>
            </div>
            <div class="filter-group">
                <label>年度</label>
                <select id="f-year" onchange="applyQuestionFilters(1)">
                    <option value="">全部年度</option>
                </select>
            </div>
            <div class="filter-group">
                <label>官方科目</label>
                <select id="f-subject" onchange="applyQuestionFilters(1)">
                    <option value="">全部官方科目</option>
                </select>
            </div>
            <div class="filter-group">
                <label>關鍵字即時搜尋（題幹／選項）</label>
                <input type="text" id="f-keyword" placeholder="輸入如：覆議、罰鍰、里長…" oninput="applyQuestionFilters(1)">
            </div>
            <div class="filter-group">
                <label>學習狀態</label>
                <select id="f-status" onchange="applyQuestionFilters(1)">
                    <option value="all">全部狀態</option>
                    <option value="todo">📌 待學習</option>
                    <option value="review">🔁 待複習</option>
                    <option value="done">✅ 已掌握</option>
                    <option value="cart">⚡ 待學＋複習（弱點）</option>
                    <option value="none">⚪ 未標記</option>
                </select>
            </div>
            <div class="filter-group">
                <label>🏷️ 我的自訂標籤</label>
                <select id="f-tag" onchange="applyQuestionFilters(1)">
                    <option value="">全部標籤</option>
                </select>
            </div>
        </div>

        <div class="quiz-toolbar">
            <div id="q-result-summary" style="font-size:0.9rem;font-weight:700;color:var(--text-secondary)"></div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
                <div id="study-progress-pill" class="study-progress-pill">進度：載入中…</div>
                <button class="btn-sm" onclick="openBackupModal()" title="匯出或匯入學習紀錄與自訂標籤">💾 筆記備份</button>
                <button class="btn-sm" id="quiz-mode-btn" onclick="toggleQuizMode()">🙈 練習模式：隱藏標準答案（點擊選項自測）</button>
                <button class="btn-sm" onclick="resetQuestionFilters()">🔄 重設篩選</button>
            </div>
        </div>

        <div id="questions-list-container"></div>
        <div id="pagination-container" style="display:flex;justify-content:center;gap:8px;margin-top:20px;flex-wrap:wrap"></div>
    </section>

    <!-- 視圖 4：完整報告與備考策略 -->
    <section class="view-panel" id="view-report">
        <div class="section-card md-content" id="full-report-container"></div>
    </section>

    <!-- 視圖 5：/knowledge-map-learning 知識地圖銜接中心 -->
    <section class="view-panel" id="view-km">
        <div class="km-hero">
            <h2 style="font-size:1.35rem;font-weight:800;color:var(--purple);margin-bottom:8px">
                🧭 `/knowledge-map-learning` 深度學習無縫銜接中心
            </h2>
            <p style="font-size:0.93rem;color:var(--text-primary);margin-bottom:14px">
                本系統已根據目標考試配分權重，將考點自動劃分為 <strong>M1 核心奪分模組</strong>、<strong>M2 進階拉分模組</strong> 與 <strong>M3 學理防禦模組</strong>，並預先綁定本報告路徑與各主題之「子考點、必背法條、易混淆邊界與歷屆代表題」。請點選下方學習模式或於下方自訂勾選考點主題，複製指令貼入對話框即可<strong>零摩擦直接展開 M1-1 深度精講</strong>！
            </p>

            <!-- 介接與環境準備指引卡片 -->
            <div class="km-guide-card">
                <div class="km-guide-header" onclick="toggleKmGuide()">
                    <div class="km-guide-title">
                        <span class="km-guide-badge">介接指引</span>
                        <span>💡 如何將本考情報告介接至 Antigravity 進行深度自學？</span>
                    </div>
                    <button class="km-guide-toggle-btn" id="km-guide-toggle-text">收合指引 ▲</button>
                </div>
                <div id="km-guide-body" class="km-guide-body">
                    <div class="km-steps-grid">
                        <div class="km-step-item">
                            <div class="km-step-num">1</div>
                            <div class="km-step-content">
                                <div class="km-step-title">步驟一：準備 / 安裝技能</div>
                                <div class="km-step-desc">確認 Antigravity 已安裝 <code>knowledge-map-learning</code> 技能套件（未安裝可點下方一鍵複製指令安裝）。</div>
                            </div>
                        </div>
                        <div class="km-step-item">
                            <div class="km-step-num">2</div>
                            <div class="km-step-content">
                                <div class="km-step-title">步驟二：選定學習模式</div>
                                <div class="km-step-desc">在下方選擇「全科衝刺」、「弱點專班」或自由勾選目標考點主題。</div>
                            </div>
                        </div>
                        <div class="km-step-item">
                            <div class="km-step-num">3</div>
                            <div class="km-step-content">
                                <div class="km-step-title">步驟三：貼入對話框開課</div>
                                <div class="km-step-desc">點擊「立即複製」，貼入對話框發送，AI 導師將自動解析本報告展開精講並逐章生成 HTML 講義！</div>
                            </div>
                        </div>
                    </div>
                    <div class="km-install-box">
                        <div class="km-install-title">
                            <span>🛠️ 若尚未安裝此技能，請點擊複製以下雲端直裝指令：</span>
                        </div>
                        <div class="km-install-actions">
                            <button class="km-install-btn" onclick="copyKmInstallCmd('ps')">
                                💻 複製 Windows PowerShell 雲端直裝指令
                            </button>
                            <button class="km-install-btn" onclick="copyKmInstallCmd('sh')">
                                🍎 複製 macOS / Linux 雲端直裝指令
                            </button>
                            <button class="km-install-btn" onclick="copyKmInstallCmd('nlp')">
                                💬 複製 Antigravity 對話框自然語言安裝語句
                            </button>
                        </div>
                    </div>
                </div>
            </div>

            <div class="km-mode-selector">
                <button class="km-mode-btn active" data-km-mode="full" onclick="selectKmMode('full')">
                    🚀 模式 A：全科系統化知識地圖（M1～M3 全選）
                </button>
                <button class="km-mode-btn" data-km-mode="m1_only" onclick="selectKmMode('m1_only')">
                    ⚡ 模式 B：高投報率衝刺班（專攻 M1 核心奪分）
                </button>
                <button class="km-mode-btn" data-km-mode="m2_only" onclick="selectKmMode('m2_only')">
                    🥈 模式 C：進階拉分專班（專攻 M2 治理實務）
                </button>
                <button class="km-mode-btn" data-km-mode="m3_only" onclick="selectKmMode('m3_only')">
                    🥉 模式 D：學理防禦專班（專攻 M3 基礎與跨域）
                </button>
                <button class="km-mode-btn" data-km-mode="essay_focus" onclick="selectKmMode('essay_focus')">
                    ✍️ 模式 E：申論題攻堅專班（涵蓋高頻申論主題）
                </button>
                <button class="km-mode-btn" data-km-mode="cart" onclick="selectKmMode('cart')">
                    🎯 模式 F：待學與弱點微課程（已選 <span id="km-cart-count">0</span> 題）
                </button>
            </div>
            <div id="km-mode-status" style="margin-top:14px;font-size:0.86rem;color:var(--purple);font-weight:700;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px">
                <span id="km-mode-desc">目前模式：🚀 模式 A（全選 11 個主題）</span>
                <button class="btn-sm btn-km" onclick="copyCurrentTextareaPrompt()">📋 立即複製當前啟動指令</button>
            </div>
        </div>

        <div class="km-module-grid" id="km-modules-preview"></div>

        <div class="section-card">
            <div class="section-card-header">
                <h3 class="section-card-title" id="km-prompt-box-title">📋 產生的 `/knowledge-map-learning` 啟動指令預覽（可直接編輯或點擊右側複製）</h3>
                <button class="btn-sm btn-km" onclick="copyCurrentTextareaPrompt()">📋 複製下方指令到剪貼簿</button>
            </div>
            <textarea id="km-prompt-textarea" class="prompt-preview-box" spellcheck="false"></textarea>
        </div>
    </section>
</main>

<!-- 右下角浮動待學清單按鈕 -->
<button id="study-cart-fab" onclick="openCartKmView()">
    🎯 弱點微課程（待學 <span id="fab-cart-count">0</span> ｜ 複習 <span id="fab-review-count">0</span>）➔ 轉知識地圖精讀
</button>

<!-- 試卷圖片預覽 Lightbox -->
<div id="lightbox-modal" onclick="if(event.target===this)closeLightbox()">
    <div class="lightbox-content">
        <div class="lightbox-bar">
            <strong id="lightbox-title" style="font-size:0.92rem">試卷原始影像預覽</strong>
            <div style="display:flex;gap:8px">
                <a id="lightbox-open-new" href="#" target="_blank" class="btn-sm" style="text-decoration:none">↗ 新分頁開啟原圖</a>
                <button class="btn-sm" onclick="closeLightbox()">✕ 關閉</button>
            </div>
        </div>
        <div class="lightbox-img-wrap">
            <img id="lightbox-img" src="" alt="試卷頁面預覽">
        </div>
    </div>
</div>

<!-- 自訂標籤管理彈窗 -->
<div id="tag-modal" class="custom-modal" onclick="if(event.target===this)closeTagModal()">
    <div class="custom-modal-content">
        <div class="custom-modal-header">
            <h3 id="tag-modal-title" style="font-size:1rem;font-weight:800;color:var(--accent);margin:0">🏷️ 管理題目自訂標籤</h3>
            <button class="btn-sm" onclick="closeTagModal()">✕ 關閉</button>
        </div>
        <div class="custom-modal-body">
            <div style="font-size:0.86rem;color:var(--text-secondary);margin-bottom:12px" id="tag-modal-qinfo"></div>
            <div style="display:flex;gap:8px;margin-bottom:14px">
                <input type="text" id="new-tag-input" placeholder="輸入自訂標籤（如：釋字748、粗心、申論佳作）" style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:7px;background:var(--bg-main);color:var(--text-primary);font-size:0.88rem" onkeydown="if(event.key==='Enter')addNewTagFromModal()">
                <button class="btn-sm btn-km" onclick="addNewTagFromModal()">➕ 加入標籤</button>
            </div>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <div style="font-size:0.82rem;font-weight:700;color:var(--text-secondary)">
                    📌 點選快速貼標 / 取消貼標（標籤池）：
                </div>
            </div>
            <div id="tag-pool-chips" style="display:flex;flex-wrap:wrap;gap:8px;min-height:40px;padding:10px;background:var(--bg-muted);border-radius:8px"></div>
        </div>
    </div>
</div>

<!-- 學習紀錄與標籤備份彈窗 -->
<div id="backup-modal" class="custom-modal" onclick="if(event.target===this)closeBackupModal()">
    <div class="custom-modal-content">
        <div class="custom-modal-header">
            <h3 style="font-size:1rem;font-weight:800;color:var(--purple);margin:0">💾 學習紀錄與標籤備份管理</h3>
            <button class="btn-sm" onclick="closeBackupModal()">✕ 關閉</button>
        </div>
        <div class="custom-modal-body">
            <p style="font-size:0.86rem;color:var(--text-secondary);margin-bottom:14px;line-height:1.5">
                您的做題狀態（📌 待學習、🔁 待複習、✅ 已掌握）與所有自訂標籤均即時儲存於此瀏覽器的 LocalStorage 中。<br>
                您可以匯出備份 JSON 檔案保存，隨時匯入跨電腦、跨瀏覽器同步！
            </p>
            <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
                <button class="btn-sm btn-km" onclick="exportUserDataJson()">📤 匯出備份 (JSON)</button>
                <label class="btn-sm" style="cursor:pointer;display:inline-flex;align-items:center;margin:0">
                    📥 匯入備份 (JSON)
                    <input type="file" id="import-user-data-file" accept=".json" style="display:none" onchange="importUserDataJson(this)">
                </label>
                <button class="btn-sm" style="color:var(--rose);border-color:var(--rose)" onclick="clearAllUserData()">🗑️ 清空所有自訂紀錄</button>
            </div>
        </div>
    </div>
</div>

<div id="toast">已複製到剪貼簿！</div>

<script id="exam-report-data" type="application/json">{JSON_PAYLOAD}</script>
<script>
const DATA = JSON.parse(document.getElementById('exam-report-data').textContent);
let currentScope = 'target'; // 'target' or 'all'
let quizHideAnswers = true;  // 預設開啟自測模式（點擊選項批改）
let filteredQuestions = [];
let currentPage = 1;
const PAGE_SIZE = 25;
const studyCart = new Set();
const STORAGE_KEY = 'exam_study_data_' + (DATA.study_subject || 'default');
let userStudyData = {
    statuses: {},  // qid -> 'none' | 'todo' | 'review' | 'done'
    tags: {},      // qid -> ['tag1', 'tag2', ...]
    allTags: []    // ['tag1', 'tag2', ...]
};
let currentTaggingQid = null;
let selectedKmTopicIds = new Set(); // 知識地圖所選考點主題
let activeKmMode = 'full';          // 'full' | 'm1_only' | 'm2_only' | 'm3_only' | 'essay_focus' | 'cart' | 'custom' | 'topic'

// 動態偵測當前檔案在使用者本機的真實絕對路徑，防止搬移資料夾後指令抓錯舊路徑
function getEffectiveReportPath() {
    try {
        if (window.location && window.location.protocol === 'file:') {
            let p = decodeURIComponent(window.location.pathname || '');
            // Windows 環境：去除前導斜線 /C:/... -> C:/...
            if (/^\/[a-zA-Z]:\//i.test(p)) {
                p = p.substring(1);
            }
            p = p.replace(/\\/g, '/');
            // 若為 .html 結尾，指向同目錄下 LLM 專用的 .md 報告（防止 AI 讀取 1.85MB HTML 截斷）
            if (p.toLowerCase().endsWith('.html')) {
                p = p.replace(/\.html$/i, '.md');
            }
            if (p) return p;
        }
    } catch (e) {
        console.warn('動態偵測檔案路徑失敗，降級使用靜態路徑', e);
    }
    return DATA.report_path || '';
}

// 動態將 PDF 路徑轉為自適應相對路徑，防止搬移資料夾後絕對路徑失效
function getEffectivePdfUrl(rawPdf) {
    if (!rawPdf) return '';
    try {
        // 從 rawPdf 提取 [科目, 檔名]（最後兩層路徑）
        const clean = decodeURIComponent(rawPdf.replace(/\\/g, '/').replace(/^file:\/\/\/?/i, ''));
        const parts = clean.split('/').filter(Boolean);
        if (parts.length >= 2) {
            const relPath = parts.slice(-2).join('/');

            // 嘗試偵測當前本機報告所在目錄
            const curReport = getEffectiveReportPath();
            if (curReport) {
                const normalized = curReport.replace(/\\/g, '/');
                const analysisIdx = normalized.indexOf('/_analysis/');
                if (analysisIdx !== -1) {
                    // 位於 _analysis/<科目>/<run_id>/ 下：向上 3 層回到題庫根目錄
                    return '../../../' + relPath;
                } else {
                    // 位於題庫根目錄下：直接以相對路徑開啟同層的官方科目資料夾
                    return './' + relPath;
                }
            }
            return './' + relPath;
        }
    } catch (e) {
        console.warn('動態重定向 PDF 路徑失敗，降級使用原始路徑', e);
    }
    return rawPdf;
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function showToast(msg) {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2600);
}

function copyKmInstallCmd(type) {
    let cmd = '';
    let msg = '';
    if (type === 'ps') {
        cmd = '$u="https://github.com/Lin-Yu-kuan/mySkill/archive/refs/heads/main.zip"; $t="$env:TEMP\\\\myskill.zip"; $d="$HOME\\\\.gemini\\\\config\\\\skills\\\\knowledge-map-learning"; if (!(Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force }; Invoke-WebRequest $u -OutFile $t; Expand-Archive $t "$env:TEMP\\\\myskill-temp" -Force; Copy-Item "$env:TEMP\\\\myskill-temp\\\\mySkill-main\\\\knowledge-map-learning\\\\*" $d -Recurse -Force; Remove-Item $t,"$env:TEMP\\\\myskill-temp" -Recurse -Force; Write-Host "✅ knowledge-map-learning 技能套件已成功安裝至 Antigravity！" -ForegroundColor Green';
        msg = '✅ 已複製 Windows (PowerShell) 雲端直裝指令！';
    } else if (type === 'sh') {
        cmd = 'mkdir -p "$HOME/.gemini/config/skills/knowledge-map-learning" && curl -sSL "https://github.com/Lin-Yu-kuan/mySkill/archive/refs/heads/main.tar.gz" | tar -xz -C "$HOME/.gemini/config/skills/knowledge-map-learning" --strip-components=2 "mySkill-main/knowledge-map-learning/" && echo "✅ knowledge-map-learning 技能套件已成功安裝！"';
        msg = '✅ 已複製 macOS / Linux 雲端直裝指令！';
    } else if (type === 'nlp') {
        cmd = '請幫我從 GitHub 倉庫 (https://github.com/Lin-Yu-kuan/mySkill) 將 knowledge-map-learning 技能套件下載並安裝到我的 Antigravity 全域技能目錄 (~/.gemini/config/skills/knowledge-map-learning)。';
        msg = '✅ 已複製 Antigravity 對話框自然語言安裝語句！';
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(cmd).then(() => showToast(msg)).catch(() => fallbackExecCopy(cmd, msg));
    } else {
        fallbackExecCopy(cmd, msg);
    }
}

function toggleKmGuide() {
    const body = document.getElementById('km-guide-body');
    const btnText = document.getElementById('km-guide-toggle-text');
    if (!body || !btnText) return;
    const isHidden = body.style.display === 'none';
    body.style.display = isHidden ? 'block' : 'none';
    btnText.textContent = isHidden ? '收合指引 ▲' : '展開指引 ▼';
}

function copyTextWithFallback(text, successMsg) {
    const ta = document.getElementById('km-prompt-textarea');
    if (ta) ta.value = text;
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            showToast(successMsg || '已複製指令！可直接貼入對話框啟動 /knowledge-map-learning');
        }).catch(() => fallbackExecCopy(text, successMsg));
    } else {
        fallbackExecCopy(text, successMsg);
    }
}

function fallbackExecCopy(text, successMsg) {
    const temp = document.createElement('textarea');
    temp.value = text;
    temp.style.position = 'fixed';
    temp.style.opacity = '0';
    document.body.appendChild(temp);
    temp.select();
    try {
        document.execCommand('copy');
        showToast(successMsg || '已複製到剪貼簿！');
    } catch (e) {
        switchView('view-km');
        showToast('請從預覽框全選（Ctrl+A）複製指令');
    }
    document.body.removeChild(temp);
}

function formatTargetLabel(t) {
    if (!t) return '未指定目標考試（顯示全題庫）';
    if (typeof t === 'string') return t;
    const parts = [];
    if (t.year) parts.push(t.year + '年');
    if (t.exam_name) parts.push(t.exam_name);
    if (t.category) parts.push(t.category);
    return parts.join(' ／ ') || JSON.stringify(t);
}

function renderMath(targetEl) {
    if (window.renderMathInElement) {
        try {
            renderMathInElement(targetEl || document.body, {
                delimiters: [
                    {left: '$$', right: '$$', display: true},
                    {left: '$', right: '$', display: false},
                    {left: '\\(', right: '\\)', display: false},
                    {left: '\\[', right: '\\]', display: true}
                ],
                macros: {
                    "\\leftouterjoin": "\\mathbin{⟕}",
                    "\\rightouterjoin": "\\mathbin{⟖}",
                    "\\fullouterjoin": "\\mathbin{⟗}",
                    "\\outerjoin": "\\mathbin{⟗}"
                },
                strict: false,
                throwOnError: false
            });
        } catch (e) {
            console.warn('KaTeX render warning:', e);
        }
    }
}

function initApp() {
    document.getElementById('sidebar-subj-title').textContent = '📊 ' + DATA.study_subject;
    document.getElementById('main-title').textContent = '國考考古題深度分析：' + DATA.study_subject;
    const stBadge = document.getElementById('status-badge');
    stBadge.textContent = DATA.status === 'complete' ? '✓ 100% 視覺核對完成' : '⚠ PARTIAL';
    stBadge.className = 'status-pill ' + (DATA.status === 'complete' ? 'status-complete' : 'status-partial');
    document.getElementById('target-exam-desc').textContent =
        '🎯 目標考試：' + formatTargetLabel(DATA.target_exam) +
        '（目標 ' + DATA.target_unique_count + ' 題 ／ 全題庫 ' + DATA.all_unique_count + ' 題）';

    document.getElementById('nav-badge-topics').textContent = DATA.topics.length;
    document.getElementById('nav-badge-qbank').textContent = DATA.questions.length;

    initFilters();
    loadUserStudyData();
    renderScopeDependentViews();
    renderFullReport();
    renderKmBridge();
    applyQuestionFilters(1);
    updateTagFilterDropdown();
    updateStudyProgressUI();
    updateCartCountUI();

    renderMath();
    setTimeout(() => renderMath(), 200);
    setTimeout(() => renderMath(), 800);

    // 主題切換
    document.getElementById('theme-toggle-btn').addEventListener('click', () => {
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        if (isDark) {
            document.documentElement.removeAttribute('data-theme');
        } else {
            document.documentElement.setAttribute('data-theme', 'dark');
        }
    });

    // 頂部閱讀進度條
    window.addEventListener('scroll', () => {
        const docH = document.documentElement.scrollHeight - window.innerHeight;
        const pct = docH > 0 ? Math.min(100, (window.scrollY / docH) * 100) : 0;
        document.getElementById('reading-progress').style.width = pct + '%';
    });
}

function setScope(scope) {
    currentScope = scope;
    document.getElementById('scope-target-btn').classList.toggle('active', scope === 'target');
    document.getElementById('scope-all-btn').classList.toggle('active', scope === 'all');
    renderScopeDependentViews();
}

function switchView(viewId) {
    document.querySelectorAll('.view-panel').forEach(p => p.classList.toggle('active', p.id === viewId));
    document.querySelectorAll('.nav-item').forEach(btn => btn.classList.toggle('active', btn.dataset.view === viewId));
    window.scrollTo({ top: 0, behavior: 'smooth' });
    renderMath();
    setTimeout(() => renderMath(), 60);
}

function renderScopeDependentViews() {
    const isTarget = currentScope === 'target';
    const totalQ = isTarget ? DATA.target_unique_count : DATA.all_unique_count;
    document.getElementById('nav-badge-total').textContent = totalQ;

    // 計算申論與測驗題數（具備二次推斷防禦，避免 unknown 破壞題型統計）
    let essayCount = 0, mcqCount = 0, unknownCount = 0;
    DATA.questions.forEach(q => {
        if (isTarget && !q.target) return;
        let t = q.type;
        if (!t || t === 'unknown') {
            if (q.opts && q.opts.length >= 2) t = 'mcq';
            else if (q.score >= 5 || /^[一二三四五六七八九十]/.test(q.q) || (q.stem && q.stem.length >= 20)) t = 'essay';
            else t = 'unknown';
        }
        if (t === 'essay') {
            essayCount++;
        } else if (t === 'mcq') {
            mcqCount++;
        } else {
            unknownCount++;
        }
    });

    // 申論與測驗佔比
    const essayPct = totalQ ? ((essayCount / totalQ) * 100).toFixed(1) : '0.0';
    const mcqPct = totalQ ? ((mcqCount / totalQ) * 100).toFixed(1) : '0.0';
    const unkPct = totalQ ? ((unknownCount / totalQ) * 100).toFixed(1) : '0.0';

    // 卡片 1：題量核心
    const card1 = `
        <div class="kpi-card">
            <div class="kpi-card-header">
                <span class="kpi-label">${isTarget ? '🎯 本科核心精選題' : '📚 全題庫總收錄'}</span>
                <span class="kpi-badge badge-blue">${isTarget ? '去重真題' : '全科目題庫'}</span>
            </div>
            <div class="kpi-card-body">
                <span class="kpi-value">${totalQ.toLocaleString()}</span>
                <span class="kpi-unit">題</span>
            </div>
            <div class="kpi-sub">
                ${isTarget 
                    ? `全題庫收錄 <strong>${DATA.all_unique_count.toLocaleString()}</strong> 題（含跨類科補充 ${(DATA.all_unique_count - DATA.target_unique_count).toLocaleString()} 題）` 
                    : `涵蓋目標核心 <strong>${DATA.target_unique_count.toLocaleString()}</strong> 題 ＋ 跨類科延伸 ${(DATA.all_unique_count - DATA.target_unique_count).toLocaleString()} 題`}
            </div>
        </div>
    `;

    // 卡片 2：題型配比（申論 vs 測驗）- 雙欄視覺 + 微比例長條
    const card2 = `
        <div class="kpi-card">
            <div class="kpi-card-header">
                <span class="kpi-label">✍️ 題型配比（申論／測驗）</span>
                <span class="kpi-badge badge-purple">${isTarget ? '核心題型' : '總覽配比'}</span>
            </div>
            <div class="kpi-split">
                <div class="kpi-split-item">
                    <span class="kpi-split-label"><span class="kpi-dot purple"></span>申論題</span>
                    <span class="kpi-split-val purple">${essayCount.toLocaleString()}<span class="kpi-unit-sm" style="font-size:0.75rem;color:var(--text-muted);font-weight:600"> 題</span></span>
                    <span class="kpi-split-pct">佔 ${essayPct}%</span>
                </div>
                <div class="kpi-split-divider"></div>
                <div class="kpi-split-item">
                    <span class="kpi-split-label"><span class="kpi-dot blue"></span>單選測驗</span>
                    <span class="kpi-split-val blue">${mcqCount.toLocaleString()}<span class="kpi-unit-sm" style="font-size:0.75rem;color:var(--text-muted);font-weight:600"> 題</span></span>
                    <span class="kpi-split-pct">佔 ${mcqPct}%</span>
                </div>
            </div>
            <div class="kpi-ratio-bar" title="申論佔 ${essayPct}% ｜ 測驗佔 ${mcqPct}%${unknownCount ? ' ｜ 待確認佔 ' + unkPct + '%' : ''}">
                <div class="ratio-segment purple" style="width: ${essayPct}%"></div>
                <div class="ratio-segment blue" style="width: ${mcqPct}%"></div>
                ${unknownCount ? `<div class="ratio-segment" style="width: ${unkPct}%; background: var(--border);"></div>` : ''}
            </div>
            <div class="kpi-sub">
                ${unknownCount > 0
                    ? `題型待確認 ${unknownCount} 題；百分比分母為目前範圍全部題目。`
                    : (essayCount > 0 && mcqCount === 0
                        ? `本範圍為純申論題庫（共 ${essayCount.toLocaleString()} 題申論，無單選測驗）。`
                        : (mcqCount > 0 && essayCount === 0
                            ? `本範圍為純測驗題庫（共 ${mcqCount.toLocaleString()} 題單選測驗，無申論題）。`
                            : `題型已完整確認；百分比分母為目前範圍全部題目。`))}
            </div>
        </div>
    `;

    document.getElementById('kpi-container').innerHTML = card1 + card2;

    // 渲染橫向長條圖
    const maxHit = Math.max(1, ...DATA.topics.map(t => isTarget ? t.target_total : t.all_total));
    document.getElementById('topic-bars-container').innerHTML = DATA.topics.map((t, idx) => {
        const tot = isTarget ? t.target_total : t.all_total;
        const es = isTarget ? t.target_essay : t.all_essay;
        const mc = isTarget ? t.target_mcq : t.all_mcq;
        const pct = totalQ ? ((tot / totalQ) * 100).toFixed(1) : '0.0';
        const esW = ((es / maxHit) * 100).toFixed(2);
        const mcW = ((mc / maxHit) * 100).toFixed(2);
        return `
        <div class="bar-row" onclick="openTopicDetail('${escapeHtml(t.id)}')">
            <div class="bar-top">
                <div class="bar-title">
                    <span class="bar-rank-badge">#${idx + 1}</span>
                    <span class="km-mod-tag" style="margin-right:6px">${escapeHtml(t.km_module)}</span>
                    ${escapeHtml(t.label)}
                </div>
                <div class="bar-meta">
                    <strong>${tot}</strong> 題（佔比 ${pct}%｜申論 ${es} / 測驗 ${mc}）
                </div>
            </div>
            <div class="bar-track">
                <div class="bar-fill-essay" style="width:${esW}%" title="申論題 ${es} 題"></div>
                <div class="bar-fill-mcq" style="width:${mcW}%" title="測驗題 ${mc} 題"></div>
            </div>
        </div>`;
    }).join('');

    // 渲染年度考試分層表
    const yearRows = isTarget ? DATA.by_year_target : DATA.by_year_all;
    document.getElementById('year-table-container').innerHTML = `
        <table class="md-table">
            <thead>
                <tr>
                    <th style="width:90px">年度</th>
                    <th>考試名稱</th>
                    <th style="width:140px;text-align:right">唯一相關題數</th>
                    <th style="width:140px;text-align:center">快速檢視</th>
                </tr>
            </thead>
            <tbody>
                ${yearRows.map(r => `
                <tr>
                    <td><strong>${escapeHtml(r.year ?? '未知')}</strong></td>
                    <td>${escapeHtml(r.exam_name ?? '未知考試')}</td>
                    <td style="text-align:right"><strong>${r.unique_questions}</strong> 題</td>
                    <td style="text-align:center">
                        <button class="btn-sm" onclick="jumpToYearExam('${escapeHtml(r.year ?? '')}', '${escapeHtml(r.exam_name ?? '')}')">🔍 查看試題</button>
                    </td>
                </tr>`).join('')}
            </tbody>
        </table>
    `;

    // 渲染 11 大主題手風琴卡片
    document.getElementById('topics-accordion-container').innerHTML = DATA.topics.map((t, idx) => {
        const tot = isTarget ? t.target_total : t.all_total;
        const es = isTarget ? t.target_essay : t.all_essay;
        const mc = isTarget ? t.target_mcq : t.all_mcq;
        return `
        <div class="topic-accordion ${idx < 2 ? 'open' : ''}" id="acc-${escapeHtml(t.id)}">
            <div class="topic-acc-header" onclick="toggleAccordion('${escapeHtml(t.id)}')">
                <div class="topic-acc-left">
                    <span class="rank-circle">${idx + 1}</span>
                    <div>
                        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
                            <span class="km-mod-tag">${escapeHtml(t.km_module)}</span>
                            <strong style="font-size:1.02rem">${escapeHtml(t.label)}</strong>
                        </div>
                        <div style="font-size:0.81rem;color:var(--text-secondary);margin-top:3px">
                            🎯 目標命中 <strong>${t.target_total}</strong> 題（申 ${t.target_essay}／測 ${t.target_mcq}）｜📚 全庫命中 <strong>${t.all_total}</strong> 題（申 ${t.all_essay}／測 ${t.all_mcq}）
                        </div>
                    </div>
                </div>
                <div class="topic-acc-actions" onclick="event.stopPropagation()">
                    <button class="btn-sm btn-km" onclick="copyTopicKmPrompt('${escapeHtml(t.id)}')">🧠 用知識地圖精讀此主題</button>
                    <button class="btn-sm" onclick="jumpToTopicQuestions('${escapeHtml(t.id)}')">🔍 查看全部 ${tot} 題</button>
                </div>
            </div>
            <div class="topic-acc-body md-content">
                ${t.html || '<p>請點擊右上角「查看全部試題」檢視本主題所有題目。</p>'}
            </div>
        </div>`;
    }).join('');
    renderMath(document.getElementById('topics-accordion-container'));
}

function toggleAccordion(tid) {
    const el = document.getElementById('acc-' + tid);
    if (el) {
        el.classList.toggle('open');
        if (el.classList.contains('open')) {
            renderMath(el);
        }
    }
}

function toggleAllTopics(open) {
    document.querySelectorAll('.topic-accordion').forEach(el => {
        el.classList.toggle('open', open);
    });
    if (open) {
        renderMath();
    }
}

function openTopicDetail(tid) {
    switchView('view-topics');
    const el = document.getElementById('acc-' + tid);
    if (el) {
        el.classList.add('open');
        setTimeout(() => el.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
    }
}

function jumpToTopicQuestions(tid) {
    document.getElementById('f-scope').value = currentScope === 'target' ? 'target' : 'all';
    document.getElementById('f-topic').value = tid;
    switchView('view-explorer');
    applyQuestionFilters(1);
}

function jumpToYearExam(year, examName) {
    document.getElementById('f-scope').value = 'all';
    document.getElementById('f-year').value = year;
    document.getElementById('f-keyword').value = examName;
    switchView('view-explorer');
    applyQuestionFilters(1);
}

function initFilters() {
    const topicSel = document.getElementById('f-topic');
    DATA.topics.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = `${t.id}｜${t.label} (${t.all_total}題)`;
        topicSel.appendChild(opt);
    });

    const years = Array.from(new Set(DATA.sources.map(s => s.year).filter(Boolean))).sort((a, b) => b - a);
    const yearSel = document.getElementById('f-year');
    years.forEach(y => {
        const opt = document.createElement('option');
        opt.value = String(y);
        opt.textContent = `${y} 年`;
        yearSel.appendChild(opt);
    });

    const subjs = Array.from(new Set(DATA.sources.map(s => s.subj).filter(Boolean))).sort();
    const subjSel = document.getElementById('f-subject');
    subjs.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s;
        opt.textContent = s;
        subjSel.appendChild(opt);
    });
}

function resetQuestionFilters() {
    document.getElementById('f-scope').value = 'all';
    document.getElementById('f-topic').value = '';
    document.getElementById('f-type').value = '';
    document.getElementById('f-year').value = '';
    document.getElementById('f-subject').value = '';
    document.getElementById('f-keyword').value = '';
    if (document.getElementById('f-status')) document.getElementById('f-status').value = 'all';
    if (document.getElementById('f-tag')) document.getElementById('f-tag').value = '';
    applyQuestionFilters(1);
}

function toggleQuizMode() {
    quizHideAnswers = !quizHideAnswers;
    const btn = document.getElementById('quiz-mode-btn');
    btn.textContent = quizHideAnswers
        ? '🙈 練習模式：隱藏標準答案（點擊選項自測）'
        : '👀 檢視模式：直接顯示全部標準答案';
    renderQuestionPage(currentPage);
}

function applyQuestionFilters(page) {
    const scope = document.getElementById('f-scope').value;
    const topic = document.getElementById('f-topic').value;
    const qtype = document.getElementById('f-type').value;
    const year = document.getElementById('f-year').value;
    const subj = document.getElementById('f-subject').value;
    const kw = document.getElementById('f-keyword').value.trim().toLowerCase();
    const stFilter = document.getElementById('f-status') ? document.getElementById('f-status').value : 'all';
    const tagFilter = document.getElementById('f-tag') ? document.getElementById('f-tag').value : '';

    filteredQuestions = DATA.questions.filter(q => {
        const src = DATA.sources[q.s] || {};
        if (scope === 'target' && !q.target) return false;
        if (scope === 'dup' && (!q.dups || q.dups.length === 0)) return false;
        if (scope === 'corrected' && !q.orig_ans && q.ans !== '#') return false;
        if (scope === 'cart' && !studyCart.has(q.id)) return false;
        if (topic && !q.t.includes(topic)) return false;
        if (qtype && q.type !== qtype) return false;
        if (year && String(src.year) !== year) return false;
        if (subj && src.subj !== subj) return false;

        // 學習狀態篩選
        const curSt = userStudyData.statuses[q.id] || 'none';
        if (stFilter === 'todo' && curSt !== 'todo') return false;
        if (stFilter === 'review' && curSt !== 'review') return false;
        if (stFilter === 'done' && curSt !== 'done') return false;
        if (stFilter === 'cart' && curSt !== 'todo' && curSt !== 'review') return false;
        if (stFilter === 'none' && curSt !== 'none') return false;

        // 自訂標籤篩選
        if (tagFilter) {
            const qTags = userStudyData.tags[q.id] || [];
            if (!qTags.includes(tagFilter)) return false;
        }

        if (kw) {
            const tagStr = (userStudyData.tags[q.id] || []).join(' ');
            const hay = (q.stem + ' ' + (q.opts || []).join(' ') + ' ' + src.exam + ' ' + src.subj + ' ' + q.id + ' ' + tagStr).toLowerCase();
            if (!hay.includes(kw)) return false;
        }
        return true;
    });

    renderQuestionPage(page || 1);
}

function renderQuestionPage(page) {
    currentPage = page;
    const total = filteredQuestions.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;

    const startIdx = (currentPage - 1) * PAGE_SIZE;
    const pageItems = filteredQuestions.slice(startIdx, startIdx + PAGE_SIZE);

    document.getElementById('q-result-summary').textContent =
        `共篩選出 ${total.toLocaleString()} 道題目（目前顯示第 ${total ? startIdx + 1 : 0}～${startIdx + pageItems.length} 題，第 ${currentPage} / ${totalPages} 頁）`;

    const letters = ['A', 'B', 'C', 'D', 'E'];
    const listHtml = pageItems.map(q => {
        const src = DATA.sources[q.s] || {};
        const curStatus = userStudyData.statuses[q.id] || 'none';
        const optsHtml = (q.opts && q.opts.length)
            ? `<div class="q-options">${q.opts.map((optText, idx) => {
                const ltr = letters[idx] || String(idx + 1);
                const isAns = !quizHideAnswers && (q.ans === ltr || q.ans === '#');
                return `
                <button class="q-opt-btn ${isAns ? 'correct' : ''}" ${q.quiz_ready ? '' : 'disabled'} onclick="checkMcqOption(this, '${escapeHtml(q.id)}', '${ltr}', '${escapeHtml(q.ans)}')">
                    <span class="q-opt-letter">(${ltr})</span>
                    <span>${escapeHtml(optText)}</span>
                </button>`;
            }).join('')}</div>`
            : '';

        const ansHtml = q.type === 'mcq' && q.ans
            ? `<span class="ans-box ${quizHideAnswers ? 'ans-hidden' : ''}" onclick="this.classList.remove('ans-hidden');this.textContent='✓ 考選部答案：${escapeHtml(q.ans)}${q.orig_ans ? '（原公告 ' + escapeHtml(q.orig_ans) + '，更正/送分）' : ''}'">
                ${quizHideAnswers ? '👁️ 點擊顯示標準答案' : `✓ 考選部答案：${escapeHtml(q.ans)}${q.orig_ans ? '（原公告 ' + escapeHtml(q.orig_ans) + '）' : ''}`}
               </span>`
            : `<span style="color:var(--text-muted)">申論題（配分 ${q.score || 25} 分）</span>`;

        const effectivePdf = getEffectivePdfUrl(src.pdf);

        return `
        <div class="q-card" id="qcard-${escapeHtml(q.id)}">
            <div class="q-card-header">
                <div class="q-badges">
                    <span class="q-badge ${q.target ? 'q-badge-target' : ''}">${escapeHtml(src.year || '')}年 ${escapeHtml(src.exam)}</span>
                    <span class="q-badge">《${escapeHtml(src.subj)}》</span>
                    <span class="q-badge ${q.type === 'essay' ? 'q-badge-essay' : 'q-badge-mcq'}">
                        ${q.type === 'essay' ? '申論題' : q.type === 'mcq' ? '測驗題' : '題型待確認'} #${escapeHtml(q.q)}
                    </span>
                    ${q.t.map(tid => `<span class="bar-code" style="cursor:pointer" onclick="jumpToTopicQuestions('${escapeHtml(tid)}')">${escapeHtml(tid)}</span>`).join('')}
                    ${q.dups && q.dups.length ? `<span class="q-badge q-badge-dup">🔁 跨卷重複/相似 (${q.dups.length})</span>` : ''}
                </div>
                <div class="q-actions" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                    <select class="status-select st-${curStatus}" onchange="setQuestionStatus('${escapeHtml(q.id)}', this.value)" title="設定學習狀態">
                        <option value="none" ${curStatus === 'none' ? 'selected' : ''}>⚪ 未標記</option>
                        <option value="todo" ${curStatus === 'todo' ? 'selected' : ''}>📌 待學習</option>
                        <option value="review" ${curStatus === 'review' ? 'selected' : ''}>🔁 待複習</option>
                        <option value="done" ${curStatus === 'done' ? 'selected' : ''}>✅ 已掌握</option>
                    </select>
                    <button class="btn-sm btn-tag-add" onclick="openTagModal('${escapeHtml(q.id)}')" title="新增或管理此題自訂標籤">🏷️ 標籤</button>
                </div>
            </div>
            <div class="q-tags-bar" id="qtags-${escapeHtml(q.id)}">
                ${renderQuestionTagsHtml(q.id)}
            </div>
            <div class="q-stem">${escapeHtml(q.stem || '未保存題幹，請開啟原卷查閱。')}</div>
            ${q.quiz_ready ? '' : '<div class="q-stem">來源查閱模式：尚未確認完整作答資料，不提供自動批改。</div>'}
            ${optsHtml}
            <div class="q-footer">
                <div>${ansHtml}</div>
                <div style="display:flex;gap:6px;flex-wrap:wrap">
                    ${effectivePdf ? `<a class="btn-sm" href="${escapeHtml(effectivePdf + (q.p && q.p[0] ? ('#page=' + q.p[0]) : ''))}" target="_blank" rel="noopener noreferrer" style="text-decoration:none">📄 開啟原卷 PDF</a>` : ''}
                </div>
            </div>
        </div>`;
    }).join('');

    const qListContainer = document.getElementById('questions-list-container');
    qListContainer.innerHTML = listHtml || '<div class="section-card">找不到符合篩選條件的題目。</div>';
    renderMath(qListContainer);

    // 分頁按鈕
    const pagEl = document.getElementById('pagination-container');
    if (totalPages <= 1) {
        pagEl.innerHTML = '';
        return;
    }
    let pagHtml = '';
    const maxButtons = 9;
    let startP = Math.max(1, currentPage - 4);
    let endP = Math.min(totalPages, startP + maxButtons - 1);
    if (endP - startP < maxButtons - 1) startP = Math.max(1, endP - maxButtons + 1);

    if (currentPage > 1) {
        pagHtml += `<button class="btn-sm" onclick="renderQuestionPage(${currentPage - 1});window.scrollTo({top:200,behavior:'smooth'})">‹ 上一頁</button>`;
    }
    for (let p = startP; p <= endP; p++) {
        pagHtml += `<button class="btn-sm ${p === currentPage ? 'btn-km' : ''}" onclick="renderQuestionPage(${p});window.scrollTo({top:200,behavior:'smooth'})">${p}</button>`;
    }
    if (currentPage < totalPages) {
        pagHtml += `<button class="btn-sm" onclick="renderQuestionPage(${currentPage + 1});window.scrollTo({top:200,behavior:'smooth'})">下一頁 ›</button>`;
    }
    pagEl.innerHTML = pagHtml;
}

function checkMcqOption(btnEl, qid, chosenLtr, officialAns) {
    const parent = btnEl.parentElement;
    const allBtns = parent.querySelectorAll('.q-opt-btn');
    allBtns.forEach((b, idx) => {
        const ltr = ['A', 'B', 'C', 'D', 'E'][idx];
        b.classList.remove('correct', 'wrong');
        if (officialAns === ltr || officialAns === '#') {
            b.classList.add('correct');
        }
    });
    if (officialAns && chosenLtr !== officialAns && officialAns !== '#') {
        btnEl.classList.add('wrong');
    }
    const card = document.getElementById('qcard-' + qid);
    const ansBox = card ? card.querySelector('.ans-box') : null;
    if (ansBox) {
        ansBox.classList.remove('ans-hidden');
        ansBox.textContent = `✓ 考選部標準答案：${officialAns}`;
    }
}

function loadUserStudyData() {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) {
            const parsed = JSON.parse(raw);
            userStudyData = {
                statuses: parsed.statuses || {},
                tags: parsed.tags || {},
                allTags: Array.isArray(parsed.allTags) ? parsed.allTags : []
            };
        }
    } catch(e) {
        console.warn('載入本地學習狀態失敗', e);
    }
    syncStudyCartFromData();
}

function saveUserStudyData() {
    try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(userStudyData));
    } catch(e) {
        console.warn('儲存本地學習狀態失敗', e);
    }
    syncStudyCartFromData();
    updateCartCountUI();
    updateStudyProgressUI();
    updateTagFilterDropdown();
}

function syncStudyCartFromData() {
    studyCart.clear();
    for (const [qid, st] of Object.entries(userStudyData.statuses)) {
        if (st === 'todo' || st === 'review') {
            studyCart.add(qid);
        }
    }
}

function setQuestionStatus(qid, statusVal) {
    if (!statusVal || statusVal === 'none') {
        delete userStudyData.statuses[qid];
    } else {
        userStudyData.statuses[qid] = statusVal;
    }
    saveUserStudyData();

    // 更新當前卡片 select 樣式
    const card = document.getElementById('qcard-' + qid);
    if (card) {
        const sel = card.querySelector('.status-select');
        if (sel) {
            sel.value = statusVal || 'none';
            sel.className = `status-select st-${statusVal || 'none'}`;
        }
    }

    // 若處在狀態篩選中，重新應用過濾
    const curStatusFilter = document.getElementById('f-status') ? document.getElementById('f-status').value : 'all';
    if (curStatusFilter !== 'all') {
        applyQuestionFilters(currentPage);
    }
}

function toggleStudyCart(qid, btnEl) {
    const cur = userStudyData.statuses[qid] || 'none';
    const next = (cur === 'todo' || cur === 'review') ? 'none' : 'todo';
    setQuestionStatus(qid, next);
}

function updateCartCountUI() {
    const todoCount = Object.values(userStudyData.statuses).filter(s => s === 'todo').length;
    const reviewCount = Object.values(userStudyData.statuses).filter(s => s === 'review').length;
    const totalCount = todoCount + reviewCount;

    const fabCart = document.getElementById('fab-cart-count');
    if (fabCart) fabCart.textContent = todoCount;
    const fabRev = document.getElementById('fab-review-count');
    if (fabRev) fabRev.textContent = reviewCount;
    const kmCart = document.getElementById('km-cart-count');
    if (kmCart) kmCart.textContent = totalCount;
    const fabEl = document.getElementById('study-cart-fab');
    if (fabEl) fabEl.classList.toggle('visible', totalCount > 0);
}

function updateStudyProgressUI() {
    const todoCount = Object.values(userStudyData.statuses).filter(s => s === 'todo').length;
    const reviewCount = Object.values(userStudyData.statuses).filter(s => s === 'review').length;
    const doneCount = Object.values(userStudyData.statuses).filter(s => s === 'done').length;
    const totalQuestions = DATA.questions.length;
    const pct = totalQuestions > 0 ? Math.round((doneCount / totalQuestions) * 100) : 0;

    const pill = document.getElementById('study-progress-pill');
    if (pill) {
        pill.innerHTML = `進度：<span style="color:var(--emerald);font-weight:800">✅ 已掌握 ${doneCount}</span> ｜ <span style="color:var(--purple);font-weight:800">📌 待學 ${todoCount}</span> ｜ <span style="color:var(--amber);font-weight:800">🔁 複習 ${reviewCount}</span> <span style="color:var(--text-muted);font-size:0.75rem">(${pct}%)</span>`;
    }
}

function openTagModal(qid) {
    currentTaggingQid = qid;
    const q = DATA.questions.find(x => x.id === qid);
    const src = q ? (DATA.sources[q.s] || {}) : {};
    const titleEl = document.getElementById('tag-modal-title');
    if (titleEl) titleEl.textContent = `🏷️ 管理題目自訂標籤`;
    const infoEl = document.getElementById('tag-modal-qinfo');
    if (infoEl) {
        infoEl.textContent = q ? `【${src.year || ''}年 ${src.exam || ''}《${src.subj || ''}》${q.type === 'essay' ? '申論' : q.type === 'mcq' ? '測驗' : '題型待確認'} #${q.q}】${q.stem.substring(0, 40)}…` : '';
    }
    const inp = document.getElementById('new-tag-input');
    if (inp) inp.value = '';
    renderTagPoolChips();
    const modal = document.getElementById('tag-modal');
    if (modal) modal.classList.add('open');
    if (inp) setTimeout(() => inp.focus(), 100);
}

function closeTagModal() {
    currentTaggingQid = null;
    const modal = document.getElementById('tag-modal');
    if (modal) modal.classList.remove('open');
}

function renderTagPoolChips() {
    const container = document.getElementById('tag-pool-chips');
    if (!container) return;
    container.replaceChildren();
    const tags = userStudyData.allTags || [];
    if (!tags.length) {
        container.textContent = '尚未建立任何標籤。請在上方輸入框加入標籤。';
        return;
    }
    const qTags = (currentTaggingQid && userStudyData.tags[currentTaggingQid]) || [];
    tags.forEach(tag => {
        const count = Object.values(userStudyData.tags || {}).filter(arr => Array.isArray(arr) && arr.includes(tag)).length;
        const chip = document.createElement('span');
        chip.className = 'tag-pool-item' + (qTags.includes(tag) ? ' active' : '');
        const toggle = document.createElement('button');
        toggle.type = 'button';
        toggle.className = 'btn-sm';
        toggle.style.cssText = 'border:0;background:transparent;color:inherit';
        toggle.textContent = (qTags.includes(tag) ? '✓ ' : '+ ') + '#' + tag + ' (' + count + ')';
        toggle.onclick = () => toggleTagOnCurrentQuestion(tag);
        chip.appendChild(toggle);
        if (count === 0) {
            const remove = document.createElement('button');
            remove.type = 'button';
            remove.className = 'btn-sm';
            remove.textContent = '×';
            remove.title = '刪除此未使用標籤';
            remove.setAttribute('aria-label', '刪除未使用標籤 #' + tag);
            remove.onclick = event => { event.stopPropagation(); deleteUnusedPoolTag(tag); };
            chip.appendChild(remove);
        }
        container.appendChild(chip);
    });
}

function addNewTagFromModal() {
    const inp = document.getElementById('new-tag-input');
    if (!inp) return;
    const val = inp.value.trim().replace(/^#+/, '');
    if (!val) return;
    if (!userStudyData.allTags.includes(val)) {
        userStudyData.allTags.push(val);
    }
    if (currentTaggingQid) {
        if (!userStudyData.tags[currentTaggingQid]) {
            userStudyData.tags[currentTaggingQid] = [];
        }
        if (!userStudyData.tags[currentTaggingQid].includes(val)) {
            userStudyData.tags[currentTaggingQid].push(val);
        }
        // 更新題卡上 tags
        const bar = document.getElementById('qtags-' + currentTaggingQid);
        if (bar) bar.innerHTML = renderQuestionTagsHtml(currentTaggingQid);
    }
    inp.value = '';
    saveUserStudyData();
    renderTagPoolChips();
    updateTagFilterDropdown();
    showToast(`已建立並貼上標籤：#${val}`);
}

function toggleTagOnCurrentQuestion(tag) {
    if (!currentTaggingQid) return;
    if (!userStudyData.tags[currentTaggingQid]) {
        userStudyData.tags[currentTaggingQid] = [];
    }
    const idx = userStudyData.tags[currentTaggingQid].indexOf(tag);
    if (idx !== -1) {
        userStudyData.tags[currentTaggingQid].splice(idx, 1);
    } else {
        userStudyData.tags[currentTaggingQid].push(tag);
    }
    saveUserStudyData();
    renderTagPoolChips();
    updateTagFilterDropdown();
    const bar = document.getElementById('qtags-' + currentTaggingQid);
    if (bar) bar.innerHTML = renderQuestionTagsHtml(currentTaggingQid);
}

function removeTagFromQuestion(qid, tag) {
    if (userStudyData.tags[qid]) {
        const idx = userStudyData.tags[qid].indexOf(tag);
        if (idx !== -1) {
            userStudyData.tags[qid].splice(idx, 1);
            saveUserStudyData();
            const bar = document.getElementById('qtags-' + qid);
            if (bar) bar.innerHTML = renderQuestionTagsHtml(qid);
            renderTagPoolChips();
            updateTagFilterDropdown();
        }
    }
}

function deleteTagFromPool(tag, ev) {
    if (ev) ev.stopPropagation();
    const cnt = Object.values(userStudyData.tags || {}).filter(arr => (arr || []).includes(tag)).length;
    if (cnt > 0) {
        if (!confirm(`標籤「#${tag}」目前有 ${cnt} 道題目正在引用。\n確定要將其自所有題目與標籤池中徹底刪除嗎？`)) {
            return;
        }
        // 從所有題目移除
        Object.keys(userStudyData.tags).forEach(qid => {
            const arr = userStudyData.tags[qid];
            if (arr && arr.includes(tag)) {
                userStudyData.tags[qid] = arr.filter(t => t !== tag);
                const bar = document.getElementById('qtags-' + qid);
                if (bar) bar.innerHTML = renderQuestionTagsHtml(qid);
            }
        });
    }
    // 從 allTags 移除
    userStudyData.allTags = (userStudyData.allTags || []).filter(t => t !== tag);
    saveUserStudyData();

    // 如果目前下拉篩選選取的是被刪除的標籤，重設回全部
    const sel = document.getElementById('f-tag');
    if (sel && sel.value === tag) {
        sel.value = '';
        if (typeof applyQuestionFilters === 'function') {
            applyQuestionFilters(1);
        }
    }
    updateTagFilterDropdown();
    renderTagPoolChips();
    showToast(`已從標籤池移除標籤：#${tag}`);
}

function cleanupUnusedTags() {
    const all = userStudyData.allTags || [];
    const unused = all.filter(t => {
        const cnt = Object.values(userStudyData.tags || {}).filter(arr => (arr || []).includes(t)).length;
        return cnt === 0;
    });
    if (unused.length === 0) {
        showToast('目前沒有未使用的 0 引用標籤');
        return;
    }
    userStudyData.allTags = all.filter(t => !unused.includes(t));
    saveUserStudyData();
    updateTagFilterDropdown();
    renderTagPoolChips();
    showToast(`已清理 ${unused.length} 個未使用的標籤！`);
}

function deleteUnusedPoolTag(tag) {
    if (!(userStudyData.allTags || []).includes(tag)) return;
    const used = Object.values(userStudyData.tags || {}).some(tags => Array.isArray(tags) && tags.includes(tag));
    if (used) { showToast('此標籤仍有題目使用，無法刪除'); return; }
    userStudyData.allTags = userStudyData.allTags.filter(t => t !== tag);
    saveUserStudyData();
    const sel = document.getElementById('f-tag');
    if (sel && sel.value === tag) sel.value = '';
    updateTagFilterDropdown();
    renderTagPoolChips();
    applyQuestionFilters(1);
    showToast('已刪除未使用標籤：#' + tag);
}

function filterByTag(tag) {
    const sel = document.getElementById('f-tag');
    if (sel) {
        sel.value = tag;
        switchView('view-explorer');
        applyQuestionFilters(1);
    }
}

function updateTagFilterDropdown() {
    const sel = document.getElementById('f-tag');
    if (!sel) return;
    const curVal = sel.value;
    const tags = userStudyData.allTags || [];
    let opts = '<option value="">全部標籤</option>';
    tags.forEach(t => {
        const cnt = Object.values(userStudyData.tags).filter(arr => (arr || []).includes(t)).length;
        opts += `<option value="${escapeHtml(t)}">#${escapeHtml(t)} (${cnt})</option>`;
    });
    sel.innerHTML = opts;
    if (tags.includes(curVal)) {
        sel.value = curVal;
    }
}

function renderQuestionTagsHtml(qid) {
    const tags = userStudyData.tags[qid] || [];
    if (!tags.length) return '';
    return tags.map(t => `
        <span class="q-tag-chip">
            <span class="tag-name" onclick="filterByTag('${escapeHtml(t)}')">#${escapeHtml(t)}</span>
            <span class="tag-del" onclick="removeTagFromQuestion('${escapeHtml(qid)}', '${escapeHtml(t)}')" title="移除此標籤">&times;</span>
        </span>
    `).join('');
}

// 備份管理
function openBackupModal() {
    const modal = document.getElementById('backup-modal');
    if (modal) modal.classList.add('open');
}

function closeBackupModal() {
    const modal = document.getElementById('backup-modal');
    if (modal) modal.classList.remove('open');
}

function exportUserDataJson() {
    const exportData = {
        study_subject: DATA.study_subject,
        export_time: new Date().toISOString(),
        statuses: userStudyData.statuses,
        tags: userStudyData.tags,
        allTags: userStudyData.allTags
    };
    const jsonStr = JSON.stringify(exportData, null, 2);
    const blob = new Blob([jsonStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${DATA.study_subject}_學習紀錄與標籤備份_${new Date().toISOString().slice(0,10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('已匯出學習紀錄與標籤 JSON 備份檔！');
}

function importUserDataJson(inputEl) {
    const file = inputEl.files && inputEl.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const data = JSON.parse(e.target.result);
            if (data && (data.statuses || data.tags || data.allTags)) {
                userStudyData.statuses = Object.assign({}, userStudyData.statuses, data.statuses || {});
                userStudyData.tags = Object.assign({}, userStudyData.tags, data.tags || {});
                const combinedTags = new Set([...(userStudyData.allTags || []), ...(data.allTags || [])]);
                userStudyData.allTags = Array.from(combinedTags);
                saveUserStudyData();
                renderQuestionPage(currentPage);
                closeBackupModal();
                showToast('✅ 已成功匯入學習紀錄與標籤！');
            } else {
                alert('匯入的 JSON 格式不符合規範');
            }
        } catch(err) {
            alert('解析 JSON 備份檔失敗：' + err.message);
        }
        inputEl.value = '';
    };
    reader.readAsText(file);
}

function clearAllUserData() {
    if (confirm('確定要清空本科目的所有做題狀態與自訂標籤嗎？此操作無法還原。')) {
        userStudyData = { statuses: {}, tags: {}, allTags: [] };
        saveUserStudyData();
        renderQuestionPage(currentPage);
        closeBackupModal();
        showToast('已清空本科目的所有學習狀態與標籤');
    }
}

function openCartKmView() {
    switchView('view-km');
    selectKmMode('cart');
}

function renderFullReport() {
    const htmlStr = (DATA.report_sections || []).map(sec => `
        <article id="${escapeHtml(sec.id)}" style="margin-bottom:28px">
            ${sec.html}
        </article>
    `).join('');
    document.getElementById('full-report-container').innerHTML = htmlStr;
    renderMath(document.getElementById('full-report-container'));
}

function renderKmBridge() {
    if (selectedKmTopicIds.size === 0 && activeKmMode === 'full') {
        selectedKmTopicIds = new Set(DATA.topics.map(t => t.id));
    }
    const mods = ['M1', 'M2', 'M3'];
    const modTitles = {
        'M1': '🥇 M1 核心奪分模組（高投報率必考法制）',
        'M2': '🥈 M2 進階拉分模組（實務運作與治理監督）',
        'M3': '🥉 M3 學理與防禦模組（基礎理論與跨域比較）'
    };

    document.getElementById('km-modules-preview').innerHTML = mods.map(m => {
        const items = DATA.topics.filter(t => t.km_module === m);
        const allSelectedInMod = items.every(t => selectedKmTopicIds.has(t.id));
        const someSelectedInMod = items.some(t => selectedKmTopicIds.has(t.id));
        const selectedCount = items.filter(t => selectedKmTopicIds.has(t.id)).length;
        return `
        <div class="km-mod-card ${someSelectedInMod ? 'has-selected' : ''}" id="km-card-${m}">
            <div class="km-mod-header">
                <div>
                    <h3 class="km-mod-title">${modTitles[m]}</h3>
                    <div style="font-size:0.75rem;color:var(--text-secondary);margin-top:2px">
                        已選 ${selectedCount} / ${items.length} 主題
                    </div>
                </div>
                <button class="btn-sm" style="font-size:0.75rem;padding:3px 8px" onclick="toggleKmModuleTopics('${m}')">
                    ${allSelectedInMod ? '取消此模組' : '全選此模組'}
                </button>
            </div>
            <ul class="km-topic-list">
                ${items.map(t => {
                    const isChecked = selectedKmTopicIds.has(t.id);
                    return `
                    <li class="km-topic-item ${isChecked ? 'selected' : ''}" id="km-item-${escapeHtml(t.id)}">
                        <div class="km-topic-row">
                            <input type="checkbox" class="km-topic-cb" id="cb-km-${escapeHtml(t.id)}"
                                   ${isChecked ? 'checked' : ''}
                                   onchange="toggleKmTopicSingle('${escapeHtml(t.id)}', this.checked)">
                            <div style="flex:1">
                                <label for="cb-km-${escapeHtml(t.id)}" style="cursor:pointer;font-weight:700">
                                    <code>${escapeHtml(t.id)}</code> ${escapeHtml(t.label)}
                                </label>
                                <div style="font-size:0.78rem;color:var(--text-secondary);margin-top:2px;display:flex;justify-content:space-between;align-items:center">
                                    <span>🎯 目標 ${t.target_total} 題（申 ${t.target_essay}／測 ${t.target_mcq}）</span>
                                    <button class="btn-sm" style="padding:1px 6px;font-size:0.72rem" onclick="copyTopicKmPrompt('${escapeHtml(t.id)}')">單獨精讀</button>
                                </div>
                            </div>
                        </div>
                    </li>`;
                }).join('')}
            </ul>
        </div>`;
    }).join('');

    updateKmPromptUI();
}

function selectKmMode(mode) {
    activeKmMode = mode;
    // 切換按鈕 active 樣式
    document.querySelectorAll('.km-mode-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.kmMode === mode);
    });

    if (mode === 'full') {
        selectedKmTopicIds = new Set(DATA.topics.map(t => t.id));
    } else if (mode === 'm1_only') {
        selectedKmTopicIds = new Set(DATA.topics.filter(t => t.km_module === 'M1').map(t => t.id));
    } else if (mode === 'm2_only') {
        selectedKmTopicIds = new Set(DATA.topics.filter(t => t.km_module === 'M2').map(t => t.id));
    } else if (mode === 'm3_only') {
        selectedKmTopicIds = new Set(DATA.topics.filter(t => t.km_module === 'M3').map(t => t.id));
    } else if (mode === 'essay_focus') {
        // 篩選有申論題的主題，或申論題數 >= 3 的關鍵主題
        selectedKmTopicIds = new Set(DATA.topics.filter(t => t.target_essay > 0 || t.all_essay >= 5).map(t => t.id));
    } else if (mode === 'cart') {
        // 待學錯題微課程不以主題為限
    }

    renderKmBridge();
}

function toggleKmTopicSingle(tid, isChecked) {
    if (isChecked) {
        selectedKmTopicIds.add(tid);
    } else {
        selectedKmTopicIds.delete(tid);
    }
    activeKmMode = 'custom';
    document.querySelectorAll('.km-mode-btn').forEach(btn => btn.classList.remove('active'));
    renderKmBridge();
}

function toggleKmModuleTopics(m) {
    const modItems = DATA.topics.filter(t => t.km_module === m);
    const allSelected = modItems.every(t => selectedKmTopicIds.has(t.id));
    if (allSelected) {
        modItems.forEach(t => selectedKmTopicIds.delete(t.id));
    } else {
        modItems.forEach(t => selectedKmTopicIds.add(t.id));
    }
    activeKmMode = 'custom';
    document.querySelectorAll('.km-mode-btn').forEach(btn => btn.classList.remove('active'));
    renderKmBridge();
}

function addHighFreqCartQuestions() {
    // 從目標考試挑選具有代表性之高頻錯題／重複題 5 題加入待學清單
    const candidates = DATA.questions.filter(q => q.target && (q.dups && q.dups.length > 0 || q.orig_ans || q.type === 'essay'));
    const toAdd = candidates.slice(0, 5);
    toAdd.forEach(q => {
        userStudyData.statuses[q.id] = 'todo';
    });
    saveUserStudyData();
    showToast(`已自動將 ${toAdd.length} 道高頻精選代表題設為「📌 待學習」！`);
    selectKmMode('cart');
}

function buildFullKmPromptText(topicList, customTitleNote) {
    const targetDesc = formatTargetLabel(DATA.target_exam);
    const modGroups = {};
    topicList.forEach(t => {
        if (!modGroups[t.km_module]) modGroups[t.km_module] = [];
        modGroups[t.km_module].push(t);
    });

    const outlineLines = [];
    Object.keys(modGroups).forEach(m => {
        const mName = modGroups[m][0].km_module_name;
        outlineLines.push(`- **${mName}**`);
        modGroups[m].forEach((t) => {
            outlineLines.push(`  - 主題方向 \`${t.id}\`（目標考試命中 ${t.target_total} 題／全庫 ${t.all_total} 題）：${t.label}`);
            if (t.laws) outlineLines.push(`    - 核心法規與釋字：${t.laws}`);
            if (t.subtopics) outlineLines.push(`    - 涵蓋子主題（請依寬節點拆分原則細拆為獨立 M 節點）：${t.subtopics}`);
        });
    });

    const essayInstruction = (activeKmMode === 'essay_focus')
        ? '\n3. **申論題攻堅導向（特化要求）**：\n   - 請針對所列主題之各節點，特別加強「申論題三段論法（前言法理爭點／主文法條與大法官判決涵攝／結語制度革新評析）」的結構性論述訓練。\n   - 思考題直接改編為國家考試高分申論擬答框架思考題。'
        : '';

    return `/knowledge-map-learning 我想以國考「${DATA.study_subject}」（目標考試：${targetDesc}）為主題進行由上而下的系統化精讀${customTitleNote || ''}。

請直接參考這份已完成的考古題深度分析報告：@[${getEffectiveReportPath()}]

### 課程規劃與認識論對接要求：
1. **依據國考 ROI 權重與寬節點拆分原則建立大綱**：
   下方為本次實證分析的考點權重結構。請將高題數的大主題（如包含多個獨立制度的子主題）自動拆分為粒度適中、可獨立推理的 \`M<模組>-<節點>\` 葉節點，並在第一回合直接輸出結構化大綱且**同回合無縫展開 M1-1 深度講義與思考題**！
2. **四層認識論結合國考實證素材**：
   - **核心問題與制度痛點**：剖析該制度或法條要解決的治理困境。
   - **直覺心智模型**：用具象化架構建立法制與學理直覺。
   - **機制／法條／釋字證據**：直接扣合報告中的必背法條與大法官解釋／憲法法庭判決。
   - **適用條件與命題陷阱邊界**：點破考生最常混淆的法條比較（如核定 vs 備查、覆議 vs 復議、罰則上限等）。
   - **遷移式開放思考題**：以歷屆高鑑別度真題情境改編為課後思考題。${essayInstruction}

### 實證考點權重藍圖（已選 ${topicList.length} 個主題）：
${outlineLines.join('\n')}`;
}

function updateKmPromptUI() {
    let title = '';
    let statusDesc = '';
    let text = '';
    const selectedTopics = DATA.topics.filter(t => selectedKmTopicIds.has(t.id));

    if (activeKmMode === 'full') {
        title = '📋 模式 A：全科系統化知識地圖啟動指令（M1～M3 全選）';
        statusDesc = `目前模式：🚀 模式 A（全選 ${selectedTopics.length} 個主題）`;
        text = buildFullKmPromptText(selectedTopics, '');
    } else if (activeKmMode === 'm1_only') {
        title = '📋 模式 B：高投報率衝刺班啟動指令（專攻 M1 核心奪分模組）';
        statusDesc = `目前模式：⚡ 模式 B（專攻 M1 核心模組，共 ${selectedTopics.length} 個主題）`;
        text = buildFullKmPromptText(selectedTopics, '（專攻高投報率之 M1 核心奪分模組）');
    } else if (activeKmMode === 'm2_only') {
        title = '📋 模式 C：進階拉分專班啟動指令（專攻 M2 實務運作與治理監督）';
        statusDesc = `目前模式：🥈 模式 C（專攻 M2 治理實務，共 ${selectedTopics.length} 個主題）`;
        text = buildFullKmPromptText(selectedTopics, '（專攻 M2 進階拉分模組）');
    } else if (activeKmMode === 'm3_only') {
        title = '📋 模式 D：學理防禦專班啟動指令（專攻 M3 基礎理論與跨域比較）';
        statusDesc = `目前模式：🥉 模式 D（專攻 M3 學理防禦，共 ${selectedTopics.length} 個主題）`;
        text = buildFullKmPromptText(selectedTopics, '（專攻 M3 學理與防禦模組）');
    } else if (activeKmMode === 'essay_focus') {
        title = '📋 模式 E：申論題攻堅專班啟動指令（高頻申論考點強化論述）';
        statusDesc = `目前模式：✍️ 模式 E（高頻申論主題，共 ${selectedTopics.length} 個主題）`;
        text = buildFullKmPromptText(selectedTopics, '（重點攻堅申論題型與法理論述框架）');
    } else if (activeKmMode === 'cart') {
        const cartQs = DATA.questions.filter(q => studyCart.has(q.id));
        const todoCount = cartQs.filter(q => (userStudyData.statuses[q.id] || 'todo') === 'todo').length;
        const reviewCount = cartQs.filter(q => userStudyData.statuses[q.id] === 'review').length;
        title = `📋 模式 F：待學與弱點微課程指令（已選 ${cartQs.length} 題）`;
        statusDesc = `目前模式：🎯 模式 F（弱點微課程：📌 待學 ${todoCount} 題 ｜ 🔁 待複習 ${reviewCount} 題）`;
        if (cartQs.length === 0) {
            text = '（目前弱點清單為空。請先前往「🔍 題庫檢索與刷題器」將題目設定為「📌 待學習」或「🔁 待複習」，或點擊上方「⚡ 一鍵帶入精選代表題」！）';
            statusDesc += ' — 尚未選題，可點擊上方按鈕一鍵帶入精選題目';
        } else {
            const qLines = cartQs.map((q, idx) => {
                const src = DATA.sources[q.s] || {};
                const optsStr = (q.opts && q.opts.length)
                    ? '\n   選項：' + q.opts.map((o, i) => `(${['A','B','C','D'][i]}) ${o}`).join(' ／ ')
                    : '';
                const st = userStudyData.statuses[q.id] || 'todo';
                const stLabel = st === 'review' ? '【🔁 待複習】' : '【📌 待學習】';
                const qTags = (userStudyData.tags[q.id] || []).map(t => '#' + t).join(' ');
                const tagLabel = qTags ? `【自訂標籤：${qTags}】` : '';
                return `${idx + 1}. ${stLabel}${tagLabel}【${src.year}年 ${src.exam}《${src.subj}》題 ${q.q}｜主題：${q.t.join(', ')}】\n   題幹：${q.stem}${optsStr}\n   考選部標準答案：${q.ans || '申論題'}`;
            }).join('\n\n');
            text = `/knowledge-map-learning 我在練習「${DATA.study_subject}」歷屆考古題時，標註了以下 ${cartQs.length} 道弱點題目（含待學習與待複習考點）。請為這組弱點考點規劃一個精準的知識地圖微課程（將核心觀念拆解為 M1-1, M1-2... 節點），針對標註【待學習】的題目進行從零扎實精講，對標註【待複習】的題目著重剖析易混淆陷阱與法理邊界，並在第一回合直接給出脈絡大綱且無縫展開 M1-1 深度精講！\n\n參考分析報告：@[${getEffectiveReportPath()}]\n\n### 我的待學與弱點題目清單：\n${qLines}`;
        }
    } else if (activeKmMode === 'custom') {
        title = `📋 自訂知識地圖組合啟動指令（已選 ${selectedTopics.length} 個主題）`;
        statusDesc = `目前模式：🎛️ 自訂主題組合（已選 ${selectedTopics.length} / ${DATA.topics.length} 個主題）`;
        if (selectedTopics.length === 0) {
            text = '（請在上方模組卡片中勾選至少一個考點主題，系統將自動產生專屬學習指令）';
        } else {
            text = buildFullKmPromptText(selectedTopics, `（自訂專攻 ${selectedTopics.length} 個特定主題）`);
        }
    } else if (activeKmMode === 'topic') {
        // 單一主題模式
        return;
    }

    const titleEl = document.getElementById('km-prompt-box-title');
    if (titleEl) titleEl.textContent = title;
    const descEl = document.getElementById('km-mode-desc');
    if (descEl) descEl.textContent = statusDesc;
    const taEl = document.getElementById('km-prompt-textarea');
    if (taEl) taEl.value = text;
}

function copyFullKmPrompt() {
    selectKmMode('full');
    copyCurrentTextareaPrompt();
}

function copyTopicKmPrompt(tid) {
    const t = DATA.topics.find(x => x.id === tid);
    if (!t) return;
    const sampleQs = DATA.questions.filter(q => q.t.includes(tid) && q.target).slice(0, 4);
    const qLines = sampleQs.map(q => {
        const src = DATA.sources[q.s] || {};
        return `- [${src.year}年 ${src.exam} 題 ${q.q}]：${q.stem}`;
    }).join('\n');

    const promptText = `/knowledge-map-learning 我想專攻國考「${DATA.study_subject}」中的高頻考點主題：【${t.id}：${t.label}】（在目標考試命中 ${t.target_total} 題、全題庫命中 ${t.all_total} 題）。

請參考分析報告：@[${getEffectiveReportPath()}]

### 主題結構與拆節點要求：
- **核心法規與釋字**：${t.laws || '依報告第 2 節所述'}
- **涵蓋子主題（請將以下子主題規劃為 M1-1, M1-2... 知識脈絡節點）**：${t.subtopics || t.label}
- **常見命題模式與易混淆邊界**：${t.patterns || '依報告所述'}
- **歷屆代表真題參考**：
${qLines}

請在第一回合直接輸出此專題的知識脈絡全景圖（M1-1～Mn-x），並同回合無縫展開 M1-1 深度講義與思考題！`;

    activeKmMode = 'topic';
    document.querySelectorAll('.km-mode-btn').forEach(btn => btn.classList.remove('active'));
    selectedKmTopicIds = new Set([tid]);
    renderKmBridge();
    const titleEl = document.getElementById('km-prompt-box-title');
    if (titleEl) titleEl.textContent = `📋 單一主題深度精講指令：${t.id}（${t.label}）`;
    const descEl = document.getElementById('km-mode-desc');
    if (descEl) descEl.textContent = `目前模式：🎯 單一主題專攻（${t.id}）`;
    const taEl = document.getElementById('km-prompt-textarea');
    if (taEl) taEl.value = promptText;

    copyTextWithFallback(promptText, `🧠 已複製「${t.id}」主題專攻指令！可直接貼入對話框`);
}

function copyCurrentTextareaPrompt() {
    const text = document.getElementById('km-prompt-textarea').value;
    copyTextWithFallback(text, '📋 已複製預覽框內的啟動指令！');
}

function openLightbox(imgUrl, title) {
    document.getElementById('lightbox-title').textContent = title || '試卷原始影像預覽';
    document.getElementById('lightbox-img').src = imgUrl;
    document.getElementById('lightbox-open-new').href = imgUrl;
    document.getElementById('lightbox-modal').classList.add('open');
}

function closeLightbox() {
    document.getElementById('lightbox-modal').classList.remove('open');
    document.getElementById('lightbox-img').src = '';
}

document.addEventListener('DOMContentLoaded', initApp);
</script>
</body>
</html>
"""


def find_repo_root(run_dir: Path) -> Path | None:
    """若 run_dir 位於題庫的 _analysis/ 目錄下，尋找其對應的題庫根目錄。"""
    cur = run_dir.resolve()
    for parent in [cur, *cur.parents]:
        if parent.name == "_analysis":
            return parent.parent
    return None


def _infer_question_type(q: dict[str, Any], stem: str, options: list[str]) -> str:
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


def build_report_html(
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

    report_md = repair_latex_escapes(report_file.read_text(encoding="utf-8"))
    index_data = read_json(index_file)
    summary_target = read_json(summary_file) if summary_file.is_file() else summarize(index_data, examples=2)
    summary_all = (
        read_json(summary_all_file)
        if summary_all_file.is_file()
        else summarize(index_data, examples=2, target=None)
    )
    extraction_data = read_json(extraction_file) if extraction_file.is_file() else None

    payload = build_compact_payload(
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
    final_html = HTML_TEMPLATE.replace("{TITLE}", safe_title).replace("{JSON_PAYLOAD}", json_str)

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
        repo_root = find_repo_root(run_dir)
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
                print(f"提示：同步報告至題庫根目錄時略過：{exc}", file=sys.stderr)

    return out_file


def resolve_run_dir(input_path: Path) -> Path:
    """從使用者傳入的路徑（可能為題庫根目錄、_analysis 目錄、科目目錄或特定 run 目錄）
    自動智慧解析定位出包含有效分析成果的 run_dir。"""
    cur = input_path.resolve()
    if not cur.exists():
        raise FileNotFoundError(f"指定路徑不存在：{cur}")

    # 若傳入的就是單一 run 目錄
    if (cur / "question-index.json").is_file() and (cur / "report.md").is_file():
        return cur

    # 若是檔案（例如傳入 report.html 或 question-index.json），取其父目錄
    if cur.is_file():
        cur = cur.parent
        if (cur / "question-index.json").is_file() and (cur / "report.md").is_file():
            return cur

    # 若包含 _analysis 子目錄（例如傳入題庫根目錄），優先進入 _analysis
    search_root = cur / "_analysis" if (cur / "_analysis").is_dir() else cur

    # 搜尋所有包含 question-index.json 的子目錄
    candidates: list[Path] = []
    for p in search_root.rglob("question-index.json"):
        run_cand = p.parent
        # 排除備份目錄、暫存或非 run 目錄
        if (run_cand / "report.md").is_file() and not run_cand.name.startswith((".", "_")):
            candidates.append(run_cand)

    if not candidates:
        raise ValueError(
            f"在「{input_path}」中找不到任何包含 question-index.json 與 report.md 的有效分析成果目錄！"
        )

    # 依目錄最新修改時間排序，取最新
    candidates.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return candidates[0]


def check_report_html(html_file: Path) -> dict[str, Any]:
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
    repo_root = find_repo_root(html_file.parent)
    sync_report_html = (repo_root / "report.html").is_file() if repo_root else False
    safe_subj = re.sub(r'[\\/*?:"<>|]', "_", str(study_subject).strip())
    named_report_html = (repo_root / f"{safe_subj}_考點分析報告.html").is_file() if repo_root else False

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revise", type=Path, metavar="PATH", help="快速重新生成模式：從給定的 _analysis 或題庫路徑自動尋找最新成果目錄，重新編譯並檢查 report.html")
    parser.add_argument("--check", type=Path, metavar="HTML_PATH", help="單獨對已產出的 report.html 進行結構完整度與健康檢查")
    parser.add_argument("--run-dir", type=Path, help="分析輸出目錄（預設自動讀取該目錄下之 report.md 與 JSON 檔案）")
    parser.add_argument("--report", type=Path, help="指定 report.md 路徑")
    parser.add_argument("--summary", type=Path, help="指定目標考試 summary.json 路徑")
    parser.add_argument("--summary-all", type=Path, help="指定全題庫 summary-all.json 路徑")
    parser.add_argument("--index", type=Path, help="指定 question-index.json 路徑")
    parser.add_argument("--extraction", type=Path, help="指定 extraction.json 路徑")
    parser.add_argument("--output", type=Path, help="指定輸出之 report.html 路徑")
    parser.add_argument("--no-sync-root", action="store_true", help="停用自動同步報告至題庫根目錄")
    args = parser.parse_args()

    if args.check:
        try:
            info = check_report_html(args.check)
            print(format_check_report(info))
            return 0
        except Exception as exc:
            parser.exit(1, f"❌ 健康體檢未通過：{exc}\n")

    if args.revise:
        try:
            target_run_dir = resolve_run_dir(args.revise)
            print(f"🔍 自動鎖定最新分析成果目錄：{target_run_dir}")
            out_file = build_report_html(
                run_dir=target_run_dir,
                output_path=args.output,
                sync_root=not args.no_sync_root,
            )
            info = check_report_html(out_file)
            print(format_check_report(info))
            return 0
        except (OSError, ValueError, KeyError, TypeError) as exc:
            parser.exit(2, f"❌ 重新生成失敗：{exc}\n")

    run_dir = args.run_dir
    if run_dir is None:
        if args.report is not None:
            run_dir = args.report.resolve().parent
        elif args.index is not None:
            run_dir = args.index.resolve().parent
        else:
            parser.exit(2, "請提供 --run-dir、--revise 或同時提供 --report 與 --index\n")

    try:
        out_path = build_report_html(
            run_dir=run_dir,
            report_path=args.report,
            summary_path=args.summary,
            summary_all_path=args.summary_all,
            index_path=args.index,
            extraction_path=args.extraction,
            output_path=args.output,
            sync_root=not args.no_sync_root,
        )
        print(f"已輸出互動式報告：{out_path}")
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"無法完成：{exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
