"""CLI entrypoint for context-reliability-testing (crt)."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
import yaml
from rich.console import Console

from ._commands import RunOptions, run_eval, run_timeline
from .errors import ConfigError
from .models import RunConfig, RunResult, TimelineMode
from .resolve import ResolvedEval, ResolvedTimeline, resolve_tasks
from .scaffold import scaffold

app = typer.Typer(help="context-reliability-testing: A/B test coding-agent context stacks.")


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
    parallel: int = typer.Option(
        1,
        "--parallel",
        "-p",
        min=1,
        max=32,
        help="Max concurrent agent invocations (1=serial, max 32).",
    ),
) -> None:
    """Run tasks under different context conditions and measure results."""
    run_cfg = RunConfig.model_validate(yaml.safe_load(config.read_text()))
    out_dir = output if output != Path("out/") else run_cfg.output_dir
    run_cfg.output_dir = out_dir
    console = Console()

    try:
        resolved = resolve_tasks(tasks, commit_range, acceptance_cmd, run_cfg, out_dir)
    except ConfigError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None

    try:
        match resolved:
            case ResolvedTimeline(tasks=seq_tasks):
                if parallel > 1:
                    console.print(
                        "[yellow]Warning:[/yellow] --parallel is ignored for timeline mode "
                        "(steps are order-dependent)."
                    )
                run_timeline(run_cfg, seq_tasks, mode, out_dir, stream, console, dry_run)
            case ResolvedEval(tasks=eval_tasks):
                opts = RunOptions(
                    out_dir=out_dir,
                    stream=stream,
                    seed=seed,
                    console=console,
                    dry_run=dry_run,
                    keep_worktrees=keep_worktrees,
                    parallel=parallel,
                )
                run_eval(run_cfg, eval_tasks, opts)
    except ConfigError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from None


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
