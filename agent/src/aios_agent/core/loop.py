from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..errors import BudgetExceeded
from ..security.policy import Decision as _Verdict  # noqa: F401
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry
from .memory import WorkingMemory
from .planner import Brain, BrainState, Decision

STATUS_ANSWERED = "answered"
STATUS_BUDGET = "budget_exceeded"
STATUS_BLOCKED = "blocked"
STATUS_ERROR = "error"


def _one_line(text: str, width: int = 150) -> str:
    line = " ⏎ ".join(p.strip() for p in (text or "").splitlines() if p.strip())
    if len(line) > width:
        line = line[:width] + " …"
    return line or "—"


def _render_args(args: Dict[str, Any], limit: int = 80) -> str:
    parts = []
    for key, value in args.items():
        text = str(value).replace("\n", "⏎")
        if len(text) > limit:
            text = text[:limit] + "…"
        parts.append(f"{key}={text}")
    return ", ".join(parts)


@dataclass
class StepResult:
    index: int
    action: str
    thought: str = ""
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    output: str = ""
    ok: bool = True
    blocked: bool = False
    duration: float = 0.0

    def render(self, width: int = 150) -> str:
        head = f"étape {self.index}"
        if self.action != "tool":
            line = _one_line(self.output, width)
            return f"{head}: 💬 {line}"
        flag = "✓" if self.ok else ("⛔" if self.blocked else "✗")
        return f"{head}: {flag} {self.tool}({_render_args(self.args)}) → {_one_line(self.output, width)}"


@dataclass
class LoopOutcome:
    status: str
    answer: str
    steps: List[StepResult] = field(default_factory=list)

    @property
    def tool_calls(self) -> int:
        return sum(1 for s in self.steps if s.action == "tool")

    @property
    def blocked_calls(self) -> int:
        return sum(1 for s in self.steps if s.blocked)


class AgentLoop:
    """observe → decide → act → observe … bounded by a step/time budget."""

    def __init__(
        self,
        registry: ToolRegistry,
        brain: Brain,
        ctx: ToolContext,
        memory: Optional[WorkingMemory] = None,
        *,
        max_steps: int = 8,
        max_seconds: float = 120.0,
        echo: Optional[Any] = None,
    ) -> None:
        self.registry = registry
        self.brain = brain
        self.ctx = ctx
        self.memory = memory or WorkingMemory()
        self.max_steps = max_steps
        self.max_seconds = max_seconds
        self.echo = echo  # callable(str) — used by the CLI for live output

    # -- helpers -----------------------------------------------------------
    def _emit(self, text: str) -> None:
        if self.echo:
            try:
                self.echo(text)
            except Exception:  # pragma: no cover
                pass

    # -- main --------------------------------------------------------------
    def run(self, goal: str) -> LoopOutcome:
        started = time.monotonic()
        self.memory.set_goal(goal)
        self.memory.add("user", goal)
        self.brain.begin(goal)

        steps: List[StepResult] = []
        last_output = ""
        status = STATUS_ANSWERED
        answer = ""

        for index in range(1, self.max_steps + 1):
            if time.monotonic() - started > self.max_seconds:
                status = STATUS_BUDGET
                answer = self._timeout_answer(last_output, index)
                break

            state = BrainState(
                goal=goal,
                messages=self.memory.as_list(),
                tools=self.registry.schemas(),
                step=index,
                last_output=last_output,
            )
            decision = self._decide(state)
            if decision is None:
                status = STATUS_ERROR
                answer = "Le cerveau n'a pas pu décider (modèle indisponible)."
                break

            if not decision.is_call:
                answer = decision.message or "(aucune réponse)"
                if decision.thought:
                    self._emit(f"💭 {decision.thought}")
                steps.append(
                    StepResult(index=index, action="answer", thought=decision.thought,
                               output=answer)
                )
                self.memory.add("assistant", answer)
                break

            step = self._act(index, decision)
            steps.append(step)
            last_output = step.output or step.args.get("content", "")
            if step.blocked:
                status = STATUS_BLOCKED if not any(
                    s.ok and s.action == "tool" for s in steps
                ) else status
        else:
            # loop exhausted without an "answer" decision
            status = STATUS_BUDGET
            answer = self._budget_answer(last_output)

        if not answer:
            answer = self._budget_answer(last_output) if status == STATUS_BUDGET else answer
        return LoopOutcome(status=status, answer=answer, steps=steps)

    # -- internals ---------------------------------------------------------
    def _decide(self, state: BrainState) -> Optional[Decision]:
        try:
            return self.brain.decide(state)
        except Exception as exc:
            self._emit(f"⚠ cerveau: {type(exc).__name__}: {exc}")
            return None

    def _act(self, index: int, decision: Decision) -> StepResult:
        self._emit(
            f"🔧 {decision.tool}({', '.join(f'{k}={v!r}' for k, v in decision.args.items())})"
        )
        started = time.monotonic()
        result = self.registry.call(decision.tool, decision.args, ctx=self.ctx)
        duration = time.monotonic() - started

        body = result.output or result.error or "(vide)"
        blocked = (not result.ok) and (
            "policy denied" in result.error or "permission refused" in result.error
        )
        marker = "⛔" if blocked else ("✓" if result.ok else "✗")
        self._emit(f"   {marker} {body.splitlines()[0][:160] if body else ''}")

        note = f"[{decision.tool}] {'BLOQUÉ: ' if blocked else ''}{body}"
        self.memory.add("tool", note, name=decision.tool)
        if decision.thought:
            self.memory.add("assistant", decision.thought, name="réflexion")

        return StepResult(
            index=index,
            action="tool",
            thought=decision.thought,
            tool=decision.tool,
            args=decision.args,
            output=body,
            ok=result.ok,
            blocked=blocked,
            duration=duration,
        )

    @staticmethod
    def _budget_answer(last_output: str) -> str:
        base = "Budget d'étapes épuisé avant d'atteindre une réponse finale."
        if last_output:
            return base + f"\n\nDernier résultat :\n{last_output}"
        return base

    @staticmethod
    def _timeout_answer(last_output: str, index: int) -> str:
        base = f"Arrêt : budget temps dépassé (étape {index})."
        if last_output:
            return base + f"\n\nDernier résultat :\n{last_output}"
        return base
