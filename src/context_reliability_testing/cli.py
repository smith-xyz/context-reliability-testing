"""CLI entrypoint for context-reliability-testing (crt)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml
from jinja2 import Environment, FileSystemLoader
from rich.console import Console

from .acceptance import AcceptanceChecker
from .assertions import AssertionRunner
from .drivers import make_driver
from .drivers.stub import StubDriver
from .init import scaffold
from .models import (
    RunConfig,
    RunResult,
    SequentialTask,
    TimelineMode,
)
from .progress import run_headless, run_streaming
from .report import write_result_json, write_summary_md
from .resolve import collect_context_paths, resolve_tasks
from .runner import EvalRunner, PreflightError
from .timeline import TimelineRunner
from .workspace import WorkspaceManager

app = typer.Typer(help="context-reliability-testing: A/B test coding-agent context stacks.")

_TEMPLATES = Path(__file__).parent / "templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# crt init
# ---------------------------------------------------------------------------


@app.command()
def init(
    directory: Path = typer.Argument(Path("."), help="Repo directory to scaffold configs for."),
    output: Path = typer.Option(Path("."), "--output", "-o", help="Where to write config files."),
    test_cmd: str | None = typer.Option(
        None,
        "--test-cmd",
        help="Test command for acceptance (e.g. 'pytest', 'go test ./...').",
    ),
    model: str = typer.Option(
        "claude-sonnet-4-20250514",
        "--model",
        "-m",
        help="Model name for run config.",
    ),
) -> None:
    """Scaffold starter config files by detecting context files in a repo."""
    result = scaffold(directory.resolve(), test_cmd=test_cmd, model=model)
    out = output.resolve()
    out.mkdir(parents=True, exist_ok=True)

    config_path = out / "crt-config.yaml"
    tasks_path = out / "crt-tasks.yaml"
    assertions_path = out / "crt_assertions.py"
    config_path.write_text(result.config_yaml, encoding="utf-8")
    tasks_path.write_text(result.tasks_yaml, encoding="utf-8")
    assertions_path.write_text(result.assertions_py, encoding="utf-8")

    for warn in result.warnings:
        typer.echo(f"⚠  {warn}", err=True)
    typer.echo(f"Detected context files: {', '.join(result.detected_files) or 'none'}")
    typer.echo(f"Config:      {config_path}")
    typer.echo(f"Tasks:       {tasks_path}")
    typer.echo(f"Assertions:  {assertions_path}")
    typer.echo("\nNext steps:")
    typer.echo(f"  1. Edit {tasks_path.name} — replace sample tasks with real ones from your repo")
    typer.echo(f"  2. Edit {assertions_path.name} — add quality checks for agent output")
    typer.echo(
        f"  3. Dry run:  crt run --config {config_path.name} --tasks {tasks_path.name} --dry-run"
    )
    typer.echo(f"  4. Real run: crt run --config {config_path.name} --tasks {tasks_path.name}")


# ---------------------------------------------------------------------------
# crt run  (unified eval + timeline)
# ---------------------------------------------------------------------------


@app.command()
def run(
    config: Path = typer.Option(..., "--config", "-c", help="Run config YAML."),
    tasks: Path | None = typer.Option(None, "--tasks", "-t", help="Task set YAML."),
    commit_range: str | None = typer.Option(
        None,
        "--range",
        "-r",
        help="Auto-derive tasks from git range (e.g. HEAD~5..HEAD).",
    ),
    acceptance_cmd: str | None = typer.Option(
        None,
        "--acceptance-cmd",
        help="Default test command for auto-derived tasks.",
    ),
    mode: TimelineMode = typer.Option(
        TimelineMode.CONTINUOUS,
        "--mode",
        "-m",
        help="Task progression: continuous (build on output), anchored (reset per task).",
    ),
    output: Path = typer.Option("out/", "--output", "-o", help="Output directory."),
    seed: int = typer.Option(42, "--seed", help="RNG seed for stub driver."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show work scope without running."),
    stream: bool = typer.Option(
        False,
        "--stream",
        help="Show agent output directly (disables metric capture).",
    ),
    keep_worktrees: bool = typer.Option(
        True,
        "--keep-worktrees/--cleanup",
        help="Keep trial worktrees for inspection (default) or clean up after run.",
    ),
) -> None:
    """Run tasks under different context conditions and measure results."""
    run_cfg = RunConfig.model_validate(yaml.safe_load(config.read_text()))
    out_dir = output if output != Path("out/") else run_cfg.output_dir
    run_cfg.output_dir = out_dir
    console = Console()

    headless = not stream
    resolved = resolve_tasks(tasks, commit_range, acceptance_cmd, run_cfg, out_dir, console)

    is_timeline = (
        isinstance(resolved, list) and resolved and isinstance(resolved[0], SequentialTask)
    )

    if is_timeline:
        _run_timeline(run_cfg, resolved, mode, out_dir, stream, console, dry_run)  # type: ignore[arg-type]
    else:
        _run_eval(  # type: ignore[arg-type]
            run_cfg,
            resolved,
            out_dir,
            stream,
            seed,
            console,
            dry_run,
            headless,
            keep_worktrees,
        )


# ---------------------------------------------------------------------------
# crt run — eval mode
# ---------------------------------------------------------------------------


def _run_eval(
    run_cfg: RunConfig,
    eval_tasks: list,
    out_dir: Path,
    stream: bool,
    seed: int,
    console: Console,
    dry_run: bool,
    headless: bool,
    keep_worktrees: bool = True,
) -> None:
    total = len(eval_tasks) * len(run_cfg.conditions) * run_cfg.trials
    console.print(
        f"[bold]{len(eval_tasks)} tasks x {len(run_cfg.conditions)} conditions"
        f" x {run_cfg.trials} trials = {total} invocations[/bold]"
    )

    if dry_run:
        console.print("Dry run — no agents invoked.")
        raise typer.Exit(0)

    driver = (
        StubDriver(seed=seed)
        if run_cfg.driver.builtin == "stub"
        else make_driver(run_cfg.driver, stream=stream)
    )
    workspace = None
    if run_cfg.repo:
        workspace = WorkspaceManager(run_cfg.repo.url, out_dir / ".workspace", run_cfg.repo.commit)
        console.print("[dim]Cloning repo...[/dim]")
        workspace.clone()

    checker = AcceptanceChecker(stream=stream)
    has_assertions = any(t.assertions for t in eval_tasks)
    assertion_runner = AssertionRunner() if has_assertions else None

    runner = EvalRunner(
        config=run_cfg,
        tasks=eval_tasks,
        driver=driver,
        workspace=workspace,
        checker=checker,
        assertion_runner=assertion_runner,
        keep_worktrees=keep_worktrees,
    )
    try:
        if headless:
            trials = run_headless(runner, console, total)
        else:
            trials = run_streaming(runner, console, total)
    except PreflightError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None

    context_paths = collect_context_paths(run_cfg, workspace)
    result = RunResult.from_trials(trials, run_cfg.agent, list(run_cfg.conditions.keys()))

    all_failures = [(t, a) for t in trials for a in t.assertion_results if not a.passed]
    if all_failures:
        console.print(f"\n[red bold]{len(all_failures)} assertion failure(s):[/red bold]")
        for t, a in all_failures:
            msg = f"  {a.message}" if a.message else ""
            console.print(f"  [red]✗[/red] {t.task_id}/{t.condition}: {a.name}{msg}")
        console.print()

    console.print(f"Results: {write_result_json(result, out_dir)}")
    summary_path = write_summary_md(
        result,
        out_dir,
        context_files=context_paths,
        heuristics_config=run_cfg.heuristics_config,
    )
    console.print(f"Summary: {summary_path}")
    if workspace:
        if keep_worktrees:
            console.print(f"[dim]Worktrees preserved at: {workspace.base_dir}[/dim]")
        else:
            workspace.teardown()
            console.print("[dim]Worktrees cleaned up.[/dim]")


# ---------------------------------------------------------------------------
# crt run — timeline mode
# ---------------------------------------------------------------------------


def _run_timeline(
    run_cfg: RunConfig,
    seq_tasks: list,
    mode: TimelineMode,
    out_dir: Path,
    stream: bool,
    console: Console,
    dry_run: bool,
) -> None:
    if not run_cfg.repo:
        console.print("[red]Error:[/red] timeline tasks require 'repo' in run config.")
        raise typer.Exit(code=1)

    out_dir.mkdir(parents=True, exist_ok=True)
    console.print(
        f"[bold]{len(seq_tasks)} tasks x {len(run_cfg.conditions)} conditions"
        f" | mode={mode.value}[/bold]"
    )

    if dry_run:
        console.print("Dry run — no agents invoked.")
        raise typer.Exit(0)

    def on_step(order: int, task_id: str, passed: bool, divergence: int) -> None:
        status = "[green]PASS[/green]" if passed else "[red]FAIL[/red]"
        console.print(f"  Step {order}: {task_id} — {status} | divergence: {divergence} lines")

    runner = TimelineRunner(
        config=run_cfg,
        tasks=seq_tasks,
        driver=make_driver(run_cfg.driver, stream=stream),
        mode=mode,
        on_step=on_step,
    )
    try:
        reports = runner.run(out_dir)
    except PreflightError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None

    env = _jinja_env()
    for rpt in reports:
        console.print(f"\n=== Condition: {rpt.condition} ===")
        report_path = out_dir / f"TIMELINE-{rpt.condition}.md"
        report_path.write_text(
            env.get_template("timeline.md.j2").render(
                run_id=rpt.run_id,
                repo_url=run_cfg.repo.url,
                start_commit=run_cfg.repo.commit,
                driver=str(run_cfg.driver.command or run_cfg.driver.builtin),
                condition=rpt.condition,
                model=run_cfg.agent.model,
                timestamp=datetime.now(UTC).isoformat(),
                steps=rpt.steps,
            ),
            encoding="utf-8",
        )
        console.print(f"  Report: {report_path}")
        console.print(f"  Database: {rpt.db_path}")


# ---------------------------------------------------------------------------
# crt compare
# ---------------------------------------------------------------------------


@app.command()
def compare(
    baseline: Path = typer.Option(..., "--baseline", "-b", help="Previous results.json."),
    current: Path = typer.Option(..., "--current", "-C", help="Current results.json."),
) -> None:
    """Compare two result files and report regressions."""
    base = RunResult.model_validate_json(baseline.read_text())
    curr = RunResult.model_validate_json(current.read_text())
    typer.echo(f"Baseline: {base.run_id} ({base.timestamp.date()})")
    typer.echo(f"Current:  {curr.run_id} ({curr.timestamp.date()})\n")

    regressions = 0
    for cond in sorted(set(base.summary) | set(curr.summary)):
        base_s, curr_s = base.summary.get(cond), curr.summary.get(cond)
        if base_s and curr_s:
            delta = curr_s.pass_rate - base_s.pass_rate
            tag = "REGRESSION" if delta < 0 else "improvement" if delta > 0 else "no change"
            typer.echo(
                f"  {cond}: {base_s.pass_rate:.1%} -> {curr_s.pass_rate:.1%} ({delta:+.1%}) [{tag}]"
            )
            regressions += delta < 0
        elif curr_s:
            typer.echo(f"  {cond}: NEW ({curr_s.pass_rate:.1%})")
        elif base_s:
            typer.echo(f"  {cond}: REMOVED (was {base_s.pass_rate:.1%})")

    if regressions:
        typer.echo(f"\n{regressions} regression(s) detected.")
        raise typer.Exit(code=1)
    typer.echo("\nNo regressions.")
