from __future__ import annotations

import random
from pathlib import Path

from ..models import TokenUsage
from .base import DriverResult


class StubDriver:
    """Deterministic fake for pipeline testing. Conforms to Driver protocol."""

    def __init__(self, pass_rate: float = 0.7, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._pass_rate = pass_rate

    def execute(self, prompt: str, workspace: Path, model: str, max_turns: int) -> DriverResult:
        passed = self._rng.random() < self._pass_rate
        return DriverResult(
            tokens=TokenUsage(
                prompt=self._rng.randint(500, 3000),
                completion=self._rng.randint(200, 1500),
            ),
            tool_calls=self._rng.randint(1, 20),
            wall_time_s=round(self._rng.uniform(1.0, 15.0), 2),
            raw_output="",
            error=None if passed else "agent: stub failure",
        )
