"""inventory: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import os
from interfaces.commands import StageInput, StageResult
from interfaces.services import Storage, ManifestReader
from plugins._shared.lifecycle import StagePlugin, request_of, result_of
from plugins._shared.files import portable_relative



class InventoryPlugin(StagePlugin):
    id = 'inventory'
    name = 'inventory'

    def __init__(self, storage: Storage, manifest: ManifestReader | None = None) -> None:
        super().__init__()
        self.storage = storage
        self.manifest = manifest


    def inventory(self, root: Path, manifest: Path | None = None) -> dict[str, Any]:
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("來源必須是資料夾")
        warnings: list[str] = []
        metadata: dict[str, list[dict[str, Any]]] = {}
        if manifest:
            if self.manifest is None:
                raise ValueError("需要 ManifestReader 才能讀取 manifest")
            try:
                metadata = self.manifest.manifest_metadata(manifest)
            except (OSError, ValueError) as exc:
                warnings.append(f"manifest 未採用：{exc}")
        files: list[dict[str, Any]] = []
        contents: dict[str, list[str]] = {}
        for current, dirs, names in os.walk(root, followlinks=False, onerror=lambda exc: warnings.append(str(exc))):
            parent = Path(current)
            kept = []
            for name in sorted(dirs):
                try:
                    if name.casefold() != "_analysis" and not self.storage.linked(parent / name):
                        kept.append(name)
                except OSError as exc:
                    warnings.append(str(exc))
            dirs[:] = kept
            for name in sorted(names):
                path = parent / name
                if path.suffix.casefold() != ".pdf":
                    continue
                try:
                    if self.storage.linked(path):
                        warnings.append(f"略過連結：{path}")
                        continue
                    resolved = path.resolve(strict=True)
                    if not resolved.is_relative_to(root):
                        raise ValueError("檔案超出來源根目錄")
                    sha = self.storage.digest(resolved)
                    files.append({"path": str(resolved), "relative_path": portable_relative(path, root),
                                  "sha256": sha, "size": resolved.stat().st_size, "status": "inventoried",
                                  "metadata": metadata.get(sha, [])})
                    contents.setdefault(sha, []).append(str(resolved))
                except (OSError, ValueError) as exc:
                    files.append({"path": str(path), "status": "failed", "error": str(exc)})
        return {"schema_version": 1, "root": str(root), "files": files,
                "contents": [{"sha256": sha, "paths": paths} for sha, paths in contents.items()],
                "warnings": warnings}

    def run(self, value: StageInput) -> StageResult:
        request = request_of(value)
        data = self.inventory(request.root, request.manifest)
        self.storage.write_new(request.output, data)
        return result_of(value, artifacts={"inventory": request.output}, details={"warnings": data["warnings"]})
