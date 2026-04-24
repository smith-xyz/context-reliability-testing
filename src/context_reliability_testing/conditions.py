"""Manipulate context files inside a worktree for eval conditions."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .models import Condition

logger = logging.getLogger(__name__)


def apply_condition(
    worktree: Path,
    condition: Condition,
    context_patterns: list[str],
) -> None:
    """Apply a condition to a worktree: strip context files, then restore specified ones.

    If condition.source_dir is set, files are read from that directory.
    Otherwise files are snapshot from the worktree before stripping.
    """
    resolved = worktree.resolve()
    ext_dir = Path(condition.source_dir).resolve() if condition.source_dir else None
    to_remove = {p.resolve() for pat in context_patterns for p in resolved.glob(pat)}

    saved: dict[str, bytes] = {}
    for rel in condition.context_files:
        ext_src = (ext_dir / rel) if ext_dir else None
        wt_src = resolved / rel
        if ext_src and ext_src.is_file():
            saved[rel] = ext_src.read_bytes()
        elif wt_src.is_file():
            saved[rel] = wt_src.read_bytes()
        else:
            raise FileNotFoundError(f"context file not found: {rel}")

    for path in sorted(to_remove, key=lambda p: len(p.parts), reverse=True):
        if not path.exists():
            continue
        if path.is_file() or path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    for rel, content in saved.items():
        dst = (resolved / rel).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)

    logger.debug(
        "applied condition: removed %d paths, restored %d files",
        len(to_remove),
        len(condition.context_files),
    )
