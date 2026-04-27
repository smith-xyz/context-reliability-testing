"""Live progress display for CRT runs."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Literal

from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.table import Table

from ..models import TrialResult
from .runner import EvalRunner, PhaseInfo, PhaseKind, PhaseState


@dataclass(frozen=True)
class _PhaseDisplay:
    table_label: str
    render: Literal["print", "rule"]
    stream_template: str


_K, _S = PhaseKind, PhaseState
_PHASE_DISPLAY: dict[tuple[PhaseKind, PhaseState], _PhaseDisplay] = {
    (_K.SMOKE_TEST, _S.RUNNING): _PhaseDisplay(
        "[yellow]smoke testing agent...[/yellow]",
        "print",
        "  [yellow]…[/yellow] smoke testing agent...",
    ),
    (_K.SMOKE_TEST, _S.PASSED): _PhaseDisplay(
        "[green]smoke test passed[/green]",
        "print",
        "  [green]✓[/green] agent smoke test passed",
    ),
    (_K.PREFLIGHT, _S.RUNNING): _PhaseDisplay(
        "[yellow]running preflight...[/yellow]",
        "print",
        "  [yellow]…[/yellow] preflight: {detail}",
    ),
    (_K.PREFLIGHT, _S.PASSED): _PhaseDisplay(
        "[green]preflight passed[/green]",
        "print",
        "  [green]✓[/green] preflight: {detail} passed",
    ),
    (_K.PREFLIGHT, _S.SKIPPED): _PhaseDisplay(
        "[dim]preflight skipped[/dim]",
        "print",
        "  [green]✓[/green] preflight: {detail} skipped",
    ),
    (_K.TRIAL, _S.RUNNING): _PhaseDisplay(
        "[cyan]agent working...[/cyan]",
        "rule",
        "[bold cyan]trial: {detail}[/bold cyan]",
    ),
}


def _phase_label(phase: PhaseInfo) -> tuple[str, str]:
    """Return (rich label, task detail) for a phase."""
    entry = _PHASE_DISPLAY.get((phase.kind, phase.state))
    label = entry.table_label if entry else f"[dim]{phase.kind}[/dim]"
    return label, phase.detail


def _stream_phase(console: Console, phase: PhaseInfo) -> None:
    """Render a phase event to the streaming console."""
    entry = _PHASE_DISPLAY.get((phase.kind, phase.state))
    if entry:
        text = entry.stream_template.format(detail=phase.detail)
        if entry.render == "rule":
            console.rule(text)
            console.print("[dim]  agent working...[/dim]")
        else:
            console.print(text)
    else:
        console.rule(f"[bold]{phase.detail or phase.kind}[/bold]")


def _stream_result(console: Console, result: TrialResult, count: int, total: int) -> None:
    """Render a trial result to the streaming console."""
    status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
    time_s = f"{result.wall_time_s:.1f}s" if result.wall_time_s else "—"
    tokens = f" {result.tokens.total:,}tok" if result.tokens else ""
    cost = f" ${result.cost_usd:.4f}" if result.cost_usd is not None else ""
    console.print(f"  [{count}/{total}] {status} {time_s}{tokens}{cost}")
    if result.assertion_results:
        for a in result.assertion_results:
            if not a.passed:
                msg = f": {a.message}" if a.message else ""
                console.print(f"    [red]✗[/red] {a.name}{msg}")
    console.rule(style="dim")


# ── ProgressDisplay (headless Rich table) ───────────────────────────────


class ProgressDisplay:
    """Rich renderable that shows a live elapsed timer on the active row."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.completed: list[TrialResult] = []
        self.current_phase: PhaseInfo | None = None
        self.phase_started: float = time.monotonic()

    def start_phase(self, phase: PhaseInfo) -> None:
        self.current_phase = phase
        self.phase_started = time.monotonic()

    def finish(self, result: TrialResult) -> None:
        self.completed.append(result)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        table = Table(title="CRT Progress", expand=False, show_lines=True)
        table.add_column("#", style="dim", width=4)
        table.add_column("Phase", min_width=22)
        table.add_column("Task", min_width=16)
        table.add_column("Condition", min_width=12)
        table.add_column("Result", min_width=8)
        table.add_column("Time", justify="right", min_width=8)
        table.add_column("Tokens", justify="right", min_width=8)
        table.add_column("Cost", justify="right", min_width=8)

        for i, r in enumerate(self.completed, 1):
            if r.passed:
                status = "[green]PASS[/green]"
            elif r.assertion_results and any(not a.passed for a in r.assertion_results):
                n = sum(1 for a in r.assertion_results if not a.passed)
                status = f"[red]FAIL[/red] [dim]({n} assertion)[/dim]"
            else:
                status = "[red]FAIL[/red]"
            time_s = f"{r.wall_time_s:.1f}s" if r.wall_time_s else "—"
            tokens = f"{r.tokens.total:,}" if r.tokens else "—"
            cost = f"${r.cost_usd:.4f}" if r.cost_usd is not None else "—"
            table.add_row(
                str(i),
                "[green]done[/green]",
                r.task_id,
                r.condition,
                status,
                time_s,
                tokens,
                cost,
            )

        if len(self.completed) < self.total and self.current_phase:
            elapsed = time.monotonic() - self.phase_started
            mins, secs = divmod(int(elapsed), 60)
            timer = f"{mins}:{secs:02d}" if mins else f"{secs}s"
            label, detail = _phase_label(self.current_phase)
            table.add_row(
                str(len(self.completed) + 1),
                label,
                f"[yellow]{detail}[/yellow]" if detail else "",
                "",
                "[dim]running[/dim]",
                f"[cyan]{timer}[/cyan]",
                "",
                "",
            )

        done = len(self.completed)
        if done < self.total and self.current_phase:
            hint = self.current_phase.detail or self.current_phase.kind
            table.caption = f"{done}/{self.total} complete — {hint}"
        else:
            table.caption = f"{done}/{self.total} complete"
        yield table


# ── Public entry points ─────────────────────────────────────────────────


def run_streaming(
    runner: EvalRunner,
    console: Console,
    total: int,
    parallel: int = 1,
) -> list[TrialResult]:
    """Run trials with inline streaming output (agent stdout visible)."""
    count = 0

    def on_progress(phase: PhaseInfo, result: TrialResult | None) -> None:
        nonlocal count
        if result:
            count += 1
            _stream_result(console, result, count, total)
        else:
            _stream_phase(console, phase)

    runner.on_progress = on_progress
    return asyncio.run(runner.arun(parallel=parallel))


def run_headless(
    runner: EvalRunner,
    console: Console,
    total: int,
    parallel: int = 1,
) -> list[TrialResult]:
    """Run trials with a live-updating Rich table (no agent stdout)."""
    progress = ProgressDisplay(total)

    def on_progress(phase: PhaseInfo, result: TrialResult | None) -> None:
        if result:
            progress.finish(result)
        else:
            progress.start_phase(phase)

    runner.on_progress = on_progress
    with Live(progress, console=console, refresh_per_second=2):
        return asyncio.run(runner.arun(parallel=parallel))
