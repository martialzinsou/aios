"""Decision brains: how the agent chooses its next move.

Two interchangeable implementations:

* :class:`LLMBrain` — drives a **local** model (Ollama / llama.cpp) through the
  JSON protocol defined in :mod:`aios_agent.llm.prompts`;
* :class:`HeuristicBrain` — a deterministic, dependency-free fallback that
  keeps the agent useful when no model is loaded (and makes tests reproducible).

Both obey the same :class:`Decision` contract, so the loop never needs to know
which one is running.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..llm.base import LLMClient
from ..llm.prompts import build_system_prompt

ACTION_TOOL = "tool"
ACTION_ANSWER = "answer"
ACTION_THINK = "think"


@dataclass
class Decision:
    action: str
    thought: str = ""
    tool: str = ""
    args: Dict[str, Any] = field(default_factory=dict)
    message: str = ""

    @classmethod
    def answer(cls, message: str, thought: str = "") -> "Decision":
        return cls(action=ACTION_ANSWER, thought=thought, message=message)

    @classmethod
    def call(cls, tool: str, args: Dict[str, Any], thought: str = "") -> "Decision":
        return cls(action=ACTION_TOOL, thought=thought, tool=tool, args=args)

    @property
    def is_call(self) -> bool:
        return self.action == ACTION_TOOL and bool(self.tool)


@dataclass
class BrainState:
    goal: str
    messages: List[Dict[str, str]]
    tools: List[Dict[str, Any]]
    step: int = 0
    last_output: str = ""


class Brain:
    name = "brain"

    def begin(self, goal: str) -> None:  # pragma: no cover - optional hook
        pass

    def decide(self, state: BrainState) -> Decision:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------------------
# JSON protocol parsing
# --------------------------------------------------------------------------
def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    decoder = json.JSONDecoder()
    idx = cleaned.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(cleaned[idx:])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        idx = cleaned.find("{", idx + 1)
    return None


def parse_decision(text: str) -> Optional[Decision]:
    raw = _extract_json(text)
    if raw is None:
        return None
    action = str(raw.get("action") or "").lower()
    thought = str(raw.get("thought") or "")
    message = str(raw.get("message") or "")
    tool = str(raw.get("tool") or "")
    args = raw.get("args")
    if not isinstance(args, dict):
        args = {}
    if action == ACTION_TOOL and tool:
        return Decision.call(tool, args, thought=thought)
    if action in (ACTION_ANSWER, "") and (message or thought):
        return Decision.answer(message or thought, thought=thought)
    if tool:
        return Decision.call(tool, args, thought=thought)
    if message:
        return Decision.answer(message, thought=thought)
    return None


# --------------------------------------------------------------------------
# LLM-backed brain
# --------------------------------------------------------------------------
class LLMBrain(Brain):
    name = "llm"

    def __init__(self, client: LLMClient, *, agent_name: str = "aiOS") -> None:
        self.client = client
        self.agent_name = agent_name
        self._system = ""
        self.last_raw = ""
        self.last_usage = (0, 0)

    def begin(self, goal: str) -> None:
        self._system = ""

    def decide(self, state: BrainState) -> Decision:
        if not self._system:
            self._system = build_system_prompt(state.tools, agent_name=self.agent_name)
        messages = [{"role": "system", "content": self._system}] + state.messages
        completion = self.client.chat(messages, tools=state.tools)
        self.last_raw = completion.text
        self.last_usage = (completion.prompt_tokens, completion.completion_tokens)
        decision = parse_decision(completion.text)
        if decision is None:
            stripped = completion.text.strip()
            if stripped:
                return Decision.answer(stripped, thought="(réponse libre)")
            return Decision.answer(
                "Je n'ai pas réussi à produire une décision exploitable. "
                "Reformule, ou charge un modèle local (`ollama pull llama3.2`)."
            )
        return decision


# --------------------------------------------------------------------------
# Deterministic fallback brain
# --------------------------------------------------------------------------
_URL_RE = re.compile(r"https?://[^\s'\"<>]+")
_PATH_RE = re.compile(r"(?:^|[\s=\"'(])((?:~|\.{1,2})?/[\w./\-]+|[\w\-./]+\.[A-Za-z0-9]{1,6})\b")
_QUOTED_RE = re.compile(r"[\"'“']([^\"'“']{1,200})[\"'”']")

_MATCHERS: List[tuple] = [
    # Ordre significatif : `run_command` avant `list_dir` pour que
    # "exécute la commande ls" ne soit pas interprété comme un simple listing.
    (re.compile(r"(?i)\b(lire|lis\b|lit\b|affiche|cat\b|read\b|ouvre|montre)"), "read_file"),
    (re.compile(r"(?i)\b(exécut|execut|lance|run\s|commande\b|shell\b|terminal\b)"), "run_command"),
    (re.compile(r"(?i)\b(liste|list_dir|contenu du dossier|ls\b)"), "list_dir"),
    (re.compile(r"(?i)\b(cherch|recherch|search\b|grep\b|trouve|find\b)"), "search"),
    (re.compile(r"(?i)\b(écris|ecris|écrire|creer|crée|cree|write_file|save\b|sauve)"), "write_file"),
    (re.compile(r"(?i)\b(supprim|efface|delete\b|remove\b|rm\b)"), "delete_path"),
    (re.compile(r"(?i)\b(curl\b|wget\b|https?:|url\b|site\b|fetch\b|télécharg|telecharg)"), "http_get"),
    (re.compile(r"(?i)\b(système|systeme\b|system info|os info|cpu\b|mémoire|memoire|ram\b|disque)"), "system_info"),
    (re.compile(r"(?i)\b(processus|process\b|tâches|taches\b|jobs\b)"), "list_processes"),
]


def _first_path(text: str) -> str:
    m = _PATH_RE.search(text)
    return m.group(1) if m else ""


class HeuristicBrain(Brain):
    """Rule-based, fully offline decision maker.

    One tool call per goal, then a summary — enough to drive demos, tests and
    headless scenarios where no local model is available.
    """

    name = "heuristic"

    def __init__(self) -> None:
        self._used = False

    def begin(self, goal: str) -> None:
        self._used = False

    def decide(self, state: BrainState) -> Decision:
        if self._used or not state.tools:
            return self._final(state)
        available = {t["name"] for t in state.tools}
        goal = state.goal

        for pattern, tool in _MATCHERS:
            if not pattern.search(goal):
                continue
            if tool not in available:
                continue
            decision = self._build(tool, goal)
            if decision is not None:
                self._used = True
                return decision
        return self._final(state)

    # -- argument synthesis -------------------------------------------------
    def _build(self, tool: str, goal: str) -> Optional[Decision]:
        path = _first_path(goal)
        url_m = _URL_RE.search(goal)
        quoted = _QUOTED_RE.search(goal)

        if tool == "read_file":
            return Decision.call(tool, {"path": path or "."}, thought="lecture demandée")
        if tool == "list_dir":
            return Decision.call(tool, {"path": path or "."}, thought="listing demandé")
        if tool == "search":
            query = quoted.group(1) if quoted else _query_after(goal)
            if not query:
                return None
            return Decision.call(
                tool,
                {"query": query, "root": path or ".", "max_hits": 30},
                thought="recherche demandée",
            )
        if tool == "write_file":
            if not path:
                return None
            content = _content_for(goal, path)
            return Decision.call(
                tool, {"path": path, "content": content}, thought="écriture demandée"
            )
        if tool == "delete_path":
            if not path:
                return None
            return Decision.call(tool, {"path": path}, thought="suppression demandée")
        if tool == "run_command":
            command = _command_for(goal)
            if not command:
                return None
            return Decision.call(tool, {"command": command}, thought="exécution demandée")
        if tool == "http_get":
            if not url_m:
                return None
            return Decision.call(tool, {"url": url_m.group(0).rstrip(".,)"),},
                                 thought="requête réseau demandée")
        if tool in ("system_info", "list_processes"):
            return Decision.call(tool, {}, thought="informations système")
        return None

    def _final(self, state: BrainState) -> Decision:
        if state.last_output:
            return Decision.answer(
                f"Voilà ce que j'ai obtenu :\n\n{state.last_output}",
                thought="résumé du résultat",
            )
        return Decision.answer(
            "Je n'ai pas identifié d'action précise. Décris ce que tu veux "
            "(lire un fichier, chercher, exécuter…) ou charge un modèle local "
            "pour un raisonnement libre."
        )


def _query_after(goal: str) -> str:
    m = _QUOTED_RE.search(goal)
    if m:
        return m.group(1)
    m = re.search(r"(?i)(?:cherche|recherch|search|grep|trouve|find)\s+(?:pour\s+|tout\s+|\w+\s+)?[\"']?([^\"'\n]+?)[\"']?\s*(?:dans|in|sur|$)", goal)
    if m:
        return m.group(1).strip()
    return ""


def _content_for(goal: str, path: str) -> str:
    # 1. texte entre guillemets
    quoted = _QUOTED_RE.search(goal)
    if quoted:
        return quoted.group(1)
    # 2. `contenu: …` / `content=…`
    m = re.search(r"(?i)\b(?:contenu|content)\s*[:=]\s*(.+)$", goal, re.S)
    if m:
        return m.group(1).strip()
    # 3. `avec …`
    m = re.search(r"(?i)\bavec\s+(.+)$", goal, re.S)
    if m:
        return m.group(1).strip()
    # 4. rien d'explicite : on n'invente pas, on note l'origine
    return "Créé par aiOS"


def _command_for(goal: str) -> str:
    m = re.search(
        r"(?i)(?:exécut\w*|execut\w*|lance\w*|run)\s*"
        r"(?:la\s+commande|commande|:)?\s*(.+)$",
        goal,
        re.S,
    )
    if m:
        return m.group(1).strip().strip("\"'")
    m = _QUOTED_RE.search(goal)
    if m:
        return m.group(1)
    return ""
