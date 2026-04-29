"""CLI errors from the user's perspective: bad paths, bad files, bad flag combos.

These tests invoke the real Typer app exactly as `crt` does — assert on exit code
and on phrases a human would see, not on internal helpers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from context_reliability_testing.cli import app
from context_reliability_testing.models import (
    AgentConfig,
    ConditionSummary,
    RunResult,
    TrialResult,
)


@pytest.fixture()
def cli() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def minimal_run_config(tmp_path: Path) -> Path:
    """Valid stub-driver config so `crt run` can reach task resolution."""
    path = tmp_path / "crt-config.yaml"
    path.write_text(
        """
driver:
  builtin: stub
agent:
  model: test-model
conditions:
  only:
    context_files: []
trials: 1
""".strip(),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def minimal_tasks(tmp_path: Path) -> Path:
    path = tmp_path / "tasks.yaml"
    path.write_text(
        """
- id: t1
  prompt: noop
  acceptance:
    type: manual
""".strip(),
        encoding="utf-8",
    )
    return path


def _write_minimal_results_json(path: Path) -> None:
    rr = RunResult(
        run_id="run-a",
        timestamp=datetime.now(UTC),
        agent=AgentConfig(model="m"),
        trials=[
            TrialResult(task_id="t", condition="only", trial_number=1, passed=True),
        ],
        summary={"only": ConditionSummary(pass_rate=1.0, total_tasks=1)},
    )
    path.write_text(rr.model_dump_json(), encoding="utf-8")


def _combined(run_result: object) -> str:
    """Typer/Click Result: stdout + stderr."""
    stdout = getattr(run_result, "stdout", None) or ""
    stderr = getattr(run_result, "stderr", None) or ""
    return stdout + stderr


def test_crt_compare_errors_when_baseline_file_missing(cli: CliRunner, tmp_path: Path) -> None:
    cur = tmp_path / "current.json"
    _write_minimal_results_json(cur)
    result = cli.invoke(
        app,
        ["compare", "--baseline", str(tmp_path / "absent.json"), "--current", str(cur)],
    )
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "baseline" in out.lower()
    assert "cannot read" in out.lower()


def test_crt_compare_errors_when_current_file_missing(cli: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "baseline.json"
    _write_minimal_results_json(base)
    result = cli.invoke(
        app,
        ["compare", "--baseline", str(base), "--current", str(tmp_path / "nope.json")],
    )
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "current" in out.lower()
    assert "cannot read" in out.lower()


def test_crt_compare_errors_when_results_json_invalid(cli: CliRunner, tmp_path: Path) -> None:
    base = tmp_path / "b.json"
    cur = tmp_path / "c.json"
    base.write_text("{}", encoding="utf-8")
    cur.write_text("{}", encoding="utf-8")
    result = cli.invoke(app, ["compare", "-b", str(base), "-C", str(cur)])
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "invalid results json" in out.lower()


def test_crt_run_errors_when_config_file_missing(
    cli: CliRunner, tmp_path: Path, minimal_tasks: Path
) -> None:
    result = cli.invoke(
        app,
        ["run", "--config", str(tmp_path / "missing.yaml"), "--tasks", str(minimal_tasks)],
    )
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "config" in out.lower()
    assert "cannot read" in out.lower()


def test_crt_run_errors_when_config_yaml_broken(
    cli: CliRunner, tmp_path: Path, minimal_tasks: Path
) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("this is not: [ valid yaml :::\n", encoding="utf-8")
    result = cli.invoke(app, ["run", "-c", str(cfg), "-t", str(minimal_tasks)])
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "invalid yaml" in out.lower()


def test_crt_run_errors_when_config_not_a_run_config(
    cli: CliRunner, tmp_path: Path, minimal_tasks: Path
) -> None:
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text("only_unknown: true\n", encoding="utf-8")
    result = cli.invoke(app, ["run", "-c", str(cfg), "-t", str(minimal_tasks)])
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "invalid run config" in out.lower()


def test_crt_run_errors_when_tasks_file_missing(
    cli: CliRunner, tmp_path: Path, minimal_run_config: Path
) -> None:
    result = cli.invoke(
        app,
        ["run", "-c", str(minimal_run_config), "-t", str(tmp_path / "gone.yaml")],
    )
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "tasks" in out.lower()
    assert "cannot read" in out.lower()


def test_crt_run_errors_when_tasks_yaml_broken(
    cli: CliRunner, tmp_path: Path, minimal_run_config: Path
) -> None:
    t = tmp_path / "tasks.yaml"
    t.write_text("{broken", encoding="utf-8")
    result = cli.invoke(app, ["run", "-c", str(minimal_run_config), "-t", str(t)])
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "invalid yaml" in out.lower()
    assert "tasks" in out.lower()


def test_crt_run_errors_when_user_passes_tasks_and_range(
    cli: CliRunner, minimal_run_config: Path, minimal_tasks: Path
) -> None:
    result = cli.invoke(
        app,
        [
            "run",
            "-c",
            str(minimal_run_config),
            "-t",
            str(minimal_tasks),
            "-r",
            "HEAD~1..HEAD",
        ],
    )
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "not both" in out.lower()


def test_crt_run_errors_when_user_passes_neither_tasks_nor_range(
    cli: CliRunner, minimal_run_config: Path
) -> None:
    result = cli.invoke(app, ["run", "-c", str(minimal_run_config)])
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "tasks" in out.lower() and "range" in out.lower()


def test_crt_run_errors_when_range_used_without_repo_in_config(
    cli: CliRunner, tmp_path: Path
) -> None:
    cfg = tmp_path / "no-repo.yaml"
    cfg.write_text(
        """
driver:
  builtin: stub
agent:
  model: m
conditions:
  only:
    context_files: []
trials: 1
""".strip(),
        encoding="utf-8",
    )
    result = cli.invoke(app, ["run", "-c", str(cfg), "-r", "HEAD~1..HEAD"])
    assert result.exit_code == 1
    out = _combined(result)
    assert "Error" in out
    assert "repo" in out.lower()
    assert "range" in out.lower()
