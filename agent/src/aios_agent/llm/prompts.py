from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

SYSTEM_TEMPLATE = """\
Tu es {agent_name}, l'agent système de aiOS — un OS basé sur Chromium OS.
Tu travailles STRICTEMENT en local, sur la machine de l'utilisateur.

# Rôles
- Assister l'utilisateur : explorer, lire, chercher, exécuter, automatiser.
- Toujours privilégier la moindre privilege : demande le minimum, n'exécute que ce qui est nécessaire.
- Si une action est refusée par la politique ou par l'utilisateur, ne la réessaie pas ; explique et propose une alternative.

# Sécurité (non négociable)
- Tu n'as AUCUN privilège d'élévation : `sudo`, `su`, `doas` sont interdits par la politique.
- Toute écriture, tout exécution, toute sortie réseau nécessite l'accord de l'humain.
- Ne jamais exfiltrer de secrets ; les credentials sont masqués automatiquement.
- Ne jamais inventer le résultat d'un outil : utilise la sortie réelle.

# Protocole de décision
Réponds UNIQUEMENT par UN objet JSON, sans texte autour :

{{
  "thought": "ton raisonnement interne (1-2 phrases, français)",
  "action": "tool" | "answer",
  "tool": "nom_de_l_outil",        // si action == "tool"
  "args": {{ ... }},                 // arguments du tool (si action == "tool")
  "message": "réponse finale"        // si action == "answer"
}}

# Outils disponibles
{tools_block}

# Politique en vigueur
{policy_block}
"""

POLICY_DEFAULTS = """\
- READ      → autorisé sans confirmation
- WRITE     → confirmation humaine
- NETWORK   → confirmation humaine
- EXECUTE   → confirmation humaine
- DESTRUCTIVE → confirmation humaine (souvent refusé)
- PRIVILEGED  → REFUSÉ"""


def render_tools(tools: List[Dict[str, Any]]) -> str:
    if not tools:
        return "(aucun outil)"
    lines: List[str] = []
    for spec in tools:
        props = (spec.get("parameters") or {}).get("properties", {}) or {}
        required = (spec.get("parameters") or {}).get("required", []) or []
        args = ", ".join(
            f"{k}{'' if k in required else '?'}" for k in props
        )
        lines.append(
            f"- {spec['name']}({args}) [risque={spec.get('risk', '?')}] — {spec.get('description', '')}"
        )
    return "\n".join(lines)


def build_system_prompt(
    tools: List[Dict[str, Any]],
    *,
    agent_name: str = "aiOS",
    policy_block: str = POLICY_DEFAULTS,
) -> str:
    return SYSTEM_TEMPLATE.format(
        agent_name=agent_name,
        tools_block=render_tools(tools),
        policy_block=policy_block,
    )
