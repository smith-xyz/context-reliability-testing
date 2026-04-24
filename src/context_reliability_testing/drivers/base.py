from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..models import TokenUsage


@dataclass
class DriverResult:
    tokens: TokenUsage
    tool_calls: int
    wall_time_s: float
    raw_output: str
    error: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None


class Driver(Protocol):
    def execute(self, prompt: str, workspace: Path, model: str, max_turns: int) -> DriverResult: ...
