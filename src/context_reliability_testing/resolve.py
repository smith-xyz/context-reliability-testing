"""Task resolution: load from YAML or auto-derive from git range."""

from __future__ import annotations

from itertools import chain
from pathlib import Path

import typer
import yaml
from rich.console import Console

from .models import (
    Acceptance,
    AcceptanceType,
    EvalTask,
    RunConfig,
    SequentialTask,
)
from .workspace import WorkspaceManager


def resolve_tasks(
    tasks_path: Path | None,
    commit_range: str | None,
    acceptance_cmd: str | None,
    run_cfg: RunConfig,
    output: Path,
    console: Console,
) -> list[EvalTask] | list[SequentialTask]:
    """Load tasks from YAML or auto-derive from git range."""
    if tasks_path and commit_range:
        console.print("[red]Error:[/red] specify --tasks or --range, not both.")
        raise typer.Exit(code=1)
    if not tasks_path and not commit_range:
        console.print("[red]Error:[/red] provide --tasks or --range.")
        raise typer.Exit(code=1)

    if tasks_path:
        raw = yaml.safe_load(tasks_path.read_text())
        items = raw if isinstance(raw, list) else [raw]
        if items and "task_order" in items[0]:
            return sorted(
                [SequentialTask.model_validate(t) for t in items],
                key=lambda t: t.task_order,
            )
        return [EvalTask.model_validate(t) for t in items]

    if not run_cfg.repo:
        console.print("[red]Error:[/red] --range requires 'repo' in run config.")
        raise typer.Exit(code=1)

    acceptance = (
        Acceptance(type=AcceptanceType.TEST_COMMAND, command=acceptance_cmd)
        if acceptance_cmd
        else None
    )
    ws = WorkspaceManager(run_cfg.repo.url, output / ".workspace" / "_derive", run_cfg.repo.commit)
    ws.clone()
    try:
        seq_tasks = ws.derive_tasks(commit_range, acceptance)  # type: ignore[arg-type]
    finally:
        ws.teardown()
    console.print(f"Auto-derived {len(seq_tasks)} tasks from {commit_range}")
    return seq_tasks


def collect_context_paths(run_cfg: RunConfig, workspace: WorkspaceManager | None) -> list[Path]:
    """Gather actual context file paths from conditions for heuristic analysis."""
    if not workspace:
        return []
    files = set(chain.from_iterable(c.context_files for c in run_cfg.conditions.values()))
    clone = workspace.clone_dir
    return [clone / f for f in sorted(files) if (clone / f).exists()]
