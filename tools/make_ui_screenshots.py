#!/usr/bin/env python3
"""Génère les captures de l'**interface aiOS** (bureau en verre liquide).

Contrairement à ``make_screenshots.py`` (fenêtres de terminal), cette interface
est une application web servie par ``aios ui`` : la capture est un rendu réel
de Chrome en mode headless sur une session vivante (agent, journal, politique,
confirmations en attente).

Aucune donnée réelle n'apparaît : la démonstration ne manipule que
``/tmp/aios-demo`` et ``/tmp/aios-state``, puis les mêmes contrôles
d'anonymisation que pour les captures terminal sont appliqués à l'état JSON
que l'interface affiche.

Usage :  python tools/make_ui_screenshots.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "wiki" / "captures"
CHROME_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
]

sys.path.insert(0, str(ROOT / "agent" / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from make_screenshots import assert_anonymous, prepare_demo  # noqa: E402

from aios_agent.core.agent import Agent, AgentConfig  # noqa: E402
from aios_agent.ui import UIConfirmer, UIServer  # noqa: E402

WIDTH, HEIGHT, SCALE = 1440, 900, 2
SHOT_TIMEOUT = 45.0


# --------------------------------------------------------------------------
# Chrome headless
# --------------------------------------------------------------------------
def chrome_path() -> str:
    for cand in CHROME_CANDIDATES:
        if Path(cand).exists():
            return cand
    sys.exit("✗ aucun Chrome/Chromium trouvé pour les captures d'interface")


def chrome_shot(url: str, target: Path, *, budget: int = 6000) -> None:
    """Capture une route ; Chrome écrit le PNG puis reste suspendu (tué ensuite)."""
    chrome = chrome_path()
    profile = Path(tempfile.mkdtemp(prefix="aios-chrome-"))
    target.unlink(missing_ok=True)
    cmd = [
        chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
        "--no-first-run", "--disable-extensions", "--disable-background-networking",
        "--no-default-browser-check", "--force-color-profile=srgb",
        f"--user-data-dir={profile}",
        f"--window-size={WIDTH},{HEIGHT}",
        f"--force-device-scale-factor={SCALE}",
        f"--virtual-time-budget={budget}",
        f"--screenshot={target}",
        url,
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + SHOT_TIMEOUT
        last = -1
        stable = 0
        while time.time() < deadline:
            if target.is_file() and target.stat().st_size > 1000:
                size = target.stat().st_size
                stable = stable + 1 if size == last else 0
                last = size
                if stable >= 2:
                    break
            time.sleep(0.35)
        else:
            sys.exit(f"✗ capture expirée : {target.name}")
    finally:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
        shutil.rmtree(profile, ignore_errors=True)

    try:
        from PIL import Image
        with Image.open(target) as img:
            img.verify()
        with Image.open(target) as img:
            size = img.size
    except Exception as exc:  # pragma: no cover
        sys.exit(f"✗ capture illisible {target.name} : {exc}")
    expected = (WIDTH * SCALE, HEIGHT * SCALE)
    if size != expected:
        sys.exit(f"✗ {target.name} : {size} ≠ {expected}")


# --------------------------------------------------------------------------
# Client de l'interface
# --------------------------------------------------------------------------
class Client:
    def __init__(self, server: UIServer) -> None:
        self.base = f"http://127.0.0.1:{server.port}"
        self.token = server.token

    def call(self, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = None
        headers: Dict[str, str] = {"X-AIOS-Token": self.token}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover
            body = exc.read().decode("utf-8", "replace")
            sys.exit(f"✗ {path} → HTTP {exc.code} : {body}")

    def wait_run(self, timeout: float = 40.0) -> Dict[str, Any]:
        deadline = time.time() + timeout
        state: Dict[str, Any] = {}
        while time.time() < deadline:
            state = self.call("/api/state")
            run = state.get("run") or {}
            if run.get("status") and run.get("status") != "running":
                return state
            time.sleep(0.2)
        sys.exit("✗ exécution bloquée dans l'interface")

    def wait_pending(self, timeout: float = 20.0) -> Dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.call("/api/state")
            if state.get("pending"):
                return state
            time.sleep(0.2)
        sys.exit("✗ aucune confirmation n'est apparue dans l'interface")


# --------------------------------------------------------------------------
# Démonstration
# --------------------------------------------------------------------------
def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = prepare_demo(Path("/tmp/aios-ui-demo"))
    demo, state_dir = paths["demo"], paths["state"]

    cfg = AgentConfig(
        jail_roots=[demo],
        audit_path=str(Path(state_dir) / "audit.jsonl"),
        brain="heuristic",
        max_steps=6,
        confirm=True,
        echo=None,
    )
    confirmer = UIConfirmer()
    agent = Agent(cfg, confirmer=confirmer)
    server = UIServer(agent, confirmer=confirmer)
    server.start_background()
    client = Client(server)

    try:
        # amorçage : lecture autorisée, tentative de privilège refusée
        client.call("/api/run", {"goal": "liste le dossier " + demo})
        client.wait_run()
        client.call("/api/run", {"goal": "lis le fichier " + demo + "/notes.md"})
        client.wait_run()
        client.call("/api/run", {"goal": "exécute la commande sudo rm -rf /"})
        client.wait_run()

        base = f"{server.url}#/"
        shots: List[Dict[str, str]] = [
            dict(name="ui-01-bureau", route="apercu",
                 title="bureau — état du système"),
            dict(name="ui-02-agent", route="conversation",
                 title="agent — conversation et étapes"),
            dict(name="ui-03-systeme", route="sante",
                 title="système — diagnostic"),
            dict(name="ui-04-outils", route="outils",
                 title="outils — capacités exposées"),
            dict(name="ui-05-politique", route="politique",
                 title="politique — règles ordonnées"),
            dict(name="ui-06-journal", route="journal",
                 title="journal — chaîne d'audit SHA-256"),
        ]

        # l'état affiché ne doit contenir aucune fuite d'identité
        shown = json.dumps(client.call("/api/state"), ensure_ascii=False)
        assert_anonymous(shown, "etat-interface")

        for shot in shots:
            target = OUT / f"{shot['name']}.png"
            chrome_shot(f"{base}{shot['route']}", target)
            print(f"  ✓ {target.relative_to(ROOT)}  ({WIDTH * SCALE}×{HEIGHT * SCALE}) "
                  f"— {shot['title']}")

        # une écriture déclenche la confirmation humaine : capture du bandeau
        target = OUT / "ui-07-confirmation.png"
        client.call("/api/run", {"goal":
                                 f"crée un fichier dans {demo}/sortie.txt "
                                 f'avec le contenu "Bonjour depuis aiOS"'})
        client.wait_pending()
        chrome_shot(f"{base}conversation", target)
        print(f"  ✓ {target.relative_to(ROOT)}  ({WIDTH * SCALE}×{HEIGHT * SCALE}) "
              f"— confirmation humaine en attente")

        # on tranche : le refus ferme la session proprement
        pending = client.call("/api/state")["pending"][0]
        client.call("/api/decision", {"id": pending["id"], "approve": False})
        client.wait_run(timeout=30)

        state = client.call("/api/state")
        assert_anonymous(json.dumps(state, ensure_ascii=False), "etat-final")
        print(f"\n  session : {state['stats']['runs']} exécutions · "
              f"{state['stats']['allow']} ALLOW · {state['stats']['confirm']} CONFIRM · "
              f"{state['stats']['deny']} DENY · {state['audit']['total']} événements · "
              f"chaîne {'intacte' if state['audit']['intact'] else 'ALTÉRÉE'}")
    finally:
        server.shutdown()
        agent.shutdown()

    print(f"\n{len(list(OUT.glob('ui-*.png')))} captures d'interface dans "
          f"{OUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
