"""CRT pytest plugin: provides the ``trial`` fixture for assertion tests.

Loaded explicitly via ``-p context_reliability_testing.pytest_plugin``.
Not registered as an entry point — no global side effects on unrelated sessions.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .trial_context import TrialContext


@pytest.fixture
def trial() -> TrialContext:
    """TrialContext loaded from the JSON file path in CRT_TRIAL_CONTEXT env var."""
    ctx_path = os.environ.get("CRT_TRIAL_CONTEXT")
    if not ctx_path:
        pytest.skip("Not running inside CRT (CRT_TRIAL_CONTEXT not set)")
    data = json.loads(Path(ctx_path).read_text())
    return TrialContext(
        artifact_dir=Path(data["artifact_dir"]),
        worktree=Path(data["worktree"]),
        diff=data["diff"],
        changed_files=data["changed_files"],
        task_id=data["task_id"],
        condition=data["condition"],
        trial_number=data["trial_number"],
        passed=data["passed"],
    )
