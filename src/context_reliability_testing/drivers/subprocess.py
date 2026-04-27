from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from ..models import PromptMode, TokenUsage
from .adapters import extract_metrics
from .base import Driver, DriverResult

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 1800  # 30 min


@dataclass(frozen=True)
class _RawCompletion:
    """Intermediate result from a subprocess call before finalization."""

    output: str
    wall_time: float
    returncode: int | None
    error: str | None = None


class SubprocessDriver(Driver):
    """Runs any CLI agent via subprocess.

    Two modes:
    - stream=True (default): stdout/stderr inherited, agent renders its own UX.
    - stream=False (headless): stdout captured, NDJSON metrics extracted if available.
    """

    def __init__(
        self,
        command: list[str],
        prompt_mode: PromptMode = PromptMode.ARG,
        timeout: int = _DEFAULT_TIMEOUT,
        stream: bool = False,
    ) -> None:
        self.command = command
        self.prompt_mode = prompt_mode
        self.timeout = timeout
        self.stream = stream

    @property
    def supports_parallel(self) -> bool:
        return not self.stream

    def _build_env(
        self, prompt: str, workspace: Path, model: str, max_turns: int
    ) -> dict[str, str]:
        return {
            **os.environ,
            "CRT_WORKSPACE": str(workspace),
            "CRT_MODEL": model,
            "CRT_MAX_TURNS": str(max_turns),
            "CRT_PROMPT": prompt,
            "CRT_RESULT_FILE": str(workspace / ".crt-result.json"),
        }

    def _resolve_cmd(self, prompt: str) -> tuple[list[str], str | None]:
        cmd = list(self.command)
        stdin_input: str | None = None
        if self.prompt_mode == PromptMode.ARG:
            cmd.append(prompt)
        elif self.prompt_mode == PromptMode.STDIN:
            stdin_input = prompt
        return cmd, stdin_input

    def _to_result(self, completion: _RawCompletion, result_file: Path) -> DriverResult:
        """Single finalization path shared by sync and async execution."""
        if completion.error:
            return self._fail(completion.wall_time, completion.error, completion.output)

        error = f"agent exited {completion.returncode}" if completion.returncode != 0 else None
        if error:
            return self._fail(completion.wall_time, error, completion.output)

        if not self.stream and completion.output:
            metrics = extract_metrics(completion.output)
            if metrics:
                return DriverResult(
                    tokens=TokenUsage(
                        prompt=metrics.tokens_prompt,
                        completion=metrics.tokens_completion,
                    ),
                    tool_calls=metrics.tool_calls,
                    wall_time_s=completion.wall_time,
                    raw_output=completion.output,
                    cost_usd=metrics.cost_usd,
                    num_turns=metrics.num_turns,
                )
        return self._build_result(result_file, completion.output, completion.wall_time)

    # ----- Sync execution -----

    def execute(self, prompt: str, workspace: Path, model: str, max_turns: int) -> DriverResult:
        result_file = workspace / ".crt-result.json"
        env = self._build_env(prompt, workspace, model, max_turns)
        cmd, stdin_input = self._resolve_cmd(prompt)

        start = time.monotonic()
        if self.stream:
            completion = self._run_passthrough(cmd, stdin_input, env, workspace, start)
        else:
            completion = self._run_captured(cmd, stdin_input, env, workspace, start)

        return self._to_result(completion, result_file)

    def _run_passthrough(
        self,
        cmd: list[str],
        stdin_input: str | None,
        env: dict[str, str],
        cwd: Path,
        start: float,
    ) -> _RawCompletion:
        try:
            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=cwd,
                text=True,
                stdin=subprocess.PIPE if stdin_input is not None else None,
            )
        except OSError as exc:
            return _RawCompletion("", time.monotonic() - start, None, f"infrastructure: {exc}")

        if stdin_input is not None and proc.stdin:
            with contextlib.suppress(OSError):
                proc.stdin.write(stdin_input)
            proc.stdin.close()

        try:
            proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return _RawCompletion(
                "", time.monotonic() - start, None, f"agent timed out after {self.timeout}s"
            )

        return _RawCompletion("", time.monotonic() - start, proc.returncode)

    def _run_captured(
        self,
        cmd: list[str],
        stdin_input: str | None,
        env: dict[str, str],
        cwd: Path,
        start: float,
    ) -> _RawCompletion:
        try:
            proc = subprocess.run(
                cmd,
                input=stdin_input,
                env=env,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return _RawCompletion(
                "", time.monotonic() - start, None, f"agent timed out after {self.timeout}s"
            )
        except OSError as exc:
            return _RawCompletion("", time.monotonic() - start, None, f"infrastructure: {exc}")
        return _RawCompletion(proc.stdout + proc.stderr, time.monotonic() - start, proc.returncode)

    # ----- Async execution -----
    # Uses native asyncio.create_subprocess_exec to avoid consuming ThreadPoolExecutor
    # slots (default=8), which matters at high parallelism (up to 32 concurrent trials).
    # Shares env/cmd setup and finalization with the sync path above.

    async def execute_async(
        self, prompt: str, workspace: Path, model: str, max_turns: int
    ) -> DriverResult:
        result_file = workspace / ".crt-result.json"
        env = self._build_env(prompt, workspace, model, max_turns)
        cmd, stdin_input = self._resolve_cmd(prompt)

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                cwd=workspace,
                stdin=asyncio.subprocess.PIPE if stdin_input is not None else None,
                stdout=asyncio.subprocess.PIPE if not self.stream else None,
                stderr=asyncio.subprocess.PIPE if not self.stream else None,
            )
        except OSError as exc:
            return self._fail(time.monotonic() - start, f"infrastructure: {exc}")

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_input.encode() if stdin_input else None),
                timeout=self.timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return self._fail(time.monotonic() - start, f"agent timed out after {self.timeout}s")

        raw_output = (stdout_bytes or b"").decode() + (stderr_bytes or b"").decode()
        completion = _RawCompletion(raw_output, time.monotonic() - start, proc.returncode)
        return self._to_result(completion, result_file)

    # ----- Shared helpers -----

    def _build_result(
        self,
        result_file: Path,
        raw_output: str,
        wall_time: float,
    ) -> DriverResult:
        if not result_file.exists():
            return DriverResult(
                tokens=TokenUsage(),
                tool_calls=0,
                wall_time_s=wall_time,
                raw_output=raw_output,
            )
        try:
            data = json.loads(result_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            return self._fail(
                wall_time,
                f"infrastructure: malformed sidecar JSON: {exc}",
                raw_output,
            )
        if not isinstance(data, dict):
            return self._fail(
                wall_time,
                f"infrastructure: sidecar JSON must be an object, got {type(data).__name__}",
                raw_output,
            )
        return DriverResult(
            tokens=TokenUsage(
                prompt=data.get("tokens_prompt") or 0,
                completion=data.get("tokens_completion") or 0,
            ),
            tool_calls=data.get("tool_calls") or 0,
            wall_time_s=wall_time,
            raw_output=raw_output,
            error=data.get("error"),
        )

    @staticmethod
    def _fail(wall_time: float, error: str | None, raw_output: str = "") -> DriverResult:
        return DriverResult(
            tokens=TokenUsage(),
            tool_calls=0,
            wall_time_s=wall_time,
            raw_output=raw_output,
            error=error,
        )
