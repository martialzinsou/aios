from __future__ import annotations

from typing import Any, Dict, List

from ..security.policy import Risk
from ..security.sandbox import CommandSpec, classify_command
from .base import Tool, ToolContext, ToolResult


class RunCommand(Tool):
    """Run an external command inside the sandbox.

    The declared risk is a *floor* (EXECUTE); the effective risk is computed
    from the actual command line, so ``ls`` stays cheap while ``rm -rf`` is
    escalated to DESTRUCTIVE and ``sudo`` to PRIVILEGED (which the baseline
    policy refuses outright).
    """

    name = "run_command"
    description = (
        "Execute a shell command inside the sandbox (no shell interpolation). "
        "Risk escalates automatically for destructive or privileged commands."
    )
    risk = Risk.EXECUTE
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command line to run"},
            "cwd": {"type": "string", "description": "Working directory"},
            "timeout": {"type": "number", "description": "Seconds", "default": 30},
            "stdin": {"type": "string", "description": "Optional stdin payload"},
        },
        "required": ["command"],
    }

    def target(self, args: Dict[str, Any]) -> str:
        return str(args.get("command", ""))

    def _risk_for(self, args: Dict[str, Any]) -> Risk:
        cmd = str(args.get("command") or "")
        return classify_command(cmd.split()) if cmd else Risk.EXECUTE

    def summary(self, args: Dict[str, Any]) -> str:
        cwd = args.get("cwd")
        suffix = f" (dans {cwd})" if cwd else ""
        return f"Exécuter : {args.get('command', '?')}{suffix}"

    def execute(self, ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
        command = self.require_str(args, "command")
        spec = CommandSpec.parse(
            command,
            cwd=self.optional_str(args, "cwd") or None,
            timeout=float(args.get("timeout") or 30),
            stdin=args.get("stdin") if isinstance(args.get("stdin"), str) else None,
        )
        result = ctx.sandbox.run(spec)
        text = result.as_text()
        prefix = "" if result.ok else f"[exit {result.returncode}] "
        if result.timed_out:
            prefix = "[timeout] "
        return ToolResult(
            ok=result.ok,
            output=prefix + text,
            data={"returncode": result.returncode, "duration": result.duration},
            error="" if result.ok else (result.stderr.strip() or f"exit {result.returncode}"),
            risk=self.risk_for(args),
        )


DEFAULT_SHELL_TOOLS: List[Tool] = [RunCommand()]
