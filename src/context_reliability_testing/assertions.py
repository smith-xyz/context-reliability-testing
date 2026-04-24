"""AssertionRunner: invoke user pytest assertion files and collect results."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import AssertionOutcome
from .trial_context import TrialContext

logger = logging.getLogger(__name__)


class AssertionError_(RuntimeError):
    """Infrastructure failure in assertion execution (not a test failure)."""


class AssertionRunner:
    """Invokes pytest on user assertion files, collects results via JUnit XML."""

    def __init__(self) -> None:
        self._check_pytest()

    @staticmethod
    def _check_pytest() -> None:
        try:
            import pytest  # noqa: F401
        except ImportError:
            raise AssertionError_(
                "pytest required for assertions — "
                "install with: pip install context-reliability-testing[assertions]"
            ) from None

    def run(
        self,
        assertions_file: str,
        ctx: TrialContext,
    ) -> list[AssertionOutcome]:
        resolved = Path(assertions_file).resolve()
        if not resolved.exists():
            raise AssertionError_(f"assertions file not found: {resolved}")

        ctx_path = ctx.artifact_dir / "context.json"
        xml_path = ctx.artifact_dir / "junit.xml"

        self._invoke_pytest(resolved, ctx_path, xml_path)
        return self._parse_junit(xml_path)

    def _invoke_pytest(
        self,
        test_file: Path,
        ctx_path: Path,
        xml_path: Path,
    ) -> None:
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(test_file),
            "-p",
            "context_reliability_testing.pytest_plugin",
            f"--junitxml={xml_path}",
            "-q",
            "--no-header",
            "--tb=short",
        ]
        env = {**os.environ, "CRT_TRIAL_CONTEXT": str(ctx_path)}
        kwargs: dict = {
            "env": env,
            "cwd": str(test_file.parent),
            "timeout": 120,
        }
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, **kwargs)

        _EXIT_LABELS = {3: "internal error", 4: "usage error", 5: "no tests collected"}
        if proc.returncode >= 3:
            stderr = proc.stderr or ""
            label = _EXIT_LABELS.get(proc.returncode, f"exit {proc.returncode}")
            raise AssertionError_(
                f"pytest {label}: {stderr[:500]}" if stderr else f"pytest {label}"
            )

        if proc.returncode != 0 and proc.stdout:
            self._print_output(proc.stdout)

    def _print_output(self, output: str) -> None:
        """Print pytest output so failures are visible in the terminal."""
        import sys as _sys

        _sys.stderr.write(output)
        _sys.stderr.flush()

    @staticmethod
    def _parse_junit(xml_path: Path) -> list[AssertionOutcome]:
        if not xml_path.exists():
            raise AssertionError_("pytest did not produce JUnit XML")
        tree = ET.parse(xml_path)  # noqa: S314
        results: list[AssertionOutcome] = []
        for tc in tree.iter("testcase"):
            name = tc.get("name", "unknown")
            if tc.find("skipped") is not None:
                continue
            failure = tc.find("failure")
            error = tc.find("error")
            if failure is not None:
                results.append(
                    AssertionOutcome(
                        name=name,
                        passed=False,
                        message=failure.get("message", ""),
                    )
                )
            elif error is not None:
                results.append(
                    AssertionOutcome(
                        name=name,
                        passed=False,
                        message=f"ERROR: {error.get('message', '')}",
                    )
                )
            else:
                results.append(AssertionOutcome(name=name, passed=True))
        return results
