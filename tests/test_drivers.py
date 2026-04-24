from __future__ import annotations

import json
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from context_reliability_testing.drivers import StubDriver, make_driver
from context_reliability_testing.drivers.adapters import (
    ClaudeJsonAdapter,
    ClaudeNdjsonAdapter,
    extract_metrics,
)
from context_reliability_testing.drivers.base import DriverResult
from context_reliability_testing.drivers.subprocess import SubprocessDriver
from context_reliability_testing.models import DriverConfig


def test_stub_driver_deterministic_same_seed(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    a = StubDriver(seed=42).execute("p", ws, "m", 5)
    b = StubDriver(seed=42).execute("p", ws, "m", 5)
    assert a == b


def test_stub_driver_pass_rate_mixed_outcomes() -> None:
    d = StubDriver(pass_rate=0.5, seed=1)
    ws = Path(".")
    outcomes = [d.execute("x", ws, "m", 1).error for _ in range(80)]
    assert any(e is None for e in outcomes)
    assert any(e is not None for e in outcomes)


def test_stub_driver_populates_all_fields(tmp_path: Path) -> None:
    ws = tmp_path / "w"
    ws.mkdir()
    r = StubDriver(seed=0).execute("prompt", ws, "gpt-4", 10)
    assert isinstance(r, DriverResult)
    for f in fields(DriverResult):
        assert hasattr(r, f.name)
    assert r.tokens.prompt >= 500
    assert r.tokens.completion >= 200
    assert 1 <= r.tool_calls <= 20
    assert 1.0 <= r.wall_time_s <= 15.0
    assert r.raw_output == ""


def _py_script(body: str) -> list[str]:
    return [sys.executable, "-c", body]


def test_subprocess_driver_success_sidecar(tmp_path: Path) -> None:
    script = """
import json, os
from pathlib import Path
Path(os.environ["CRT_RESULT_FILE"]).write_text(json.dumps({
    "tokens_prompt": 7,
    "tokens_completion": 8,
    "tool_calls": 4,
    "error": None,
}))
"""
    d = SubprocessDriver(_py_script(script))
    r = d.execute("hello", tmp_path, "model-x", 3)
    assert r.error is None
    assert r.tokens.prompt == 7
    assert r.tokens.completion == 8
    assert r.tool_calls == 4
    assert r.wall_time_s >= 0
    assert isinstance(r.raw_output, str)


def test_subprocess_driver_exit_nonzero_without_sidecar(tmp_path: Path) -> None:
    script = "import sys; sys.exit(1)"
    d = SubprocessDriver(_py_script(script))
    r = d.execute("p", tmp_path, "m", 1)
    assert r.error == "agent exited 1"
    assert r.tokens.prompt == 0


def test_subprocess_driver_exit_zero_without_sidecar_is_success(tmp_path: Path) -> None:
    """Tier 1: agent exits 0, no sidecar -> success with unknown metrics."""
    script = "pass"
    d = SubprocessDriver(_py_script(script))
    r = d.execute("p", tmp_path, "m", 1)
    assert r.error is None
    assert r.tokens.prompt == 0
    assert r.tokens.completion == 0
    assert r.tool_calls == 0
    assert r.wall_time_s >= 0


def test_subprocess_driver_malformed_sidecar(tmp_path: Path) -> None:
    script = """
import os
from pathlib import Path
Path(os.environ["CRT_RESULT_FILE"]).write_text("not-json")
"""
    d = SubprocessDriver(_py_script(script))
    r = d.execute("p", tmp_path, "m", 1)
    assert r.error is not None
    assert "malformed" in r.error


def test_subprocess_driver_env_vars(tmp_path: Path) -> None:
    """All CRT env vars are always set regardless of prompt_mode."""
    script = """
import json, os
from pathlib import Path
p = Path(os.environ["CRT_RESULT_FILE"])
p.write_text(json.dumps({
    "tokens_prompt": 0,
    "tokens_completion": 0,
    "tool_calls": 0,
    "error": None,
    "echo_workspace": os.environ["CRT_WORKSPACE"],
    "echo_model": os.environ["CRT_MODEL"],
    "echo_turns": os.environ["CRT_MAX_TURNS"],
    "echo_prompt": os.environ["CRT_PROMPT"],
    "echo_result": os.environ["CRT_RESULT_FILE"],
}))
"""
    from context_reliability_testing.models import PromptMode

    d = SubprocessDriver(_py_script(script), prompt_mode=PromptMode.ENV)
    prompt = "task-line"
    r = d.execute(prompt, tmp_path, "my-model", 9)
    assert r.error is None
    data = json.loads((tmp_path / ".crt-result.json").read_text())
    assert data["echo_workspace"] == str(tmp_path)
    assert data["echo_model"] == "my-model"
    assert data["echo_turns"] == "9"
    assert data["echo_prompt"] == prompt
    assert data["echo_result"] == str(tmp_path / ".crt-result.json")


def test_subprocess_driver_prompt_mode_arg(tmp_path: Path) -> None:
    """prompt_mode=arg appends prompt as last CLI argument."""
    script = """
import sys, json, os
from pathlib import Path
Path(os.environ["CRT_RESULT_FILE"]).write_text(json.dumps({
    "tokens_prompt": 0, "tokens_completion": 0, "tool_calls": 0,
    "echo_argv_last": sys.argv[-1],
}))
"""
    from context_reliability_testing.models import PromptMode

    d = SubprocessDriver(_py_script(script), prompt_mode=PromptMode.ARG)
    r = d.execute("my-task-prompt", tmp_path, "m", 1)
    assert r.error is None
    data = json.loads((tmp_path / ".crt-result.json").read_text())
    assert data["echo_argv_last"] == "my-task-prompt"


def test_subprocess_driver_prompt_mode_stdin(tmp_path: Path) -> None:
    """prompt_mode=stdin pipes prompt to stdin."""
    script = """
import sys, json, os
from pathlib import Path
stdin_content = sys.stdin.read()
Path(os.environ["CRT_RESULT_FILE"]).write_text(json.dumps({
    "tokens_prompt": 0, "tokens_completion": 0, "tool_calls": 0,
    "echo_stdin": stdin_content,
}))
"""
    from context_reliability_testing.models import PromptMode

    d = SubprocessDriver(_py_script(script), prompt_mode=PromptMode.STDIN)
    r = d.execute("stdin-prompt-text", tmp_path, "m", 1)
    assert r.error is None
    data = json.loads((tmp_path / ".crt-result.json").read_text())
    assert data["echo_stdin"] == "stdin-prompt-text"


def test_subprocess_driver_prompt_mode_env(tmp_path: Path) -> None:
    """prompt_mode=env delivers prompt only via $CRT_PROMPT, not stdin or arg."""
    script = """
import sys, json, os
from pathlib import Path
Path(os.environ["CRT_RESULT_FILE"]).write_text(json.dumps({
    "tokens_prompt": 0, "tokens_completion": 0, "tool_calls": 0,
    "echo_env_prompt": os.environ.get("CRT_PROMPT", ""),
    "echo_argc": len(sys.argv),
}))
"""
    from context_reliability_testing.models import PromptMode

    d = SubprocessDriver(_py_script(script), prompt_mode=PromptMode.ENV)
    r = d.execute("env-only-prompt", tmp_path, "m", 1)
    assert r.error is None
    data = json.loads((tmp_path / ".crt-result.json").read_text())
    assert data["echo_env_prompt"] == "env-only-prompt"
    assert data["echo_argc"] == 1  # python -c "script" -> sys.argv == ['-c']


def test_make_driver_stub() -> None:
    d = make_driver(DriverConfig(builtin="stub"))
    assert isinstance(d, StubDriver)


def test_make_driver_subprocess() -> None:
    d = make_driver(DriverConfig(command=["echo"]))
    assert isinstance(d, SubprocessDriver)


def test_make_driver_both_raises() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DriverConfig(builtin="stub", command=["echo"])


def test_make_driver_neither_raises() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        DriverConfig()


# --- Metrics extraction tests (headless mode) ---


def test_claude_ndjson_extracts_result_metrics() -> None:
    raw = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init", "model": "test"}),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "input": {"command": "ls"},
                            },
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": "f.py"},
                            },
                        ]
                    },
                }
            ),
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": 1500, "output_tokens": 800},
                    "total_cost_usd": 0.0123,
                    "num_turns": 5,
                }
            ),
        ]
    )
    metrics = ClaudeNdjsonAdapter().extract(raw)
    assert metrics is not None
    assert metrics.tokens_prompt == 1500
    assert metrics.tokens_completion == 800
    assert metrics.tool_calls == 2
    assert metrics.cost_usd == 0.0123
    assert metrics.num_turns == 5


def test_claude_ndjson_returns_none_for_plain_text() -> None:
    assert ClaudeNdjsonAdapter().extract("just some plain output\n") is None


def test_claude_ndjson_returns_none_without_result_event() -> None:
    raw = json.dumps({"type": "system", "subtype": "init", "model": "test"})
    assert ClaudeNdjsonAdapter().extract(raw) is None


def test_claude_ndjson_returns_none_for_invalid_json() -> None:
    assert ClaudeNdjsonAdapter().extract("{broken json\n") is None


def test_claude_json_extracts_metrics() -> None:
    """Claude --output-format json produces a single JSON envelope."""
    raw = json.dumps(
        {
            "type": "result",
            "result": "Here is the fix...",
            "usage": {"input_tokens": 2000, "output_tokens": 500},
            "total_cost_usd": 0.0456,
            "num_turns": 3,
            "session_id": "abc123",
        }
    )
    metrics = ClaudeJsonAdapter().extract(raw)
    assert metrics is not None
    assert metrics.tokens_prompt == 2000
    assert metrics.tokens_completion == 500
    assert metrics.cost_usd == 0.0456
    assert metrics.num_turns == 3


def test_claude_json_returns_none_for_non_result() -> None:
    raw = json.dumps({"type": "system", "subtype": "init"})
    assert ClaudeJsonAdapter().extract(raw) is None


def test_claude_json_returns_none_for_ndjson() -> None:
    raw = json.dumps({"type": "system"}) + "\n" + json.dumps({"type": "result"})
    assert ClaudeJsonAdapter().extract(raw) is None


def test_claude_json_returns_none_for_plain_text() -> None:
    assert ClaudeJsonAdapter().extract("just text output") is None


def testextract_metrics_prefers_claude_json_over_ndjson() -> None:
    """Single JSON envelope is tried first."""
    raw = json.dumps(
        {
            "type": "result",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.001,
            "num_turns": 1,
        }
    )
    metrics = extract_metrics(raw)
    assert metrics is not None
    assert metrics.tokens_prompt == 100


def testextract_metrics_falls_back_to_claude_ndjson() -> None:
    raw = "\n".join(
        [
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": 300, "output_tokens": 150},
                }
            ),
        ]
    )
    metrics = extract_metrics(raw)
    assert metrics is not None
    assert metrics.tokens_prompt == 300


def test_subprocess_driver_claude_json_metrics(tmp_path: Path) -> None:
    """Full integration: Claude --output-format json, CRT extracts metrics."""
    envelope = {
        "type": "result",
        "result": "Done",
        "usage": {"input_tokens": 1000, "output_tokens": 400},
        "total_cost_usd": 0.05,
        "num_turns": 7,
        "session_id": "test-123",
    }
    script = f"import json; print(json.dumps({json.dumps(envelope)}))"
    d = SubprocessDriver(_py_script(script), stream=False)
    r = d.execute("p", tmp_path, "m", 1)
    assert r.error is None
    assert r.tokens.prompt == 1000
    assert r.tokens.completion == 400
    assert r.cost_usd == 0.05
    assert r.num_turns == 7
