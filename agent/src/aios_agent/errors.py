from __future__ import annotations


class AgentError(Exception):
    """Base class for all aiOS agent errors."""


class SecurityError(AgentError):
    """Raised when an action is denied by policy or the sandbox."""


class PolicyDenied(SecurityError):
    """The declarative policy returned DENY for this action."""

    def __init__(self, action: str, target: str, reason: str = "") -> None:
        self.action = action
        self.target = target
        self.reason = reason
        msg = f"policy denied {action!r} on {target!r}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class ConfirmationRejected(SecurityError):
    """The human operator declined the confirmation prompt."""


class SandboxViolation(SecurityError):
    """An action tried to escape its path jail or resource limits."""


class BudgetExceeded(AgentError):
    """The agent consumed its step / tool / time budget."""


class ToolNotFound(AgentError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"unknown tool: {name!r}")


class ToolFailed(AgentError):
    """A tool executed but reported a failure."""
