"""Render RunResult to JSON and markdown via Jinja2 templates."""

from __future__ import annotations

import logging
from collections import Counter
from itertools import chain
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from .models import RunResult, TrialResult

logger = logging.getLogger(__name__)

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)


def _analyze_context_files(
    context_files: list[Path],
    heuristics_config: Path,
) -> dict | None:
    """Run heuristic classification on context files for the report."""
    try:
        from .heuristics import RuleParser, load_heuristics_config
    except Exception:
        return None

    cfg = load_heuristics_config(heuristics_config)
    parser = RuleParser(cfg)
    all_rules = list(chain.from_iterable(parser.parse(f) for f in context_files if f.exists()))

    if not all_rules:
        return None

    counts = Counter(r.classification.value for r in all_rules)
    return {
        "total": len(all_rules),
        "negative": counts.get("negative", 0),
        "positive": counts.get("positive", 0),
        "informational": counts.get("informational", 0),
        "files": [str(f.name) for f in context_files if f.exists()],
    }


def _aggregate_assertions(
    trials: list[TrialResult],
    conditions: list[str],
) -> list[dict] | None:
    """Build per-assertion, per-condition pass rates for the template."""
    all_names: list[str] = []
    for t in trials:
        for a in t.assertion_results:
            if a.name not in all_names:
                all_names.append(a.name)
    if not all_names:
        return None

    rows = []
    for name in all_names:
        row: dict = {"name": name, "conditions": {}}
        for cond in conditions:
            cond_trials = [t for t in trials if t.condition == cond]
            relevant = [a for t in cond_trials for a in t.assertion_results if a.name == name]
            total = len(relevant)
            passed = sum(1 for a in relevant if a.passed)
            row["conditions"][cond] = {
                "passed": passed,
                "total": total,
                "pct": f"{passed / total * 100:.0f}%" if total else "n/a",
            }
        rows.append(row)
    return rows


def write_result_json(result: RunResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "results.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def write_summary_md(
    result: RunResult,
    output_dir: Path,
    template: str = "summary.md.j2",
    context_files: list[Path] | None = None,
    heuristics_config: Path | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "SUMMARY.md"

    context_analysis = (
        _analyze_context_files(context_files, heuristics_config)
        if context_files and heuristics_config
        else None
    )
    conditions = list(result.summary.keys())
    assertion_summary = _aggregate_assertions(result.trials, conditions)

    path.write_text(
        _env.get_template(template).render(
            run_id=result.run_id,
            timestamp=result.timestamp.isoformat(),
            agent=result.agent,
            summary=result.summary,
            trials=result.trials,
            context_analysis=context_analysis,
            assertion_summary=assertion_summary,
            conditions=conditions,
        ),
        encoding="utf-8",
    )
    return path
