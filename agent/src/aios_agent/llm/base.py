from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class Completion:
    text: str
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMClient(ABC):
    """Minimal chat interface. Implementations must be *local* for aiOS."""

    name: str = "llm"
    model: str = ""

    @abstractmethod
    def chat(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> Completion:
        raise NotImplementedError

    def available(self) -> bool:
        return True

    def describe(self) -> str:
        return f"{self.name}:{self.model or '?'}"
