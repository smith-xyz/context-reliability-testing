"""Acceptance criteria evaluation via pluggable strategies."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import AcceptanceType, EvalTask


@dataclass
class AcceptanceResult:
    passed: bool
    reason: str


class AcceptanceStrategy(Protocol):
    def check(self, task: EvalTask, worktree: Path) -> AcceptanceResult: ...


class TestCommandStrategy:
    """Run a shell command; exit 0 = pass."""

    def __init__(self, *, stream: bool = False) -> None:
        self.stream = stream

    def check(self, task: EvalTask, worktree: Path) -> AcceptanceResult:
        cmd = task.acceptance.command
        if not cmd:
            return AcceptanceResult(
                passed=False, reason="acceptance.command is required for test_command"
            )
        timeout = task.acceptance.timeout_s
        if self.stream:
            return self._run_passthrough(cmd, worktree, timeout)
        return self._run_captured(cmd, worktree, timeout)

    def _run_captured(self, cmd: str, worktree: Path, timeout: int) -> AcceptanceResult:
        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                cwd=worktree,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            return AcceptanceResult(passed=False, reason=f"test command timed out after {timeout}s")
        if proc.returncode == 0:
            return AcceptanceResult(passed=True, reason="")
        err = (proc.stderr or proc.stdout or "").strip() or f"exit code {proc.returncode}"
        return AcceptanceResult(passed=False, reason=err)

    def _run_passthrough(self, cmd: str, worktree: Path, timeout: int) -> AcceptanceResult:
        """Run with stdout inherited so the user sees test output directly."""
        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                cwd=worktree,
                text=True,
            )
        except OSError as exc:
            return AcceptanceResult(passed=False, reason=str(exc))
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return AcceptanceResult(passed=False, reason=f"test command timed out after {timeout}s")
        if proc.returncode == 0:
            return AcceptanceResult(passed=True, reason="")
        return AcceptanceResult(passed=False, reason=f"exit code {proc.returncode}")


class DiffCheckStrategy:
    """Verify expected files appear in git diff output."""

    def check(self, task: EvalTask, worktree: Path) -> AcceptanceResult:
        expected = task.acceptance.expected_files or []
        if not expected:
            return AcceptanceResult(
                passed=False, reason="acceptance.expected_files is required for diff_check"
            )
        try:
            proc = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=worktree,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            return AcceptanceResult(passed=False, reason="git diff timed out after 60s")
        if proc.returncode != 0:
            err = (proc.stderr or "").strip() or f"git diff failed with {proc.returncode}"
            return AcceptanceResult(passed=False, reason=err)
        changed = {line.strip() for line in (proc.stdout or "").splitlines() if line.strip()}
        missing = [f for f in expected if f not in changed]
        if missing:
            return AcceptanceResult(
                passed=False, reason=f"expected files not in diff: {', '.join(missing)}"
            )
        return AcceptanceResult(passed=True, reason="")


class ManualStrategy:
    """Always fails — human must review."""

    def check(self, task: EvalTask, worktree: Path) -> AcceptanceResult:
        return AcceptanceResult(passed=False, reason="manual review required")


def _default_strategies(
    *,
    stream: bool = False,
) -> dict[AcceptanceType, AcceptanceStrategy]:
    return {
        AcceptanceType.TEST_COMMAND: TestCommandStrategy(stream=stream),
        AcceptanceType.DIFF_CHECK: DiffCheckStrategy(),
        AcceptanceType.MANUAL: ManualStrategy(),
    }


class AcceptanceChecker:
    def __init__(
        self,
        strategies: dict[AcceptanceType, AcceptanceStrategy] | None = None,
        *,
        stream: bool = False,
    ) -> None:
        self._strategies = strategies or _default_strategies(stream=stream)

    def check(self, task: EvalTask, worktree: Path) -> AcceptanceResult:
        strategy = self._strategies.get(task.acceptance.type)
        if not strategy:
            return AcceptanceResult(
                passed=False, reason=f"no strategy for {task.acceptance.type!r}"
            )
        return strategy.check(task, worktree)

    def preflight(self, task: EvalTask, worktree: Path) -> AcceptanceResult:
        return self.check(task, worktree)
