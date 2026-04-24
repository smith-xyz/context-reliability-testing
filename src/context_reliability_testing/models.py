"""Pydantic models aligned with schema/*.json contracts."""

from __future__ import annotations

import itertools
import logging
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)


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


class SequentialTask(BaseModel):
    """A task in a timeline evaluation sequence."""

    id: str
    prompt: str
    task_order: int = Field(ge=1)
    resolved_commit: str
    acceptance: Acceptance
    marker: str | None = None
    metadata: TaskMetadata | None = None


class TokenUsage(BaseModel):
    prompt: int = 0
    completion: int = 0

    @property
    def total(self) -> int:
        return self.prompt + self.completion


class AssertionOutcome(BaseModel):
    name: str
    passed: bool
    message: str | None = None


class TrialResult(BaseModel):
    task_id: str
    condition: str
    trial_number: int = Field(ge=1)
    passed: bool
    tokens: TokenUsage | None = None
    wall_time_s: float | None = None
    tool_calls: int | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    error: str | None = None
    artifact_dir: str | None = None
    assertion_results: list[AssertionOutcome] = []


class ConditionSummary(BaseModel):
    pass_rate: float
    total_tasks: int
    mean_tokens: float = 0
    mean_time_s: float = 0
    mean_tool_calls: float = 0
    mean_cost_usd: float | None = None
    mean_num_turns: float | None = None
    pass_rate_stddev: float | None = None


class RunResult(BaseModel):
    run_id: str
    timestamp: datetime
    agent: AgentConfig
    trials: list[TrialResult]
    summary: dict[str, ConditionSummary]

    @classmethod
    def from_trials(
        cls,
        trials: list[TrialResult],
        agent: AgentConfig,
        conditions: list[str],
    ) -> RunResult:
        by_cond: dict[str, list[TrialResult]] = defaultdict(list)
        for t in trials:
            by_cond[t.condition].append(t)

        summary: dict[str, ConditionSummary] = {}
        for cond in conditions:
            ct = by_cond.get(cond, [])
            if not ct:
                continue

            pass_rate = sum(1 for t in ct if t.passed) / len(ct)
            tokens = [t.tokens.total for t in ct if t.tokens]
            times = [t.wall_time_s for t in ct if t.wall_time_s is not None]
            tools = [t.tool_calls for t in ct if t.tool_calls is not None]
            costs = [t.cost_usd for t in ct if t.cost_usd is not None]
            turns = [t.num_turns for t in ct if t.num_turns is not None]

            by_task = {
                tid: list(group)
                for tid, group in itertools.groupby(
                    sorted(ct, key=lambda t: t.task_id), key=lambda t: t.task_id
                )
            }
            stddev = None
            if len(by_task) > 1:
                rates = [
                    sum(1 for t in group if t.passed) / len(group) for group in by_task.values()
                ]
                stddev = round(statistics.stdev(rates), 4) if len(rates) > 1 else None

            summary[cond] = ConditionSummary(
                pass_rate=round(pass_rate, 4),
                total_tasks=len(by_task),
                mean_tokens=round(statistics.mean(tokens), 1) if tokens else 0,
                mean_time_s=round(statistics.mean(times), 2) if times else 0,
                mean_tool_calls=round(statistics.mean(tools), 1) if tools else 0,
                mean_cost_usd=round(statistics.mean(costs), 4) if costs else None,
                mean_num_turns=round(statistics.mean(turns), 1) if turns else None,
                pass_rate_stddev=stddev,
            )

        return cls(
            run_id=str(uuid4()),
            timestamp=datetime.now(UTC),
            agent=agent,
            trials=trials,
            summary=summary,
        )
