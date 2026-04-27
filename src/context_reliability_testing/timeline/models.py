"""Timeline data models: step metrics, snapshots, and run comparisons."""

from __future__ import annotations

import base64
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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
    cost_usd: float | None = None
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

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["step"]["tokens"] = self.step.tokens.model_dump()
        d["snapshot"]["diff_compressed"] = base64.b64encode(self.snapshot.diff_compressed).decode()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TimelineStep:
        s = d["step"]
        snap = d["snapshot"]
        return cls(
            step=StepMetrics(**{**s, "tokens": TokenUsage(**s["tokens"])}),
            snapshot=SnapshotMetrics(
                files_differ=snap["files_differ"],
                line_delta=snap["line_delta"],
                diff_compressed=base64.b64decode(snap["diff_compressed"]),
            ),
        )


def write_step_jsonl(path: Path, step: TimelineStep) -> None:
    """Append a single step as a JSON line."""
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(step.to_dict()) + "\n")


def read_steps_jsonl(path: Path) -> list[TimelineStep]:
    """Read all steps from a JSONL file."""
    steps: list[TimelineStep] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            steps.append(TimelineStep.from_dict(json.loads(line)))
    return steps


@dataclass
class RunComparison:
    run_a_id: str
    run_b_id: str
    aligned_steps: list[tuple[TimelineStep | None, TimelineStep | None]]
