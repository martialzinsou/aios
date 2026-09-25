"""aiOS agent — on-device agentic AI with a least-privilege security core."""
from __future__ import annotations

__version__ = "0.1.0"

from .core.agent import Agent, AgentConfig, AgentResult
from .core.loop import StepResult
from .security.policy import Decision, Risk
from .tools.registry import ToolRegistry

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentResult",
    "StepResult",
    "Decision",
    "Risk",
    "ToolRegistry",
    "__version__",
]
