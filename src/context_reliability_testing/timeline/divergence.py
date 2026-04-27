"""TimelineTracker: append-only SQLite store for timeline evaluation data."""

from __future__ import annotations

import json
import sqlite3
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..models import TokenUsage
from .models import RunComparison, SnapshotMetrics, StepMetrics, TimelineStep

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
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def create_run(
        self, repo_url: str, start_commit: str, driver: str, condition: str, model: str
    ) -> str:
        run_id = str(uuid4())
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            """INSERT INTO runs (run_id, repo_url, start_commit, driver,
               condition_name, agent_model, created_at)
               VALUES (:run_id, :repo_url, :start_commit, :driver,
                       :condition_name, :agent_model, :created_at)""",
            {
                "run_id": run_id,
                "repo_url": repo_url,
                "start_commit": start_commit,
                "driver": driver,
                "condition_name": condition,
                "agent_model": model,
                "created_at": now,
            },
        )
        self._conn.commit()
        return run_id

    def record_step(self, run_id: str, step: TimelineStep) -> None:
        now = datetime.now(UTC).isoformat()
        s, snap = step.step, step.snapshot
        params = {
            "run_id": run_id,
            "task_id": s.task_id,
            "task_order": s.task_order,
            "resolved_commit": s.resolved_commit,
            "marker": s.marker,
            "tests_pass": int(s.tests_pass),
            "files_changed_agent": json.dumps(s.files_changed_agent),
            "files_changed_actual": json.dumps(s.files_changed_actual),
            "files_overlap_pct": s.files_overlap_pct,
            "tokens_prompt": s.tokens.prompt,
            "tokens_completion": s.tokens.completion,
            "wall_time_s": s.wall_time_s,
            "tool_calls": s.tool_calls,
            "error": s.error,
            "snapshot_files_differ": snap.files_differ,
            "snapshot_line_delta": snap.line_delta,
            "created_at": now,
        }
        cursor = self._conn.execute(
            """INSERT INTO steps (run_id, task_id, task_order, resolved_commit, marker,
               tests_pass, files_changed_agent, files_changed_actual, files_overlap_pct,
               tokens_prompt, tokens_completion, wall_time_s, tool_calls, error,
               snapshot_files_differ, snapshot_line_delta, created_at)
               VALUES (:run_id, :task_id, :task_order, :resolved_commit, :marker,
                       :tests_pass, :files_changed_agent, :files_changed_actual,
                       :files_overlap_pct, :tokens_prompt, :tokens_completion,
                       :wall_time_s, :tool_calls, :error,
                       :snapshot_files_differ, :snapshot_line_delta, :created_at)""",
            params,
        )
        step_id = cursor.lastrowid
        self._conn.execute(
            """INSERT INTO diffs (step_id, diff_type, diff_compressed, diff_size_bytes, created_at)
               VALUES (:step_id, :diff_type, :diff_compressed, :diff_size_bytes, :created_at)""",
            {
                "step_id": step_id,
                "diff_type": "snapshot",
                "diff_compressed": snap.diff_compressed,
                "diff_size_bytes": len(zlib.decompress(snap.diff_compressed)),
                "created_at": now,
            },
        )
        self._conn.commit()

    def get_run(self, run_id: str) -> list[TimelineStep]:
        rows = self._conn.execute(
            "SELECT * FROM steps WHERE run_id = ? ORDER BY task_order", (run_id,)
        ).fetchall()
        steps: list[TimelineStep] = []
        for row in rows:
            diff_row = self._conn.execute(
                "SELECT diff_compressed FROM diffs WHERE step_id = ? AND diff_type = 'snapshot'",
                (row["id"],),
            ).fetchone()
            diff_data = diff_row["diff_compressed"] if diff_row else zlib.compress(b"")
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
        return [dict(row) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> TimelineTracker:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _row_to_step(row: sqlite3.Row, diff_compressed: bytes) -> TimelineStep:
        return TimelineStep(
            step=StepMetrics(
                task_id=row["task_id"],
                task_order=row["task_order"],
                resolved_commit=row["resolved_commit"],
                marker=row["marker"],
                tests_pass=bool(row["tests_pass"]),
                files_changed_agent=json.loads(row["files_changed_agent"]),
                files_changed_actual=json.loads(row["files_changed_actual"]),
                files_overlap_pct=row["files_overlap_pct"] or 0.0,
                tokens=TokenUsage(
                    prompt=row["tokens_prompt"] or 0,
                    completion=row["tokens_completion"] or 0,
                ),
                wall_time_s=row["wall_time_s"] or 0.0,
                tool_calls=row["tool_calls"] or 0,
                error=row["error"],
            ),
            snapshot=SnapshotMetrics(
                files_differ=row["snapshot_files_differ"],
                line_delta=row["snapshot_line_delta"],
                diff_compressed=diff_compressed,
            ),
        )
