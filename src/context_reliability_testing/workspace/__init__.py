"""Git workspace management: clone, worktrees, conditions."""

from ..errors import WorkspaceError
from .conditions import apply_condition
from .manager import DiffStat, WorkspaceManager

__all__ = ["DiffStat", "WorkspaceError", "WorkspaceManager", "apply_condition"]
