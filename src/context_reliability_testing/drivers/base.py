from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class Driver(Protocol):
    def execute(self, prompt: str, workspace: Path, model: str, max_turns: int) -> DriverResult: ...

    @property
    def supports_parallel(self) -> bool:
        return True

    async def execute_async(
        self, prompt: str, workspace: Path, model: str, max_turns: int
    ) -> DriverResult:
        return await asyncio.to_thread(self.execute, prompt, workspace, model, max_turns)
