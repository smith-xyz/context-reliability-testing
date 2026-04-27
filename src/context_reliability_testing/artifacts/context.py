"""Stable data contract for trial assertions. No external dependencies."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

_PATH_FIELDS = frozenset({"artifact_dir", "worktree"})


@dataclass(frozen=True)
class TrialContext:
    """Immutable snapshot of a completed trial, provided to user assertion tests."""

    artifact_dir: Path
    worktree: Path
    diff: str
    changed_files: list[str]
    task_id: str
    condition: str
    trial_number: int
    passed: bool

    def to_dict(self) -> dict[str, Any]:
        return {k: str(v) if k in _PATH_FIELDS else v for k, v in dataclasses.asdict(self).items()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrialContext:
        return cls(**{k: Path(v) if k in _PATH_FIELDS else v for k, v in data.items()})

    @cached_property
    def added_lines(self) -> list[str]:
        return [
            line[1:]
            for line in self.diff.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]

    @cached_property
    def removed_lines(self) -> list[str]:
        return [
            line[1:]
            for line in self.diff.splitlines()
            if line.startswith("-") and not line.startswith("---")
        ]

    def read_file(self, path: str) -> str:
        return (self.worktree / path).read_text()

    def file_exists(self, path: str) -> bool:
        return (self.worktree / path).exists()

    def file_contains(self, path: str, pattern: str) -> bool:
        """Check if a worktree file matches a regex pattern."""
        if not self.file_exists(path):
            return False
        return bool(re.search(pattern, self.read_file(path)))
