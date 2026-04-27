"""Tests for task resolution: auto-derive from git range, diff_stat_range."""

from __future__ import annotations

from pathlib import Path

from context_reliability_testing.models import Acceptance, AcceptanceType
from context_reliability_testing.resolve import derive_tasks
from context_reliability_testing.workspace import WorkspaceManager

from .conftest import init_repo_with_commits


class TestAutoDerive:
    def test_derive_tasks_from_range(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        shas = init_repo_with_commits(origin, 3)
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        tasks = derive_tasks(ws, f"{shas[0]}..{shas[2]}")
        assert len(tasks) == 2
        assert tasks[0].resolved_commit == shas[1]
        assert tasks[1].resolved_commit == shas[2]
        assert tasks[0].task_order == 1
        assert tasks[1].task_order == 2
        assert "file1" in tasks[0].prompt
        ws.teardown()

    def test_derive_tasks_custom_acceptance(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        shas = init_repo_with_commits(origin, 2)
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        acc = Acceptance(type=AcceptanceType.TEST_COMMAND, command="go test ./...")
        tasks = derive_tasks(ws, f"{shas[0]}..{shas[1]}", acceptance=acc)
        assert len(tasks) == 1
        assert tasks[0].acceptance.command == "go test ./..."
        ws.teardown()

    def test_derive_tasks_empty_range(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        shas = init_repo_with_commits(origin, 1)
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        tasks = derive_tasks(ws, f"{shas[0]}..{shas[0]}")
        assert tasks == []
        ws.teardown()


class TestDiffStatRange:
    def test_diff_stat_range_shows_changed_files(self, tmp_path: Path) -> None:
        origin = tmp_path / "origin"
        shas = init_repo_with_commits(origin, 3)
        ws = WorkspaceManager(str(origin), tmp_path / "ws")
        ws.clone()
        ds = ws.diff_stat_range(f"{shas[1]}~1", shas[1])
        assert "file1.txt" in ds.files_changed
        assert ds.lines_added >= 1
        ws.teardown()
