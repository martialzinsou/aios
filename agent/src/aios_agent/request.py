"""Petit client du service AF_UNIX d'aiOS.

Lit des objectifs sur stdin (une ligne = un objet JSON ``{"goal": …}`` ou une
phrase simple), les envoie au service et affiche la réponse JSON.

    echo '{"goal": "liste le dossier /home/chronos/user"}' | aios-request
    aios-request --goal "infos système"
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from typing import Any, Dict, Iterable, Iterator, List, Optional

DEFAULT_SOCKET = "/run/aios/agent.sock"


def iter_goals(lines: Iterable[str]) -> Iterator[Dict[str, Any]]:
    """Une ligne d'entrée → un objet requête."""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except ValueError:
                obj = {"goal": line}
            yield obj if isinstance(obj, dict) else {"goal": str(obj)}
        else:
            yield {"goal": line}


def request(socket_path: str, payload: Dict[str, Any],
            timeout: float = 60.0) -> Dict[str, Any]:
    """Envoie un objet, renvoie la réponse décodée."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(socket_path)
            sock.sendall((json.dumps(payload, ensure_ascii=False)
                          + "\n").encode("utf-8"))
            with sock.makefile("r", encoding="utf-8") as fh:
                line = fh.readline()
    except OSError as exc:
        return {"status": "error",
                "answer": f"service injoignable ({socket_path}): {exc}"}
    if not line:
        return {"status": "error", "answer": "aucune réponse du service"}
    try:
        reply = json.loads(line)
    except ValueError as exc:
        return {"status": "error", "answer": f"réponse illisible: {exc}"}
    return reply if isinstance(reply, dict) else {"status": "error",
                                                  "answer": str(reply)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aios-request",
        description="Envoie des objectifs au service aiOS (AF_UNIX, 0600).",
    )
    p.add_argument("--socket",
                   default=os.environ.get("AIOS_SOCKET", DEFAULT_SOCKET),
                   help=f"chemin du socket (défaut: {DEFAULT_SOCKET})")
    p.add_argument("--goal", action="append", default=[], metavar="OBJECTIF",
                   help="objectif à envoyer (répétable); sinon lit stdin")
    p.add_argument("--timeout", type=float, default=60.0,
                   help="délai d'attente de la réponse (s)")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    goals: Iterable[Dict[str, Any]]
    if args.goal:
        goals = [{"goal": g} for g in args.goal]
    elif not sys.stdin.isatty():
        goals = iter_goals(sys.stdin)
    else:
        print("aucun objectif : fournis --goal ou écris sur stdin",
              file=sys.stderr)
        return 1

    payload = list(goals)
    if not payload:
        print("aucun objectif", file=sys.stderr)
        return 1

    status = 0
    for item in payload:
        reply = request(args.socket, item, args.timeout)
        print(json.dumps(reply, ensure_ascii=False, indent=2))
        if reply.get("status") == "error" and "aucune" in str(reply.get("answer")):
            status = 1
    return status


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
