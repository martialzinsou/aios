"""On-device LLM clients.

Two wire protocols are supported, both reachable **without leaving the
machine**:

* ``OllamaClient``  → Ollama's ``/api/chat``  (default ``127.0.0.1:11434``)
* ``LLamaCppClient`` → the OpenAI-compatible server shipped with llama.cpp
  (default ``127.0.0.1:8080/v1``)

If neither answers, the agent transparently degrades to the deterministic
:class:`~aios_agent.core.planner.HeuristicBrain`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from .base import Completion, LLMClient

_TIMEOUT = 60


def _post_json(url: str, payload: Dict[str, Any], timeout: int = _TIMEOUT) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_json(url: str, timeout: int = 5) -> Dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(
        self,
        model: str = "llama3.2",
        host: str = "http://127.0.0.1:11434",
        timeout: int = _TIMEOUT,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        try:
            _get_json(f"{self.host}/api/tags", timeout=2)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def chat(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> Completion:
        payload = {
            "model": self.model,
            "messages": list(messages),
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        raw = _post_json(f"{self.host}/api/chat", payload, self.timeout)
        content = (raw.get("message") or {}).get("content", "") or ""
        return Completion(
            text=content,
            model=self.model,
            prompt_tokens=int(raw.get("prompt_eval_count") or 0),
            completion_tokens=int(raw.get("eval_count") or 0),
        )


class LLamaCppClient(LLMClient):
    """OpenAI-compatible ``/v1/chat/completions`` endpoint (llama.cpp server)."""

    name = "llama.cpp"

    def __init__(
        self,
        model: str = "local-model",
        host: str = "http://127.0.0.1:8080",
        timeout: int = _TIMEOUT,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    def available(self) -> bool:
        try:
            _get_json(f"{self.host}/v1/models", timeout=2)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def chat(
        self,
        messages: Sequence[Dict[str, str]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> Completion:
        payload = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        raw = _post_json(f"{self.host}/v1/chat/completions", payload, self.timeout)
        choices = raw.get("choices") or []
        text = choices[0].get("message", {}).get("content", "") if choices else ""
        usage = raw.get("usage") or {}
        return Completion(
            text=text,
            model=self.model,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )


def detect_local_client(
    preferred: str = "auto",
    model: str = "",
) -> Optional[LLMClient]:
    """Probe the loopback interface for a local inference server."""
    candidates: List[LLMClient] = []
    if preferred in ("auto", "ollama"):
        candidates.append(OllamaClient(model=model or "llama3.2"))
    if preferred in ("auto", "llama.cpp"):
        candidates.append(LLamaCppClient(model=model or "local-model"))
    for client in candidates:
        if client.available():
            return client
    return None
