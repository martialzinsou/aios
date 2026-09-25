"""Human-in-the-loop confirmation.

The agent is never allowed to self-approve a ``CONFIRM`` verdict.  The
``Confirmer`` interface is deliberately minimal so it can be backed by a CLI
prompt, a GUI dialog, a D-Bus call from the ChromeOS session, or (in tests) a
stub.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class ConfirmationRequest:
    action: str
    target: str
    risk: str
    summary: str
    rule_id: str = ""
    reason: str = ""
    payload: Optional[Dict[str, Any]] = None

    def render(self) -> str:
        lines = [
            "┌─ aiOS wants your permission " + "─" * 30,
            f"│ action : {self.action}",
            f"│ target : {self.target or '-'}",
            f"│ risk   : {self.risk}",
            f"│ why    : {self.reason or 'policy requires confirmation'}",
            f"│ detail : {self.summary}",
        ]
        if self.rule_id:
            lines.append(f"│ rule   : {self.rule_id}")
        lines.append("└" + "─" * 54)
        return "\n".join(lines)


class Confirmer:
    """Interface: return ``True`` to approve, ``False`` to reject."""

    def confirm(self, request: ConfirmationRequest) -> bool:  # pragma: no cover
        raise NotImplementedError


class AlwaysDeny(Confirmer):
    def confirm(self, request: ConfirmationRequest) -> bool:
        return False


class AlwaysAllow(Confirmer):
    """Test / batch mode.  Never used as the interactive default."""

    def confirm(self, request: ConfirmationRequest) -> bool:
        return True


class CallbackConfirmer(Confirmer):
    def __init__(self, callback: Callable[[ConfirmationRequest], bool]) -> None:
        self._callback = callback
        self.seen: list = []

    def confirm(self, request: ConfirmationRequest) -> bool:
        self.seen.append(request)
        return bool(self._callback(request))


class CLIConfirmer(Confirmer):
    def __init__(self, stream=None, input_fn=input) -> None:
        self._out = stream or sys.stderr
        self._input = input_fn

    def confirm(self, request: ConfirmationRequest) -> bool:
        self._out.write(request.render() + "\n")
        self._out.write("  Approve? [y/N] ")
        self._out.flush()
        try:
            answer = self._input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"y", "yes", "o", "oui"}
