"""Git workspace management: clone, worktrees, conditions."""

from .conditions import apply_condition
from .manager import DiffStat, WorkspaceError, WorkspaceManager

__all__ = ["DiffStat", "WorkspaceError", "WorkspaceManager", "apply_condition"]
