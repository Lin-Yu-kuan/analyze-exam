"""workspace: migrated domain behavior with explicit dependencies."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from datetime import datetime
import hashlib
import json
import os
import secrets
import uuid
from interfaces.commands import CommandRequest, StageInput, StageResult
from plugins._shared.lifecycle import StagePlugin, request_of, result_of
from plugins._shared.files import safe_name



class WorkspacePlugin(StagePlugin):
    id = 'workspace'
    name = 'workspace'


    def digest(self, path: Path) -> str:
        """串流計算內容識別。"""
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else self._digest_stream(stream)

    def _digest_stream(self, stream: Any) -> str:
        result = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
        return result.hexdigest()

    def linked(self, path: Path) -> bool:
        """Windows junction 與符號連結都不追蹤。"""
        return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)

    def read_json(self, path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8-sig") as stream:
            return json.load(stream)

    def source_path(self, source: dict[str, Any], root: Path) -> Path:
        """還原選取來源的實際路徑；優先採用相對題庫根目錄的 relative_path。

    題庫整體搬移後，舊的絕對 path 會失效，但 relative_path 搭配新盤點的 root 仍正確。
    兩者都提供且舊 path 仍存在時必須指向同一檔案，避免靜默換成別的試卷。
    """
        relative = source.get("relative_path")
        absolute = source.get("path")
        if relative:
            parts = PurePosixPath(str(relative).replace("\\", "/"))
            if parts.is_absolute() or ".." in parts.parts or PureWindowsPath(str(relative)).drive:
                raise ValueError("relative_path 必須是題庫根目錄內的相對路徑")
            resolved = (root / Path(*parts.parts)).resolve()
            if absolute:
                old = Path(absolute)
                if old.exists() and old.resolve() != resolved:
                    raise ValueError("selection 的 path 與 relative_path 指向不同檔案")
            return resolved
        if absolute:
            return Path(absolute).resolve()
        raise ValueError("selection 來源需提供 relative_path 或 path")

    def select_sources(self, data: dict[str, Any], selection: dict[str, Any] | None,
                       pages: list[int] | None) -> tuple[Path, list[dict[str, Any]]]:
        """文字與圖片共用同一選取邊界，選取前僅允許首頁預覽。"""
        if not selection and pages != [1]:
            raise ValueError("全面擷取前須提供 selection；無選取時只允許 --pages 1")
        if pages is not None and (not pages or any(page < 1 for page in pages)):
            raise ValueError("頁碼必須是正整數")
        root = Path(data["root"]).resolve(strict=True)
        available = {str(Path(item["path"]).resolve()): item for item in data["files"] if item["status"] == "inventoried"}
        chosen = list(available.values())
        if selection is not None:
            if selection.get("schema_version") != 1 or not selection.get("study_subject"):
                raise ValueError("selection 缺少支援的版本或備考科目")
            chosen = []
            for source in selection.get("selected_sources", []):
                key = str(self.source_path(source, root))
                item = available.get(key)
                if not item or item["sha256"] != source.get("sha256"):
                    raise ValueError("selection 與盤點來源／雜湊不一致，請重新盤點")
                if item not in chosen:
                    chosen.append(item)
            if not chosen:
                raise ValueError("尚未選取來源")
        return root, chosen

    def verify_source(self, path: Path, root: Path, sha: str) -> None:
        """每次使用快取前也重新確認來源位置與內容。"""
        if not path.resolve(strict=True).is_relative_to(root):
            raise ValueError("來源已移出根目錄")
        if any(self.linked(part) for part in [path, *path.parents] if part != root and part.is_relative_to(root)):
            raise ValueError("來源已變成連結")
        if self.digest(path) != sha:
            raise ValueError("來源內容已變更，請重新盤點")

    def run_dir(self, root: Path, subject: str, output_root: Path | None = None) -> Path:
        """在來源 _analysis 或指定的外部輸出根目錄下建立唯一執行目錄。"""
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("來源必須是資料夾")
        if output_root is None:
            analysis_root = root / "_analysis"
            base = analysis_root / safe_name(subject)
            for directory in (analysis_root, base):
                if directory.exists() and self.linked(directory):
                    raise ValueError("輸出目錄不可為連結")
        else:
            raw = Path(output_root)
            if raw.is_symlink():
                raise ValueError("輸出根目錄不可為連結")
            out = raw.resolve()
            if out.exists() and (not out.is_dir() or self.linked(out)):
                raise ValueError("輸出根目錄必須是可寫入的一般資料夾")
            out.mkdir(parents=True, exist_ok=True)
            base = out / safe_name(subject)
            if base.exists() and self.linked(base):
                raise ValueError("輸出目錄不可為連結")
        for _ in range(5):
            candidate = base / f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
            try:
                candidate.mkdir(parents=True, exist_ok=False)
                return candidate
            except FileExistsError:
                continue
        raise ValueError("無法建立唯一執行目錄")

    def write_new(self, path: Path, data: dict[str, Any]) -> None:
        """以暫存檔原子發佈，排他建立輸出；不覆寫既有資料。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("輸出不可為連結")
        temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            with temp.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temp, path)
            except FileExistsError:
                raise FileExistsError(f"輸出已存在：{path}") from None
            except OSError:
                # 少數檔案系統不支援硬連結；退回排他建立，仍不覆寫。
                with path.open("xb") as target, temp.open("rb") as source:
                    target.write(source.read())
                    target.flush()
                    os.fsync(target.fileno())
        finally:
            temp.unlink(missing_ok=True)

    def preflight(self, request: CommandRequest) -> None:
        if request.command in {"inventory", "extract", "prepare", "packet", "merge", "summary", "render"}:
            same_resume = (request.command == "render" and request.resume is not None
                           and request.output.resolve() == request.resume.resolve())
            if (request.output.exists() or request.output.is_symlink()) and not same_resume:
                raise ValueError(f"輸出已存在：{request.output}")
        if request.command == "finalize":
            for name in ("question-index.json", "summary.json"):
                path = request.run_dir / name
                if path.exists() or path.is_symlink():
                    raise ValueError(f"輸出已存在：{path}")

    def run(self, value: StageInput) -> StageResult:
        request = request_of(value)
        if request.command == "revise":
            run = self.resolve_run_dir(request.path)
            return result_of(value, request=replace(request, run_dir=run), artifacts={"run_dir": run})
        run = self.run_dir(request.root, request.subject, request.output_root)
        output = run / "inventory.json" if request.command == "scan" else request.output
        return result_of(value, request=replace(request, run_dir=run, output=output), artifacts={"run_dir": run})


    def find_repo_root(self, run_dir: Path) -> Path | None:
        """若 run_dir 位於題庫的 _analysis/ 目錄下，尋找其對應的題庫根目錄。"""
        cur = run_dir.resolve()
        for parent in [cur, *cur.parents]:
            if parent.name == "_analysis":
                return parent.parent
        return None


    def resolve_run_dir(self, input_path: Path) -> Path:
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
