from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..llm.base import LLMClient
from ..llm.local import detect_local_client
from ..security.audit import AuditLog
from ..security.confirmation import AlwaysDeny, CLIConfirmer, Confirmer
from ..security.permission import PermissionManager
from ..security.policy import StaticPolicy
from ..security.sandbox import Sandbox
from ..tools import Tool, default_registry
from ..tools.base import ToolContext
from ..tools.registry import ToolRegistry
from .loop import AgentLoop, LoopOutcome, StepResult
from .memory import EpisodicMemory, WorkingMemory
from .planner import Brain, HeuristicBrain, LLMBrain


@dataclass
class AgentConfig:
    """Everything that shapes the agent's blast radius and its brain."""

    jail_roots: Sequence[str] = field(default_factory=lambda: [str(Path.home())])
    policy_path: Optional[str] = None
    audit_path: Optional[str] = None
    episodes_path: Optional[str] = None

    #: interactive confirmation (CLI).  ``False`` ⇒ every non-read is refused.
    confirm: bool = True
    #: pre-approve repeated identical calls for ``grant_ttl`` seconds.
    remember_confirmations: bool = False
    grant_ttl: float = 300.0

    max_steps: int = 8
    max_seconds: float = 120.0

    brain: str = "auto"           # auto | llm | heuristic
    llm_backend: str = "auto"     # auto | ollama | llama.cpp
    llm_model: str = ""
    agent_name: str = "aiOS"

    allow_network_commands: bool = True
    echo: Optional[Callable[[str], None]] = None

    @classmethod
    def unattended(cls, **kw: Any) -> "AgentConfig":
        """Headless / CI profile: no prompts, everything non-read is denied."""
        kw.setdefault("confirm", False)
        kw.setdefault("brain", "heuristic")
        return cls(**kw)


@dataclass
class AgentResult:
    goal: str
    status: str
    answer: str
    steps: List[StepResult]
    brain: str
    session_id: str
    duration: float
    audit_seq: int = 0

    @property
    def tool_calls(self) -> int:
        return sum(1 for s in self.steps if s.action == "tool")

    @property
    def blocked_calls(self) -> int:
        return sum(1 for s in self.steps if s.blocked)

    def render(self) -> str:
        icon = {"answered": "✔", "blocked": "⛔", "budget_exceeded": "⏱",
                "error": "✖"}.get(self.status, "•")
        lines = [f"{icon} [{self.status}] cerveau={self.brain} "
                 f"· {len(self.steps)} étape(s) · {self.duration:.2f}s", ""]
        lines.append(self.answer.strip())
        transcript = [s.render() for s in self.steps]
        if transcript:
            lines += ["", "transcript :"] + ["  " + t for t in transcript]
        return "\n".join(lines)


class Agent:
    """Facade: wires sandbox + policy + audit + brain into one runnable agent."""

    def __init__(
        self,
        config: Optional[AgentConfig] = None,
        *,
        extra_tools: Sequence[Tool] = (),
        confirmer: Optional[Confirmer] = None,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self.config = config or AgentConfig()
        self.session_id = uuid.uuid4().hex[:12]
        self.started_at = time.time()

        # --- security core -------------------------------------------------
        self.audit = AuditLog(
            self.config.audit_path or AuditLog.default_path()
        )
        if self.config.policy_path:
            self.policy = StaticPolicy.load(self.config.policy_path)
        else:
            self.policy = StaticPolicy.default()

        if confirmer is not None:
            chosen_confirmer = confirmer
        elif self.config.confirm:
            chosen_confirmer = CLIConfirmer()
        else:
            chosen_confirmer = AlwaysDeny()

        self.permissions = PermissionManager(
            policy=self.policy,
            confirmer=chosen_confirmer,
            audit=self.audit,
            grant_ttl=self.config.grant_ttl,
            remember_confirmations=self.config.remember_confirmations,
        )
        self.sandbox = Sandbox(
            self.config.jail_roots,
            allow_network_commands=self.config.allow_network_commands,
        )
        self.ctx = ToolContext(
            sandbox=self.sandbox,
            permissions=self.permissions,
            session_id=self.session_id,
        )

        # --- tools & memory ------------------------------------------------
        self.registry: ToolRegistry = default_registry(extra_tools)
        self.memory = WorkingMemory()
        self.episodic = EpisodicMemory(
            self.config.episodes_path or EpisodicMemory.default_path()
        )

        # --- brain ---------------------------------------------------------
        self._explicit_llm = llm_client
        self.brain = self._resolve_brain()

        self._announcer = Announcer(self.config.echo)
        self.loop = AgentLoop(
            registry=self.registry,
            brain=self.brain,
            ctx=self.ctx,
            memory=self.memory,
            max_steps=self.config.max_steps,
            max_seconds=self.config.max_seconds,
            echo=self._announcer.emit,
        )

        self.audit.append(
            "session_start",
            actor="system",
            outcome="ok",
            detail={
                "session": self.session_id,
                "brain": self.brain.name,
                "jail": [str(r) for r in self.sandbox.allowed_roots],
                "tools": len(self.registry.names()),
            },
        )

    # -- brain resolution --------------------------------------------------
    def _resolve_brain(self) -> Brain:
        choice = (self.config.brain or "auto").lower()
        if choice == "heuristic":
            return HeuristicBrain()
        if choice == "llm":
            client = self._explicit_llm or detect_local_client(
                self.config.llm_backend, self.config.llm_model
            )
            if client is None:
                raise RuntimeError(
                    "brain='llm' mais aucun serveur local détecté "
                    "(ollama ou llama.cpp sur la loopback)."
                )
            return LLMBrain(client, agent_name=self.config.agent_name)
        # auto
        client = self._explicit_llm or detect_local_client(
            self.config.llm_backend, self.config.llm_model
        )
        return LLMBrain(client, agent_name=self.config.agent_name) if client else HeuristicBrain()

    # -- run ---------------------------------------------------------------
    def run(self, goal: str) -> AgentResult:
        goal = (goal or "").strip()
        if not goal:
            return AgentResult(
                goal="", status="error", answer="Objectif vide.", steps=[],
                brain=self.brain.name, session_id=self.session_id, duration=0.0,
            )
        started = time.monotonic()
        self._announcer.emit(f"▶ session {self.session_id} · cerveau={self.brain.name}")
        try:
            outcome: LoopOutcome = self.loop.run(goal)
        except Exception as exc:  # last-resort: never crash the caller
            self.audit.append(
                "session_error", actor="system", outcome="crash",
                detail={"error": f"{type(exc).__name__}: {exc}"},
            )
            return AgentResult(
                goal=goal, status="error",
                answer=f"Erreur interne : {type(exc).__name__}: {exc}",
                steps=[], brain=self.brain.name, session_id=self.session_id,
                duration=time.monotonic() - started,
            )
        duration = time.monotonic() - started

        self.episodic.record(
            goal=goal,
            outcome=outcome.status,
            steps=len(outcome.steps),
            summary=outcome.answer[:400],
            started_at=self.started_at,
        )
        self.audit.append(
            "session_end",
            actor="system",
            outcome=outcome.status,
            detail={
                "session": self.session_id,
                "steps": len(outcome.steps),
                "tool_calls": outcome.tool_calls,
                "blocked": outcome.blocked_calls,
                "duration": round(duration, 3),
            },
        )
        return AgentResult(
            goal=goal,
            status=outcome.status,
            answer=outcome.answer,
            steps=outcome.steps,
            brain=self.brain.name,
            session_id=self.session_id,
            duration=duration,
            audit_seq=len(self.audit.records()),
        )

    # -- introspection -----------------------------------------------------
    def tools(self) -> List[Dict[str, Any]]:
        return self.registry.schemas()

    def policy_dump(self) -> Dict[str, Any]:
        return self.policy.to_dict()

    def audit_verify(self) -> bool:
        return self.audit.verify()

    def shutdown(self) -> None:
        n = self.permissions.revoke_all()
        self.audit.append(
            "session_stop", actor="system", outcome="ok",
            detail={"revoked_grants": n},
        )


class Announcer:
    def __init__(self, echo: Optional[Callable[[str], None]]) -> None:
        self._echo = echo

    def emit(self, text: str) -> None:
        if not self._echo:
            return
        try:
            self._echo(text)
        except Exception:  # pragma: no cover
            pass
