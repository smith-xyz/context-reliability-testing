---
description: Review CRT code changes for quality, correctness, and project conventions
globs:
alwaysApply: false
---

# Code Review

Review changes to context-reliability-testing.

## Checklist

1. Correctness — does the change do what it claims? Edge cases handled?
2. Model-schema sync — if `models.py` changed, is `schema/*.json` updated too?
3. Driver contract — errors in `DriverResult.error`, not raised exceptions
4. Tests — new behavior has a `test_*.py` counterpart using `StubDriver(seed=N)`
5. Lint — `uv run ruff check src tests` passes
6. Diff scope — no drive-by refactors mixed into the PR (per CONTRIBUTING.md)

## Feedback format

- critical — must fix before merge
- suggestion — worth improving, not blocking
- nit — optional, take it or leave it
- question — need clarification on intent

## What to push back on

- New dependencies without clear justification
- Nested loops where `itertools` would flatten
- Bare `except` or swallowed errors
- Comments that narrate what code does instead of why
- Changes to public model fields without schema update
