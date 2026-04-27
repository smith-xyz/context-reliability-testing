"""Tests for the eval runner, preflight, and report pipeline."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from context_reliability_testing.drivers.stub import StubDriver
from context_reliability_testing.errors import PreflightError
from context_reliability_testing.evaluation import (
    AcceptanceChecker,
    EvalRunner,
    PhaseInfo,
    PhaseState,
    TrialExecutor,
)
from context_reliability_testing.models import (
    Acceptance,
    AcceptanceType,
    EvalTask,
    RunConfig,
    RunResult,
)
from context_reliability_testing.reporting import write_result_json, write_summary_md
from context_reliability_testing.workspace import WorkspaceManager

from .conftest import init_repo_with_commits

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def _load_fixtures() -> tuple[RunConfig, list[EvalTask]]:
    cfg = RunConfig.model_validate(
        yaml.safe_load((EXAMPLES / "run-config.sample.yaml").read_text())
    )
    tasks = [
        EvalTask.model_validate(t)
        for t in yaml.safe_load((EXAMPLES / "eval-set.sample.yaml").read_text())
    ]
    return cfg, tasks


def _make_runner(
    cfg: RunConfig,
    tasks: list[EvalTask],
    seed: int = 1,
    workspace: WorkspaceManager | None = None,
) -> EvalRunner:
    driver = StubDriver(seed=seed)
    executor = TrialExecutor(
        workspace=workspace,
        driver=driver,
        checker=AcceptanceChecker(),
        assertion_runner=None,
        config=cfg,
    )
    return EvalRunner(config=cfg, tasks=tasks, executor=executor)


def test_stub_runner_produces_correct_trial_count() -> None:
    cfg, tasks = _load_fixtures()
    runner = _make_runner(cfg, tasks)
    trials = runner.run()
    expected = len(tasks) * len(cfg.conditions) * cfg.trials
    assert len(trials) == expected


def test_stub_runner_deterministic_with_seed() -> None:
    cfg, tasks = _load_fixtures()
    r1 = _make_runner(cfg, tasks, seed=42).run()
    r2 = _make_runner(cfg, tasks, seed=42).run()
    assert [t.passed for t in r1] == [t.passed for t in r2]


def test_report_writes_files(tmp_path: Path) -> None:
    cfg, tasks = _load_fixtures()
    trials = _make_runner(cfg, tasks).run()
    result = RunResult.from_trials(trials, cfg.agent, list(cfg.conditions.keys()))

    rj = write_result_json(result, tmp_path)
    sm = write_summary_md(result, tmp_path)

    assert rj.exists()
    assert sm.exists()
    assert "pass_rate" in rj.read_text()
    assert "## Results by condition" in sm.read_text()


def test_async_runner_produces_correct_trial_count() -> None:
    cfg, tasks = _load_fixtures()
    runner = _make_runner(cfg, tasks)
    trials = asyncio.run(runner.arun(parallel=1))
    expected = len(tasks) * len(cfg.conditions) * cfg.trials
    assert len(trials) == expected


def test_parallel_runner_produces_correct_trial_count() -> None:
    cfg, tasks = _load_fixtures()
    runner = _make_runner(cfg, tasks)
    trials = asyncio.run(runner.arun(parallel=4))
    expected = len(tasks) * len(cfg.conditions) * cfg.trials
    assert len(trials) == expected


def test_parallel_results_sorted_deterministically() -> None:
    cfg, tasks = _load_fixtures()
    runner = _make_runner(cfg, tasks)
    trials = asyncio.run(runner.arun(parallel=4))
    keys = [(t.task_id, t.condition, t.trial_number) for t in trials]
    assert keys == sorted(keys)


def test_parallel_no_artifact_dir_collisions() -> None:
    """With parallel > 1, each trial gets a unique artifact dir (no collisions)."""
    cfg, tasks = _load_fixtures()
    runner = _make_runner(cfg, tasks)
    trials = asyncio.run(runner.arun(parallel=4))
    dirs_or_none = [t.artifact_dir for t in trials]
    dirs = [d for d in dirs_or_none if d is not None]
    assert len(dirs) == len(set(dirs)), "artifact dir collision detected"


def test_parallel_clamps_to_work_items(tmp_path: Path) -> None:
    """Requesting more parallelism than work items is clamped silently."""
    cfg, tasks = _load_fixtures()
    ws = WorkspaceManager(".", tmp_path / "ws")
    runner = _make_runner(cfg, tasks, workspace=ws)
    total = len(tasks) * len(cfg.conditions) * cfg.trials
    effective = runner._effective_parallel(total + 100)
    assert effective == total


def test_parallel_clamps_without_workspace() -> None:
    """Without workspace, parallel is always clamped to 1."""
    cfg, tasks = _load_fixtures()
    runner = _make_runner(cfg, tasks, workspace=None)
    effective = runner._effective_parallel(4)
    assert effective == 1


# ---------------------------------------------------------------------------
# Preflight tests
# ---------------------------------------------------------------------------


class TestPreflight:
    def _make_preflight_runner(
        self, tmp_path: Path, acceptance_cmd: str, *, pass_rate: float = 0.7
    ) -> EvalRunner:
        origin = tmp_path / "origin"
        init_repo_with_commits(origin, 1)
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
        executor = TrialExecutor(
            workspace=ws,
            driver=StubDriver(seed=1, pass_rate=pass_rate),
            checker=AcceptanceChecker(),
            assertion_runner=None,
            config=cfg,
        )
        return EvalRunner(config=cfg, tasks=tasks, executor=executor)

    def test_preflight_passes_on_good_repo(self, tmp_path: Path) -> None:
        runner = self._make_preflight_runner(tmp_path, "true")
        ws = runner._workspace
        assert ws is not None
        wt = ws.create_worktree("preflight")
        try:
            runner._preflight_done = {}
            runner._preflight_task(runner.tasks[0], wt)
        finally:
            ws.cleanup_worktree(wt)
            ws.teardown()

    def test_preflight_raises_on_broken_repo(self, tmp_path: Path) -> None:
        runner = self._make_preflight_runner(tmp_path, "false")
        ws = runner._workspace
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
        runner = self._make_preflight_runner(tmp_path, "false")
        with pytest.raises(PreflightError):
            runner.run()
        runner._workspace.teardown()  # type: ignore[union-attr]

    def test_preflight_deduplicates_same_command(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        init_repo_with_commits(origin, 1)
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
        executor = TrialExecutor(
            workspace=ws,
            driver=StubDriver(seed=1),
            checker=AcceptanceChecker(),
            assertion_runner=None,
            config=cfg,
        )
        runner = EvalRunner(config=cfg, tasks=tasks, executor=executor)
        phases: list[PhaseInfo] = []
        runner.on_progress = lambda phase, _r: phases.append(phase)
        wt = ws.create_worktree("preflight")
        try:
            runner._preflight_done = {}
            runner._preflight_task(tasks[0], wt)
            runner._preflight_task(tasks[1], wt)
            assert any(p.state == PhaseState.SKIPPED for p in phases)
        finally:
            ws.cleanup_worktree(wt)
            ws.teardown()

    def test_preflight_skips_manual(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        init_repo_with_commits(origin, 1)
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
        executor = TrialExecutor(
            workspace=ws,
            driver=StubDriver(seed=1),
            checker=AcceptanceChecker(),
            assertion_runner=None,
            config=cfg,
        )
        runner = EvalRunner(config=cfg, tasks=tasks, executor=executor)
        wt = ws.create_worktree("preflight")
        try:
            runner._preflight_done = {}
            runner._preflight_task(tasks[0], wt)
        finally:
            ws.cleanup_worktree(wt)
            ws.teardown()
