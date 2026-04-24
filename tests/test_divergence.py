"""Tests for SQLite timeline tracker."""

from __future__ import annotations

import sqlite3
import zlib
from pathlib import Path

import pytest

from context_reliability_testing.divergence import (
    RunComparison,
    SnapshotMetrics,
    StepMetrics,
    TimelineStep,
    TimelineTracker,
)
from context_reliability_testing.models import TokenUsage


def _step(
    *,
    task_id: str = "t1",
    task_order: int = 1,
    resolved_commit: str | None = "abc",
    marker: str | None = None,
    tests_pass: bool = True,
    files_changed_agent: list[str] | None = None,
    files_changed_actual: list[str] | None = None,
    files_overlap_pct: float = 1.0,
    tokens: TokenUsage | None = None,
    wall_time_s: float = 0.1,
    tool_calls: int = 0,
    error: str | None = None,
) -> StepMetrics:
    return StepMetrics(
        task_id=task_id,
        task_order=task_order,
        resolved_commit=resolved_commit,
        marker=marker,
        tests_pass=tests_pass,
        files_changed_agent=files_changed_agent or ["a.go"],
        files_changed_actual=files_changed_actual or ["a.go"],
        files_overlap_pct=files_overlap_pct,
        tokens=tokens or TokenUsage(prompt=1, completion=2),
        wall_time_s=wall_time_s,
        tool_calls=tool_calls,
        error=error,
    )


def _snapshot(
    *,
    files_differ: int = 0,
    line_delta: int = 0,
    diff_compressed: bytes | None = None,
) -> SnapshotMetrics:
    return SnapshotMetrics(
        files_differ=files_differ,
        line_delta=line_delta,
        diff_compressed=diff_compressed if diff_compressed is not None else zlib.compress(b""),
    )


def test_create_run_returns_id(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with TimelineTracker(db) as t:
        run_id = t.create_run(
            repo_url="https://example.com/r.git",
            start_commit="main",
            driver="stub",
            condition="c1",
            model="m1",
        )
    assert isinstance(run_id, str)
    assert len(run_id) > 0


def test_record_step_get_run_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    tracker = TimelineTracker(db)
    run_id = tracker.create_run("u", "s", "d", "c", "m")
    for order in (1, 2, 3):
        tracker.record_step(
            run_id,
            TimelineStep(
                step=_step(task_id=f"id{order}", task_order=order),
                snapshot=_snapshot(files_differ=order, line_delta=order * 2),
            ),
        )
    steps = tracker.get_run(run_id)
    assert [s.step.task_order for s in steps] == [1, 2, 3]
    assert [s.step.task_id for s in steps] == ["id1", "id2", "id3"]
    assert [s.snapshot.files_differ for s in steps] == [1, 2, 3]
    tracker.close()


def test_diff_compression_roundtrip(tmp_path: Path) -> None:
    raw = b"diff --git a/x b/x\n+hello\n"
    compressed = zlib.compress(raw)
    db = tmp_path / "test.db"
    with TimelineTracker(db) as t:
        run_id = t.create_run("u", "s", "d", "c", "m")
        t.record_step(
            run_id,
            TimelineStep(
                step=_step(),
                snapshot=_snapshot(diff_compressed=compressed),
            ),
        )
        loaded = t.get_run(run_id)[0]
    assert zlib.decompress(loaded.snapshot.diff_compressed) == raw


def test_compare_runs_alignment(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    tracker = TimelineTracker(db)
    a = tracker.create_run("u", "s", "d", "c", "m")
    b = tracker.create_run("u", "s", "d", "c", "m")
    for order in (1, 2, 3):
        tracker.record_step(
            a,
            TimelineStep(
                step=_step(task_order=order, task_id=f"a{order}"),
                snapshot=_snapshot(),
            ),
        )
    for order in (1, 2):
        tracker.record_step(
            b,
            TimelineStep(
                step=_step(task_order=order, task_id=f"b{order}"),
                snapshot=_snapshot(),
            ),
        )
    cmp = tracker.compare_runs(a, b)
    assert isinstance(cmp, RunComparison)
    assert cmp.run_a_id == a
    assert cmp.run_b_id == b
    assert len(cmp.aligned_steps) == 3
    assert cmp.aligned_steps[0][0] is not None and cmp.aligned_steps[0][1] is not None
    assert cmp.aligned_steps[1][0] is not None and cmp.aligned_steps[1][1] is not None
    assert cmp.aligned_steps[2][0] is not None and cmp.aligned_steps[2][1] is None
    tracker.close()


def test_list_runs_returns_metadata(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with TimelineTracker(db) as t:
        rid = t.create_run("https://r.git", "main", "stub", "cond", "model-x")
        runs = t.list_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == rid
    assert runs[0]["repo_url"] == "https://r.git"
    assert runs[0]["start_commit"] == "main"
    assert runs[0]["driver"] == "stub"
    assert runs[0]["condition_name"] == "cond"
    assert runs[0]["agent_model"] == "model-x"
    assert "created_at" in runs[0]


def test_context_manager_closes_connection(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    tracker: TimelineTracker | None = None
    with TimelineTracker(db) as t:
        tracker = t
        t.create_run("u", "s", "d", "c", "m")
    assert tracker is not None
    with pytest.raises(sqlite3.ProgrammingError):
        tracker._conn.execute("SELECT 1")
