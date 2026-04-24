"""Timeline evaluation service: cumulative divergence tracking against real history."""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .acceptance import AcceptanceChecker
from .conditions import apply_condition
from .divergence import SnapshotMetrics, StepMetrics, TimelineStep, TimelineTracker
from .drivers import Driver
from .models import (
    Condition,
    EvalTask,
    FailurePolicy,
    RunConfig,
    SequentialTask,
    TimelineMode,
)
from .runner import PreflightError
from .workspace import DiffStat, WorkspaceManager

logger = logging.getLogger(__name__)

__all__ = ["TimelineRunner", "ConditionReport", "PreflightError"]


@dataclass
class ConditionReport:
    condition: str
    run_id: str
    db_path: Path
    report_path: Path
    steps: list[TimelineStep]


@dataclass
class TimelineRunner:
    """Runs sequential tasks per condition and tracks divergence."""

    config: RunConfig
    tasks: list[SequentialTask]
    driver: Driver
    mode: TimelineMode = TimelineMode.CONTINUOUS
    checker: AcceptanceChecker = field(default_factory=AcceptanceChecker)
    on_step: Callable[[int, str, bool, int], None] | None = None

    def preflight(self, out_dir: Path) -> None:
        """Verify acceptance passes on unmodified repo at starting commit."""
        repo = self.config.repo
        if not repo:
            raise ValueError("timeline requires 'repo' in run config")
        first = self.tasks[0]
        ws = WorkspaceManager(repo.url, out_dir / ".workspace" / "_preflight", repo.commit)
        ws.clone()
        wt = ws.create_worktree("preflight")
        task = EvalTask(id=first.id, prompt=first.prompt, acceptance=first.acceptance)
        result = self.checker.preflight(task, wt)
        ws.teardown()
        if not result.passed:
            raise PreflightError(
                f"Preflight failed for '{first.id}': {result.reason}. "
                "Fix the repo baseline before running timeline."
            )

    def run(self, out_dir: Path) -> list[ConditionReport]:
        """Run all conditions and return per-condition reports."""
        repo = self.config.repo
        if not repo:
            raise ValueError("timeline requires 'repo' in run config")

        self.preflight(out_dir)
        reports: list[ConditionReport] = []

        for cond_name, condition in self.config.conditions.items():
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

        db_path = out_dir / f"timeline-{cond_name}.db"
        with TimelineTracker(db_path) as tracker:
            run_id = tracker.create_run(
                repo.url,
                repo.commit,
                str(self.config.driver.command or self.config.driver.builtin),
                cond_name,
                self.config.agent.model,
            )

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
                    raise RuntimeError("worktree not initialized — check timeline mode config")

                step = self._run_step(ws, worktree, seq_task)
                tracker.record_step(run_id, step)

                if self.on_step:
                    self.on_step(
                        seq_task.task_order,
                        seq_task.id,
                        step.step.tests_pass,
                        step.snapshot.line_delta,
                    )

                if (
                    not step.step.tests_pass
                    and self.config.on_failure == FailurePolicy.SKIP_REMAINING
                ):
                    break

        with TimelineTracker(db_path) as reader:
            steps = reader.get_run(run_id)

        ws.teardown()
        return ConditionReport(
            condition=cond_name,
            run_id=run_id,
            db_path=db_path,
            report_path=db_path,  # placeholder, CLI writes the actual report
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

        diff_output, ds = _safe_diff(ws, worktree, task.resolved_commit)
        files_agent = _safe_agent_files(ws, worktree)
        files_actual = _safe_actual_files(ws, task.resolved_commit)

        return TimelineStep(
            step=StepMetrics(
                task_id=task.id,
                task_order=task.task_order,
                resolved_commit=task.resolved_commit,
                marker=task.marker,
                tests_pass=ar.passed,
                files_changed_agent=files_agent,
                files_changed_actual=files_actual,
                files_overlap_pct=_overlap(files_agent, files_actual),
                tokens=dr.tokens,
                wall_time_s=dr.wall_time_s,
                tool_calls=dr.tool_calls,
                error=dr.error,
            ),
            snapshot=SnapshotMetrics(
                files_differ=len(ds.files_changed),
                line_delta=ds.lines_added + ds.lines_removed,
                diff_compressed=zlib.compress(diff_output.encode()),
            ),
        )


def _safe_diff(ws: WorkspaceManager, worktree: Path, ref: str) -> tuple[str, DiffStat]:
    empty = DiffStat(files_changed=[], lines_added=0, lines_removed=0)
    try:
        return ws.git(["diff", ref, "HEAD"], cwd=worktree), ws.diff_stat(worktree, ref)
    except Exception as exc:
        logger.debug("diff against %s failed: %s", ref, exc)
        return "", empty


def _safe_agent_files(ws: WorkspaceManager, worktree: Path) -> list[str]:
    try:
        out = ws.git(["diff", "--name-only", "HEAD~1", "HEAD"], cwd=worktree)
        return [f for f in out.strip().splitlines() if f]
    except Exception as exc:
        logger.debug("agent diff failed: %s", exc)
        return []


def _safe_actual_files(ws: WorkspaceManager, resolved_commit: str) -> list[str]:
    try:
        return ws.diff_stat_range(f"{resolved_commit}~1", resolved_commit).files_changed
    except Exception as exc:
        logger.debug("actual diff for %s failed: %s", resolved_commit, exc)
        return []


def _overlap(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 0.0
    union = set(a) | set(b)
    return len(set(a) & set(b)) / len(union) if union else 0.0
