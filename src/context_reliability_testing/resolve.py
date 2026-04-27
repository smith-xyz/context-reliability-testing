"""Task resolution: load from YAML or auto-derive from git range."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from pathlib import Path

import yaml

from .errors import ConfigError
from .models import (
    Acceptance,
    AcceptanceType,
    EvalTask,
    RunConfig,
    SequentialTask,
)
from .workspace import WorkspaceManager


@dataclass
class ResolvedEval:
    tasks: list[EvalTask]


@dataclass
class ResolvedTimeline:
    tasks: list[SequentialTask]


_RECORD_SEP = "---CRT-RECORD---"


def derive_tasks(
    ws: WorkspaceManager,
    range_spec: str,
    acceptance: Acceptance | None = None,
) -> list[SequentialTask]:
    """Build SequentialTask list from git log over a commit range."""
    if acceptance is None:
        acceptance = Acceptance(type=AcceptanceType.TEST_COMMAND, command="make test")
    fmt = f"%H%x00%s%x00%b{_RECORD_SEP}"
    out = ws.git(["log", "--reverse", f"--format={fmt}", range_spec], cwd=ws.clone_dir)
    tasks: list[SequentialTask] = []
    for order, block in enumerate(out.split(_RECORD_SEP), 1):
        block = block.strip()
        if not block:
            continue
        parts = block.split("\0", 2)
        if len(parts) < 2:
            continue
        sha, subject = parts[0], parts[1]
        body = parts[2].strip() if len(parts) > 2 else ""
        prompt = f"{subject}\n\n{body}".strip() if body else subject
        tasks.append(
            SequentialTask(
                id=f"commit-{sha[:8]}",
                prompt=prompt,
                task_order=order,
                resolved_commit=sha,
                acceptance=acceptance,
                marker=subject,
            )
        )
    return tasks


def resolve_tasks(
    tasks_path: Path | None,
    commit_range: str | None,
    acceptance_cmd: str | None,
    run_cfg: RunConfig,
    output: Path,
) -> ResolvedEval | ResolvedTimeline:
    """Load tasks from YAML or auto-derive from git range.

    Raises ConfigError for invalid or conflicting arguments.
    """
    if tasks_path and commit_range:
        raise ConfigError("specify --tasks or --range, not both")
    if not tasks_path and not commit_range:
        raise ConfigError("provide --tasks or --range")

    if tasks_path:
        raw = yaml.safe_load(tasks_path.read_text())
        items = raw if isinstance(raw, list) else [raw]
        if items and "task_order" in items[0]:
            return ResolvedTimeline(
                sorted(
                    [SequentialTask.model_validate(t) for t in items],
                    key=lambda t: t.task_order,
                )
            )
        return ResolvedEval([EvalTask.model_validate(t) for t in items])

    if not commit_range or not run_cfg.repo:
        raise ConfigError("--range requires 'repo' in run config")

    acceptance = (
        Acceptance(type=AcceptanceType.TEST_COMMAND, command=acceptance_cmd)
        if acceptance_cmd
        else None
    )
    ws = WorkspaceManager(run_cfg.repo.url, output / ".workspace" / "_derive", run_cfg.repo.commit)
    ws.clone()
    try:
        seq_tasks = derive_tasks(ws, commit_range, acceptance)
    finally:
        ws.teardown()
    return ResolvedTimeline(seq_tasks)


def collect_context_paths(run_cfg: RunConfig, workspace: WorkspaceManager | None) -> list[Path]:
    """Gather actual context file paths from conditions for heuristic analysis."""
    if not workspace:
        return []
    files = set(chain.from_iterable(c.context_files for c in run_cfg.conditions.values()))
    clone = workspace.clone_dir
    return [clone / f for f in sorted(files) if (clone / f).exists()]
