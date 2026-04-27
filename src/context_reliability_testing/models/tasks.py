"""Task and acceptance models."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class AcceptanceType(StrEnum):
    TEST_COMMAND = "test_command"
    DIFF_CHECK = "diff_check"
    MANUAL = "manual"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Acceptance(BaseModel):
    type: AcceptanceType
    command: str | None = None
    expected_files: list[str] | None = None
    timeout_s: int = Field(default=300, ge=1)


class TaskMetadata(BaseModel):
    difficulty: Difficulty | None = None
    category: str | None = None
    source: str | None = None


class EvalTask(BaseModel):
    id: str
    prompt: str
    acceptance: Acceptance
    metadata: TaskMetadata | None = None
    assertions: str | None = None


class SequentialTask(BaseModel):
    """A task in a timeline evaluation sequence."""

    id: str
    prompt: str
    task_order: int = Field(ge=1)
    resolved_commit: str
    acceptance: Acceptance
    marker: str | None = None
    metadata: TaskMetadata | None = None
