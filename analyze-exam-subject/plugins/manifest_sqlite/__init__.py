"""manifest: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import sqlite3
from plugins._shared.lifecycle import Lifecycle
from interfaces.services import ManifestReadError



class ManifestPlugin(Lifecycle):
    id = 'manifest'
    name = 'manifest'


    def manifest_metadata(self, path: Path) -> dict[str, list[dict[str, Any]]]:
        self.require_active()
        try:
            return self._read_metadata(path)
        except (sqlite3.Error, OSError, ValueError) as error:
            raise ManifestReadError(str(error)) from error

    def _read_metadata(self, path: Path) -> dict[str, list[dict[str, Any]]]:
        """只讀已知 schema，依內容雜湊連結來源，不讀取資料庫指向的檔案。"""
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only=ON")
            version = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if not version or str(version[0]) not in {"1", "2"}:
                raise ValueError("不支援的 manifest 版本")
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
            SELECT a.sha256, a.asset_id, a.record_id, a.snapshot_id, a.role,
                   r.year, r.exam_name, r.category, r.subject
            FROM attachments a JOIN snapshot_records r
            ON a.record_id=r.record_id AND a.snapshot_id=r.snapshot_id
            WHERE a.sha256 IS NOT NULL
        """)
            result: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                data = dict(row)
                sha = data.pop("sha256")
                result.setdefault(sha, []).append(data)
            return result
        finally:
            conn.close()
