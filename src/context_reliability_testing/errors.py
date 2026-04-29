"""Domain errors for context-reliability-testing."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError


class CRTError(Exception):
    """Base for errors the CLI prints as a short, non-traced message."""


class ConfigError(CRTError, ValueError):
    """Invalid or conflicting configuration."""


class PreflightError(CRTError, RuntimeError):
    """Preflight check failed before a full run."""

    @classmethod
    def baseline(cls, task_id: str, reason: str, *, hint: str) -> PreflightError:
        return cls(f"Preflight failed for '{task_id}': {reason}. {hint}")

    @classmethod
    def smoke_test(cls, error: str, *, hint: str, output: str = "") -> PreflightError:
        detail = f"\n\nAgent output:\n{output}" if output else ""
        return cls(f"Agent smoke test failed: {error}. {hint}{detail}")


class WorkspaceError(CRTError):
    """Git workspace operation failed."""


class DriverConfigError(CRTError, ValueError):
    """Driver configuration cannot be resolved."""

    @classmethod
    def unknown_builtin(cls, name: str) -> DriverConfigError:
        return cls(f"unknown builtin driver: {name!r}")


class DataValidationError(CRTError, ValueError):
    """File contents not valid for the expected schema."""

    @classmethod
    def yaml_parse(cls, path: Path, exc: yaml.YAMLError) -> DataValidationError:
        return cls(f"invalid YAML in {path}: {exc}")

    @classmethod
    def schema(cls, context: str, exc: ValidationError) -> DataValidationError:
        return cls(f"invalid {context}: {exc}")

    @classmethod
    def not_mapping(cls, path: Path, actual_type: str) -> DataValidationError:
        return cls(f"expected YAML mapping in {path}, got {actual_type}")


class FileReadError(CRTError):
    """A path could not be read."""

    def __init__(self, path: Path, context: str = "") -> None:
        self.path = path
        label = f"{context} " if context else ""
        super().__init__(f"cannot read {label}file: {path}")


class ContextFileError(CRTError):
    """A condition references a context file that is not present."""

    def __init__(self, rel: str) -> None:
        self.rel = rel
        super().__init__(f"context file not found: {rel}")


class InternalError(CRTError, RuntimeError):
    """Invariant violated; indicates a bug."""
