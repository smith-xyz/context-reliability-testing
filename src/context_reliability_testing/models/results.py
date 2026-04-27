"""Trial result and run summary models."""

from __future__ import annotations

import itertools
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field

from .config import AgentConfig


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


def _summarize_condition(ct: list[TrialResult]) -> ConditionSummary:
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
        rates = [sum(1 for t in group if t.passed) / len(group) for group in by_task.values()]
        stddev = round(statistics.stdev(rates), 4) if len(rates) > 1 else None

    return ConditionSummary(
        pass_rate=round(pass_rate, 4),
        total_tasks=len(by_task),
        mean_tokens=round(statistics.mean(tokens), 1) if tokens else 0,
        mean_time_s=round(statistics.mean(times), 2) if times else 0,
        mean_tool_calls=round(statistics.mean(tools), 1) if tools else 0,
        mean_cost_usd=round(statistics.mean(costs), 4) if costs else None,
        mean_num_turns=round(statistics.mean(turns), 1) if turns else None,
        pass_rate_stddev=stddev,
    )


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
            summary[cond] = _summarize_condition(ct)

        return cls(
            run_id=str(uuid4()),
            timestamp=datetime.now(UTC),
            agent=agent,
            trials=trials,
            summary=summary,
        )
