from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .core.agent import Agent, AgentConfig
from .security.audit import AuditLog
from .security.confirmation import AlwaysAllow, AlwaysDeny, CLIConfirmer, Confirmer
from .security.policy import StaticPolicy
from .llm.local import detect_local_client

BANNER = "aiOS agent {version} — on-device, moindre privilège"


def _default_jail() -> List[str]:
    env = os.environ.get("AIOS_JAIL")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    return [str(Path.home())]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="aios",
        description="Assistant agentique local de aiOS (OS basé sur Chromium OS).",
    )
    p.add_argument("--version", action="version", version=f"aios {__version__}")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--jail", action="append", metavar="DIR",
                        help="racine autorisée (répétable; défaut: $HOME)")
    common.add_argument("--policy", metavar="FILE", help="fichier de politique JSON")
    common.add_argument("--audit", metavar="FILE", help="fichier d'audit JSONL")
    common.add_argument("--brain", choices=["auto", "llm", "heuristic"], default="auto")
    common.add_argument("--llm-backend", choices=["auto", "ollama", "llama.cpp"],
                        default="auto")
    common.add_argument("--llm-model", default="")
    common.add_argument("--steps", type=int, default=8, help="budget d'étapes")
    common.add_argument("--seconds", type=float, default=120.0, help="budget temps (s)")
    mode = common.add_mutually_exclusive_group()
    mode.add_argument("--no-confirm", action="store_true",
                      help="mode headless : refuse toute action non-lecture")
    mode.add_argument("--trust", action="store_true",
                      help="DANGEREUX : approuve tout sans demander")

    sub = p.add_subparsers(dest="cmd")

    run = sub.add_parser("run", parents=[common], help="exécute un objectif unique")
    run.add_argument("goal", nargs="+")
    run.add_argument("--json", action="store_true", help="sortie JSON")

    chat = sub.add_parser("chat", parents=[common], help="session interactive")
    chat.add_argument("--once", metavar="GOAL", help="exécute puis quitte")

    sub.add_parser("tools", parents=[common], help="liste les outils exposés")
    sub.add_parser("policy", parents=[common], help="affiche la politique effective")
    sub.add_parser("doctor", parents=[common], help="diagnostic environnement")

    serve_p = sub.add_parser("serve", parents=[common],
                             help="démarre le service local (socket UNIX)")
    serve_p.add_argument("--socket", default="/run/aios/agent.sock",
                         help="chemin du socket UNIX (0600)")
    serve_p.add_argument("--no-daemon", action="store_true",
                         help="garde le processus au premier plan (défaut)")

    ui_p = sub.add_parser("ui", parents=[common],
                          help="ouvre l'interface graphique locale (verre liquide)")
    ui_p.add_argument("--host", default="127.0.0.1",
                      help="liaison TCP — loopback uniquement (défaut)")
    ui_p.add_argument("--port", type=int, default=0,
                      help="port TCP, 0 = aléatoire (défaut)")
    ui_p.add_argument("--no-browser", action="store_true",
                      help="n'ouvre pas le navigateur automatiquement")

    audit = sub.add_parser("audit", parents=[common], help="journal d'audit")
    audit.add_argument("--verify", action="store_true", help="vérifie la chaîne de hachage")
    audit.add_argument("-n", type=int, default=20, help="nombre d'enregistrements")
    return p


def _confirmer(args: argparse.Namespace) -> Confirmer:
    if getattr(args, "trust", False):
        print("⚠  --trust : toutes les actions seront approuvées sans demande.",
              file=sys.stderr)
        return AlwaysAllow()
    if getattr(args, "no_confirm", False):
        return AlwaysDeny()
    return CLIConfirmer()


def make_agent(args: argparse.Namespace,
               confirmer: Optional[Confirmer] = None) -> Agent:
    cfg = AgentConfig(
        jail_roots=args.jail or _default_jail(),
        policy_path=args.policy,
        audit_path=args.audit,
        confirm=False if (args.trust or args.no_confirm) else True,
        remember_confirmations=bool(args.trust),
        max_steps=args.steps,
        max_seconds=args.seconds,
        brain=args.brain,
        llm_backend=args.llm_backend,
        llm_model=args.llm_model,
        echo=lambda s: print(s, file=sys.stderr),
    )
    return Agent(cfg, confirmer=confirmer or _confirmer(args))


# --------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    agent = make_agent(args)
    goal = " ".join(args.goal)
    result = agent.run(goal)
    agent.shutdown()
    if args.json:
        print(json.dumps({
            "status": result.status,
            "answer": result.answer,
            "brain": result.brain,
            "duration": round(result.duration, 3),
            "steps": [
                {
                    "index": s.index, "action": s.action, "tool": s.tool,
                    "args": s.args, "ok": s.ok, "blocked": s.blocked,
                    "output": s.output[:2000],
                }
                for s in result.steps
            ],
        }, ensure_ascii=False, indent=2))
    else:
        print(result.render())
    # "blocked" is a legitimate outcome: the safety layer did its job.
    return 0 if result.status in ("answered", "budget_exceeded", "blocked") else 1


def cmd_chat(args: argparse.Namespace) -> int:
    agent = make_agent(args)
    print(BANNER.format(version=__version__))
    print("  tape 'quit' pour sortir · 'tools' · 'policy' · 'audit'\n")

    if args.once:
        result = agent.run(args.once)
        print(result.render())
        agent.shutdown()
        return 0

    while True:
        try:
            goal = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not goal:
            continue
        if goal.lower() in {"quit", "exit", "q"}:
            break
        if goal.lower() == "tools":
            for spec in agent.tools():
                print(f"  {spec['name']:<14} [{spec['risk']:<11}] {spec['description']}")
            continue
        if goal.lower() == "policy":
            print(json.dumps(agent.policy_dump(), ensure_ascii=False, indent=2))
            continue
        if goal.lower() == "audit":
            _print_audit(agent, n=20, verify=True)
            continue
        result = agent.run(goal)
        print(result.render())
        print()
    agent.shutdown()
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    agent = make_agent(args)
    for spec in agent.tools():
        props = (spec.get("parameters") or {}).get("properties", {}) or {}
        print(f"{spec['name']:<14} {spec['risk']:<11} {spec['description']}")
        if props:
            print(f"{'':<14} args: {', '.join(props)}")
    return 0


def cmd_policy(args: argparse.Namespace) -> int:
    if args.policy:
        policy = StaticPolicy.load(args.policy)
    else:
        policy = StaticPolicy.default()
    print(json.dumps(policy.to_dict(), ensure_ascii=False, indent=2))
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    path = args.audit or AuditLog.default_path()
    if not Path(path).exists():
        print(f"aucun journal : {path}", file=sys.stderr)
        return 1
    log = AuditLog(path)
    ok = log.verify()
    if args.verify:
        print("intègre ✓" if ok else "ALTÉRÉ ✗", file=sys.stderr)
        return 0 if ok else 2
    for rec in log.records()[-args.n:]:
        print(
            f"{rec['seq']:>4} {rec['ts']} {rec['event']:<14} "
            f"{rec.get('action', ''):<16} {rec.get('outcome', ''):<14} "
            f"{rec.get('approved_by', '')}"
        )
    return 0 if ok else 2


def _print_audit(agent: Agent, n: int, verify: bool) -> None:
    recs = agent.audit.records()[-n:]
    for rec in recs:
        print(f"  {rec['seq']:>4} {rec['event']:<14} {rec.get('action', ''):<16} "
              f"{rec.get('outcome', '')}")
    print(f"  chaîne : {'intègre' if agent.audit_verify() else 'ALTÉRÉ'}")


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    agent = make_agent(args)
    socket_path = args.socket

    def _ready(path: str) -> None:
        print(f"aiOS agent en écoute sur {path}", file=sys.stderr)
        print(f"  cerveau={agent.brain.name}  jail={args.jail or _default_jail()}",
              file=sys.stderr)

    try:
        serve(agent, socket_path, ready=_ready)
    except KeyboardInterrupt:
        print("\narrêt demandé", file=sys.stderr)
    finally:
        agent.shutdown()
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    from .ui import UIConfirmer, UIServer

    interactive = not (args.trust or args.no_confirm)
    confirmer = UIConfirmer()
    agent = make_agent(args, confirmer=confirmer if interactive else _confirmer(args))
    try:
        server = UIServer(agent, host=args.host, port=args.port,
                          confirmer=confirmer)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        agent.shutdown()
        return 2

    url = server.url
    print(f"aiOS console : {url}", file=sys.stderr)
    print(f"  cerveau={agent.brain.name}  jail={args.jail or _default_jail()}",
          file=sys.stderr)
    if not interactive:
        print("  ⚠ --trust/--no-confirm : aucune confirmation ne sera demandée",
              file=sys.stderr)
    if not args.no_browser:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:  # pragma: no cover - environnement sans navigateur
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\narrêt demandé", file=sys.stderr)
    finally:
        server.shutdown()
        agent.shutdown()
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    print(BANNER.format(version=__version__))
    print(f"  python   : {sys.version.split()[0]}")
    print(f"  jail     : {args.jail or _default_jail()}")
    print(f"  audit    : {args.audit or AuditLog.default_path()}")
    client = detect_local_client(args.llm_backend, args.llm_model)
    if client:
        print(f"  modèle   : {client.describe()}  (local ✓)")
    else:
        print("  modèle   : aucun serveur local — repli sur le cerveau heuristique")
        print("             → `ollama serve && ollama pull llama3.2`")
    print(f"  politique: {args.policy or '(défaut, moindre privilège)'}")
    return 0


_COMMANDS = {
    "run": cmd_run,
    "chat": cmd_chat,
    "tools": cmd_tools,
    "policy": cmd_policy,
    "audit": cmd_audit,
    "serve": cmd_serve,
    "ui": cmd_ui,
    "doctor": cmd_doctor,
}


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        args = parser.parse_args((argv or sys.argv[1:]) + ["chat"])
    handler = _COMMANDS.get(args.cmd)
    if handler is None:  # pragma: no cover
        parser.print_help()
        return 2
    try:
        return handler(args)
    except KeyboardInterrupt:  # pragma: no cover
        print("\ninterrompu", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
