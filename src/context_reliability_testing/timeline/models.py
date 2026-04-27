"""Timeline data models: step metrics, snapshots, and run comparisons."""

from __future__ import annotations

from dataclasses import dataclass

from ..models import TokenUsage


@dataclass
class StepMetrics:
    task_id: str
    task_order: int
    resolved_commit: str | None
    marker: str | None
    tests_pass: bool
    files_changed_agent: list[str]
    files_changed_actual: list[str]
    files_overlap_pct: float
    tokens: TokenUsage
    wall_time_s: float
    tool_calls: int
    error: str | None = None


@dataclass
class SnapshotMetrics:
    files_differ: int
    line_delta: int
    diff_compressed: bytes


@dataclass
class TimelineStep:
    step: StepMetrics
    snapshot: SnapshotMetrics


@dataclass
class RunComparison:
    run_a_id: str
    run_b_id: str
    aligned_steps: list[tuple[TimelineStep | None, TimelineStep | None]]
