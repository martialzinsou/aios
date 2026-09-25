"""Local IPC server: lets the ChromeOS session talk to the agent over a
``AF_UNIX`` socket.

Wire format is newline-delimited JSON:

    → {"goal": "liste le dossier /home/user", "session": "abc"}
    ← {"status": "answered", "answer": "...", "steps": [...]}

The socket is created with mode ``0600``. When aiOS owns the directory that
holds it, the directory is tightened to ``0700`` as well; a pre-existing
directory owned by someone else (a system ``/tmp``, say) is left untouched —
the socket's own mode is what protects the agent.
"""
from __future__ import annotations

import json
import os
import socket
import socketserver
import stat
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .core.agent import Agent

_BACKLOG = 8
_MAX_LINE = 64 * 1024


def _reply(obj: Dict[str, Any]) -> bytes:
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:  # pragma: no cover - exercised via client socket
        server: "AgentServer" = self.server  # type: ignore[assignment]
        while True:
            try:
                raw = self.rfile.readline(_MAX_LINE)
            except (OSError, ValueError):
                return
            if not raw:
                return
            line = raw.strip()
            if not line:
                continue
            if line in (b"quit", b"exit"):
                self.wfile.write(_reply({"status": "bye"}))
                return
            try:
                req = json.loads(line.decode("utf-8"))
                if not isinstance(req, dict):
                    raise ValueError("expected a JSON object")
            except (ValueError, UnicodeDecodeError) as exc:
                self.wfile.write(_reply({"status": "error", "answer": f"requête invalide: {exc}"}))
                continue

            goal = str(req.get("goal") or "").strip()
            if not goal:
                self.wfile.write(_reply({"status": "error", "answer": "objectif manquant"}))
                continue
            result = server.agent.run(goal)
            self.wfile.write(_reply({
                "status": result.status,
                "answer": result.answer,
                "brain": result.brain,
                "session": result.session_id,
                "duration": round(result.duration, 3),
                "blocked": result.blocked_calls,
                "steps": [
                    {
                        "i": s.index, "action": s.action, "tool": s.tool,
                        "ok": s.ok, "blocked": s.blocked,
                        "output": s.output[:4000],
                    }
                    for s in result.steps
                ],
            }))


class AgentServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, agent: Agent, path: str) -> None:
        self.agent = agent
        self.path = path
        parent = Path(path).parent
        fresh = not parent.exists()
        parent.mkdir(parents=True, exist_ok=True)
        if fresh:
            os.chmod(str(parent), 0o700)
        else:
            st = parent.stat()
            if st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) & 0o077:
                os.chmod(str(parent), 0o700)
        if os.path.exists(path):
            os.unlink(path)
        super().__init__(path, _Handler)
        os.chmod(path, 0o600)


def serve(agent: Agent, socket_path: str, *, ready=None) -> None:
    """Blocking serve loop. ``ready`` is called once the socket is listening."""
    server = AgentServer(agent, socket_path)
    if ready is not None:
        ready(socket_path)
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        try:
            os.unlink(socket_path)
        except OSError:
            pass


def serve_in_thread(agent: Agent, socket_path: str) -> AgentServer:
    """Test helper: start the server on a background thread."""
    server = AgentServer(agent, socket_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def ask(socket_path: str, goal: str, *, timeout: float = 60.0) -> Dict[str, Any]:
    """Convenience client used by scripts and tests."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(socket_path)
        sock.sendall((json.dumps({"goal": goal}, ensure_ascii=False) + "\n").encode("utf-8"))
        with sock.makefile("r", encoding="utf-8") as fh:
            line = fh.readline()
    if not line:
        return {"status": "error", "answer": "aucune réponse"}
    return json.loads(line)


def socket_is_live(path: str) -> bool:
    try:
        mode = os.stat(path).st_mode
        if not stat.S_ISSOCK(mode):
            return False
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(path)
        return True
    except OSError:
        return False
