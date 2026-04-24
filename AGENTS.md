# Agent Guidelines

See README.md for architecture and CONTRIBUTING.md for setup/checks.

## Guardrails

- Models in `models.py` must stay aligned with `schema/*.json` — update both together
- `Driver` is a Protocol (`drivers/base.py`) — return errors in `DriverResult.error`, don't raise
- Metric extraction goes in `drivers/adapters.py`, not the driver itself
- Local/scratch configs go in `local/` (gitignored)

## Style

- `from __future__ import annotations` in every module
- `X | None` not `Optional[X]`, `StrEnum` for string enums
- Pydantic v2 models for external data, dataclasses for stateful internals
- Logging via `logging.getLogger(__name__)`, never print
- Classes have single responsibility — services in modules, not god-objects
- Comprehensions over loops where clearer, `map()`/`filter()` for transforms
- Flatten nested loops with `itertools` (`product`, `chain`, `groupby`) — no 3+ deep nesting
- Errors must carry context: `raise XError("what failed") from e`
- Type hints on all function signatures
