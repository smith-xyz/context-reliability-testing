from __future__ import annotations

import json
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import TokenUsage


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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    repo_url TEXT NOT NULL,
    start_commit TEXT NOT NULL,
    driver TEXT NOT NULL,
    condition_name TEXT NOT NULL,
    agent_model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    task_id TEXT NOT NULL,
    task_order INTEGER NOT NULL,
    resolved_commit TEXT,
    marker TEXT,
    tests_pass INTEGER NOT NULL,
    files_changed_agent TEXT NOT NULL,
    files_changed_actual TEXT NOT NULL,
    files_overlap_pct REAL,
    tokens_prompt INTEGER DEFAULT 0,
    tokens_completion INTEGER DEFAULT 0,
    wall_time_s REAL DEFAULT 0,
    tool_calls INTEGER DEFAULT 0,
    error TEXT,
    snapshot_files_differ INTEGER NOT NULL,
    snapshot_line_delta INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS diffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id INTEGER NOT NULL REFERENCES steps(id),
    diff_type TEXT NOT NULL CHECK(diff_type IN ('snapshot', 'step_agent')),
    diff_compressed BLOB NOT NULL,
    diff_size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


class TimelineTracker:
    """Append-only SQLite store for timeline evaluation data."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_SCHEMA)

    def create_run(
        self, repo_url: str, start_commit: str, driver: str, condition: str, model: str
    ) -> str:
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, repo_url, start_commit, driver, condition, model, now),
        )
        self._conn.commit()
        return run_id

    def record_step(self, run_id: str, step: TimelineStep) -> None:
        now = datetime.now(UTC).isoformat()
        s, snap = step.step, step.snapshot
        cursor = self._conn.execute(
            """INSERT INTO steps (run_id, task_id, task_order, resolved_commit, marker,
               tests_pass, files_changed_agent, files_changed_actual, files_overlap_pct,
               tokens_prompt, tokens_completion, wall_time_s, tool_calls, error,
               snapshot_files_differ, snapshot_line_delta, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                s.task_id,
                s.task_order,
                s.resolved_commit,
                s.marker,
                int(s.tests_pass),
                json.dumps(s.files_changed_agent),
                json.dumps(s.files_changed_actual),
                s.files_overlap_pct,
                s.tokens.prompt,
                s.tokens.completion,
                s.wall_time_s,
                s.tool_calls,
                s.error,
                snap.files_differ,
                snap.line_delta,
                now,
            ),
        )
        step_id = cursor.lastrowid
        self._conn.execute(
            "INSERT INTO diffs (step_id, diff_type, diff_compressed, diff_size_bytes, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                step_id,
                "snapshot",
                snap.diff_compressed,
                len(zlib.decompress(snap.diff_compressed)),
                now,
            ),
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> list[TimelineStep]:
        rows = self._conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY task_order", (run_id,)
        ).fetchall()
        steps: list[TimelineStep] = []
        for row in rows:
            step_id = row[0]
            diff_row = self._conn.execute(
                "SELECT diff_compressed FROM diffs WHERE step_id = ? AND diff_type = 'snapshot'",
                (step_id,),
            ).fetchone()
            diff_data = diff_row[0] if diff_row else zlib.compress(b"")
            steps.append(self._row_to_step(row, diff_data))
        return steps

    def compare_runs(self, run_id_a: str, run_id_b: str) -> RunComparison:
        steps_a = {s.step.task_order: s for s in self.get_run(run_id_a)}
        steps_b = {s.step.task_order: s for s in self.get_run(run_id_b)}
        all_orders = sorted(set(steps_a.keys()) | set(steps_b.keys()))
        aligned = [(steps_a.get(o), steps_b.get(o)) for o in all_orders]
        return RunComparison(run_id_a, run_id_b, aligned)

    def list_runs(self) -> list[dict[str, Any]]:
        rows = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        cols = [
            "run_id",
            "repo_url",
            "start_commit",
            "driver",
            "condition_name",
            "agent_model",
            "created_at",
        ]
        return [dict(zip(cols, row, strict=True)) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> TimelineTracker:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _row_to_step(row: tuple[Any, ...], diff_compressed: bytes) -> TimelineStep:
        return TimelineStep(
            step=StepMetrics(
                task_id=row[2],
                task_order=row[3],
                resolved_commit=row[4],
                marker=row[5],
                tests_pass=bool(row[6]),
                files_changed_agent=json.loads(row[7]),
                files_changed_actual=json.loads(row[8]),
                files_overlap_pct=row[9] or 0.0,
                tokens=TokenUsage(prompt=row[10] or 0, completion=row[11] or 0),
                wall_time_s=row[12] or 0.0,
                tool_calls=row[13] or 0,
                error=row[14],
            ),
            snapshot=SnapshotMetrics(
                files_differ=row[15],
                line_delta=row[16],
                diff_compressed=diff_compressed,
            ),
        )
