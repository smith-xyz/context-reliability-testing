"""Shared test fixtures for git repository scaffolding."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def _run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one committed file. Returns repo path."""
    _run_git(["init"], cwd=tmp_path)
    _run_git(["config", "user.email", "test@test"], cwd=tmp_path)
    _run_git(["config", "user.name", "test"], cwd=tmp_path)
    (tmp_path / "a.txt").write_text("a\n")
    _run_git(["add", "."], cwd=tmp_path)
    _run_git(["commit", "-m", "init"], cwd=tmp_path)
    return tmp_path


def init_repo_with_commits(path: Path, num_commits: int = 3) -> list[str]:
    """Create a repo with N commits, return list of SHAs oldest-first."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init"], cwd=path)
    _run_git(["config", "user.email", "t@t"], cwd=path)
    _run_git(["config", "user.name", "t"], cwd=path)
    shas: list[str] = []
    for i in range(num_commits):
        (path / f"file{i}.txt").write_text(f"content {i}\n")
        _run_git(["add", "."], cwd=path)
        _run_git(["commit", "-m", f"commit {i}: add file{i}"], cwd=path)
        shas.append(_run_git(["rev-parse", "HEAD"], cwd=path))
    return shas


def init_repo_with_context(path: Path) -> str:
    """Create a repo with a .cursor directory and return HEAD SHA."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git(["init"], cwd=path)
    _run_git(["config", "user.email", "test@test"], cwd=path)
    _run_git(["config", "user.name", "test"], cwd=path)
    (path / "README.md").write_text("hello\n")
    (path / ".cursor").mkdir()
    (path / ".cursor" / "rules.md").write_text("rules\n")
    _run_git(["add", "."], cwd=path)
    _run_git(["commit", "-m", "init"], cwd=path)
    return _run_git(["rev-parse", "HEAD"], cwd=path)
