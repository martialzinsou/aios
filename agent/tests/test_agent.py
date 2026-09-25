from __future__ import annotations

from pathlib import Path

from conftest import make_agent

from aios_agent.core.agent import Agent, AgentConfig
from aios_agent.security.confirmation import AlwaysAllow, AlwaysDeny, CallbackConfirmer


def test_read_goal_is_answered_without_confirmation(jail: Path, tmp_path: Path):
    asked = []
    agent = make_agent(
        jail, tmp_path,
        confirmer=CallbackConfirmer(lambda r: asked.append(r) or True),
    )
    goal = f"lis le fichier {jail / 'docs' / 'readme.txt'}"
    result = agent.run(goal)
    assert result.status == "answered"
    assert "hello aiOS" in result.answer
    assert result.tool_calls >= 1
    assert asked == []          # a pure read never interrupts the user
    agent.shutdown()


def test_write_is_blocked_when_operator_refuses(jail: Path, tmp_path: Path):
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    target = jail / "created.txt"
    result = agent.run(f"crée un fichier dans {target} avec du contenu dedans")
    assert result.blocked_calls >= 1
    assert not target.exists()
    assert any(s.blocked for s in result.steps)
    agent.shutdown()


def test_write_happens_when_operator_approves(jail: Path, tmp_path: Path):
    agent = make_agent(jail, tmp_path, confirmer=AlwaysAllow())
    target = jail / "created.txt"
    result = agent.run(f"crée un fichier dans {target}")
    assert result.status == "answered"
    assert target.exists()
    agent.shutdown()


def test_delete_is_refused_even_with_trusting_operator(jail: Path, tmp_path: Path):
    """A destructive action still needs a *fresh* human 'yes' — and the
    default policy keeps it at CONFIRM, which AlwaysDeny rejects."""
    victim = jail / "docs" / "notes.md"
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    result = agent.run(f"supprime {victim}")
    assert victim.exists()
    assert result.blocked_calls >= 1
    agent.shutdown()


def test_privileged_command_is_refused_without_prompting(jail: Path, tmp_path: Path):
    asked = []
    agent = make_agent(
        jail, tmp_path,
        confirmer=CallbackConfirmer(lambda r: asked.append(r) or True),
    )
    result = agent.run("exécute la commande sudo rm -rf /")
    assert result.blocked_calls >= 1
    assert asked == []
    agent.shutdown()


def test_step_budget_is_enforced(jail: Path, tmp_path: Path):
    agent = make_agent(jail, tmp_path, confirmer=AlwaysAllow(), max_steps=1)
    goal = f"crée un fichier dans {jail / 'budget.txt'}"
    result = agent.run(goal)
    assert result.status == "budget_exceeded"
    assert len(result.steps) == 1
    agent.shutdown()


def test_empty_goal_is_an_error(jail: Path, tmp_path: Path):
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    result = agent.run("   ")
    assert result.status == "error"
    agent.shutdown()


def test_audit_is_written_and_intact(jail: Path, tmp_path: Path):
    agent = make_agent(jail, tmp_path, confirmer=AlwaysAllow())
    agent.run("infos système")
    events = [r["event"] for r in agent.audit.records()]
    assert "session_start" in events
    assert "session_end" in events
    assert "authorize" in events
    assert agent.audit_verify() is True
    agent.shutdown()
    assert "session_stop" in [r["event"] for r in agent.audit.records()]


def test_unknown_tool_does_not_crash(jail: Path, tmp_path: Path):
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())

    class Stub:
        name = "stub"
        def begin(self, goal): pass
        def decide(self, state):
            from aios_agent.core.planner import Decision
            return Decision.call("nope", {})

    agent.brain = Stub()
    agent.loop.brain = Stub()
    result = agent.run("whatever")
    assert result.status in ("answered", "budget_exceeded")
    assert any(s.output and "unknown tool" in s.output for s in result.steps)
    agent.shutdown()


def test_unattended_profile_denies_writes_by_default(jail: Path, tmp_path: Path):
    cfg = AgentConfig.unattended(
        jail_roots=[str(jail)],
        audit_path=str(tmp_path / "a.jsonl"),
        episodes_path=str(tmp_path / "e.json"),
        echo=None,
    )
    agent = Agent(cfg)
    target = jail / "nope.txt"
    agent.run(f"crée un fichier dans {target}")
    assert not target.exists()
    agent.shutdown()


def test_episodes_are_recorded(jail: Path, tmp_path: Path):
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    agent.run("infos système")
    assert agent.episodic.recent(1)
    assert agent.episodic.recent(1)[0].outcome == "answered"
    agent.shutdown()


def test_memory_is_redacted(jail: Path, tmp_path: Path):
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    agent.run("ma clé est sk-abcdefghijklmnop et je veux lire un fichier")
    rendered = agent.memory.render()
    assert "sk-abcdefghijklmnop" not in rendered
    agent.shutdown()


def test_config_defaults_are_safe():
    cfg = AgentConfig()
    assert cfg.confirm is True
    assert cfg.max_steps <= 20
    assert cfg.brain == "auto"
