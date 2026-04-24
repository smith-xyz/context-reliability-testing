"""Round-trip validation: load sample YAML files through pydantic models."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from context_reliability_testing.models import DriverConfig, EvalTask, RunConfig, SequentialTask

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def test_eval_set_sample_loads() -> None:
    raw = yaml.safe_load((EXAMPLES / "eval-set.sample.yaml").read_text())
    tasks = [EvalTask.model_validate(t) for t in raw]
    assert len(tasks) == 3
    assert tasks[0].id == "fix-panic-on-nil-config"
    assert tasks[0].acceptance.type.value == "test_command"


def test_run_config_sample_loads() -> None:
    raw = yaml.safe_load((EXAMPLES / "run-config.sample.yaml").read_text())
    cfg = RunConfig.model_validate(raw)
    assert "no_context" in cfg.conditions
    assert cfg.conditions["no_context"].context_files == []
    assert cfg.agent.model == "claude-sonnet-4-20250514"
    assert cfg.trials == 1


def test_eval_task_rejects_missing_fields() -> None:
    with pytest.raises(ValueError):
        EvalTask.model_validate({"id": "x"})


def test_run_config_defaults() -> None:
    cfg = RunConfig.model_validate(
        {
            "agent": {"model": "test-model"},
            "conditions": {"bare": {"context_files": []}},
        }
    )
    assert cfg.trials == 1
    assert cfg.agent.temperature == 0
    assert cfg.agent.max_steps == 50


def test_driver_config_validates_exactly_one() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DriverConfig.model_validate({"builtin": "stub", "command": ["echo"]})
    with pytest.raises(ValueError, match="exactly one"):
        DriverConfig.model_validate({})


def test_sequential_task_loads() -> None:
    task = SequentialTask.model_validate(
        {
            "id": "step-a",
            "prompt": "Do the thing",
            "task_order": 1,
            "resolved_commit": "deadbeef",
            "acceptance": {"type": "manual"},
            "marker": "decision-1",
        }
    )
    assert task.id == "step-a"
    assert task.task_order == 1
    assert task.resolved_commit == "deadbeef"
    assert task.marker == "decision-1"


def test_run_config_with_repo() -> None:
    cfg = RunConfig.model_validate(
        {
            "agent": {"model": "test"},
            "conditions": {"bare": {"context_files": []}},
            "repo": {"url": "https://github.com/example/repo", "commit": "main"},
        }
    )
    assert cfg.repo is not None
    assert cfg.repo.url.endswith("repo")
