"""TrialExecutor: async boundary for single-trial lifecycle.

Domain classes (WorkspaceManager, AcceptanceChecker, TrialBundle, AssertionRunner)
stay pure sync. This executor wraps them via asyncio.to_thread and owns the
asyncio.Lock for git worktree serialization.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from .acceptance import AcceptanceChecker
from .assertions import AssertionRunner
from .conditions import apply_condition
from .drivers.base import Driver
from .models import AssertionOutcome, Condition, EvalTask, RunConfig, TrialResult
from .trial_bundle import TrialBundle
from .workspace import WorkspaceManager

logger = logging.getLogger(__name__)


@dataclass
class TrialExecutor:
    """Runs a single trial's full lifecycle asynchronously."""

    workspace: WorkspaceManager | None
    driver: Driver
    checker: AcceptanceChecker
    assertion_runner: AssertionRunner | None
    config: RunConfig
    keep_worktrees: bool = True
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def execute(
        self, task: EvalTask, cond_name: str, condition: Condition, trial_num: int
    ) -> TrialResult:
        rendered = self.config.prompt_template.format(
            prompt=task.prompt,
            acceptance_cmd=task.acceptance.command or "",
            task_id=task.id,
        )
        if self.workspace:
            return await self._execute_with_workspace(
                task, cond_name, condition, trial_num, rendered
            )
        return await self._execute_without_workspace(task, cond_name, trial_num, rendered)

    async def _execute_with_workspace(
        self,
        task: EvalTask,
        cond_name: str,
        condition: Condition,
        trial_num: int,
        rendered: str,
    ) -> TrialResult:
        assert self.workspace is not None
        ws = self.workspace

        async with self._lock:
            worktree = await asyncio.to_thread(
                ws.create_worktree,
                f"{task.id}-{cond_name}-{trial_num}",
                None,
                self.keep_worktrees,
            )

        await asyncio.to_thread(apply_condition, worktree, condition, self.config.context_patterns)
        dr = await self.driver.execute_async(
            rendered, worktree, self.config.agent.model, self.config.agent.max_steps
        )
        exceeded_max = dr.num_turns is not None and dr.num_turns > self.config.agent.max_steps
        ar = await asyncio.to_thread(self.checker.check, task, worktree)
        artifact_dir, assertion_results = await self._collect_artifacts(
            task, cond_name, trial_num, worktree, ar.passed
        )

        assertions_passed = all(a.passed for a in assertion_results)
        overall_passed = ar.passed and assertions_passed and not exceeded_max
        error = dr.error or (None if ar.passed else ar.reason)
        if exceeded_max:
            error = f"agent used {dr.num_turns} turns (max_steps={self.config.agent.max_steps})"

        return TrialResult(
            task_id=task.id,
            condition=cond_name,
            trial_number=trial_num,
            passed=overall_passed,
            tokens=dr.tokens,
            wall_time_s=dr.wall_time_s,
            tool_calls=dr.tool_calls,
            cost_usd=dr.cost_usd,
            num_turns=dr.num_turns,
            error=error,
            artifact_dir=artifact_dir,
            assertion_results=assertion_results,
        )

    async def _execute_without_workspace(
        self,
        task: EvalTask,
        cond_name: str,
        trial_num: int,
        rendered: str,
    ) -> TrialResult:
        dr = await self.driver.execute_async(
            rendered, Path("."), self.config.agent.model, self.config.agent.max_steps
        )
        return TrialResult(
            task_id=task.id,
            condition=cond_name,
            trial_number=trial_num,
            passed=dr.error is None,
            tokens=dr.tokens,
            wall_time_s=dr.wall_time_s,
            tool_calls=dr.tool_calls,
            cost_usd=dr.cost_usd,
            num_turns=dr.num_turns,
            error=dr.error,
        )

    async def _collect_artifacts(
        self,
        task: EvalTask,
        cond_name: str,
        trial_num: int,
        worktree: Path,
        passed: bool,
    ) -> tuple[str | None, list[AssertionOutcome]]:
        bundle = TrialBundle(
            output_dir=self.config.output_dir,
            task_id=task.id,
            condition=cond_name,
            trial_number=trial_num,
            worktree=worktree,
            passed=passed,
            exclude_patterns=self.config.context_patterns,
        )
        await asyncio.to_thread(bundle.capture_diff)
        bundle.write()
        bundle.write_context_json()

        assertion_results: list[AssertionOutcome] = []
        if task.assertions and self.assertion_runner:
            ctx = bundle.to_context()
            try:
                assertion_results = await asyncio.to_thread(
                    self.assertion_runner.run, task.assertions, ctx
                )
            except Exception:
                logger.exception("Assertion execution failed for %s", task.id)

        return str(bundle.artifact_dir), assertion_results
