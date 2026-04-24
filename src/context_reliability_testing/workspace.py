"""Git workspace lifecycle: bare clone and worktrees."""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import Acceptance, AcceptanceType, SequentialTask

logger = logging.getLogger(__name__)

_RECORD_SEP = "---CRT-RECORD---"


@dataclass
class DiffStat:
    files_changed: list[str]
    lines_added: int
    lines_removed: int


class WorkspaceError(Exception):
    """Raised when a git workspace operation fails."""


class WorkspaceManager:
    """Git operations: clone, worktree create/destroy, diff stats. Never pushes."""

    def __init__(self, repo_url: str, base_dir: Path, pin_commit: str | None = None) -> None:
        self.repo_url = repo_url
        self.base_dir = base_dir.resolve()
        self.pin_commit = pin_commit
        self._clone_dir = self.base_dir / ".clone"
        self._worktrees: list[Path] = []

    @property
    def clone_dir(self) -> Path:
        return self._clone_dir

    def clone(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if self._clone_dir.exists():
            raise WorkspaceError(f"clone destination already exists: {self._clone_dir}")
        self.git(["clone", "--bare", self.repo_url, str(self._clone_dir)])
        self.git(
            ["remote", "set-url", "--push", "origin", "PUSH_DISABLED_BY_CRT"],
            cwd=self._clone_dir,
        )
        if self.pin_commit is not None:
            spec = f"{self.pin_commit}^{{commit}}"
            try:
                self.git(["rev-parse", "--verify", spec], cwd=self._clone_dir)
            except WorkspaceError as e:
                raise WorkspaceError(
                    f"pin_commit not found in repository: {self.pin_commit}"
                ) from e

    def create_worktree(
        self, name: str, commit: str | None = None, persistent: bool = False
    ) -> Path:
        target = commit or self.pin_commit or "HEAD"
        path = self.base_dir / name
        self.git(["worktree", "add", str(path), target], cwd=self._clone_dir)
        if not persistent:
            self._worktrees.append(path)
        logger.debug("worktree created: %s at %s", name, path)
        return path

    def cleanup_worktree(self, worktree: Path) -> None:
        self.git(["worktree", "remove", "--force", str(worktree)], cwd=self._clone_dir)
        if worktree in self._worktrees:
            self._worktrees.remove(worktree)
        logger.debug("worktree removed: %s", worktree)

    def diff_stat(self, worktree: Path, ref: str) -> DiffStat:
        out = self.git(["diff", "--numstat", ref], cwd=worktree)
        return self._parse_numstat(out)

    def diff_stat_range(self, ref_a: str, ref_b: str) -> DiffStat:
        """Diff between two refs on the bare clone (no worktree needed)."""
        out = self.git(["diff", "--numstat", ref_a, ref_b], cwd=self._clone_dir)
        return self._parse_numstat(out)

    def derive_tasks(
        self, range_spec: str, acceptance: Acceptance | None = None
    ) -> list[SequentialTask]:
        """Build SequentialTask list from git log over a commit range.

        Uses a record separator to handle multiline commit messages safely.
        """
        if acceptance is None:
            acceptance = Acceptance(type=AcceptanceType.TEST_COMMAND, command="make test")
        fmt = f"%H%x00%s%x00%b{_RECORD_SEP}"
        out = self.git(["log", "--reverse", f"--format={fmt}", range_spec], cwd=self._clone_dir)
        tasks: list[SequentialTask] = []
        for order, block in enumerate(out.split(_RECORD_SEP), 1):
            block = block.strip()
            if not block:
                continue
            parts = block.split("\0", 2)
            if len(parts) < 2:
                continue
            sha, subject = parts[0], parts[1]
            body = parts[2].strip() if len(parts) > 2 else ""
            prompt = f"{subject}\n\n{body}".strip() if body else subject
            tasks.append(
                SequentialTask(
                    id=f"commit-{sha[:8]}",
                    prompt=prompt,
                    task_order=order,
                    resolved_commit=sha,
                    acceptance=acceptance,
                    marker=subject,
                )
            )
        return tasks

    def teardown(self) -> None:
        for wt in list(self._worktrees):
            self.cleanup_worktree(wt)
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
        logger.debug("workspace torn down: %s", self.base_dir)

    def git(self, args: list[str], cwd: Path | None = None) -> str:
        try:
            proc = subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            err = (e.stderr or "").strip()
            raise WorkspaceError(f"git {' '.join(args)} failed: {err}") from e
        return proc.stdout or ""

    @staticmethod
    def _parse_numstat(out: str) -> DiffStat:
        files: list[str] = []
        added = removed = 0
        for line in out.splitlines():
            parts = line.strip().split("\t", 2)
            if len(parts) < 3:
                continue
            a, r, name = parts
            files.append(name)
            if a != "-" and r != "-":
                added += int(a)
                removed += int(r)
        return DiffStat(files_changed=files, lines_added=added, lines_removed=removed)
