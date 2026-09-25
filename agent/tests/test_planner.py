from __future__ import annotations

from aios_agent.core.planner import (
    BrainState,
    Decision,
    HeuristicBrain,
    parse_decision,
)
from aios_agent.llm.prompts import build_system_prompt, render_tools

TOOLS = [
    {"name": "read_file", "description": "read", "risk": "read",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                    "required": ["path"]}},
    {"name": "list_dir", "description": "list", "risk": "read",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                    "required": ["path"]}},
    {"name": "search", "description": "search", "risk": "read",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                    "required": ["query"]}},
    {"name": "system_info", "description": "sys", "risk": "read",
     "parameters": {"type": "object", "properties": {}}},
    {"name": "run_command", "description": "exec", "risk": "execute",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                    "required": ["command"]}},
]


# -- protocol parsing ------------------------------------------------------
def test_parse_plain_json():
    d = parse_decision('{"thought":"x","action":"tool","tool":"read_file","args":{"path":"/a"}}')
    assert d is not None and d.is_call
    assert d.tool == "read_file" and d.args == {"path": "/a"}


def test_parse_json_in_markdown_fence():
    text = "```json\n{\"action\":\"answer\",\"message\":\"bonjour\"}\n```"
    d = parse_decision(text)
    assert d is not None and not d.is_call
    assert d.message == "bonjour"


def test_parse_json_with_surrounding_prose():
    text = 'Voici ma décision:\n{"action":"tool","tool":"system_info","args":{}}\nmerci'
    d = parse_decision(text)
    assert d is not None and d.tool == "system_info"


def test_parse_garbage_returns_none():
    assert parse_decision("") is None
    assert parse_decision("je ne sais pas") is None
    assert parse_decision("{not json}") is None


def test_parse_args_must_be_object():
    d = parse_decision('{"action":"tool","tool":"system_info","args":[1,2]}')
    assert d is not None and d.args == {}


def test_decision_helpers():
    assert Decision.answer("ok").action == "answer"
    c = Decision.call("t", {"a": 1})
    assert c.is_call and c.tool == "t"


# -- prompt ----------------------------------------------------------------
def test_system_prompt_contains_tools_and_protocol():
    prompt = build_system_prompt(TOOLS, agent_name="aiOS")
    assert "read_file" in prompt
    assert '"action": "tool"' in prompt or '"action"' in prompt
    assert "PRIVILEGED" in prompt or "PRIVILEGE" in prompt.upper()


def test_render_tools_lists_required_marker():
    block = render_tools(TOOLS)
    assert "read_file(path)" in block
    assert "system_info()" in block


# -- heuristic brain -------------------------------------------------------
def state(goal: str, tools=None) -> BrainState:
    return BrainState(goal=goal, messages=[], tools=tools if tools is not None else TOOLS)


def test_heuristic_maps_read():
    brain = HeuristicBrain()
    brain.begin("lis le fichier /tmp/x.txt")
    d = brain.decide(state("lis le fichier /tmp/x.txt"))
    assert d.tool == "read_file"
    assert d.args["path"] == "/tmp/x.txt"


def test_heuristic_maps_list():
    brain = HeuristicBrain()
    g = "lister le dossier /home/u/projet"
    brain.begin(g)
    d = brain.decide(state(g))
    assert d.tool == "list_dir"
    assert d.args["path"] == "/home/u/projet"


def test_heuristic_maps_system_info():
    brain = HeuristicBrain()
    g = "infos système"
    brain.begin(g)
    d = brain.decide(state(g))
    assert d.tool == "system_info"


def test_heuristic_returns_answer_after_one_tool():
    brain = HeuristicBrain()
    g = "infos système"
    brain.begin(g)
    first = brain.decide(state(g))
    assert first.is_call
    second = brain.decide(state(g, tools=TOOLS))
    assert not second.is_call
    assert "Dernier" in second.message or second.message


def test_heuristic_falls_back_to_answer_when_nothing_matches():
    brain = HeuristicBrain()
    g = "bonjour comment vas-tu"
    brain.begin(g)
    d = brain.decide(state(g))
    assert not d.is_call
    assert d.message


def test_heuristic_never_uses_unavailable_tool():
    brain = HeuristicBrain()
    g = "exécute la commande echo hi"
    brain.begin(g)
    d = brain.decide(state(g, tools=[]))
    assert not d.is_call


def test_heuristic_prefers_run_command_over_ls():
    """`ls` inside an explicit order must not degrade to a plain listing."""
    brain = HeuristicBrain()
    g = "exécute sudo ls"
    brain.begin(g)
    d = brain.decide(state(g))
    assert d.tool == "run_command"
    assert d.args["command"] == "sudo ls"


def test_heuristic_still_maps_plain_listing():
    brain = HeuristicBrain()
    g = "liste le dossier /home/u"
    brain.begin(g)
    d = brain.decide(state(g))
    assert d.tool == "list_dir"
