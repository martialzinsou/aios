from __future__ import annotations

from typing import Iterable, List

from .base import Tool, ToolContext, ToolResult
from .filesystem import DEFAULT_FS_TOOLS
from .network import DEFAULT_NET_TOOLS
from .registry import ToolRegistry
from .shell import DEFAULT_SHELL_TOOLS
from .system import DEFAULT_SYSTEM_TOOLS

__all__ = [
    "Tool",
    "ToolContext",
    "ToolResult",
    "ToolRegistry",
    "default_registry",
    "default_tools",
]


def default_tools() -> List[Tool]:
    """Every capability shipped with aiOS, in registration order."""
    tools: List[Tool] = []
    tools.extend(DEFAULT_FS_TOOLS)
    tools.extend(DEFAULT_SYSTEM_TOOLS)
    tools.extend(DEFAULT_SHELL_TOOLS)
    tools.extend(DEFAULT_NET_TOOLS)
    return tools


def default_registry(extra: Iterable[Tool] = ()) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register_many(default_tools())
    registry.register_many(extra)
    return registry
