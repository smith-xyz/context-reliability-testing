"""Shared error types used across evaluation and timeline subpackages."""

from __future__ import annotations


class PreflightError(RuntimeError):
    """Raised when acceptance checks fail on the unmodified repo."""


class ConfigError(ValueError):
    """Raised for invalid or conflicting configuration."""
