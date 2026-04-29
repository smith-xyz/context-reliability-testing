"""CLI entrypoint for context-reliability-testing (crt)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NoReturn

import typer
from rich.console import Console
from rich.markup import escape

from ._commands import RunOptions, run_eval, run_timeline
from .errors import CRTError
from .models import RunConfig, RunResult, TimelineMode
from .parsing import parse_yaml, read_text, validate_json, validate_model
from .resolve import ResolvedEval, ResolvedTimeline, resolve_tasks

logger = logging.getLogger(__name__)


def _print_crt_error(console: Console, exc: CRTError) -> None:
    """CRT domain errors: fixed header, message body (multi-line safe, markup escaped)."""
    text = str(exc).strip() or exc.__class__.__name__
    console.print("[red bold]Error[/red bold]")
    for line in text.splitlines():
        console.print(f"  [red]{escape(line)}[/red]")


def _print_unexpected_error(console: Console, exc: BaseException, *, verbose: bool) -> None:
    """Everything else: different label so it is obvious this was not a CRTError."""
    logger.exception("unexpected error")
    if verbose:
        console.print("[yellow bold]Unexpected error[/yellow bold]")
        console.print_exception()
        return
    text = str(exc).strip() or exc.__class__.__name__
    console.print("[yellow bold]Unexpected error[/yellow bold]")
    for line in text.splitlines():
        console.print(f"  [dim]{escape(line)}[/dim]")


def _exit_with_error(console: Console, exc: BaseException, *, verbose: bool = False) -> NoReturn:
    if isinstance(exc, CRTError):
        _print_crt_error(console, exc)
    else:
        _print_unexpected_error(console, exc, verbose=verbose)
    raise typer.Exit(code=1) from None


app = typer.Typer(help="context-reliability-testing: A/B test coding-agent context stacks.")


@app.callback()
def main(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging."),
) -> None:
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# crt init
# ---------------------------------------------------------------------------


@app.command()
def init(
    ctx: typer.Context,
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
    from .scaffold import scaffold

    console = Console()
    verbose = bool(ctx.obj.get("verbose"))
    try:
        result = scaffold(directory.resolve(), test_cmd=test_cmd, model=model)
        out = output.resolve()
        out.mkdir(parents=True, exist_ok=True)

        config_path = out / "crt-config.yaml"
        tasks_path = out / "crt-tasks.yaml"
        assertions_path = out / "crt_assertions.py"
        config_path.write_text(result.config_yaml, encoding="utf-8")
        tasks_path.write_text(result.tasks_yaml, encoding="utf-8")
        assertions_path.write_text(result.assertions_py, encoding="utf-8")
    except typer.Exit:
        raise
    except Exception as exc:
        _exit_with_error(console, exc, verbose=verbose)

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
    ctx: typer.Context,
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
    console = Console()
    verbose = bool(ctx.obj.get("verbose"))
    try:
        cfg_path = config.resolve()
        cfg_text = read_text(cfg_path, "config")
        raw = parse_yaml(cfg_text, cfg_path)
        run_cfg = validate_model(RunConfig, raw, "run config")

        out_dir = output if output != Path("out/") else run_cfg.output_dir
        run_cfg.output_dir = out_dir
        resolved = resolve_tasks(tasks, commit_range, acceptance_cmd, run_cfg, out_dir)

        if (
            isinstance(resolved, ResolvedEval)
            and parallel == 1
            and len(resolved.tasks) > 1
            and not stream
        ):
            console.print("[dim]Tip: use --parallel/-p to run trials concurrently.[/dim]")

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
    except typer.Exit:
        raise
    except Exception as exc:
        _exit_with_error(console, exc, verbose=verbose)


# ---------------------------------------------------------------------------
# crt compare
# ---------------------------------------------------------------------------


@app.command()
def compare(
    ctx: typer.Context,
    baseline: Path = typer.Option(..., "--baseline", "-b", help="Previous results.json."),
    current: Path = typer.Option(..., "--current", "-C", help="Current results.json."),
) -> None:
    """Compare two result files and report regressions."""
    console = Console()
    verbose = bool(ctx.obj.get("verbose"))
    try:
        base_raw = read_text(baseline, "baseline")
        curr_raw = read_text(current, "current")
        base = validate_json(RunResult, base_raw, "results JSON")
        curr = validate_json(RunResult, curr_raw, "results JSON")
    except typer.Exit:
        raise
    except Exception as exc:
        _exit_with_error(console, exc, verbose=verbose)

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
