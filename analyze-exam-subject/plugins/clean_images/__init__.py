"""clean-images: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from pathlib import Path
from typing import Any
import os
from interfaces.commands import StageInput, StageResult
from interfaces.services import Storage
from plugins._shared.lifecycle import StagePlugin, request_of, result_of



class CleanImagesPlugin(StagePlugin):
    id = 'clean-images'
    name = 'clean-images'

    def __init__(self, storage: Storage) -> None:
        super().__init__()
        self.storage = storage


    def clean_images(self, target: Path) -> dict[str, Any]:
        """安全清理分析衍生之 render-images 資料夾以釋放磁碟空間。

    僅刪除名為 'render-images' 之資料夾及其內部暫存圖片，不跟隨符號連結，
    亦不刪除任何 JSON 索引、Markdown 報告或原始試題 PDF。
    """
        target = target.resolve(strict=True)
        if not target.is_dir():
            raise ValueError("目標路徑必須是資料夾")
        if self.storage.linked(target):
            raise ValueError("目標不可為連結或接合點")
    
        candidates: list[Path] = []
        if target.name.casefold() == "render-images":
            candidates.append(target)
        else:
            for current, dirs, _ in os.walk(target, followlinks=False):
                parent = Path(current)
                kept = []
                for d in dirs:
                    sub = parent / d
                    if not self.storage.linked(sub):
                        if d.casefold() == "render-images":
                            candidates.append(sub)
                        else:
                            kept.append(d)
                dirs[:] = kept
    
        freed_files = 0
        freed_bytes = 0
        removed_dirs: list[str] = []
    
        for img_dir in candidates:
            if self.storage.linked(img_dir):
                continue
            dir_bytes = 0
            dir_files = 0
            # 逐檔安全驗證：僅刪除 render 產生的圖片或暫存檔
            for root_dir, _, filenames in os.walk(img_dir, topdown=False, followlinks=False):
                p = Path(root_dir)
                for fname in filenames:
                    file_path = p / fname
                    if not self.storage.linked(file_path):
                        size = file_path.stat().st_size
                        file_path.unlink()
                        dir_bytes += size
                        dir_files += 1
                try:
                    p.rmdir()
                except OSError:
                    pass
            freed_files += dir_files
            freed_bytes += dir_bytes
            removed_dirs.append(str(img_dir))
    
        return {"freed_bytes": freed_bytes, "freed_files": freed_files, "removed_dirs": removed_dirs}

    def run(self, value: StageInput) -> StageResult:
        return result_of(value, details=self.clean_images(request_of(value).path))
