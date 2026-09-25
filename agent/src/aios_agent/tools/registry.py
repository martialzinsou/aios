"""Tool registry — the only place where a tool actually gets executed.

``ToolRegistry.call`` is a wrapper around four invariants:

* the tool exists;
* the permission manager approves the call (policy + human);
* the call is recorded in the audit log;
* failures never propagate raw tracebacks to the model.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from ..errors import SecurityError, ToolNotFound
from ..security.policy import Risk
from .base import Tool, ToolContext, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    # -- registration ------------------------------------------------------
    def register(self, tool: Tool) -> "ToolRegistry":
        if not tool.name:
            raise ValueError("tool must declare a name")
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool
        return self

    def register_many(self, tools: Iterable[Tool]) -> "ToolRegistry":
        for t in tools:
            self.register(t)
        return self

    def get(self, name: str) -> Tool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(name)
        return tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> List[str]:
        return sorted(self._tools)

    def schemas(self, *, include_hidden: bool = False) -> List[Dict[str, Any]]:
        return [
            t.schema()
            for t in sorted(self._tools.values(), key=lambda t: t.name)
            if include_hidden or not t.hidden
        ]

    # -- execution ---------------------------------------------------------
    def call(
        self,
        name: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        ctx: ToolContext,
        auto_approve: bool = False,
    ) -> ToolResult:
        args = dict(args or {})
        audit = ctx.permissions.audit

        try:
            tool = self.get(name)
        except ToolNotFound:
            audit.append(
                "tool_call", action=name, outcome="unknown_tool",
                detail={"args": args},
            )
            return ToolResult.failure(f"unknown tool: {name}", Risk.READ)

        target = ""
        try:
            target = tool.target(args) or ""
        except Exception:  # pragma: no cover - target() must not break the call
            target = ""

        risk = tool.risk_for(args)

        # 1. least-privilege authorisation
        try:
            auth = ctx.permissions.authorize(
                tool.name,
                risk,
                target,
                summary=tool.summary(args),
                payload=args,
            )
        except Exception as exc:  # pragma: no cover
            return ToolResult.failure(f"authorisation error: {exc}", risk)

        if not auth.allowed:
            audit.append(
                "tool_call",
                action=tool.name,
                target=target,
                verdict=auth.verdict.value,
                approved_by=auth.approved_by,
                outcome="blocked",
                detail={"rule": auth.rule_id, "reason": auth.reason},
            )
            if auth.verdict.value == "deny":
                msg = f"policy denied {tool.name}: {auth.reason}"
            else:
                msg = f"permission refused for {tool.name}: {auth.reason}"
            return ToolResult.failure(msg, risk)

        # 2. execution
        try:
            result = tool.execute(ctx, args)
        except SecurityError as exc:
            audit.append(
                "tool_call", action=tool.name, target=target,
                approved_by=auth.approved_by, outcome="sandbox_blocked",
                detail={"error": str(exc)},
            )
            return ToolResult.failure(f"sandbox: {exc}", risk)
        except Exception as exc:
            audit.append(
                "tool_call", action=tool.name, target=target,
                approved_by=auth.approved_by, outcome="error",
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )
            return ToolResult.failure(f"{type(exc).__name__}: {exc}", risk)

        # 3. audit outcome
        audit.append(
            "tool_call",
            action=tool.name,
            target=target,
            verdict=auth.verdict.value,
            approved_by=auth.approved_by,
            outcome="ok" if result.ok else "failed",
            detail={"args": args, "error": result.error},
        )
        return result
