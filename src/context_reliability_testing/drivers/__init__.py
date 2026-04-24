from __future__ import annotations

from ..models import DriverConfig
from .base import Driver, DriverResult
from .stub import StubDriver
from .subprocess import SubprocessDriver

__all__ = ["Driver", "DriverResult", "StubDriver", "SubprocessDriver", "make_driver"]


def make_driver(config: DriverConfig, *, stream: bool = False) -> Driver:
    """Resolve a DriverConfig to a concrete Driver instance."""
    if config.builtin == "stub":
        return StubDriver()
    if config.command:
        return SubprocessDriver(
            config.command,
            prompt_mode=config.prompt_mode,
            stream=stream,
        )
    if config.builtin:
        raise ValueError(f"unknown builtin driver: {config.builtin!r}")
    raise ValueError("driver config: need builtin or command")
