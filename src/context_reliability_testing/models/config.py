"""Run configuration models."""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


class AgentConfig(BaseModel):
    model: str
    temperature: float = 0
    max_steps: int = 50
    annotations: dict[str, str] = {}


class Condition(BaseModel):
    context_files: list[str]
    source_dir: str | None = None
    """Local directory containing custom context files to inject. If omitted,
    files are taken from the repo worktree."""


class RepoConfig(BaseModel):
    url: str
    commit: str = "main"


class PromptMode(StrEnum):
    ARG = "arg"
    STDIN = "stdin"
    ENV = "env"


class DriverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str] | None = None
    builtin: str | None = None
    prompt_mode: PromptMode = PromptMode.ARG

    @model_validator(mode="after")
    def exactly_one_set(self) -> DriverConfig:
        if bool(self.command) == bool(self.builtin):
            raise ValueError("specify exactly one of 'command' or 'builtin'")
        return self


class TimelineMode(StrEnum):
    CONTINUOUS = "continuous"
    ANCHORED = "anchored"


class FailurePolicy(StrEnum):
    CONTINUE = "continue"
    SKIP_REMAINING = "skip_remaining"
    ROLLBACK = "rollback_to_last_passing"


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent: AgentConfig
    conditions: dict[str, Condition]
    trials: int = Field(default=1, ge=1)
    output_dir: Path = Path("out/")
    repo: RepoConfig | None = None
    driver: DriverConfig = DriverConfig(builtin="stub")
    context_patterns: list[str] = []
    heuristics_config: Path | None = None
    on_failure: FailurePolicy = FailurePolicy.CONTINUE
    prompt_template: str = "{prompt}"

    @model_validator(mode="after")
    def _warn_deterministic_trials(self) -> RunConfig:
        if self.trials > 1 and self.agent.temperature == 0:
            logger.warning(
                "trials=%d with temperature=0 — results will be similar across trials. "
                "Set temperature > 0 for more variance.",
                self.trials,
            )
        return self
