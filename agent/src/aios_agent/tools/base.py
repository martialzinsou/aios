from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from ..security.policy import Risk
from ..security.permission import PermissionManager
from ..security.sandbox import Sandbox


@dataclass
class ToolContext:
    """Everything a tool is allowed to reach.

    Tools never hold global references — they receive this context, which keeps
    the blast radius of a compromised tool to the sandbox + permissions it was
    handed.
    """

    sandbox: Sandbox
    permissions: PermissionManager
    state_dir: Optional[str] = None
    session_id: str = ""

    def jail(self, path: str) -> str:
        return str(self.sandbox.resolve(path))


@dataclass
class ToolResult:
    ok: bool
    output: str
    data: Any = None
    error: str = ""
    risk: Risk = Risk.READ

    @classmethod
    def failure(cls, error: str, risk: Risk = Risk.READ) -> "ToolResult":
        return cls(ok=False, output="", error=error, risk=risk)

    @classmethod
    def success(cls, output: str, data: Any = None, risk: Risk = Risk.READ) -> "ToolResult":
        return cls(ok=True, output=output, data=data, risk=risk)


class Tool:
    """Base class for every capability exposed to the model.

    Subclasses declare their :class:`Risk` statically — a tool cannot lower its
    own risk at call time, which is what makes the policy meaningful.
    """

    name: str = ""
    description: str = ""
    risk: Risk = Risk.READ
    parameters: Dict[str, Any] = {"type": "object", "properties": {}}
    hidden: bool = False  # hidden tools are never advertised to the model

    def target(self, args: Dict[str, Any]) -> str:
        """Return the object this call touches (path, host, …) for policy matching."""
        return ""

    def risk_for(self, args: Dict[str, Any]) -> Risk:
        """Effective risk for this call.

        Tools may *raise* their declared risk (e.g. ``rm -rf`` → DESTRUCTIVE)
        but never lower it — that keeps the static ``risk`` a hard floor.
        """
        declared = self.risk
        try:
            effective = self._risk_for(args)
        except Exception:  # pragma: no cover
            return declared
        return effective if effective.rank > declared.rank else declared

    def _risk_for(self, args: Dict[str, Any]) -> Risk:
        return self.risk

    def summary(self, args: Dict[str, Any]) -> str:
        """Human-readable one-liner shown in the confirmation prompt."""
        return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        raise NotImplementedError

    def schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "risk": self.risk.value,
        }

    # -- argument helpers --------------------------------------------------
    @staticmethod
    def require_str(args: Dict[str, Any], key: str) -> str:
        value = args.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"missing required string argument: {key!r}")
        return value

    @staticmethod
    def optional_str(args: Dict[str, Any], key: str, default: str = "") -> str:
        value = args.get(key, default)
        return default if value is None else str(value)
