"""status: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from interfaces.commands import StageInput, StageResult
from interfaces.services import Storage
from plugins._shared.lifecycle import StagePlugin, request_of, result_of



class StatusPlugin(StagePlugin):
    id = 'status'
    name = 'status'

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage


    def describe_work(self, work: dict) -> dict:
        """列出尚未處理的批次與已完成快取，避免為了看進度而讀完整 work.json。"""
        if work.get("kind") != "compact-work":
            raise ValueError("不是 compact-work")
        pending = [{"batch_id": batch["batch_id"], "source": batch["source"],
                    "pages": [page["p"] for page in batch["pages"]]} for batch in work.get("batches", [])]
        return {"schema_version": 1, "kind": "compact-status",
                "study_subject": work["config"]["study_subject"], "target_exam": work["config"].get("target_exam"),
                "batch_count": len(pending), "page_count": sum(len(batch["pages"]) for batch in pending),
                "pending_batches": pending, "cached_sources": sorted(work.get("cached", {})),
                "warnings": list(work.get("warnings", []))}

    def run(self, value: StageInput) -> StageResult:
        r = request_of(value)
        return result_of(value, details=self.describe_work(self.storage.read_json(r.work)))
