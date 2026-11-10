"""summary: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from collections import Counter, defaultdict
from interfaces.commands import CommandRequest, StageInput, StageResult
from interfaces.services import Storage
from plugins._shared.lifecycle import StagePlugin, request_of, result_of
from plugins._shared.analysis import SUMMARY_SCHEMA, _UNSET, validate_target, matches_target, _report_link, _with_link, _parse_target



class SummaryPlugin(StagePlugin):
    id = 'summary'
    name = 'summary'

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage

    def preflight(self, request: CommandRequest) -> None:
        if request.command in {"summary", "finalize"}:
            if not 0 <= request.examples <= 5:
                raise ValueError("每主題代表題數需介於 0～5")
            if request.target is not None:
                validate_target(_parse_target(request.target))


    def summarize(self, index: dict, examples: int = 2, target: Any = _UNSET, link_base: Path | None = None) -> dict:
        """計唯一題、主題及年度考試分層，不推測獨立命題事件。

    link_base 為 summary／報告所在目錄；提供時為每個來源補上相對於報告的可攜連結。
    """
        if examples < 0 or examples > 5:
            raise ValueError("每主題代表題數需介於 0～5")
        root = index.get("source_root")
        ids = [q["question_id"] for q in index["questions"]]
        if len(ids) != len(set(ids)):
            raise ValueError("索引包含重複 question_id")
        if target is _UNSET:
            target = index.get("target_exam")
        validate_target(target)
        countable = [q for q in index["questions"] if q.get("countable", True)]
        all_related = [q for q in countable if q.get("analysis_status") == "completed"
                       and q["relevance"] in {"relevant", "cross_domain"}]
        related = [q for q in all_related if any(matches_target(ref, target) for ref in q["source_refs"])]
        groups: dict[tuple, set[str]] = defaultdict(set)
        hits = Counter()
        samples: dict[str, list[dict]] = defaultdict(list)
        for q in related:
            for tag in set(q["topic_ids"]):
                hits[tag] += 1
                if len(samples[tag]) < examples:
                    samples[tag].append({"id": q["question_id"], "pages": q["pages"],
                                         "summary": q.get("summary", ""),
                                         "sources": [_with_link(r, root, link_base) for r in q["source_refs"]
                                                     if matches_target(r, target)]})
            for ref in q["source_refs"]:
                if matches_target(ref, target):
                    groups[(ref.get("year"), ref.get("exam_name"))].add(q["question_id"])
        by_year = [{"year": key[0], "exam_name": key[1], "unique_questions": len(value)}
                   for key, value in sorted(groups.items(),
                                            key=lambda item: (item[0][0] is None, str(item[0][0]).zfill(6),
                                                              str(item[0][1] or "")))]
        source_files: dict[str, dict] = {}
        for q in index["questions"]:
            for ref in q.get("source_refs", []):
                key = ref.get("relative_path") or ref.get("path")
                if key and key not in source_files:
                    source_files[key] = {"relative_path": ref.get("relative_path"), "sha256": ref.get("sha256"),
                                         "official_subject": ref.get("official_subject"), "year": ref.get("year"),
                                         "exam_name": ref.get("exam_name"),
                                         "link": _report_link(root, ref.get("relative_path"), link_base)}
        summary_data = {"schema_version": SUMMARY_SCHEMA, "study_subject": index["study_subject"], "target_exam": target,
                        "source_root": root, "source_files": list(source_files.values()),
                        "status": index["status"], "coverage": index["coverage"],
                        "unique_related_questions": len(related),
                        "supplemental_related_questions": len(all_related) - len(related),
                        "pending_questions": sum(q.get("analysis_status") != "completed" for q in countable),
                        "irrelevant_questions": sum(q.get("analysis_status") == "completed" and q["relevance"] == "irrelevant"
                                                    for q in countable),
                        "uncertain_questions": sum(q["relevance"] == "uncertain" for q in countable),
                        "unreadable_questions": sum(q.get("analysis_status") == "unreadable" for q in countable),
                        "duplicate_candidate_count": sum(len(q.get("duplicate_candidates", [])) for q in countable),
                        "answer_pair_count": sum(len(q.get("answer_refs", [])) for q in countable),
                        "event_count": None,
                        "event_note": "未建立命題事件對應；年度考試分層不等於事件數。",
                        "topics": [{"topic_id": t["topic_id"], "label": t.get("label", t["topic_id"]),
                                    "count": hits[t["topic_id"]], "examples": samples[t["topic_id"]]} for t in index["topics"]],
                        "by_year_exam": by_year,
                        "notes": ["主題可多標籤，合計可能超過相關題數。",
                                  "年度考試分層不是獨立命題事件計數；未知年份或考試保留 null。"]}
        if index.get("custom_rules"):
            summary_data["custom_rules"] = index["custom_rules"]
        return summary_data

    def run(self, value: StageInput) -> StageResult:
        r = request_of(value)
        data = self.summarize(self.storage.read_json(r.index), r.examples, _parse_target(r.target), link_base=r.output.parent)
        self.storage.write_new(r.output, data)
        return result_of(value, artifacts={"summary": r.output}, details={"status": data["status"]})
