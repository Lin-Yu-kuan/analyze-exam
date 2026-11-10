"""prepare: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from pathlib import Path
from interfaces.commands import StageInput, StageResult
from interfaces.services import Storage
from plugins._shared.lifecycle import StagePlugin, request_of, result_of
from plugins._shared.analysis import fingerprint, _short_list, _normalise_ref, _analysis_config, validate_target



class PreparePlugin(StagePlugin):
    id = 'prepare'
    name = 'prepare'

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage


    def prepare(self, inventory: dict, selection: dict, renders: dict, topics: list[dict],
                prior: dict | None = None, batch_size: int = 5) -> dict:
        """本機來源登錄與模型工作包分離；快取以整份內容為單位。"""
        if not 1 <= batch_size <= 20:
            raise ValueError("每批頁數必須介於 1～20")
        topic_ids = [topic["topic_id"] for topic in topics]
        if not topics or len(set(topic_ids)) != len(topic_ids) or any(not isinstance(t, str) or not t for t in topic_ids):
            raise ValueError("topics 必須提供不重複的非空 topic_id")
        if not selection.get("classification_version"):
            raise ValueError("請提供 classification_version")
        validate_target(selection.get("target_exam"))
        root, chosen = self.storage.select_sources(inventory, selection, None)
        chosen_paths = {str(Path(item["path"]).resolve()) for item in chosen}
        for item in chosen:
            self.storage.verify_source(Path(item["path"]), root, item["sha256"])
        if renders.get("kind") != "exam-render-index":
            raise ValueError("需要 render-index")
    
        analysis = _analysis_config(selection, topics)
        signature = fingerprint(analysis)
        config = {**analysis, "target_exam": selection.get("target_exam")}
        sources: dict[str, dict] = {}
        for item in selection["selected_sources"]:
            path = self.storage.source_path(item, root)
            if str(path) not in chosen_paths:
                raise ValueError("來源不在選取內")
            sha = item["sha256"]
            sid = "S" + sha[:16]
            if sid in sources and sources[sid]["sha256"] != sha:
                raise ValueError("來源短 ID 碰撞")
            source = sources.setdefault(sid, {"sha256": sha, "refs": [], "pages": {}, "total_pages": None})
            ref = _normalise_ref(item, path, root)
            if ref not in source["refs"]:
                source["refs"].append(ref)
    
        renders_by_sha: dict[str, dict] = {}
        for doc in renders.get("documents", []):
            sha = doc.get("sha256")
            if sha in renders_by_sha:
                raise ValueError("render-index 同一來源出現多次，請先選定版本")
            renders_by_sha[sha] = doc
    
        warnings: list[str] = []
        verification: dict[str, dict] = {}
        for sid, source in sources.items():
            document = renders_by_sha.get(source["sha256"])
            if document is None:
                raise ValueError(f"渲染索引缺少來源 {sid}，請先對已選來源執行 render")
            total = document.get("total_pages")
            if type(total) is not int or total <= 0:
                raise ValueError(f"來源 {sid} 缺少有效頁數，請重新 render")
            source["total_pages"] = total
            pages: dict[str, dict] = {}
            failed: list[dict] = []
            for page in document.get("pages", []):
                number = page.get("page")
                if type(number) is not int or not 1 <= number <= total:
                    raise ValueError("渲染索引含無效頁碼")
                if page.get("status") == "rendered":
                    image = Path(page["image_path"]).resolve()
                    if self.storage.digest(image) != page["image_sha256"]:
                        raise ValueError("圖片雜湊不符，請先恢復渲染")
                    if str(number) in pages:
                        raise ValueError("渲染索引同一來源頁碼重複，請先選定使用的圖片版本")
                    pages[str(number)] = {"image": str(image), "sha256": page["image_sha256"]}
                else:
                    failed.append({"page": number, "error": page.get("error")})
            if not pages:
                raise ValueError(f"來源 {sid} 沒有可用的渲染頁，請重新 render")
            source["pages"] = pages
            source["rendered_pages"] = sorted(map(int, pages))
            source["failed_pages"] = failed
            source["missing_pages"] = sorted(set(range(1, total + 1)) - set(source["rendered_pages"]))
            source["roles"] = sorted({ref.get("role") or "unknown" for ref in source["refs"]})
            for ref in source["refs"]:
                stat = Path(ref["path"]).stat()
                verification[ref["path"]] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
            for image in source["pages"].values():
                stat = Path(image["image"]).stat()
                verification[image["image"]] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size}
            if source["missing_pages"]:
                warnings.append(f"{sid} 只渲染 {len(pages)}/{total} 頁；未渲染頁：{_short_list(source['missing_pages'])}")
            if failed:
                warnings.append(f"{sid} 有 {len(failed)} 頁渲染失敗：{_short_list([row['page'] for row in failed])}")
    
        reusable = prior.get("cache", {}) if prior and prior.get("config_hash") == signature else {}
        cached: dict[str, dict] = {}
        batches: list[dict] = []
        for sid, source in sources.items():
            source["cache_key"] = fingerprint([signature, source["sha256"], source["total_pages"],
                                               {p: row["sha256"] for p, row in source["pages"].items()},
                                               source["roles"]])
            old = reusable.get(source["cache_key"])
            if old and old.get("complete") is True:
                cached[sid] = old
                continue
            numbers = sorted(map(int, source["pages"]))
            for start in range(0, len(numbers), batch_size):
                batches.append({"batch_id": f"{sid}-{start // batch_size + 1}", "source": sid,
                                "pages": [{"p": p, "image": source["pages"][str(p)]["image"]}
                                          for p in numbers[start:start + batch_size]]})
        return {"schema_version": 1, "kind": "compact-work", "config": config, "config_hash": signature,
                "source_root": str(root),
                "sources": sources, "batches": batches, "cached": cached, "warnings": warnings,
                "verification": verification}

    def run(self, value: StageInput) -> StageResult:
        r = request_of(value)
        data = self.prepare(self.storage.read_json(r.inventory), self.storage.read_json(r.selection),
                            self.storage.read_json(r.render_index), self.storage.read_json(r.topics)["topics"],
                            None if r.no_cache else (self.storage.read_json(r.prior) if r.prior else None), r.batch_size)
        self.storage.write_new(r.output, data)
        return result_of(value, artifacts={"work": r.output}, details={
            "pending_batches": [b["batch_id"] for b in data["batches"]], "reused_sources": len(data["cached"]),
            "incomplete_sources": [{"source": sid, "missing_pages": source["missing_pages"],
                                    "failed_pages": [row["page"] for row in source["failed_pages"]]}
                                   for sid, source in data["sources"].items() if source["missing_pages"] or source["failed_pages"]]})
