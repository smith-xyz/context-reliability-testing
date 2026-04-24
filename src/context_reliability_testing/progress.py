"""Live progress display for CRT runs."""

from __future__ import annotations

import time

from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.table import Table

from .models import TrialResult
from .runner import EvalRunner


class ProgressDisplay:
    """Rich renderable that shows a live elapsed timer on the active row."""

    def __init__(self, total: int) -> None:
        self.total = total
        self.completed: list[TrialResult] = []
        self.current_phase = ""
        self.phase_started: float = time.monotonic()

    @staticmethod
    def _phase_label(phase: str) -> tuple[str, str]:
        """Return (short label, task detail) for a phase string."""
        if phase.startswith("preflight: agent smoke test"):
            if "passed" in phase:
                return "[green]smoke test passed[/green]", ""
            return "[yellow]smoke testing agent...[/yellow]", ""
        if phase.startswith("preflight:"):
            rest = phase.removeprefix("preflight:").strip()
            if "passed" in rest:
                return "[green]preflight passed[/green]", rest.split(" —")[0].split(" passed")[0]
            if "skipped" in rest:
                return "[dim]preflight skipped[/dim]", rest.split(" —")[0]
            return "[yellow]running preflight...[/yellow]", rest.split(" (")[0]
        if phase.startswith("trial:"):
            return "[cyan]agent working...[/cyan]", phase.removeprefix("trial:").strip()
        return f"[dim]{phase}[/dim]", ""

    def start_phase(self, phase: str) -> None:
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
            cost = f"${r.cost_usd:.4f}" if r.cost_usd is not None else "—"
            table.add_row(
                str(i),
                "[green]done[/green]",
                r.task_id,
                r.condition,
                status,
                time_s,
                cost,
            )

        if len(self.completed) < self.total:
            elapsed = time.monotonic() - self.phase_started
            mins, secs = divmod(int(elapsed), 60)
            timer = f"{mins}:{secs:02d}" if mins else f"{secs}s"
            label, detail = self._phase_label(self.current_phase)
            table.add_row(
                str(len(self.completed) + 1),
                label,
                f"[yellow]{detail}[/yellow]" if detail else "",
                "",
                "[dim]running[/dim]",
                f"[cyan]{timer}[/cyan]",
                "",
            )

        done = len(self.completed)
        if done < self.total:
            phase = self.current_phase
            if phase.startswith("trial:"):
                hint = phase.removeprefix("trial:").strip()
            elif phase.startswith("preflight:"):
                hint = phase.removeprefix("preflight:").strip()
            else:
                hint = phase
            table.caption = f"{done}/{self.total} complete — {hint}"
        else:
            table.caption = f"{done}/{self.total} complete"
        yield table


def run_streaming(
    runner: EvalRunner,
    console: Console,
    total: int,
) -> list[TrialResult]:
    """Run trials with inline streaming output (agent stdout visible)."""
    count = 0

    def on_progress(phase: str, result: TrialResult | None) -> None:
        nonlocal count
        if result:
            count += 1
            status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
            time_s = f"{result.wall_time_s:.1f}s" if result.wall_time_s else "—"
            cost = f" ${result.cost_usd:.4f}" if result.cost_usd is not None else ""
            console.print(f"  [{count}/{total}] {status} {time_s}{cost}")
            if result.assertion_results:
                failed = [a for a in result.assertion_results if not a.passed]
                for a in failed:
                    msg = f": {a.message}" if a.message else ""
                    console.print(f"    [red]✗[/red] {a.name}{msg}")
            console.rule(style="dim")
        elif phase.startswith("preflight: agent smoke test"):
            if "passed" in phase:
                console.print("  [green]✓[/green] agent smoke test passed")
            else:
                console.print("  [yellow]…[/yellow] smoke testing agent...")
        elif phase.startswith("preflight:"):
            if "passed" in phase or "skipped" in phase:
                console.print(f"  [green]✓[/green] {phase}")
            else:
                console.print(f"  [yellow]…[/yellow] {phase}")
        elif phase.startswith("trial:"):
            console.rule(f"[bold cyan]{phase}[/bold cyan]")
            console.print("[dim]  agent working...[/dim]")
        else:
            console.rule(f"[bold]{phase}[/bold]")

    runner.on_progress = on_progress
    return runner.run()


def run_headless(
    runner: EvalRunner,
    console: Console,
    total: int,
) -> list[TrialResult]:
    """Run trials with a live-updating Rich table (no agent stdout)."""
    progress = ProgressDisplay(total)

    def on_progress(phase: str, result: TrialResult | None) -> None:
        if result:
            progress.finish(result)
        else:
            progress.start_phase(phase)

    runner.on_progress = on_progress
    with Live(progress, console=console, refresh_per_second=2):
        return runner.run()
