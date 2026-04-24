"""Tests for crt init scaffolding."""

from __future__ import annotations

from pathlib import Path

import yaml

from context_reliability_testing.init import scaffold


def test_scaffold_detects_agents_md(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Rules\n- Do not hardcode\n")
    result = scaffold(tmp_path)
    assert "AGENTS.md" in result.detected_files
    config = yaml.safe_load(result.config_yaml)
    assert "full_context" in config["conditions"]
    assert "AGENTS.md" in config["conditions"]["full_context"]["context_files"]


def test_scaffold_detects_cursor_rules(tmp_path: Path) -> None:
    rules_dir = tmp_path / ".cursor" / "rules"
    rules_dir.mkdir(parents=True)
    (rules_dir / "go.md").write_text("# Go rules\n")
    result = scaffold(tmp_path)
    assert ".cursor/rules/go.md" in result.detected_files


def test_scaffold_no_context_files(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hi')\n")
    result = scaffold(tmp_path)
    assert result.detected_files == []
    config = yaml.safe_load(result.config_yaml)
    assert "no_context" in config["conditions"]
    assert "full_context" not in config["conditions"]


def test_scaffold_test_cmd_flows_to_tasks(tmp_path: Path) -> None:
    result = scaffold(tmp_path, test_cmd="pytest -x")
    tasks = yaml.safe_load(result.tasks_yaml)
    assert tasks[0]["acceptance"]["command"] == "pytest -x"


def test_scaffold_model_override(tmp_path: Path) -> None:
    result = scaffold(tmp_path, model="gpt-4o")
    config = yaml.safe_load(result.config_yaml)
    assert config["agent"]["model"] == "gpt-4o"


def test_scaffold_warns_nonstandard_naming(tmp_path: Path) -> None:
    (tmp_path / "agent.md").write_text("# Rules\n")
    result = scaffold(tmp_path)
    assert result.detected_files == []
    assert any("agent.md" in w and "AGENTS.md" in w for w in result.warnings)


def test_scaffold_no_warning_when_standard_exists(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# Rules\n")
    (tmp_path / "agents.md").write_text("# Alias\n")
    result = scaffold(tmp_path)
    assert "AGENTS.md" in result.detected_files
    assert not any("agents.md" in w for w in result.warnings)
