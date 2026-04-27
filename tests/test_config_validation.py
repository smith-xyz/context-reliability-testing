"""Tests for configuration validation: timeout, trials warnings, timeline mode."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from context_reliability_testing.evaluation.acceptance import AcceptanceChecker
from context_reliability_testing.models import (
    Acceptance,
    AcceptanceType,
    EvalTask,
    RunConfig,
    TimelineMode,
)


class TestTimeout:
    def test_acceptance_timeout_default(self) -> None:
        acc = Acceptance(type=AcceptanceType.TEST_COMMAND, command="echo hi")
        assert acc.timeout_s == 300

    def test_acceptance_timeout_custom(self) -> None:
        acc = Acceptance(type=AcceptanceType.TEST_COMMAND, command="echo hi", timeout_s=60)
        assert acc.timeout_s == 60

    def test_timeout_used_by_checker(self, git_repo: Path) -> None:
        task = EvalTask(
            id="t",
            prompt="p",
            acceptance=Acceptance(
                type=AcceptanceType.TEST_COMMAND,
                command="sleep 5",
                timeout_s=1,
            ),
        )
        checker = AcceptanceChecker()
        result = checker.check(task, git_repo)
        assert not result.passed
        assert "timed out" in result.reason


class TestTrialsWarning:
    def test_warns_on_deterministic_trials(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            RunConfig.model_validate(
                {
                    "agent": {"model": "m", "temperature": 0},
                    "conditions": {"bare": {"context_files": []}},
                    "trials": 3,
                }
            )
        assert any("temperature=0" in r.message for r in caplog.records)

    def test_no_warning_with_nonzero_temp(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            RunConfig.model_validate(
                {
                    "agent": {"model": "m", "temperature": 0.7},
                    "conditions": {"bare": {"context_files": []}},
                    "trials": 3,
                }
            )
        assert not any("temperature=0" in r.message for r in caplog.records)

    def test_no_warning_single_trial(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            RunConfig.model_validate(
                {
                    "agent": {"model": "m", "temperature": 0},
                    "conditions": {"bare": {"context_files": []}},
                    "trials": 1,
                }
            )
        assert not any("temperature=0" in r.message for r in caplog.records)


class TestTimelineMode:
    def test_enum_values(self) -> None:
        assert TimelineMode.CONTINUOUS.value == "continuous"
        assert TimelineMode.ANCHORED.value == "anchored"
