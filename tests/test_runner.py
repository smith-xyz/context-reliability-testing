"""Tests for the eval runner and report pipeline."""

from __future__ import annotations

from pathlib import Path

import yaml

from context_reliability_testing.drivers.stub import StubDriver
from context_reliability_testing.models import EvalTask, RunConfig, RunResult
from context_reliability_testing.report import write_result_json, write_summary_md
from context_reliability_testing.runner import EvalRunner

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


def test_stub_runner_produces_correct_trial_count() -> None:
    cfg, tasks = _load_fixtures()
    runner = EvalRunner(config=cfg, tasks=tasks, driver=StubDriver(seed=1))
    trials = runner.run()
    expected = len(tasks) * len(cfg.conditions) * cfg.trials
    assert len(trials) == expected


def test_stub_runner_deterministic_with_seed() -> None:
    cfg, tasks = _load_fixtures()
    r1 = EvalRunner(config=cfg, tasks=tasks, driver=StubDriver(seed=42)).run()
    r2 = EvalRunner(config=cfg, tasks=tasks, driver=StubDriver(seed=42)).run()
    assert [t.passed for t in r1] == [t.passed for t in r2]


def test_report_writes_files(tmp_path: Path) -> None:
    cfg, tasks = _load_fixtures()
    trials = EvalRunner(config=cfg, tasks=tasks, driver=StubDriver(seed=1)).run()
    result = RunResult.from_trials(trials, cfg.agent, list(cfg.conditions.keys()))

    rj = write_result_json(result, tmp_path)
    sm = write_summary_md(result, tmp_path)

    assert rj.exists()
    assert sm.exists()
    assert "pass_rate" in rj.read_text()
    assert "## Results by condition" in sm.read_text()
