"""Pure path presentation helpers."""
from pathlib import Path
import re
import unicodedata

TOOL_VERSION = "1"

WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}

def portable_relative(path: Path, root: Path) -> str:
    """題庫內相對路徑一律用 `/` 分隔，整個題庫搬移或跨平台後仍可還原。"""
    return path.relative_to(root).as_posix()

def safe_name(value: str, fallback: str = "未命名科目") -> str:
    """清理 Windows／POSIX 都不安全的名稱，保留中文可讀性。"""
    text = unicodedata.normalize("NFC", str(value)).strip()
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        return fallback
    if text.split(".")[0].upper() in WINDOWS_RESERVED:
        text = "_" + text
    text = text[:80].rstrip(" .")
    return text or fallback
