"""Scaffold starter configs by detecting context files in a repo."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from pathlib import Path

import yaml

_CONTEXT_GLOBS = [
    "AGENTS.md",
    "CLAUDE.md",
    ".cursorrules",
    ".cursor/rules/*.md",
    ".cursor/rules/*.mdc",
    ".github/copilot-instructions.md",
]

_MISSPELLING_MAP: dict[str, str] = {
    "agents.md": "AGENTS.md",
    "agent.md": "AGENTS.md",
    "claude.md": "CLAUDE.md",
    "copilot.md": ".github/copilot-instructions.md",
}


_SAMPLE_ASSERTIONS = '''\
"""Quality assertions run after each trial. Customize for your project."""


def test_agent_made_changes(trial):
    assert trial.changed_files, "Agent didn't change any files"


def test_reasonable_diff_size(trial):
    assert len(trial.changed_files) <= 10, (
        f"Agent modified {len(trial.changed_files)} files"
    )
'''


@dataclass
class ScaffoldResult:
    detected_files: list[str]
    warnings: list[str]
    config_yaml: str
    tasks_yaml: str
    assertions_py: str


def scaffold(
    repo_dir: Path,
    *,
    test_cmd: str | None = None,
    model: str = "claude-sonnet-4-20250514",
) -> ScaffoldResult:
    detected = _detect_context_files(repo_dir)
    warnings = _check_naming(repo_dir)

    config = _build_config(detected, model=model)
    tasks = _build_tasks(test_cmd=test_cmd)

    return ScaffoldResult(
        detected_files=detected,
        warnings=warnings,
        config_yaml=yaml.dump(config, default_flow_style=False, sort_keys=False),
        tasks_yaml=yaml.dump(tasks, default_flow_style=False, sort_keys=False),
        assertions_py=_SAMPLE_ASSERTIONS,
    )


def _detect_context_files(repo_dir: Path) -> list[str]:
    paths = chain.from_iterable(repo_dir.glob(pat) for pat in _CONTEXT_GLOBS)
    return sorted({str(p.relative_to(repo_dir)) for p in paths if p.is_file()})


def _check_naming(repo_dir: Path) -> list[str]:
    """Flag non-standard context file names."""
    warnings: list[str] = []
    for wrong, correct in _MISSPELLING_MAP.items():
        if (repo_dir / wrong).is_file() and not (repo_dir / correct).is_file():
            warnings.append(f"Found '{wrong}' — the standard name is '{correct}'")
    return warnings


def _build_config(detected: list[str], model: str) -> dict:
    conditions: dict[str, dict] = {"no_context": {"context_files": []}}
    if detected:
        conditions["full_context"] = {"context_files": detected}

    return {
        "agent": {
            "model": model,
            "temperature": 0,
            "max_steps": 50,
        },
        "context_patterns": _CONTEXT_GLOBS,
        "conditions": conditions,
        "trials": 1,
        "output_dir": "out/",
        "driver": {"builtin": "stub"},
        "prompt_template": (
            "You are working in a Git repository. Your task:\n\n"
            "{prompt}\n\n"
            "When done, this command verifies your work: {acceptance_cmd}"
        ),
    }


def _build_tasks(test_cmd: str | None = None) -> list[dict]:
    acceptance: dict = {
        "type": "test_command",
        "command": test_cmd or "echo 'replace with real test command'",
    }

    return [
        {
            "id": "sample-task",
            "prompt": "Replace this with a real task prompt describing what the agent should do.",
            "acceptance": acceptance,
            "assertions": "crt_assertions.py",
            "metadata": {"difficulty": "easy", "category": "sample"},
        },
    ]
