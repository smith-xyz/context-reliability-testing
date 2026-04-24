"""AcceptanceChecker behavior for test, diff, and manual acceptance."""

from __future__ import annotations

import subprocess
from pathlib import Path

from context_reliability_testing.acceptance import AcceptanceChecker, AcceptanceResult
from context_reliability_testing.models import Acceptance, AcceptanceType, EvalTask


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "t"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    (path / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


def test_run_test_success(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    task = EvalTask(
        id="t1",
        prompt="p",
        acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command="echo hello"),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, tmp_path)
    assert res == AcceptanceResult(passed=True, reason="")


def test_run_test_failure(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    task = EvalTask(
        id="t2",
        prompt="p",
        acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command="exit 1"),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, tmp_path)
    assert res.passed is False
    assert res.reason


def test_check_diff_expected_present(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("b\n")
    task = EvalTask(
        id="t3",
        prompt="p",
        acceptance=Acceptance(
            type=AcceptanceType.DIFF_CHECK,
            expected_files=["a.txt"],
        ),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, tmp_path)
    assert res == AcceptanceResult(passed=True, reason="")


def test_check_diff_expected_missing(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "a.txt").write_text("b\n")
    task = EvalTask(
        id="t4",
        prompt="p",
        acceptance=Acceptance(
            type=AcceptanceType.DIFF_CHECK,
            expected_files=["a.txt", "missing.txt"],
        ),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, tmp_path)
    assert res.passed is False
    assert "missing.txt" in res.reason


def test_custom_strategy_injection(tmp_path: Path) -> None:
    """Verify callers can inject custom acceptance strategies."""
    _init_repo(tmp_path)

    class AlwaysPass:
        def check(self, task: EvalTask, worktree: Path) -> AcceptanceResult:
            return AcceptanceResult(passed=True, reason="custom")

    checker = AcceptanceChecker(strategies={AcceptanceType.TEST_COMMAND: AlwaysPass()})
    task = EvalTask(
        id="t-custom",
        prompt="p",
        acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command="false"),
    )
    res = checker.check(task, tmp_path)
    assert res.passed is True
    assert res.reason == "custom"


def test_manual_returns_not_passed(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    task = EvalTask(
        id="t5",
        prompt="p",
        acceptance=Acceptance(type=AcceptanceType.MANUAL),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, tmp_path)
    assert res == AcceptanceResult(passed=False, reason="manual review required")
