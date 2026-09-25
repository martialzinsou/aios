"""Interface graphique locale d'aiOS.

Un serveur HTTP **sur la loopback uniquement** expose une application web
(panneaux en verre liquide) qui pilote l'agent : conversation, outils,
politique, journal d'audit et — surtout — la file des confirmations
humaines, que l'humain résout depuis le navigateur (I2).

Sécurité
--------
* liaison sur ``127.0.0.1`` (ou ``::1``) : aucun port exposé sur le réseau ;
* jeton par session, exigé sur **toutes** les routes ``/api/*`` ;
* vérification du champ ``Host`` (protection contre le relier-pour-noyer) ;
* CSP stricte, ``Cache-Control: no-store`` et aucune requête sortante ;
* une confirmation non résolue avant le délai expire sur **refus** (fail-closed).

Le service historique (``aios serve``, socket ``AF_UNIX 0600``) reste le
canal IPC de la session Chromium OS — cette interface est un client
supplémentaire, pas son remplaçant.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from .. import __version__
from ..core.agent import Agent, AgentResult
from ..security.confirmation import ConfirmationRequest, Confirmer

_ASSETS = Path(__file__).resolve().parent / "assets"
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_MAX_BODY = 64 * 1024
_CONFIRM_TIMEOUT = 600.0


# --------------------------------------------------------------------------
# Confirmation humaine portée par l'interface
# --------------------------------------------------------------------------
class _Pending:
    __slots__ = ("key", "request", "event", "approved")

    def __init__(self, key: str, request: ConfirmationRequest) -> None:
        self.key = key
        self.request = request
        self.event = threading.Event()
        self.approved = False

    def to_dict(self) -> Dict[str, Any]:
        req = self.request
        return {
            "id": self.key,
            "action": req.action,
            "target": req.target,
            "risk": req.risk,
            "summary": req.summary,
            "rule_id": req.rule_id,
            "reason": req.reason,
        }


class UIConfirmer(Confirmer):
    """Bloque l'exécution tant qu'humain n'a pas tranché dans l'interface.

    L'agent ne peut pas s'auto-approuver (I2) : il n'a aucun accès à cette
    file, qui vit dans le processus du service. Une expiration rend ``False``.
    """

    def __init__(self, timeout: float = _CONFIRM_TIMEOUT) -> None:
        self.timeout = timeout
        self._lock = threading.Lock()
        self._pending: Dict[str, _Pending] = {}

    def confirm(self, request: ConfirmationRequest) -> bool:
        item = _Pending(secrets.token_urlsafe(9), request)
        with self._lock:
            self._pending[item.key] = item
        try:
            item.event.wait(self.timeout)
        finally:
            with self._lock:
                self._pending.pop(item.key, None)
        return item.event.is_set() and item.approved

    def decide(self, key: str, approve: bool) -> bool:
        with self._lock:
            item = self._pending.get(key)
        if item is None:
            return False
        item.approved = bool(approve)
        item.event.set()
        return True

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [p.to_dict() for p in self._pending.values()]


# --------------------------------------------------------------------------
# État exposé à l'interface
# --------------------------------------------------------------------------
def _doctor(agent: Agent) -> Dict[str, Any]:
    import sys

    brain = agent.brain
    label = ""
    if hasattr(brain, "label"):
        try:
            label = brain.label()
        except Exception:  # pragma: no cover - jamais bloquant
            label = ""
    elif getattr(brain, "name", ""):
        label = str(brain.name)
    if label and label == getattr(brain, "name", ""):
        label = ""
    return {
        "python": sys.version.split()[0],
        "version": __version__,
        "brain": getattr(brain, "name", type(brain).__name__),
        "model": label,
        "jail": list(agent.config.jail_roots),
        "audit_path": str(agent.audit.path or "(journal en mémoire)"),
        "audit_intact": bool(agent.audit_verify()),
        "policy_source": agent.config.policy_path or "(défaut)",
        "max_steps": agent.config.max_steps,
        "max_seconds": agent.config.max_seconds,
        "confirm": bool(agent.config.confirm),
    }


def _stats(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compteurs agrégés — le verdict est porté par chaque `authorize`."""
    stats = {"events": len(records), "allow": 0, "deny": 0, "confirm": 0,
             "tool_calls": 0, "blocked": 0, "runs": 0}
    for rec in records:
        verdict = str(rec.get("verdict") or "").lower()
        if not verdict:
            outcome = str(rec.get("outcome") or "").lower()
            verdict = {"auto": "allow", "granted": "allow",
                       "approved": "confirm", "rejected": "confirm",
                       "denied": "deny"}.get(outcome, "")
        if verdict == "allow":
            stats["allow"] += 1
        elif verdict == "deny":
            stats["deny"] += 1
        elif verdict == "confirm":
            stats["confirm"] += 1
        event = rec.get("event")
        if event == "tool_call":
            stats["tool_calls"] += 1
        elif event == "session_end":
            stats["runs"] += 1
        if rec.get("blocked"):
            stats["blocked"] += 1
    return stats


class UIServer:
    """Serveur de l'interface : HTTP loopback + jeton + file de confirmations."""

    def __init__(
        self,
        agent: Agent,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        token: Optional[str] = None,
        confirmer: Optional[UIConfirmer] = None,
    ) -> None:
        if host not in _ALLOWED_HOSTS:
            raise ValueError(f"liaison refusée (loopback uniquement) : {host}")
        self.agent = agent
        self.host = host
        self.token = token or secrets.token_urlsafe(24)
        self.confirmer = confirmer or UIConfirmer()
        self._lock = threading.Lock()
        self._run: Optional[Dict[str, Any]] = None
        self._history: List[Dict[str, Any]] = []
        self._serving = False

        ui = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = f"aiOS-ui/{__version__}"
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # pas de bruit
                return

            @property
            def _host_ok(self) -> bool:
                raw = (self.headers.get("Host") or "").strip().lower()
                hostpart = raw.rsplit(":", 1)[0]
                return hostpart in _ALLOWED_HOSTS

            def _json(self, code: int, obj: Dict[str, Any]) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self) -> bool:
                given = (self.headers.get("X-AIOS-Token") or "").strip()
                if not given:
                    query = parse_qs(urlparse(self.path).query)
                    given = (query.get("t") or [""])[0]
                return bool(given) and secrets.compare_digest(given, ui.token)

            def _deny(self, code: int, message: str) -> None:
                self._json(code, {"status": "error", "error": message})

            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if not self._host_ok:
                    self._deny(403, "host refusé")
                    return
                if path in ("/", "/index.html"):
                    self._file("index.html", "text/html; charset=utf-8")
                    return
                if path.startswith("/assets/"):
                    self._asset(path[len("/assets/"):])
                    return
                if path == "/api/health":
                    self._json(200, {"ok": True, "version": __version__})
                    return
                if path.startswith("/api/"):
                    if not self._authorized():
                        self._deny(401, "jeton manquant ou invalide")
                        return
                    if path == "/api/state":
                        self._json(200, ui.state())
                        return
                    self._deny(404, "route inconnue")
                    return
                self._deny(404, "introuvable")

            def do_POST(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if not self._host_ok:
                    self._deny(403, "host refusé")
                    return
                if not self._authorized():
                    self._deny(401, "jeton manquant ou invalide")
                    return
                try:
                    payload = self._body()
                except ValueError as exc:
                    self._deny(400, str(exc))
                    return
                if path == "/api/run":
                    goal = str(payload.get("goal") or "").strip()
                    if not goal:
                        self._deny(400, "objectif manquant")
                        return
                    ok, message = ui.start_run(goal)
                    self._json(200 if ok else 409,
                               {"status": "accepted" if ok else "busy",
                                "message": message})
                    return
                if path == "/api/decision":
                    key = str(payload.get("id") or "")
                    approved = bool(payload.get("approve"))
                    if not key:
                        self._deny(400, "identifiant manquant")
                        return
                    if not ui.confirmer.decide(key, approved):
                        self._deny(404, "demande introuvable ou déjà résolue")
                        return
                    self._json(200, {"status": "decided",
                                     "approved": approved})
                    return
                self._deny(404, "route inconnue")

            def _body(self) -> Dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0 or length > _MAX_BODY:
                    raise ValueError("corps de requête invalide")
                raw = self.rfile.read(length)
                try:
                    obj = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError(f"JSON invalide : {exc}")
                if not isinstance(obj, dict):
                    raise ValueError("objet JSON attendu")
                return obj

            def _asset(self, name: str) -> None:
                if "/" in name or name.startswith("."):
                    self._deny(404, "introuvable")
                    return
                target = (_ASSETS / name).resolve()
                if target.parent != _ASSETS.resolve() or not target.is_file():
                    self._deny(404, "introuvable")
                    return
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                self._file(name, f"{ctype}; charset=utf-8"
                           if ctype.startswith("text/") or ctype.endswith(("javascript",))
                           else ctype)

            def _file(self, name: str, ctype: str) -> None:
                target = _ASSETS / name
                if not target.is_file():
                    self._deny(404, "introuvable")
                    return
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; img-src 'self' data:; "
                    "style-src 'self'; script-src 'self'; "
                    "connect-src 'self'; base-uri 'none'; form-action 'none'",
                )
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.daemon_threads = True
        self._httpd.ui = self  # type: ignore[attr-defined]

    # -- accès -------------------------------------------------------------
    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}/?t={self.token}"

    # -- exécution ---------------------------------------------------------
    def start_run(self, goal: str) -> Tuple[bool, str]:
        with self._lock:
            if self._run and self._run.get("status") == "running":
                return False, "une exécution est déjà en cours"
            self._run = {
                "status": "running",
                "goal": goal,
                "started": time.time(),
                "session": self.agent.session_id,
            }
        threading.Thread(target=self._execute, args=(goal,),
                         daemon=True).start()
        return True, "exécution lancée"

    def _execute(self, goal: str) -> None:
        started = time.time()
        try:
            result = self.agent.run(goal)
            entry = _result_payload(result, round(time.time() - started, 3))
        except Exception as exc:  # pragma: no cover - repli visible en UI
            entry = {"status": "error", "goal": goal,
                     "answer": f"erreur : {exc}", "steps": [], "duration": 0.0}
        with self._lock:
            self._run = entry
            self._history.append(entry)
            self._history = self._history[-20:]

    # -- état --------------------------------------------------------------
    def state(self) -> Dict[str, Any]:
        agent = self.agent
        records = agent.audit.records()
        with self._lock:
            run = dict(self._run) if self._run else None
            history = list(self._history)
        return {
            "server": {"version": __version__, "uptime":
                       round(time.time() - agent.started_at, 1)},
            "doctor": _doctor(agent),
            "conversation": agent.memory.as_list(),
            "run": run,
            "history": history,
            "pending": self.confirmer.snapshot(),
            "tools": agent.tools(),
            "policy": agent.policy_dump(),
            "audit": {"records": records[-40:],
                      "total": len(records),
                      "intact": bool(agent.audit_verify())},
            "grants": [
                {"id": g.id, "action": g.action, "target": g.target,
                 "approved_by": g.approved_by, "ttl": g.ttl}
                for g in agent.permissions.active_grants
            ],
            "stats": _stats(records),
        }

    # -- cycle de vie ------------------------------------------------------
    def serve_forever(self) -> None:  # pragma: no cover - boucle bloquante
        self._serving = True
        try:
            self._httpd.serve_forever(poll_interval=0.2)
        finally:
            self._serving = False
            self._httpd.server_close()

    def start_background(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, daemon=True)
        thread.start()
        # attend que le port soit effectivement attribué
        for _ in range(200):
            if self._serving:
                break
            time.sleep(0.01)
        return thread

    def shutdown(self) -> None:
        if self._serving:
            self._httpd.shutdown()
        else:  # pragma: no cover - arrêt sans boucle
            self._httpd.server_close()


def _result_payload(result: AgentResult, duration: float) -> Dict[str, Any]:
    return {
        "status": result.status,
        "goal": result.goal,
        "answer": result.answer,
        "brain": result.brain,
        "session": result.session_id,
        "duration": duration,
        "blocked": result.blocked_calls,
        "steps": [
            {"i": s.index, "action": s.action, "tool": s.tool, "ok": s.ok,
             "blocked": s.blocked, "output": (s.output or "")[:4000]}
            for s in result.steps
        ],
    }
