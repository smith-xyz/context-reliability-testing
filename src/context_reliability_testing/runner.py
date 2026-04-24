"""Eval runner using pluggable driver protocol."""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .acceptance import AcceptanceChecker
from .assertions import AssertionRunner
from .conditions import apply_condition
from .drivers import Driver, StubDriver
from .models import AcceptanceType, AssertionOutcome, Condition, EvalTask, RunConfig, TrialResult
from .trial_bundle import TrialBundle
from .workspace import WorkspaceManager

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, TrialResult | None], None]
"""Called with (phase_label, result_or_none). Phases: preflight, trial, done."""


class PreflightError(RuntimeError):
    """Raised when acceptance checks fail on the unmodified repo."""


@dataclass
class EvalRunner:
    """Executes eval sweep: tasks x conditions x trials."""

    config: RunConfig
    tasks: list[EvalTask]
    driver: Driver = field(default_factory=lambda: StubDriver(seed=42))
    workspace: WorkspaceManager | None = None
    checker: AcceptanceChecker = field(default_factory=AcceptanceChecker)
    assertion_runner: AssertionRunner | None = None
    keep_worktrees: bool = True
    on_progress: ProgressCallback | None = None

    def _emit(self, phase: str, result: TrialResult | None = None) -> None:
        if self.on_progress:
            self.on_progress(phase, result)

    def _preflight_agent(self, cwd: Path) -> None:
        """Smoke-test the driver with a trivial prompt to catch config issues early."""
        if isinstance(self.driver, StubDriver):
            return
        self._emit("preflight: agent smoke test")
        dr = self.driver.execute(
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
        self._emit("preflight: agent smoke test passed")

    def _preflight_task(self, task: EvalTask, worktree: Path) -> None:
        """Just-in-time preflight for a single task. Deduplicates by command."""
        if task.acceptance.type in (AcceptanceType.MANUAL, AcceptanceType.DIFF_CHECK):
            return
        cmd = task.acceptance.command or task.acceptance.type.value
        if cmd in self._preflight_done:
            self._emit(
                f"preflight: {task.id} — same command as {self._preflight_done[cmd]}, skipped"
            )
            return
        self._emit(f"preflight: {task.id} ({cmd})")
        result = self.checker.preflight(task, worktree)
        if not result.passed:
            raise PreflightError(
                f"Preflight failed for task '{task.id}': {result.reason}. "
                "Fix the repo baseline before running evaluations."
            )
        self._preflight_done[cmd] = task.id
        self._emit(f"preflight: {task.id} passed")

    def run(self) -> list[TrialResult]:
        self._preflight_done: dict[str, str] = {}
        results: list[TrialResult] = []
        current_task: str | None = None

        # Single shared worktree for preflight — preserves build/test caches
        preflight_wt: Path | None = None
        if self.workspace:
            preflight_wt = self.workspace.create_worktree("preflight")

        try:
            self._preflight_agent(preflight_wt or Path("."))

            trials = itertools.product(
                self.tasks,
                self.config.conditions.items(),
                range(1, self.config.trials + 1),
            )
            for task, (cond_name, condition), trial_num in trials:
                if task.id != current_task:
                    if preflight_wt:
                        self._preflight_task(task, preflight_wt)
                    current_task = task.id
                self._emit(f"trial: {task.id} / {cond_name} #{trial_num}")
                result = self._run_trial(task, cond_name, condition, trial_num)
                self._emit("result", result)
                results.append(result)
        finally:
            if preflight_wt and self.workspace:
                self.workspace.cleanup_worktree(preflight_wt)

        return results

    def _render_prompt(self, task: EvalTask) -> str:
        return self.config.prompt_template.format(
            prompt=task.prompt,
            acceptance_cmd=task.acceptance.command or "",
            task_id=task.id,
        )

    def _collect_artifacts(
        self,
        task: EvalTask,
        cond_name: str,
        trial_num: int,
        worktree: Path,
        passed: bool,
    ) -> tuple[str | None, list[AssertionOutcome]]:
        """Capture diff, write artifacts, run assertions if configured."""
        bundle = TrialBundle(
            output_dir=self.config.output_dir,
            task_id=task.id,
            condition=cond_name,
            trial_number=trial_num,
            worktree=worktree,
            passed=passed,
            exclude_patterns=self.config.context_patterns,
        )
        bundle.capture_diff()
        bundle.write()
        bundle.write_context_json()

        assertion_results: list[AssertionOutcome] = []
        if task.assertions and self.assertion_runner:
            ctx = bundle.to_context()
            try:
                assertion_results = self.assertion_runner.run(task.assertions, ctx)
            except Exception:
                logger.exception("Assertion execution failed for %s", task.id)

        return str(bundle.artifact_dir), assertion_results

    def _run_trial(
        self, task: EvalTask, cond_name: str, condition: Condition, trial_num: int
    ) -> TrialResult:
        rendered = self._render_prompt(task)
        if self.workspace:
            worktree = self.workspace.create_worktree(
                f"{task.id}-{cond_name}-{trial_num}", persistent=self.keep_worktrees
            )
            apply_condition(worktree, condition, self.config.context_patterns)
            dr = self.driver.execute(
                rendered,
                worktree,
                self.config.agent.model,
                self.config.agent.max_steps,
            )
            exceeded_max = dr.num_turns is not None and dr.num_turns > self.config.agent.max_steps
            ar = self.checker.check(task, worktree)
            artifact_dir, assertion_results = self._collect_artifacts(
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
        else:
            dr = self.driver.execute(
                rendered,
                Path("."),
                self.config.agent.model,
                self.config.agent.max_steps,
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
