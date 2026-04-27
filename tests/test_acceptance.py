"""AcceptanceChecker behavior for test, diff, and manual acceptance."""

from __future__ import annotations

from pathlib import Path

from context_reliability_testing.evaluation.acceptance import AcceptanceChecker, AcceptanceResult
from context_reliability_testing.models import Acceptance, AcceptanceType, EvalTask


def test_run_test_success(git_repo: Path) -> None:
    task = EvalTask(
        id="t1",
        prompt="p",
        acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command="echo hello"),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, git_repo)
    assert res == AcceptanceResult(passed=True, reason="")


def test_run_test_failure(git_repo: Path) -> None:
    task = EvalTask(
        id="t2",
        prompt="p",
        acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command="exit 1"),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, git_repo)
    assert res.passed is False
    assert res.reason


def test_check_diff_expected_present(git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("b\n")
    task = EvalTask(
        id="t3",
        prompt="p",
        acceptance=Acceptance(
            type=AcceptanceType.DIFF_CHECK,
            expected_files=["a.txt"],
        ),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, git_repo)
    assert res == AcceptanceResult(passed=True, reason="")


def test_check_diff_expected_missing(git_repo: Path) -> None:
    (git_repo / "a.txt").write_text("b\n")
    task = EvalTask(
        id="t4",
        prompt="p",
        acceptance=Acceptance(
            type=AcceptanceType.DIFF_CHECK,
            expected_files=["a.txt", "missing.txt"],
        ),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, git_repo)
    assert res.passed is False
    assert "missing.txt" in res.reason


def test_custom_strategy_injection(git_repo: Path) -> None:
    """Verify callers can inject custom acceptance strategies."""

    class AlwaysPass:
        def check(self, task: EvalTask, worktree: Path) -> AcceptanceResult:
            return AcceptanceResult(passed=True, reason="custom")

    checker = AcceptanceChecker(strategies={AcceptanceType.TEST_COMMAND: AlwaysPass()})
    task = EvalTask(
        id="t-custom",
        prompt="p",
        acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command="false"),
    )
    res = checker.check(task, git_repo)
    assert res.passed is True
    assert res.reason == "custom"


def test_manual_returns_not_passed(git_repo: Path) -> None:
    task = EvalTask(
        id="t5",
        prompt="p",
        acceptance=Acceptance(type=AcceptanceType.MANUAL),
    )
    checker = AcceptanceChecker()
    res = checker.check(task, git_repo)
    assert res == AcceptanceResult(passed=False, reason="manual review required")
