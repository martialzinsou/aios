from __future__ import annotations

import json
import os
import socket
import stat
from pathlib import Path

import pytest

from conftest import make_agent

from aios_agent.security.confirmation import AlwaysDeny
from aios_agent.server import ask, serve_in_thread, socket_is_live


def test_socket_permissions_and_roundtrip(jail: Path, tmp_path: Path, sock_dir: Path):
    sock_path = str(sock_dir / "agent.sock")
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    server = serve_in_thread(agent, sock_path)
    try:
        assert socket_is_live(sock_path)

        st = os.stat(sock_path)
        assert stat.S_ISSOCK(st.st_mode)
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert stat.S_IMODE(os.stat(sock_dir).st_mode) == 0o700

        reply = ask(sock_path, "infos système")
        assert reply["status"] == "answered"
        assert reply["brain"] == "heuristic"
        assert reply["steps"]
    finally:
        server.shutdown()
        server.server_close()
        agent.shutdown()


def test_server_rejects_malformed_requests(jail: Path, tmp_path: Path, sock_dir: Path):
    sock_path = str(sock_dir / "agent.sock")
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    server = serve_in_thread(agent, sock_path)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(sock_path)
            s.sendall(b"this is not json\n")
            with s.makefile("r", encoding="utf-8") as fh:
                bad = json.loads(fh.readline())
        assert bad["status"] == "error"

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(sock_path)
            s.sendall(b'{"goal": ""}\n')
            with s.makefile("r", encoding="utf-8") as fh:
                empty = json.loads(fh.readline())
        assert empty["status"] == "error"

        # repeated requests on one connection are supported
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(10)
            s.connect(sock_path)
            payload = ('{"goal": "infos système"}\n' * 2).encode("utf-8")
            s.sendall(payload)
            with s.makefile("r", encoding="utf-8") as fh:
                first = json.loads(fh.readline())
                second = json.loads(fh.readline())
        assert first["status"] == second["status"] == "answered"
    finally:
        server.shutdown()
        server.server_close()
        agent.shutdown()


def test_server_tightens_directory_it_owns(jail: Path, tmp_path: Path,
                                            sock_dir: Path):
    """Un dossier pré-existant, à nous et trop ouvert, passe en 0700."""
    os.chmod(sock_dir, 0o755)
    sock_path = str(sock_dir / "agent.sock")
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    server = serve_in_thread(agent, sock_path)
    try:
        assert stat.S_IMODE(os.stat(sock_dir).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(sock_path).st_mode) == 0o600
        assert socket_is_live(sock_path)
    finally:
        server.shutdown()
        server.server_close()
        agent.shutdown()


def test_server_leaves_foreign_directory_alone(jail: Path, tmp_path: Path,
                                               sock_dir: Path,
                                               monkeypatch: pytest.MonkeyPatch):
    """/tmp appartient à quelqu'un d'autre : on n'y touche pas, on casse pas.

    Régression : chmod 0700 sur un dossier qui n'est pas le nôtre lève
    EPERM (macOS, /tmp root) et le service ne démarre pas du tout.
    """
    import aios_agent.server as server_mod
    real_uid = os.geteuid()
    monkeypatch.setattr(server_mod.os, "geteuid", lambda: real_uid + 1)
    os.chmod(sock_dir, 0o777)
    sock_path = str(sock_dir / "agent.sock")
    agent = make_agent(jail, tmp_path, confirmer=AlwaysDeny())
    server = serve_in_thread(agent, sock_path)
    try:
        assert stat.S_IMODE(os.stat(sock_dir).st_mode) == 0o777
        assert stat.S_IMODE(os.stat(sock_path).st_mode) == 0o600
        assert ask(sock_path, "infos système")["status"] == "answered"
    finally:
        server.shutdown()
        server.server_close()
        agent.shutdown()


def test_socket_is_not_live_on_missing_path(tmp_path: Path):
    assert not socket_is_live(str(tmp_path / "nope.sock"))

def test_socket_is_not_live_on_regular_file(tmp_path: Path):
    f = tmp_path / "not-a-socket"
    f.write_text("x", "utf-8")
    assert not socket_is_live(str(f))
