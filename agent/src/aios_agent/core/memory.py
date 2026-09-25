"""Agent memory: working context + durable episodic store."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..security.redaction import redact, redact_object

_ROLE_MARK = {"system": "⚙", "user": "👤", "assistant": "🤖", "tool": "🔧"}


@dataclass
class Message:
    role: str
    content: str
    name: str = ""
    ts: float = field(default_factory=time.time)

    def render(self) -> str:
        mark = _ROLE_MARK.get(self.role, "•")
        head = f"{mark} {self.name or self.role}"
        body = self.content.strip()
        if len(body) > 2000:
            body = body[:2000] + f"\n… [{len(body) - 2000} caractères tronqués]"
        return f"{head}:\n{body}"


class WorkingMemory:
    """Bounded conversation window with character-based budgeting."""

    def __init__(self, max_chars: int = 24_000) -> None:
        self.max_chars = max_chars
        self.messages: List[Message] = []
        self._goal: str = ""

    def set_goal(self, goal: str) -> None:
        self._goal = redact(goal)

    @property
    def goal(self) -> str:
        return self._goal

    def add(self, role: str, content: str, name: str = "") -> Message:
        msg = Message(role=role, content=redact(content), name=name)
        self.messages.append(msg)
        self._trim()
        return msg

    def _trim(self) -> None:
        # Never drop the goal (first user message); shed oldest observations.
        while self._total_chars() > self.max_chars and len(self.messages) > 2:
            for i, m in enumerate(self.messages):
                if m.role == "tool":
                    self.messages.pop(i)
                    break
            else:
                self.messages.pop(1)
                break

    def _total_chars(self) -> int:
        return sum(len(m.content) for m in self.messages)

    def render(self) -> str:
        parts = []
        if self._goal:
            parts.append(f"OBJECTIF: {self._goal}")
        parts.extend(m.render() for m in self.messages)
        return "\n\n".join(parts)

    def as_list(self) -> List[Dict[str, str]]:
        return [
            {"role": m.role, "content": m.content, "name": m.name}
            for m in self.messages
        ]


@dataclass
class Episode:
    id: str
    goal: str
    outcome: str
    steps: int
    started_at: float
    finished_at: float
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EpisodicMemory:
    """Durable record of finished sessions (what worked, what was refused)."""

    def __init__(self, path: "str | Path | None" = None, max_episodes: int = 200) -> None:
        self._path = Path(path) if path else None
        self.max_episodes = max_episodes
        self.episodes: List[Episode] = []
        if self._path and self._path.exists():
            self._load()

    @classmethod
    def default_path(cls) -> Path:
        import os

        base = os.environ.get("AIOS_STATE_DIR")
        if base:
            return Path(base) / "episodes.json"
        return Path.home() / ".local" / "share" / "aios" / "episodes.json"

    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.episodes = [Episode(**e) for e in raw]

    def record(
        self,
        goal: str,
        outcome: str,
        steps: int,
        summary: str = "",
        started_at: Optional[float] = None,
    ) -> Episode:
        ep = Episode(
            id=uuid.uuid4().hex[:12],
            goal=redact(goal),
            outcome=outcome,
            steps=steps,
            started_at=started_at or time.time(),
            finished_at=time.time(),
            summary=redact(summary),
        )
        self.episodes.append(ep)
        if len(self.episodes) > self.max_episodes:
            self.episodes = self.episodes[-self.max_episodes :]
        self._save()
        return ep

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps([e.to_dict() for e in self.episodes], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def recent(self, n: int = 5) -> List[Episode]:
        return self.episodes[-n:]
