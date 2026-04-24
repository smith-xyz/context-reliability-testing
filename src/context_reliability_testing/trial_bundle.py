"""TrialBundle: factory that collects trial artifacts and produces TrialContext."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

from .trial_context import TrialContext

logger = logging.getLogger(__name__)


class TrialBundle:
    """Collects trial artifacts, writes them to a persistent directory,
    and produces a frozen TrialContext for assertion tests."""

    def __init__(
        self,
        output_dir: Path,
        task_id: str,
        condition: str,
        trial_number: int,
        worktree: Path,
        passed: bool,
        exclude_patterns: list[str] | None = None,
    ) -> None:
        ts = int(time.time())
        self._artifact_dir = (
            output_dir / "artifacts" / f"{task_id}-{condition}-{trial_number}-{ts}"
        ).resolve()
        self._task_id = task_id
        self._condition = condition
        self._trial_number = trial_number
        self._worktree = worktree
        self._passed = passed
        self._exclude_patterns = exclude_patterns or []
        self._diff: str = ""
        self._changed_files: list[str] = []

    @property
    def artifact_dir(self) -> Path:
        return self._artifact_dir

    def capture_diff(self) -> None:
        """Run git diff HEAD in the worktree, excluding context pattern files."""
        cmd = ["git", "diff", "HEAD", "--"]
        for pat in self._exclude_patterns:
            cmd.append(f":!{pat}")
        try:
            result = subprocess.run(
                cmd,
                cwd=self._worktree,
                capture_output=True,
                text=True,
                check=False,
            )
            self._diff = result.stdout
        except OSError:
            logger.warning("git not available for diff capture")
            return

        self._changed_files = list(
            {
                line.split(" b/")[-1]
                for line in self._diff.splitlines()
                if line.startswith("diff --git")
            }
        )

    def write(self) -> None:
        """Persist all collected artifacts to disk."""
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        if self._diff:
            (self._artifact_dir / "diff.patch").write_text(self._diff)
        if self._changed_files:
            (self._artifact_dir / "changed_files.txt").write_text(
                "\n".join(sorted(self._changed_files)) + "\n"
            )

    def write_context_json(self) -> Path:
        """Serialize TrialContext to context.json in artifact dir. Returns path."""
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        ctx_path = self._artifact_dir / "context.json"
        ctx_path.write_text(
            json.dumps(
                {
                    "artifact_dir": str(self._artifact_dir),
                    "worktree": str(self._worktree),
                    "diff": self._diff,
                    "changed_files": self._changed_files,
                    "task_id": self._task_id,
                    "condition": self._condition,
                    "trial_number": self._trial_number,
                    "passed": self._passed,
                }
            )
        )
        return ctx_path

    def to_context(self) -> TrialContext:
        """Produce the frozen TrialContext for pytest consumption."""
        return TrialContext(
            artifact_dir=self._artifact_dir,
            worktree=self._worktree,
            diff=self._diff,
            changed_files=self._changed_files,
            task_id=self._task_id,
            condition=self._condition,
            trial_number=self._trial_number,
            passed=self._passed,
        )
