"""Tests for gap fixes: auto-derive, actual-files, timeout, trials warning,
anchored mode, preflight, and diff_stat_range."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from context_reliability_testing.acceptance import AcceptanceChecker
from context_reliability_testing.drivers.stub import StubDriver
from context_reliability_testing.models import (
    Acceptance,
    AcceptanceType,
    EvalTask,
    RunConfig,
    TimelineMode,
)
from context_reliability_testing.runner import EvalRunner, PreflightError
from context_reliability_testing.workspace import WorkspaceManager


def _run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo_with_commits(path: Path, num_commits: int = 3) -> list[str]:
    """Create a repo with N commits, return list of SHAs oldest-first."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init"], cwd=path)
    _run_git(["config", "user.email", "t@t"], cwd=path)
    _run_git(["config", "user.name", "t"], cwd=path)
    shas: list[str] = []
    for i in range(num_commits):
        (path / f"file{i}.txt").write_text(f"content {i}\n")
        _run_git(["add", "."], cwd=path)
        _run_git(["commit", "-m", f"commit {i}: add file{i}"], cwd=path)
        shas.append(_run_git(["rev-parse", "HEAD"], cwd=path))
    return shas


# --- Gap 1: auto-derive ---


class TestAutoDerive:
    def test_derive_tasks_from_range(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        shas = _init_repo_with_commits(origin, 3)
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        tasks = ws.derive_tasks(f"{shas[0]}..{shas[2]}")
        assert len(tasks) == 2
        assert tasks[0].resolved_commit == shas[1]
        assert tasks[1].resolved_commit == shas[2]
        assert tasks[0].task_order == 1
        assert tasks[1].task_order == 2
        assert "file1" in tasks[0].prompt
        ws.teardown()

    def test_derive_tasks_custom_acceptance(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        shas = _init_repo_with_commits(origin, 2)
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        acc = Acceptance(type=AcceptanceType.TEST_COMMAND, command="go test ./...")
        tasks = ws.derive_tasks(f"{shas[0]}..{shas[1]}", acceptance=acc)
        assert len(tasks) == 1
        assert tasks[0].acceptance.command == "go test ./..."
        ws.teardown()

    def test_derive_tasks_empty_range(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        shas = _init_repo_with_commits(origin, 1)
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        tasks = ws.derive_tasks(f"{shas[0]}..{shas[0]}")
        assert tasks == []
        ws.teardown()


# --- Gap 2: diff_stat_range ---


class TestDiffStatRange:
    def test_diff_stat_range_shows_changed_files(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        shas = _init_repo_with_commits(origin, 3)
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        ds = ws.diff_stat_range(f"{shas[1]}~1", shas[1])
        assert "file1.txt" in ds.files_changed
        assert ds.lines_added >= 1
        ws.teardown()


# --- Gap 4: timeout ---


class TestTimeout:
    def test_acceptance_timeout_default(self) -> None:
        acc = Acceptance(type=AcceptanceType.TEST_COMMAND, command="echo hi")
        assert acc.timeout_s == 300

    def test_acceptance_timeout_custom(self) -> None:
        acc = Acceptance(type=AcceptanceType.TEST_COMMAND, command="echo hi", timeout_s=60)
        assert acc.timeout_s == 60

    def test_timeout_used_by_checker(self, tmp_path: Path) -> None:
        tmp_path.mkdir(exist_ok=True)
        subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "t"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )
        (tmp_path / "x").write_text("x")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "i"], cwd=tmp_path, check=True, capture_output=True)
        task = EvalTask(
            id="t",
            prompt="p",
            acceptance=Acceptance(
                type=AcceptanceType.TEST_COMMAND,
                command="sleep 5",
                timeout_s=1,
            ),
        )
        checker = AcceptanceChecker()
        result = checker.check(task, tmp_path)
        assert not result.passed
        assert "timed out" in result.reason


# --- Gap 5: trials warning ---


class TestTrialsWarning:
    def test_warns_on_deterministic_trials(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            RunConfig.model_validate(
                {
                    "agent": {"model": "m", "temperature": 0},
                    "conditions": {"bare": {"context_files": []}},
                    "trials": 3,
                }
            )
        assert any("temperature=0" in r.message for r in caplog.records)

    def test_no_warning_with_nonzero_temp(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            RunConfig.model_validate(
                {
                    "agent": {"model": "m", "temperature": 0.7},
                    "conditions": {"bare": {"context_files": []}},
                    "trials": 3,
                }
            )
        assert not any("temperature=0" in r.message for r in caplog.records)

    def test_no_warning_single_trial(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            RunConfig.model_validate(
                {
                    "agent": {"model": "m", "temperature": 0},
                    "conditions": {"bare": {"context_files": []}},
                    "trials": 1,
                }
            )
        assert not any("temperature=0" in r.message for r in caplog.records)


# --- Gap 6: TimelineMode enum ---


class TestTimelineMode:
    def test_enum_values(self) -> None:
        assert TimelineMode.CONTINUOUS.value == "continuous"
        assert TimelineMode.ANCHORED.value == "anchored"


# --- Gap 7: preflight ---


class TestPreflight:
    def _make_runner(
        self, tmp_path: Path, acceptance_cmd: str, *, pass_rate: float = 0.7
    ) -> EvalRunner:
        origin = tmp_path / "origin"
        _init_repo_with_commits(origin, 1)
        cfg = RunConfig.model_validate(
            {
                "agent": {"model": "m"},
                "conditions": {"bare": {"context_files": []}},
                "repo": {"url": str(origin), "commit": "HEAD"},
            }
        )
        tasks = [
            EvalTask(
                id="t1",
                prompt="p",
                acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command=acceptance_cmd),
            )
        ]
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        return EvalRunner(
            config=cfg,
            tasks=tasks,
            driver=StubDriver(seed=1, pass_rate=pass_rate),
            workspace=ws,
        )

    def test_preflight_passes_on_good_repo(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path, "true")
        ws = runner.workspace
        assert ws is not None
        wt = ws.create_worktree("preflight")
        try:
            runner._preflight_done = {}
            runner._preflight_task(runner.tasks[0], wt)
        finally:
            ws.cleanup_worktree(wt)
            ws.teardown()

    def test_preflight_raises_on_broken_repo(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path, "false")
        ws = runner.workspace
        assert ws is not None
        wt = ws.create_worktree("preflight")
        try:
            runner._preflight_done = {}
            with pytest.raises(PreflightError, match="Preflight failed"):
                runner._preflight_task(runner.tasks[0], wt)
        finally:
            ws.cleanup_worktree(wt)
            ws.teardown()

    def test_run_calls_preflight(self, tmp_path: Path) -> None:
        runner = self._make_runner(tmp_path, "false")
        with pytest.raises(PreflightError):
            runner.run()
        runner.workspace.teardown()  # type: ignore[union-attr]

    def test_preflight_deduplicates_same_command(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        _init_repo_with_commits(origin, 1)
        cfg = RunConfig.model_validate(
            {
                "agent": {"model": "m"},
                "conditions": {"bare": {"context_files": []}},
                "repo": {"url": str(origin), "commit": "HEAD"},
            }
        )
        tasks = [
            EvalTask(
                id="t1",
                prompt="p",
                acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command="true"),
            ),
            EvalTask(
                id="t2",
                prompt="p",
                acceptance=Acceptance(type=AcceptanceType.TEST_COMMAND, command="true"),
            ),
        ]
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        runner = EvalRunner(config=cfg, tasks=tasks, driver=StubDriver(seed=1), workspace=ws)
        phases: list[str] = []
        runner.on_progress = lambda phase, _r: phases.append(phase)
        wt = ws.create_worktree("preflight")
        try:
            runner._preflight_done = {}
            runner._preflight_task(tasks[0], wt)
            runner._preflight_task(tasks[1], wt)
            assert any("skipped" in p for p in phases)
        finally:
            ws.cleanup_worktree(wt)
            ws.teardown()

    def test_preflight_skips_manual(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        _init_repo_with_commits(origin, 1)
        cfg = RunConfig.model_validate(
            {
                "agent": {"model": "m"},
                "conditions": {"bare": {"context_files": []}},
                "repo": {"url": str(origin), "commit": "HEAD"},
            }
        )
        tasks = [
            EvalTask(
                id="t1",
                prompt="p",
                acceptance=Acceptance(type=AcceptanceType.MANUAL),
            )
        ]
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        runner = EvalRunner(config=cfg, tasks=tasks, driver=StubDriver(seed=1), workspace=ws)
        wt = ws.create_worktree("preflight")
        try:
            runner._preflight_done = {}
            runner._preflight_task(tasks[0], wt)
        finally:
            ws.cleanup_worktree(wt)
            ws.teardown()
