"""Eval runner: orchestrates preflight, grid dispatch, and result collection."""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from ..drivers import StubDriver
from ..drivers.base import Driver
from ..errors import PreflightError
from ..models import AcceptanceType, Condition, EvalTask, RunConfig, TrialResult
from ..workspace import WorkspaceManager
from .acceptance import AcceptanceChecker
from .executor import TrialExecutor

logger = logging.getLogger(__name__)

_MAX_PARALLEL = 32
_WARN_PARALLEL = 16


class PhaseKind(StrEnum):
    SMOKE_TEST = "smoke_test"
    PREFLIGHT = "preflight"
    TRIAL = "trial"
    RESULT = "result"


class PhaseState(StrEnum):
    RUNNING = "running"
    PASSED = "passed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PhaseInfo:
    kind: PhaseKind
    state: PhaseState = PhaseState.RUNNING
    detail: str = ""


ProgressCallback = Callable[[PhaseInfo, TrialResult | None], None]


@dataclass
class EvalRunner:
    """Orchestrates eval sweep: preflight, grid dispatch, result collection.

    Trial lifecycle is delegated to TrialExecutor (injected via DI).
    """

    config: RunConfig
    tasks: list[EvalTask]
    executor: TrialExecutor
    on_progress: ProgressCallback | None = None
    _preflight_done: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    @property
    def _workspace(self) -> WorkspaceManager | None:
        return self.executor.workspace

    @property
    def _checker(self) -> AcceptanceChecker:
        return self.executor.checker

    @property
    def _driver(self) -> Driver:
        return self.executor.driver

    def _emit(self, phase: PhaseInfo, result: TrialResult | None = None) -> None:
        if self.on_progress:
            self.on_progress(phase, result)

    def _preflight_agent(self, cwd: Path) -> None:
        """Smoke-test the driver with a trivial prompt to catch config issues early."""
        if isinstance(self._driver, StubDriver):
            return
        self._emit(PhaseInfo(PhaseKind.SMOKE_TEST))
        dr = self.executor.driver.execute(
            "Reply with the word HELLO and nothing else.",
            cwd,
            self.config.agent.model,
            1,
        )
        if dr.error:
            detail = dr.raw_output.strip()[:500] if dr.raw_output.strip() else ""
            hint = f"\n\nAgent output:\n{detail}" if detail else ""
            raise PreflightError(
                f"Agent smoke test failed: {dr.error}. "
                f"Check your driver config, agent auth, and CLI flags.{hint}"
            )
        self._emit(PhaseInfo(PhaseKind.SMOKE_TEST, PhaseState.PASSED))

    def _preflight_task(self, task: EvalTask, worktree: Path) -> None:
        """Just-in-time preflight for a single task. Deduplicates by command."""
        if task.acceptance.type in (AcceptanceType.MANUAL, AcceptanceType.DIFF_CHECK):
            return
        cmd = task.acceptance.command or task.acceptance.type.value
        if cmd in self._preflight_done:
            self._emit(PhaseInfo(PhaseKind.PREFLIGHT, PhaseState.SKIPPED, detail=task.id))
            return
        self._emit(PhaseInfo(PhaseKind.PREFLIGHT, detail=task.id))
        result = self._checker.check(task, worktree)
        if not result.passed:
            raise PreflightError(
                f"Preflight failed for task '{task.id}': {result.reason}. "
                "Fix the repo baseline before running evaluations."
            )
        self._preflight_done[cmd] = task.id
        self._emit(PhaseInfo(PhaseKind.PREFLIGHT, PhaseState.PASSED, detail=task.id))

    def _effective_parallel(self, parallel: int) -> int:
        total = len(self.tasks) * len(self.config.conditions) * self.config.trials
        effective = min(parallel, total)
        if not self._workspace and effective > 1:
            logger.warning(
                "parallel=%d but no workspace configured — clamping to 1 "
                "(all trials share Path('.'), not parallelizable)",
                parallel,
            )
            return 1
        if hasattr(self._driver, "supports_parallel") and not self._driver.supports_parallel:
            logger.warning(
                "parallel=%d but driver does not support parallel execution "
                "(e.g. streaming mode) — clamping to 1",
                parallel,
            )
            return 1
        if effective != parallel:
            logger.info("parallel=%d clamped to %d (total work items)", parallel, effective)
        if effective > _WARN_PARALLEL:
            logger.warning(
                "parallel=%d — high parallelism may incur significant API costs "
                "and network pressure",
                effective,
            )
        return effective

    def run(self) -> list[TrialResult]:
        """Sync entry point — delegates to async run."""
        return asyncio.run(self.arun(parallel=1))

    async def arun(self, parallel: int = 1) -> list[TrialResult]:
        self._preflight_done.clear()
        effective = self._effective_parallel(parallel)

        preflight_wt: Path | None = None
        ws = self._workspace
        if ws:
            preflight_wt = ws.create_worktree("preflight")

        try:
            self._preflight_agent(preflight_wt or Path("."))
            for task in self.tasks:
                if preflight_wt:
                    self._preflight_task(task, preflight_wt)
        finally:
            if preflight_wt and ws:
                ws.cleanup_worktree(preflight_wt)

        sem = asyncio.Semaphore(effective)
        grid = list(
            itertools.product(
                self.tasks,
                self.config.conditions.items(),
                range(1, self.config.trials + 1),
            )
        )

        async def _guarded(
            task: EvalTask,
            cond_name: str,
            condition: Condition,
            trial_num: int,
        ) -> TrialResult:
            async with sem:
                trial_detail = f"{task.id} / {cond_name} #{trial_num}"
                self._emit(PhaseInfo(PhaseKind.TRIAL, detail=trial_detail))
                result = await self.executor.execute(task, cond_name, condition, trial_num)
                self._emit(PhaseInfo(PhaseKind.RESULT), result)
                return result

        results = await asyncio.gather(
            *[
                _guarded(task, cond_name, condition, trial_num)
                for task, (cond_name, condition), trial_num in grid
            ]
        )
        results_list = list(results)
        results_list.sort(key=lambda r: (r.task_id, r.condition, r.trial_number))
        return results_list
