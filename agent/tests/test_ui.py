"""Interface graphique d'aiOS : serveur loopback, jeton, confirmations."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from aios_agent.core.agent import Agent  # noqa: E402
from aios_agent.security.confirmation import ConfirmationRequest  # noqa: E402
from aios_agent.ui import UIConfirmer, UIServer  # noqa: E402

from conftest import make_agent  # noqa: E402


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
class Api:
    def __init__(self, server: UIServer) -> None:
        self.base = f"http://127.0.0.1:{server.port}"
        self.token = server.token

    def get(self, path: str, *, token: bool = True,
            host: str | None = None) -> tuple[int, bytes]:
        headers = {}
        if token:
            headers["X-AIOS-Token"] = self.token
        if host is not None:
            headers["Host"] = host
        return self._open(urllib.request.Request(self.base + path, headers=headers))

    def post(self, path: str, payload: dict, *, token: bool = True) -> tuple[int, bytes]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-AIOS-Token"] = self.token
        req = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
        return self._open(req)

    @staticmethod
    def _open(req: urllib.request.Request) -> tuple[int, bytes]:
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def json(self, path: str) -> dict:
        code, body = self.get(path)
        assert code == 200, body
        return json.loads(body.decode("utf-8"))

    def wait_run(self, timeout: float = 40.0) -> dict:
        deadline = time.time() + timeout
        state: dict = {}
        while time.time() < deadline:
            state = self.json("/api/state")
            run = state.get("run") or {}
            if run.get("status") and run["status"] != "running":
                return state
            time.sleep(0.15)
        raise AssertionError("exécution jamais terminée")

    def wait_pending(self, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.json("/api/state")
            if state["pending"]:
                return state
            time.sleep(0.15)
        raise AssertionError("aucune confirmation apparue")


@pytest.fixture
def agent(jail: Path, tmp_path: Path) -> Agent:
    a = make_agent(jail, tmp_path, confirmer=UIConfirmer(), max_steps=6)
    yield a
    a.shutdown()


@pytest.fixture
def api(agent: Agent):
    confirmer = agent.permissions.confirmer  # UIConfirmer passé à make_agent
    server = UIServer(agent, confirmer=confirmer)
    server.start_background()
    yield Api(server)
    server.shutdown()


# --------------------------------------------------------------------------
# surface publique
# --------------------------------------------------------------------------
def test_only_loopback_binding_is_accepted(jail: Path, tmp_path: Path) -> None:
    agent = make_agent(jail, tmp_path, confirmer=UIConfirmer())
    try:
        for host in ("0.0.0.0", "192.168.1.10", "::", "example.com"):
            with pytest.raises(ValueError, match="loopback"):
                UIServer(agent, host=host)
    finally:
        agent.shutdown()


def test_shell_and_assets_are_served(api: Api) -> None:
    for path in ("/", "/index.html", "/assets/ui.css", "/assets/ui.js"):
        code, body = api.get(path, token=False)
        assert code == 200, path
        assert body, path


def test_health_needs_no_token(api: Api) -> None:
    code, body = api.get("/api/health", token=False)
    assert code == 200
    assert json.loads(body)["ok"] is True


def test_api_requires_the_session_token(api: Api) -> None:
    assert api.get("/api/state", token=False)[0] == 401
    req = urllib.request.Request(f"{api.base}/api/state",
                                 headers={"X-AIOS-Token": "mauvais-jeton"})
    assert api._open(req)[0] == 401
    assert api.json("/api/state")["doctor"]["confirm"] is True


def test_foreign_host_header_is_refused(api: Api) -> None:
    assert api.get("/", token=False, host="evil.example")[0] == 403
    assert api.get("/api/state", host="127.0.0.1.evil.example")[0] == 403
    # le bon hôte, avec le port, reste accepté
    assert api.get("/api/state", host=f"127.0.0.1:{api.base.rsplit(':', 1)[1]}")[0] == 200


def test_state_shape(api: Api) -> None:
    state = api.json("/api/state")
    for key in ("server", "doctor", "conversation", "run", "history", "pending",
                "tools", "policy", "audit", "grants", "stats"):
        assert key in state, key
    assert len(state["tools"]) >= 9
    assert state["policy"]["rules"]
    assert state["audit"]["intact"] is True
    assert set(state["stats"]) >= {"events", "allow", "deny", "confirm"}


def test_unknown_api_route_is_404(api: Api) -> None:
    assert api.get("/api/inconnu")[0] == 404


# --------------------------------------------------------------------------
# exécution
# --------------------------------------------------------------------------
def test_read_run_stays_in_the_sandbox(api: Api, jail: Path) -> None:
    goal = "lis le fichier " + str(jail / "docs" / "readme.txt")
    code, body = api.post("/api/run", {"goal": goal})
    assert code == 200
    state = api.wait_run()
    assert state["run"]["status"] == "answered"
    assert not state["pending"]
    assert "hello aiOS" in state["run"]["answer"]
    assert any(m["role"] == "user" for m in state["conversation"])


def test_write_run_blocks_until_a_human_decides(api: Api, jail: Path) -> None:
    target = jail / "sortie.txt"
    goal = f"crée un fichier dans {target} avec le contenu bonjour"
    assert api.post("/api/run", {"goal": goal})[0] == 200

    state = api.wait_pending()
    pending = state["pending"][0]
    assert pending["action"] == "write_file"
    assert str(target) in pending["target"]

    code, body = api.post("/api/decision", {"id": pending["id"], "approve": True})
    assert code == 200

    state = api.wait_run()
    assert state["run"]["status"] in ("answered", "denied")
    assert not state["pending"]
    if state["run"]["status"] == "answered" and not state["run"]["blocked"]:
        assert target.exists()


def test_refused_confirmation_never_reaches_the_tool(api: Api, jail: Path) -> None:
    target = jail / "refuse.txt"
    goal = f"crée un fichier dans {target} avec le contenu secret"
    assert api.post("/api/run", {"goal": goal})[0] == 200

    pending = api.wait_pending()["pending"][0]
    assert api.post("/api/decision", {"id": pending["id"], "approve": False})[0] == 200

    state = api.wait_run()
    assert not target.exists()
    assert state["run"]["blocked"] >= 1


def test_second_run_is_refused_while_one_pending(api: Api, jail: Path) -> None:
    goal = "crée un fichier dans " + str(jail / "occupe.txt") \
        + " avec le contenu a"
    assert api.post("/api/run", {"goal": goal})[0] == 200
    state = api.wait_pending()
    # une exécution attend la décision humaine : la suivante est rejetée
    assert api.post("/api/run", {"goal": "liste le dossier " + str(jail)})[0] == 409
    api.post("/api/decision", {"id": state["pending"][0]["id"], "approve": False})
    api.wait_run()


def test_run_requires_a_goal(api: Api) -> None:
    assert api.post("/api/run", {"goal": "   "})[0] == 400
    assert api.post("/api/decision", {"id": "", "approve": True})[0] == 400


def test_decision_on_unknown_pending_is_404(api: Api) -> None:
    assert api.post("/api/decision", {"id": "pas-la", "approve": True})[0] == 404


# --------------------------------------------------------------------------
# confirmation humaine
# --------------------------------------------------------------------------
def _request(action: str = "write_file", target: str = "/tmp/x") -> ConfirmationRequest:
    return ConfirmationRequest(action=action, target=target, risk="WRITE",
                               summary="écrire 3 octets", rule_id="default",
                               reason="moindre privilège")


def test_confirmation_times_out_as_a_refusal() -> None:
    confirmer = UIConfirmer(timeout=0.05)
    started = time.time()
    assert confirmer.confirm(_request()) is False
    assert time.time() - started < 5
    assert confirmer.snapshot() == []


def test_pending_disappears_once_decided() -> None:
    confirmer = UIConfirmer(timeout=5.0)
    result: dict = {}

    def worker() -> None:
        result["ok"] = confirmer.confirm(_request())

    import threading

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.time() + 5
    while not confirmer.snapshot() and time.time() < deadline:
        time.sleep(0.02)
    pending = confirmer.snapshot()
    assert pending and pending[0]["action"] == "write_file"
    assert confirmer.decide(pending[0]["id"], approve=True) is True
    thread.join(timeout=5)
    assert result["ok"] is True
    assert confirmer.snapshot() == []


def test_decision_on_an_unknown_key_is_rejected() -> None:
    assert UIConfirmer(timeout=1).decide("inconnu", approve=True) is False
