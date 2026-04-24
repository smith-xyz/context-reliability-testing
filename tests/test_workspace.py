"""WorkspaceManager and apply_condition integration with a local git repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from context_reliability_testing.conditions import apply_condition
from context_reliability_testing.models import Condition
from context_reliability_testing.workspace import WorkspaceError, WorkspaceManager


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init"], cwd=path)
    _run_git(["config", "user.email", "test@test"], cwd=path)
    _run_git(["config", "user.name", "test"], cwd=path)
    (path / "README.md").write_text("hello\n")
    (path / ".cursor").mkdir()
    (path / ".cursor" / "rules.md").write_text("rules\n")
    _run_git(["add", "."], cwd=path)
    _run_git(["commit", "-m", "init"], cwd=path)
    rev = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return rev


def test_workspace_worktree_lifecycle_and_diff_stat(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    head = _init_repo(origin)
    base = tmp_path / "ws"
    wm = WorkspaceManager(str(origin), base)
    wm.clone()
    wt = wm.create_worktree("trial")
    assert (wt / "README.md").read_text() == "hello\n"
    (wt / "README.md").write_text("hello world\n")
    stat = wm.diff_stat(wt, head)
    assert "README.md" in stat.files_changed
    assert stat.lines_added >= 1
    wm.cleanup_worktree(wt)
    assert not wt.exists()
    wm.teardown()
    assert not base.exists()


def test_teardown_removes_clone_and_tracked_worktrees(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _init_repo(origin)
    base = tmp_path / "ws2"
    wm = WorkspaceManager(str(origin), base)
    wm.clone()
    wt = wm.create_worktree("a")
    assert (base / ".clone").exists()
    wm.teardown()
    assert not base.exists()
    assert not wt.exists()


def test_pin_commit_invalid_raises(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _init_repo(origin)
    wm = WorkspaceManager(str(origin), tmp_path / "ws3", pin_commit="0" * 40)
    with pytest.raises(WorkspaceError, match="pin_commit"):
        wm.clone()


def test_apply_condition_removes_and_restores(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    _init_repo(origin)
    base = tmp_path / "ws4"
    wm = WorkspaceManager(str(origin), base)
    wm.clone()
    wt = wm.create_worktree("cond")
    src = tmp_path / "fixtures"
    (src / ".cursor").mkdir(parents=True)
    (src / ".cursor" / "rules.md").write_text("replacement\n")
    apply_condition(
        wt,
        Condition(context_files=[".cursor/rules.md"], source_dir=str(src)),
        context_patterns=[".cursor/**"],
    )
    assert (wt / ".cursor" / "rules.md").read_text() == "replacement\n"
    wm.teardown()


def test_apply_condition_preserves_worktree_file(tmp_path: Path) -> None:
    """When no source_dir, context files are kept from the worktree itself."""
    origin = tmp_path / "origin"
    _init_repo(origin)
    base = tmp_path / "ws5"
    wm = WorkspaceManager(str(origin), base)
    wm.clone()
    wt = wm.create_worktree("cond")
    assert (wt / ".cursor" / "rules.md").is_file()
    original = (wt / ".cursor" / "rules.md").read_text()
    apply_condition(
        wt,
        Condition(context_files=[".cursor/rules.md"]),
        context_patterns=[".cursor/**"],
    )
    assert (wt / ".cursor" / "rules.md").read_text() == original
    wm.teardown()
