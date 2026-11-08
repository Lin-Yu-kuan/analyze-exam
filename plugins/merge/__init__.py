"""merge: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
from collections import defaultdict
import math
from interfaces.commands import StageInput, StageResult
from interfaces.services import Storage
from plugins._shared.lifecycle import StagePlugin, request_of, result_of
from plugins._shared.analysis import INDEX_SCHEMA, MAX_SAMPLE_CHARS, MAX_WHY_CHARS, MAX_EVIDENCE_CHARS, RELEVANCE, ANSWER_ROLES, _load_extraction, _normalise_answers, _normalise_duplicates, _coverage



class MergePlugin(StagePlugin):
    id = 'merge'
    name = 'merge'

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage


    def merge(self, work: dict, results: list[dict], visual_review: dict | None = None,
              extraction: dict | None = None, fast_verify: bool = False) -> dict:
        """合併精簡判斷並補回來源；可選讀 visual-review 與 extraction 驗證證據。"""
        if work.get("kind") != "compact-work":
            raise ValueError("不是 compact-work")
        visual = self._load_visual(visual_review, work["sources"])
        text_pages = _load_extraction(extraction, work["sources"])
        verification = work.get("verification", {})
        if visual is not None and visual["available"] is not True:
            if any(entry.get("reviewed_pages") for entry in results):
                raise ValueError("host_vision 未確認為 true，reviewed_pages 必須留空")
        topic_ids = {t["topic_id"] for t in work["config"]["topics"]}
        questions: dict[str, dict] = {}
        reviewed: dict[str, set[int]] = defaultdict(set)
        entries = [{"source": sid, **entry["result"]} for sid, entry in work.get("cached", {}).items()] + results
        compact_rows: dict[str, list[dict]] = defaultdict(list)
        for entry in entries:
            sid = entry["source"]
            if sid not in work["sources"]:
                raise ValueError("未知來源 ID")
            source = work["sources"][sid]
            valid_pages = set(map(int, source["pages"]))
            seen = entry.get("reviewed_pages", [])
            if any(type(p) is not int or p not in valid_pages for p in seen):
                raise ValueError("視覺核對頁碼不在來源內")
            seen_set = set(seen)
            reviewed[sid].update(seen_set)
            for row in entry.get("questions", []):
                if all(ref.get("role") in ANSWER_ROLES for ref in source["refs"]):
                    raise ValueError("答案附件不得計為新題目")
                p, number, rel = row["p"], row["q"], row["r"]
                pages = row.get("pages", [p])
                if (type(p) is not int or p not in valid_pages or not isinstance(number, str) or not number.strip()
                        or not isinstance(pages, list) or not pages or min(pages) != p
                        or any(type(v) is not int or v not in valid_pages for v in pages)):
                    raise ValueError("題號／跨頁引用無效")
                if rel not in RELEVANCE:
                    raise ValueError("未知相關性代碼")
                tags = row.get("t", [])
                if not isinstance(tags, list) or any(not isinstance(tag, str) or tag not in topic_ids for tag in tags):
                    raise ValueError("未知主題 ID")
                if rel in {"r", "c"} and not tags:
                    raise ValueError("相關題必須有主題")
                why = row.get("why", "")
                if why is None:
                    why = ""
                if not isinstance(why, str) or len(why.strip()) > MAX_WHY_CHARS:
                    raise ValueError("why 必須是短字串")
                why = why.strip()
                if rel in {"c", "u"} and not why:
                    raise ValueError("交叉／待確認題需提供簡短理由")
                sample = row.get("sample", "")
                if sample is None:
                    sample = ""
                if not isinstance(sample, str) or len(sample.strip()) > MAX_SAMPLE_CHARS:
                    raise ValueError("sample 必須是短字串")
                sample = sample.strip()
                question_type = row.get("question_type", "unknown")
                if question_type not in ("essay", "mcq", "unknown"):
                    raise ValueError("question_type 必須為 essay、mcq 或 unknown")
                if "count" in row and type(row["count"]) is not bool:
                    raise ValueError("count 必須為布林值")
                score = row.get("score")
                if score is not None and (type(score) not in {int, float} or isinstance(score, bool)
                                          or not math.isfinite(score) or score < 0):
                    raise ValueError("score 必須是非負有限數字")
                parent = row.get("parent")
                if parent is not None and (not isinstance(parent, str) or not parent.strip()):
                    raise ValueError("parent 必須是題目 ID 或 null")
                parent = parent.strip() if isinstance(parent, str) else None
                evidence = row.get("evidence", "")
                if evidence is None:
                    evidence = ""
                if not isinstance(evidence, str) or len(evidence.strip()) > MAX_EVIDENCE_CHARS:
                    raise ValueError("evidence 必須是短字串")
                evidence = evidence.strip()
                answers_raw = row.get("answers") or []
                answers = _normalise_answers(answers_raw, work)
                duplicates = _normalise_duplicates(row.get("duplicate_of"))
                normalized = {"p": p, "q": number.strip(), "r": rel, "t": sorted(set(tags)),
                              "pages": sorted(set(pages)), "count": row.get("count", True),
                              "why": why, "sample": sample, "answers": answers_raw,
                              "duplicate_of": duplicates, "score": score,
                              "parent": parent, "evidence": evidence, "question_type": question_type}
                qid = f"{sid}-P{p}-Q{number.strip()}"
                if qid in questions:
                    if questions[qid]["_compact"] != normalized:
                        raise ValueError(f"同一題有衝突判斷：{qid}")
                    questions[qid]["_full_reviewed"] = (
                        questions[qid]["_full_reviewed"] or set(questions[qid]["pages"]).issubset(seen_set))
                    continue
                compact_rows[sid].append(normalized)
                questions[qid] = {"question_id": qid, "content_sha256": source["sha256"], "pages": normalized["pages"],
                                  "question_number": number.strip(), "countable": normalized["count"],
                                  "question_type": question_type,
                                  "relevance": RELEVANCE[rel], "topic_ids": normalized["t"],
                                  "reason": why, "summary": sample, "source_refs": source["refs"],
                                  "answer_refs": answers, "score": score, "parent_question_id": parent,
                                  "evidence_excerpt": evidence,
                                  "_source": sid, "_source_sha": source["sha256"],
                                  "_full_reviewed": set(normalized["pages"]).issubset(seen_set),
                                  "_duplicates": duplicates, "_compact": normalized}
    
        for question in questions.values():
            for duplicate in question["_duplicates"]:
                if duplicate["id"] == question["question_id"]:
                    raise ValueError("題目不可標記為自己的重複題")
                if duplicate["id"] not in questions:
                    raise ValueError(f"duplicate_of 指向未知題目：{duplicate['id']}")
            parent_id = question["parent_question_id"]
            if parent_id:
                if parent_id == question["question_id"]:
                    raise ValueError("題目不可標記為自己的父題")
                if parent_id not in questions:
                    raise ValueError(f"parent 指向未知題目：{parent_id}")
                if questions[parent_id]["countable"] is not False:
                    raise ValueError("父題必須設 count:false")
    
        source_status: dict[str, str] = {}
        cache: dict[str, dict] = {}
        for sid, source in work["sources"].items():
            # 即使工作包來自上次，也核對 PDF 與圖片內容，不盲信快取。
            for ref in source["refs"]:
                self._verify_file(Path(ref["path"]), source["sha256"], verification, fast_verify, "來源已變更，請重新 prepare")
            for image in source["pages"].values():
                self._verify_file(Path(image["image"]), image["sha256"], verification, fast_verify,
                             "圖片已變更，請重新 render／prepare")
            source_questions = [q for q in questions.values() if q["_source"] == sid]
            total = source["total_pages"]
            page_complete = type(total) is int and total > 0 and reviewed[sid] == set(range(1, total + 1))
            no_uncertain = all(row["r"] != "u" for row in compact_rows[sid])
            visual_complete = False
            if visual is not None:
                visual_complete = visual["available"] is True and type(total) is int and total > 0 and all(
                    (source["sha256"], page) in visual["pages"]
                    and visual["pages"][(source["sha256"], page)]["status"] == "checked"
                    for page in range(1, total + 1))
            complete = page_complete and no_uncertain and all(
                question["_full_reviewed"] for question in source_questions) and visual_complete
            source_status[sid] = "complete" if complete else "partial"
            cache[source["cache_key"]] = {"complete": complete, "result": {
                "reviewed_pages": sorted(reviewed[sid]), "questions": compact_rows[sid]}}
    
        for question in questions.values():
            states: list[str] = []
            evidence: list[dict] = []
            full_reviewed = question["_full_reviewed"]
            for page in question["pages"]:
                if visual is None:
                    # 沒有 visual-review 就沒有實際開圖證據；只保留文字暫判。
                    state = "not_checked"
                    row = None
                else:
                    row = visual["pages"].get((question["_source_sha"], page))
                    if visual["available"] is not True or row is None:
                        state = "not_checked"
                    elif row["status"] == "unreadable":
                        state = "unreadable"
                    elif row["status"] == "checked" and full_reviewed:
                        state = "checked"
                    else:
                        state = "not_checked"
                states.append(state)
                source_page = work["sources"][question["_source"]]["pages"][str(page)]
                if state in {"checked", "unreadable"} and row is not None and row.get("image_path"):
                    evidence.append({"source_sha256": question["_source_sha"], "page": page,
                                     "image_path": row["image_path"], "image_sha256": row["image_sha256"]})
                else:
                    evidence.append({"source_sha256": question["_source_sha"], "page": page,
                                     "image_path": source_page["image"], "image_sha256": source_page["sha256"]})
            if "unreadable" in states:
                visual_status = "unreadable"
            elif all(state == "checked" for state in states):
                visual_status = "checked"
            else:
                visual_status = "not_checked"
            checked = visual_status == "checked"
            question["visual_status"] = visual_status
            if checked:
                if text_pages is not None and not any(
                        (question["_source_sha"], page) in text_pages for page in question["pages"]):
                    question["interpretation_method"] = "vision_only"
                else:
                    question["interpretation_method"] = "text_and_vision"
            else:
                question["interpretation_method"] = "text_only"
            if visual_status == "unreadable":
                question["analysis_status"] = "unreadable"
            elif checked and question["relevance"] != "uncertain":
                question["analysis_status"] = "completed"
            else:
                question["analysis_status"] = "needs_review"
            question["image_evidence"] = evidence
            question["duplicate_candidates"] = [{"other_question_id": duplicate["id"], "reason": duplicate["why"]}
                                                for duplicate in question["_duplicates"]]
            question.pop("_source")
            question.pop("_source_sha")
            question.pop("_full_reviewed")
            question.pop("_duplicates")
            question.pop("_compact")
    
        coverage, paths = _coverage(work, reviewed, visual, source_status, text_pages)
        warnings = list(work.get("warnings", []))
        if visual is None:
            warnings.append("未提供 visual-review；本次只產生 partial 的文字暫判，所有題目維持 not_checked。")
        elif visual["available"] is not True:
            warnings.append("host_vision 未確認為 true；本次只產生 partial 的文字暫判。")
        result = {"schema_version": INDEX_SCHEMA, **work["config"], "config_hash": work["config_hash"],
                  "source_root": work.get("source_root"),
                  "status": "complete" if source_status and all(s == "complete" for s in source_status.values()) else "partial",
                  "source_status": paths, "questions": list(questions.values()), "cache": cache,
                  "coverage": coverage, "warnings": warnings}
        return result

    def _load_visual(self, review: dict | None, sources: dict[str, dict]) -> dict | None:
        """讀取宿主實際開圖紀錄；不信任 reviewed_pages 的自我宣告。"""
        if review is None:
            return None
        if review.get("schema_version") != 1:
            raise ValueError("visual-review schema_version 必須是 1")
        host = review.get("host_vision")
        if not isinstance(host, dict) or host.get("available") not in {True, False, None}:
            raise ValueError("visual-review 缺少有效的 host_vision")
        available = host.get("available")
        if available is True:
            probe_image = host.get("probe_image")
            if not probe_image or not host.get("probe_result"):
                raise ValueError("host_vision 為 true 時必須提供實際 probe_image 與 probe_result")
            Path(probe_image).resolve(strict=True)
        by_sha = {source["sha256"]: source for source in sources.values()}
        pages: dict[tuple[str, int], dict] = {}
        for row in review.get("pages", []):
            sha = row.get("source_sha256")
            page = row.get("page")
            if sha not in by_sha:
                raise ValueError("visual-review 含未知來源")
            if type(page) is not int or str(page) not in by_sha[sha]["pages"]:
                raise ValueError("visual-review 頁碼不在已渲染範圍")
            status = row.get("visual_status")
            if status not in {"not_checked", "checked", "unreadable"}:
                raise ValueError("visual-review 含無效 visual_status")
            image_path = row.get("image_path")
            image_sha = row.get("image_sha256")
            resolved = None
            if image_path and image_sha:
                path = Path(image_path).resolve(strict=True)
                if self.storage.digest(path) != image_sha:
                    raise ValueError("visual-review 圖片雜湊不符")
                resolved = str(path)
            if status == "checked":
                if not resolved:
                    raise ValueError("visual-review 的 checked 頁缺少圖片證據")
                if available is not True:
                    raise ValueError("host_vision 未確認為 true，不能標記 checked")
            key = (sha, page)
            if key in pages:
                raise ValueError("visual-review 同一頁出現多次")
            pages[key] = {"status": status, "image_path": resolved, "image_sha256": image_sha,
                          "observations": row.get("observations", ""), "reason": row.get("reason", "")}
        return {"available": available, "pages": pages}

    def _verify_file(self, path: Path, expected: str, verification: dict, fast: bool, message: str) -> None:
        """完整雜湊是預設；fast 只跳過 mtime/size 未變的檔案，變更仍會重算。"""
        if fast:
            record = verification.get(str(path))
            try:
                stat = path.stat()
            except OSError:
                stat = None
            if record and stat and record.get("mtime_ns") == stat.st_mtime_ns and record.get("size") == stat.st_size:
                return
        if self.storage.digest(path) != expected:
            raise ValueError(message)

    def run(self, value: StageInput) -> StageResult:
        r = request_of(value)
        work = r.work or r.run_dir / "work.json"
        output = r.run_dir / "question-index.json" if r.command == "finalize" else r.output
        review = r.visual_review
        extraction = r.extraction
        if r.command == "finalize":
            if review is None and (r.run_dir / "visual-review.json").is_file(): review = r.run_dir / "visual-review.json"
            if extraction is None and (r.run_dir / "extraction.json").is_file(): extraction = r.run_dir / "extraction.json"
        data = self.merge(self.storage.read_json(work), [row for path in r.results for row in self.storage.read_json(path)["results"]],
                          self.storage.read_json(review) if review else None,
                          self.storage.read_json(extraction) if extraction else None, fast_verify=r.fast_verify)
        if r.require_complete and data["status"] != "complete":
            raise ValueError("分析尚未完成，不能標記為完整")
        self.storage.write_new(output, data)
        next_request = replace(r, index=output, output=r.run_dir / "summary.json") if r.command == "finalize" else r
        return result_of(value, request=next_request, artifacts={"index": output}, details={"status": data["status"]})
