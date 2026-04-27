"""Live progress display for timeline runs."""

from __future__ import annotations

import time
from dataclasses import dataclass

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.table import Table
from rich.text import Text

from ..models import TokenUsage


@dataclass(frozen=True)
class _CompletedStep:
    condition: str
    task_id: str
    passed: bool | None  # None = manual review
    divergence: int
    wall_time_s: float
    tokens: TokenUsage
    cost_usd: float | None


class TimelineProgressDisplay:
    """Rich renderable: live-updating table for timeline execution."""

    def __init__(self, total: int) -> None:
        self.total = total
        self._completed: list[_CompletedStep] = []
        self._condition: str = ""
        self._active_task: str | None = None
        self._step_started: float = time.monotonic()
        self._preflight: str = ""  # "", "running", "passed", "skipped"

    def set_preflight(self, status: str) -> None:
        self._preflight = status

    def start_condition(self, name: str) -> None:
        self._condition = name

    def start_step(self, task_id: str) -> None:
        self._active_task = task_id
        self._step_started = time.monotonic()

    def finish_step(
        self,
        task_id: str,
        passed: bool | None,
        divergence: int,
        wall_time: float,
        tokens: TokenUsage,
        cost_usd: float | None,
    ) -> None:
        self._completed.append(
            _CompletedStep(
                self._condition,
                task_id,
                passed,
                divergence,
                wall_time,
                tokens,
                cost_usd,
            )
        )
        self._active_task = None

    def _preflight_text(self) -> Text | None:
        match self._preflight:
            case "running":
                return Text.from_markup("  [yellow]…[/yellow] preflight running")
            case "passed":
                return Text.from_markup("  [green]✓[/green] preflight passed")
            case "skipped":
                return Text.from_markup("  [dim]—[/dim] preflight skipped (manual acceptance)")
            case _:
                return None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        table = Table(title="CRT Timeline", expand=False, show_lines=True)
        table.add_column("#", style="dim", width=4)
        table.add_column("Condition", min_width=14)
        table.add_column("Task", min_width=18)
        table.add_column("Status", min_width=8)
        table.add_column("Divergence", justify="right", min_width=10)
        table.add_column("Time", justify="right", min_width=8)
        table.add_column("Tokens", justify="right", min_width=8)
        table.add_column("Cost", justify="right", min_width=8)

        for i, s in enumerate(self._completed, 1):
            if s.passed is None:
                status = "[dim]manual[/dim]"
            elif s.passed:
                status = "[green]PASS[/green]"
            else:
                status = "[red]FAIL[/red]"
            tokens = f"{s.tokens.total:,}" if s.tokens else "—"
            cost = f"${s.cost_usd:.4f}" if s.cost_usd is not None else "—"
            table.add_row(
                str(i),
                s.condition,
                s.task_id,
                status,
                f"{s.divergence} lines",
                f"{s.wall_time_s:.1f}s",
                tokens,
                cost,
            )

        if self._active_task:
            elapsed = time.monotonic() - self._step_started
            mins, secs = divmod(int(elapsed), 60)
            timer = f"{mins}:{secs:02d}" if mins else f"{secs}s"
            table.add_row(
                str(len(self._completed) + 1),
                f"[yellow]{self._condition}[/yellow]",
                f"[yellow]{self._active_task}[/yellow]",
                "[dim]running[/dim]",
                "",
                f"[cyan]{timer}[/cyan]",
                "",
                "",
            )

        done = len(self._completed)
        table.caption = f"{done}/{self.total} steps — Ctrl+C to abort"
        pf = self._preflight_text()
        yield Group(pf, table) if pf else table
