"""Typed, in-process command and stage envelopes; existing disk schemas stay unchanged."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CommandRequest:
    command: str
    root: Path | None = None
    subject: str | None = None
    output_root: Path | None = None
    manifest: Path | None = None
    inventory: Path | None = None
    selection: Path | None = None
    pages: tuple[int, ...] | None = None
    resume: Path | None = None
    output: Path | None = None
    dpi: float = 150.0
    render_index: Path | None = None
    topics: Path | None = None
    prior: Path | None = None
    no_cache: bool = False
    batch_size: int = 5
    work: Path | None = None
    batch_id: str | None = None
    context: int = 0
    results: tuple[Path, ...] = ()
    visual_review: Path | None = None
    extraction: Path | None = None
    fast_verify: bool = False
    require_complete: bool = False
    index: Path | None = None
    examples: int = 2
    target: str | None = None
    run_dir: Path | None = None
    report: Path | None = None
    summary: Path | None = None
    summary_all: Path | None = None
    no_sync_root: bool = False
    path: Path | None = None


@dataclass(frozen=True)
class StageResult:
    request: CommandRequest
    artifacts: dict[str, Path] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


StageInput = CommandRequest | StageResult
