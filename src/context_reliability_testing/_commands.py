"""CLI command orchestration — builds runners and prints results.

Kept separate from cli.py so the CLI module is just arg parsing + delegation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from rich.console import Console
from rich.live import Live

from .artifacts import AssertionRunner
from .drivers import make_driver
from .drivers.stub import StubDriver
from .errors import ConfigError, PreflightError
from .evaluation import (
    AcceptanceChecker,
    EvalRunner,
    TrialExecutor,
    run_headless,
    run_streaming,
)
from .models import (
    EvalTask,
    RunConfig,
    RunResult,
    SequentialTask,
    TimelineMode,
    TokenUsage,
    TrialResult,
)
from .reporting import write_result_json, write_summary_md
from .resolve import collect_context_paths
from .timeline import TimelineRunner
from .timeline.progress import TimelineProgressDisplay
from .workspace import WorkspaceManager


@dataclass(frozen=True)
class RunOptions:
    out_dir: Path
    stream: bool
    seed: int
    console: Console
    dry_run: bool
    keep_worktrees: bool = True
    parallel: int = 1

    @property
    def headless(self) -> bool:
        return not self.stream


_TEMPLATES = Path(__file__).parent / "templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES),
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def run_eval(
    run_cfg: RunConfig,
    eval_tasks: list[EvalTask],
    opts: RunOptions,
) -> None:
    total = len(eval_tasks) * len(run_cfg.conditions) * run_cfg.trials
    opts.console.print(
        f"[bold]{len(eval_tasks)} tasks x {len(run_cfg.conditions)} conditions"
        f" x {run_cfg.trials} trials = {total} invocations[/bold]"
    )

    if opts.dry_run:
        opts.console.print("Dry run — no agents invoked.")
        return

    driver = (
        StubDriver(seed=opts.seed)
        if run_cfg.driver.builtin == "stub"
        else make_driver(run_cfg.driver, stream=opts.stream)
    )
    workspace = None
    if run_cfg.repo:
        workspace = WorkspaceManager(
            run_cfg.repo.url, opts.out_dir / ".workspace", run_cfg.repo.commit
        )
        opts.console.print("[dim]Cloning repo...[/dim]")
        workspace.clone()

    checker = AcceptanceChecker(stream=opts.stream)
    has_assertions = any(t.assertions for t in eval_tasks)
    assertion_runner = AssertionRunner() if has_assertions else None

    executor = TrialExecutor(
        workspace=workspace,
        driver=driver,
        checker=checker,
        assertion_runner=assertion_runner,
        config=run_cfg,
        keep_worktrees=opts.keep_worktrees,
    )
    runner = EvalRunner(
        config=run_cfg,
        tasks=eval_tasks,
        executor=executor,
    )
    try:
        if opts.headless:
            trials = run_headless(runner, opts.console, total, parallel=opts.parallel)
        else:
            trials = run_streaming(runner, opts.console, total, parallel=opts.parallel)
    except PreflightError as exc:
        raise ConfigError(str(exc)) from exc

    _report_eval(trials, run_cfg, workspace, opts)


def _report_eval(
    trials: list[TrialResult],
    run_cfg: RunConfig,
    workspace: WorkspaceManager | None,
    opts: RunOptions,
) -> None:
    """Post-run reporting: print failures, write result files, cleanup."""
    context_paths = collect_context_paths(run_cfg, workspace)
    result = RunResult.from_trials(trials, run_cfg.agent, list(run_cfg.conditions.keys()))

    all_failures = [(t, a) for t in trials for a in t.assertion_results if not a.passed]
    if all_failures:
        opts.console.print(f"\n[red bold]{len(all_failures)} assertion failure(s):[/red bold]")
        for t, a in all_failures:
            msg = f"  {a.message}" if a.message else ""
            opts.console.print(f"  [red]✗[/red] {t.task_id}/{t.condition}: {a.name}{msg}")
        opts.console.print()

    opts.console.print(f"Results: {write_result_json(result, opts.out_dir)}")
    summary_path = write_summary_md(
        result,
        opts.out_dir,
        context_files=context_paths,
        heuristics_config=run_cfg.heuristics_config,
    )
    opts.console.print(f"Summary: {summary_path}")
    if workspace:
        if opts.keep_worktrees:
            opts.console.print(f"[dim]Worktrees preserved at: {workspace.base_dir}[/dim]")
        else:
            workspace.teardown()
            opts.console.print("[dim]Worktrees cleaned up.[/dim]")


def run_timeline(
    run_cfg: RunConfig,
    seq_tasks: list[SequentialTask],
    mode: TimelineMode,
    out_dir: Path,
    stream: bool,
    console: Console,
    dry_run: bool,
) -> None:
    if not run_cfg.repo:
        raise ConfigError("timeline tasks require 'repo' in run config")

    out_dir.mkdir(parents=True, exist_ok=True)
    console.print(
        f"[bold]{len(seq_tasks)} tasks x {len(run_cfg.conditions)} conditions"
        f" | mode={mode.value}[/bold]"
    )

    if dry_run:
        console.print("Dry run — no agents invoked.")
        return

    total = len(seq_tasks) * len(run_cfg.conditions)
    progress = TimelineProgressDisplay(total)

    def on_preflight(status: str) -> None:
        progress.set_preflight(status)

    def on_condition(name: str) -> None:
        progress.start_condition(name)

    def on_step_start(task_id: str) -> None:
        progress.start_step(task_id)

    def on_step(
        order: int,
        task_id: str,
        passed: bool | None,
        divergence: int,
        wall_time: float,
        tokens: TokenUsage,
        cost_usd: float | None,
    ) -> None:
        progress.finish_step(task_id, passed, divergence, wall_time, tokens, cost_usd)

    runner = TimelineRunner(
        config=run_cfg,
        tasks=seq_tasks,
        driver=make_driver(run_cfg.driver, stream=stream),
        mode=mode,
        on_step=on_step,
        on_step_start=on_step_start,
        on_condition=on_condition,
        on_preflight=on_preflight,
    )
    try:
        with Live(progress, console=console, refresh_per_second=2):
            reports = runner.run(out_dir)
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted.[/yellow] Partial results written to output dir.")
        return
    except PreflightError as exc:
        raise ConfigError(str(exc)) from exc

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
        console.print(f"  Data: {rpt.jsonl_path}")
