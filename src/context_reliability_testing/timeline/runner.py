"""Timeline evaluation service: cumulative divergence tracking against real history."""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..drivers import Driver
from ..drivers.base import DriverResult
from ..errors import ConfigError, CRTError, InternalError, PreflightError
from ..evaluation.acceptance import AcceptanceChecker
from ..models import (
    AcceptanceType,
    Condition,
    EvalTask,
    FailurePolicy,
    RunConfig,
    SequentialTask,
    TimelineMode,
    TokenUsage,
)
from ..workspace import DiffStat, WorkspaceManager, apply_condition
from .models import SnapshotMetrics, StepMetrics, TimelineStep, read_steps_jsonl, write_step_jsonl

logger = logging.getLogger(__name__)


@dataclass
class ConditionReport:
    condition: str
    run_id: str
    jsonl_path: Path
    steps: list[TimelineStep]


@dataclass
class TimelineRunner:
    """Runs sequential tasks per condition and tracks divergence."""

    config: RunConfig
    tasks: list[SequentialTask]
    driver: Driver
    mode: TimelineMode = TimelineMode.CONTINUOUS
    checker: AcceptanceChecker = field(default_factory=AcceptanceChecker)
    on_step: (
        Callable[[int, str, bool | None, int, float, TokenUsage, float | None], None] | None
    ) = None
    on_step_start: Callable[[str], None] | None = None
    on_condition: Callable[[str], None] | None = None
    on_preflight: Callable[[str], None] | None = None

    def preflight(self, out_dir: Path) -> None:
        """Verify acceptance passes on unmodified repo at starting commit."""
        repo = self.config.repo
        if not repo:
            raise ConfigError("timeline mode requires 'repo' in run config")
        first = self.tasks[0]
        if first.acceptance.type == AcceptanceType.MANUAL:
            if self.on_preflight:
                self.on_preflight("skipped")
            return
        if self.on_preflight:
            self.on_preflight("running")
        ws = WorkspaceManager(repo.url, out_dir / ".workspace" / "_preflight", repo.commit)
        ws.clone()
        wt = ws.create_worktree("preflight")
        task = EvalTask(id=first.id, prompt=first.prompt, acceptance=first.acceptance)
        result = self.checker.check(task, wt)
        ws.teardown()
        if not result.passed:
            raise PreflightError.baseline(
                first.id,
                result.reason,
                hint="Fix the repo baseline before running timeline.",
            )
        if self.on_preflight:
            self.on_preflight("passed")

    def run(self, out_dir: Path) -> list[ConditionReport]:
        """Run all conditions and return per-condition reports."""
        repo = self.config.repo
        if not repo:
            raise ConfigError("timeline mode requires 'repo' in run config")

        self.preflight(out_dir)
        reports: list[ConditionReport] = []

        for cond_name, condition in self.config.conditions.items():
            if self.on_condition:
                self.on_condition(cond_name)
            report = self._run_condition(cond_name, condition, out_dir)
            reports.append(report)

        return reports

    def _run_condition(
        self, cond_name: str, condition: Condition, out_dir: Path
    ) -> ConditionReport:
        repo = self.config.repo
        assert repo is not None
        ws = WorkspaceManager(repo.url, out_dir / ".workspace" / cond_name, repo.commit)
        ws.clone()

        worktree: Path | None = None
        if self.mode == TimelineMode.CONTINUOUS:
            worktree = ws.create_worktree("timeline", persistent=True)
            apply_condition(worktree, condition, self.config.context_patterns)

        jsonl_path = out_dir / f"timeline-{cond_name}.jsonl"
        run_id = str(__import__("uuid").uuid4())

        for seq_task in self.tasks:
            if self.mode == TimelineMode.ANCHORED:
                if worktree is not None:
                    ws.cleanup_worktree(worktree)
                worktree = ws.create_worktree(
                    f"timeline-{seq_task.task_order}",
                    commit=seq_task.resolved_commit,
                    persistent=True,
                )
                apply_condition(worktree, condition, self.config.context_patterns)

            if worktree is None:
                raise InternalError("worktree not initialized — check timeline mode config")

            if self.on_step_start:
                self.on_step_start(seq_task.id)
            step = self._run_step(ws, worktree, seq_task)
            write_step_jsonl(jsonl_path, step)

            if self.on_step:
                passed: bool | None = (
                    None
                    if seq_task.acceptance.type == AcceptanceType.MANUAL
                    else step.step.tests_pass
                )
                self.on_step(
                    seq_task.task_order,
                    seq_task.id,
                    passed,
                    step.snapshot.line_delta,
                    step.step.wall_time_s,
                    step.step.tokens,
                    step.step.cost_usd,
                )

            if (
                not step.step.tests_pass
                and seq_task.acceptance.type != AcceptanceType.MANUAL
                and self.config.on_failure == FailurePolicy.SKIP_REMAINING
            ):
                break

        steps = read_steps_jsonl(jsonl_path)
        ws.teardown()
        return ConditionReport(
            condition=cond_name,
            run_id=run_id,
            jsonl_path=jsonl_path,
            steps=steps,
        )

    def _render_prompt(self, task: SequentialTask) -> str:
        return self.config.prompt_template.format(
            prompt=task.prompt,
            acceptance_cmd=task.acceptance.command or "",
            task_id=task.id,
        )

    def _run_step(self, ws: WorkspaceManager, worktree: Path, task: SequentialTask) -> TimelineStep:
        rendered = self._render_prompt(task)
        dr = self.driver.execute(
            rendered, worktree, self.config.agent.model, self.config.agent.max_steps
        )
        eval_task = EvalTask(
            id=task.id,
            prompt=task.prompt,
            acceptance=task.acceptance,
            metadata=task.metadata,
        )
        ar = self.checker.check(eval_task, worktree)

        step_metrics = self._build_step_metrics(ws, worktree, task, ar.passed, dr)
        snapshot = self._build_snapshot(ws, worktree, task.resolved_commit)
        return TimelineStep(step=step_metrics, snapshot=snapshot)

    def _build_step_metrics(
        self,
        ws: WorkspaceManager,
        worktree: Path,
        task: SequentialTask,
        tests_pass: bool,
        dr: DriverResult,
    ) -> StepMetrics:
        files_agent = self._safe_agent_files(ws, worktree)
        files_actual = self._safe_actual_files(ws, task.resolved_commit)
        return StepMetrics(
            task_id=task.id,
            task_order=task.task_order,
            resolved_commit=task.resolved_commit,
            marker=task.marker,
            tests_pass=tests_pass,
            files_changed_agent=files_agent,
            files_changed_actual=files_actual,
            files_overlap_pct=_overlap(files_agent, files_actual),
            tokens=dr.tokens,
            wall_time_s=dr.wall_time_s,
            tool_calls=dr.tool_calls,
            cost_usd=dr.cost_usd,
            error=dr.error,
        )

    def _build_snapshot(
        self, ws: WorkspaceManager, worktree: Path, resolved_commit: str
    ) -> SnapshotMetrics:
        diff_output, ds = self._safe_diff(ws, worktree, resolved_commit)
        return SnapshotMetrics(
            files_differ=len(ds.files_changed),
            line_delta=ds.lines_added + ds.lines_removed,
            diff_compressed=zlib.compress(diff_output.encode()),
        )

    @staticmethod
    def _safe_diff(ws: WorkspaceManager, worktree: Path, ref: str) -> tuple[str, DiffStat]:
        empty = DiffStat(files_changed=[], lines_added=0, lines_removed=0)
        try:
            return ws.git(["diff", ref, "HEAD"], cwd=worktree), ws.diff_stat(worktree, ref)
        except CRTError:
            raise
        except Exception as exc:
            logger.debug("diff against %s failed: %s", ref, exc)
            return "", empty

    @staticmethod
    def _safe_agent_files(ws: WorkspaceManager, worktree: Path) -> list[str]:
        try:
            out = ws.git(["diff", "--name-only", "HEAD~1", "HEAD"], cwd=worktree)
            return [f for f in out.strip().splitlines() if f]
        except CRTError:
            raise
        except Exception as exc:
            logger.debug("agent diff failed: %s", exc)
            return []

    @staticmethod
    def _safe_actual_files(ws: WorkspaceManager, resolved_commit: str) -> list[str]:
        try:
            return ws.diff_stat_range(f"{resolved_commit}~1", resolved_commit).files_changed
        except CRTError:
            raise
        except Exception as exc:
            logger.debug("actual diff for %s failed: %s", resolved_commit, exc)
            return []


def _overlap(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 0.0
    union = set(a) | set(b)
    return len(set(a) & set(b)) / len(union) if union else 0.0
