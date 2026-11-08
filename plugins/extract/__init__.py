"""extract: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from pathlib import Path
from typing import Any
from interfaces.commands import CommandRequest, StageInput, StageResult
from interfaces.services import Storage
from plugins._shared.lifecycle import StagePlugin, request_of, result_of
from plugins._shared.files import TOOL_VERSION



class ExtractPlugin(StagePlugin):
    id = 'extract'
    name = 'extract'

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage


    def extract(self, data: dict[str, Any], selection: dict[str, Any] | None,
                pages: list[int] | None, resume: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            import pypdf
        except ImportError as exc:
            raise ValueError("缺少 pypdf；請在選定環境執行 python -m pip install pypdf") from exc
        root, chosen = self.storage.select_sources(data, selection, pages)
        if resume is not None and (resume.get("schema_version") != 1 or resume.get("kind") is not None):
            raise ValueError("resume 不是支援的擷取索引")
        config = {"tool_version": TOOL_VERSION, "pypdf_version": pypdf.__version__, "page_scope": pages}
        cache = {}
        if resume and all(resume.get(key) == value for key, value in config.items()):
            cache = {doc["sha256"]: doc for doc in resume.get("documents", []) if doc.get("status") == "extracted"}
        grouped: dict[str, list[str]] = {}
        documents: list[dict[str, Any]] = []
        for item in chosen:
            path = Path(item["path"])
            try:
                self.storage.verify_source(path, root, item["sha256"])
                grouped.setdefault(item["sha256"], []).append(str(path))
            except (OSError, ValueError) as exc:
                documents.append({"sha256": item["sha256"], "paths": [str(path)], "status": "failed", "pages": [], "error": str(exc)})
        for sha, paths in grouped.items():
            if sha in cache:
                documents.append({**cache[sha], "paths": paths, "reused": True})
                continue
            doc: dict[str, Any] = {"sha256": sha, "paths": paths, "status": "extracted", "pages": []}
            try:
                with Path(paths[0]).open("rb") as stream:
                    reader = pypdf.PdfReader(stream)
                    if reader.is_encrypted and not reader.decrypt(""):
                        raise ValueError("PDF 已加密，無法讀取")
                    count = len(reader.pages)
                    if not count:
                        raise ValueError("PDF 沒有頁面")
                    doc["total_pages"] = count
                    for number in pages if pages is not None else range(1, count + 1):
                        try:
                            if number > count:
                                raise ValueError("頁碼超出 PDF 範圍")
                            text = reader.pages[number - 1].extract_text() or ""
                            doc["pages"].append({"page": number, "text": text,
                                                 "status": "text_extracted" if text.strip() else "needs_visual_review"})
                        except Exception as exc:
                            doc["pages"].append({"page": number, "text": "", "status": "failed", "error": str(exc)})
                            doc["status"] = "partial"
            except Exception as exc:
                doc.update(status="failed", error=str(exc))
            documents.append(doc)
        return {"schema_version": 1, **config, "documents": documents}

    def preflight(self, request: CommandRequest) -> None:
        try:
            import pypdf
        except ImportError as error:
            raise ValueError("需要 pypdf；請在選定環境安裝 requirements.txt") from error

    def run(self, value: StageInput) -> StageResult:
        r = request_of(value)
        data = self.extract(self.storage.read_json(r.inventory), self.storage.read_json(r.selection) if r.selection else None,
                            list(r.pages) if r.pages is not None else None, self.storage.read_json(r.resume) if r.resume else None)
        self.storage.write_new(r.output, data)
        return result_of(value, artifacts={"extraction": r.output})
