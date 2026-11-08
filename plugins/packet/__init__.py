"""packet: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from interfaces.commands import StageInput, StageResult
from interfaces.services import Storage
from plugins._shared.lifecycle import StagePlugin, request_of, result_of
from plugins._shared.analysis import MAX_SAMPLE_CHARS, RULES



class PacketPlugin(StagePlugin):
    id = 'packet'
    name = 'packet'

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage


    def packet(self, work: dict, batch_id: str, context: int = 0) -> dict:
        """只輸出當批圖片與分類表；context 可帶同卷前後相鄰頁供跨頁題續讀。"""
        if not 0 <= context <= 2:
            raise ValueError("context 必須介於 0～2")
        batch = next((b for b in work["batches"] if b["batch_id"] == batch_id), None)
        if batch is None:
            raise ValueError("找不到工作批次")
        source = work["sources"][batch["source"]]
        all_pages = sorted(map(int, source["pages"]))
        batch_pages = [page["p"] for page in batch["pages"]]
        positions = [all_pages.index(page) for page in batch_pages]
        context_numbers: list[int] = []
        if context and positions:
            before = all_pages[max(0, min(positions) - context):min(positions)]
            after = all_pages[max(positions) + 1:max(positions) + 1 + context]
            context_numbers = [page for page in [*before, *after] if page not in batch_pages]
        return {"study_subject": work["config"]["study_subject"], "topics": work["config"]["topics"],
                "source_roles": sorted({r.get("role") or "unknown" for r in source["refs"]}),
                "rules": list(RULES), "max_sample_chars": MAX_SAMPLE_CHARS, **batch,
                "context_pages": [{"p": page, "image": source["pages"][str(page)]["image"], "role": "context"}
                                   for page in context_numbers],
                "reply": "results:[{source,reviewed_pages,questions:[{p,q,r,t}]}]；c/u 加 why，跨頁加 pages，父題加 count:false。可選 answers、duplicate_of、sample。"}

    def run(self, value: StageInput) -> StageResult:
        r = request_of(value)
        data = self.packet(self.storage.read_json(r.work), r.batch_id, r.context)
        self.storage.write_new(r.output, data)
        return result_of(value, artifacts={"packet": r.output})
