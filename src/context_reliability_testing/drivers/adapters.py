"""Agent output adapters for extracting metrics from CLI tool output.

Each adapter understands one agent CLI's output format and returns
AgentMetrics or None. extract_metrics() chains all registered adapters;
first match wins. The sidecar file (CRT_RESULT_FILE) is the universal
fallback, handled separately in SubprocessDriver._build_result.

To add support for a new agent CLI:
1. Create a class implementing MetricsAdapter
2. Add an instance to _ADAPTERS
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass
class AgentMetrics:
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tool_calls: int = 0
    cost_usd: float | None = None
    num_turns: int | None = None


class MetricsAdapter(Protocol):
    """Tries to parse agent output into metrics. Returns None if format doesn't match."""

    def extract(self, raw: str) -> AgentMetrics | None: ...


class ClaudeJsonAdapter:
    """Claude Code --output-format json: single JSON envelope."""

    def extract(self, raw: str) -> AgentMetrics | None:
        stripped = raw.strip()
        if not stripped.startswith("{"):
            return None
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict) or data.get("type") != "result":
            return None
        usage = data.get("usage") or {}
        return AgentMetrics(
            tokens_prompt=usage.get("input_tokens") or 0,
            tokens_completion=usage.get("output_tokens") or 0,
            tool_calls=0,
            cost_usd=data.get("total_cost_usd"),
            num_turns=data.get("num_turns"),
        )


class ClaudeNdjsonAdapter:
    """Claude Code --output-format stream-json: newline-delimited JSON events."""

    def extract(self, raw: str) -> AgentMetrics | None:
        metrics = AgentMetrics()
        found_result = False
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return None
            if not isinstance(event, dict) or "type" not in event:
                return None

            etype = event.get("type")
            if etype == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use":
                        metrics.tool_calls += 1
            elif etype == "result":
                found_result = True
                usage = event.get("usage") or {}
                metrics.tokens_prompt = usage.get("input_tokens") or 0
                metrics.tokens_completion = usage.get("output_tokens") or 0
                metrics.cost_usd = event.get("total_cost_usd")
                metrics.num_turns = event.get("num_turns")

        return metrics if found_result else None


_ADAPTERS: list[MetricsAdapter] = [
    ClaudeJsonAdapter(),
    ClaudeNdjsonAdapter(),
]


def extract_metrics(raw: str) -> AgentMetrics | None:
    """Try each registered adapter; first match wins."""
    for adapter in _ADAPTERS:
        result = adapter.extract(raw)
        if result is not None:
            return result
    return None
