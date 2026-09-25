from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import make_agent

from aios_agent.request import iter_goals, main, request
from aios_agent.security.confirmation import AlwaysDeny
from aios_agent.server import serve_in_thread


def test_iter_goals_accepts_json_and_plain_text():
    goals = list(iter_goals([
        '{"goal": "infos système", "session": "abc"}',
        'liste le dossier /home/chronos/user',
        '   ',
        '{pas du json',
    ]))
    assert goals == [
        {"goal": "infos système", "session": "abc"},
        {"goal": "liste le dossier /home/chronos/user"},
        {"goal": "{pas du json"},
    ]


def test_goal_flag_roundtrip(jail: Path, tmp_path: Path, sock_dir: Path,
                             capsys):
    sock_path = str(sock_dir / "agent.sock")
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    server = serve_in_thread(agent, sock_path)
    try:
        assert main(["--socket", sock_path, "--goal", "infos système"]) == 0
        reply = json.loads(capsys.readouterr().out)
        assert reply["status"] == "answered"
        assert reply["brain"] == "heuristic"
    finally:
        server.shutdown()
        server.server_close()
        agent.shutdown()


def test_stdin_jsonl_roundtrip(jail: Path, tmp_path: Path, sock_dir: Path,
                               monkeypatch, capsys):
    import io

    sock_path = str(sock_dir / "agent.sock")
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    server = serve_in_thread(agent, sock_path)
    try:
        monkeypatch.setattr("sys.stdin",
                            io.StringIO('{"goal": "infos système"}\n'))
        assert main(["--socket", sock_path]) == 0
        reply = json.loads(capsys.readouterr().out)
        assert reply["status"] == "answered"
    finally:
        server.shutdown()
        server.server_close()
        agent.shutdown()


def test_request_without_goal_fails(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin",
                        type("S", (), {"isatty": staticmethod(lambda: True)})())
    assert main([]) == 1
    assert "objectif" in capsys.readouterr().err


def test_request_reports_dead_socket(sock_dir: Path):
    reply = request(str(sock_dir / "nope.sock"), {"goal": "x"}, timeout=1.0)
    assert reply["status"] == "error"
