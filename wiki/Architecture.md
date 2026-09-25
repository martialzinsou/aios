# Architecture

## 1. Vue par couches

```
┌───────────────────────────────────────────────────────────────────────┐
│  Session utilisateur                                                  │
│  aios run │ chat │ serve │ tools │ policy │ audit │ doctor            │
│                              │ cli.py                                 │
├──────────────────────────────┼────────────────────────────────────────┤
│  Orchestrage                core/                                    │
│  Agent ── AgentLoop ── Memory ── Decision (Brain)                    │
│                              │                                        │
├──────────────────────────────┼────────────────────────────────────────┤
│  Sécurité                    security/                               │
│  PermissionManager ── StaticPolicy ── Confirmer ── Sandbox ── Audit   │
│                              │                                        │
├──────────────────────────────┼────────────────────────────────────────┤
│  Exécution                   tools/                                  │
│  ToolRegistry ── filesystem │ shell │ system │ network               │
│                              │                                        │
├──────────────────────────────┼────────────────────────────────────────┤
│  Modèle                      llm/                                    │
│  OllamaClient │ LLamaCppClient │ prompts (protocole JSON)            │
└──────────────────────────────┴────────────────────────────────────────┘
```

Chaque flèche descendante est une **validation**, jamais un contournement :
`Brain → Loop → Registry → Permissions → Sandbox → Audit`.

## 2. Les modules

| Module | Rôle | Note clé |
|---|---|---|
| `core/agent.py` | `Agent`, `AgentConfig` : câble tout le reste | une seule classe d'entrée |
| `core/loop.py` | `AgentLoop` : la boucle bornée | budget étapes **et** temps (I12) |
| `core/planner.py` | `Decision`, `LLMBrain`, `HeuristicBrain` | protocole JSON strict |
| `core/memory.py` | `WorkingMemory` (fenêtre) + `EpisodicMemory` (durée) | mémoire = donnée, jamais confiance |
| `security/policy.py` | `Risk`, `Decision`, `Rule`, `StaticPolicy` | première correspondance gagne |
| `security/permission.py` | `PermissionManager`, `Grant`, `Authorization` | le point de passage obligé |
| `security/confirmation.py` | `Confirmer` : CLI, callback, `AlwaysAllow`, `AlwaysDeny` | `CONFIRM` ≠ `ALLOW` |
| `security/sandbox.py` | `Sandbox`, `CommandSpec`, `classify_command` | jail, rlimits, env scrub |
| `security/audit.py` | `AuditLog` chaîné SHA-256 | `verify()` détecte toute altération |
| `security/redaction.py` | `redact` / `redact_object` | masquage récursif des secrets |
| `tools/base.py` | `Tool`, `ToolContext`, `ToolResult` | risque statique, plancher incompressible |
| `tools/registry.py` | `ToolRegistry` | **seul** point d'exécution (I1) |
| `llm/local.py` | `OllamaClient`, `LLamaCppClient` | loopback uniquement |
| `llm/prompts.py` | consigne système + schéma de décision | le modèle ne renvoie qu'un objet JSON |
| `server.py` | service `AF_UNIX` `0600` (NDJSON) | voir I11 |
| `cli.py` | les 7 sous-commandes | `run chat tools policy doctor serve audit` |

## 3. Flux d'un appel

```
utilisateur          cli/serve        Agent         Loop         Registry      Permission    Sandbox      Audit
    │                   │              │             │              │             │            │            │
    │  aios run "…"     │              │             │              │             │            │            │
    ├──────────────────►│  Agent.run() │             │              │             │            │            │
    │                   ├─────────────►│             │              │             │            │            │
    │                   │              │ Brain.decide│              │             │            │            │
    │                   │              ├────────────►│              │             │            │            │
    │                   │              │             │ Registry.call│             │            │            │
    │                   │              │             ├─────────────►│ authorize() │            │            │
    │                   │              │             │              ├────────────►│            │            │
    │                   │              │             │              │             │ append()   │            │
    │                   │              │             │              │             ├───────────────────────────►│
    │                   │              │             │              │  CONFIRM ?  │            │            │
    │◄───────────────────────────────────────────────────────────────────────────┤ (humain)   │            │
    │  « approuver ? [y/N] »           │             │              │             │            │            │
    ├───────────────────────────────────────────────────────────────────────────►│            │            │
    │                   │              │             │              │  ALLOW      │ resolve()  │            │
    │                   │              │             │              ├─────────────►│───────────►│            │
    │                   │              │             │              │  ToolResult │            │ append()   │
    │                   │              │             │              │             │            ├───────────►│
    │                   │              │  Decision   │              │             │            │            │
    │                   │              │◄────────────┤              │             │            │            │
    │◄──────────────────┤  réponse     │             │              │             │            │            │
```

Points d'arrêt :

- un **`DENY`** est retourné **avant** d'interroger l'humain (I3) ;
- un **`CONFIRM`** ne peut être résolu que par un `Confirmer` (I2) ;
- **tout** appel et **toute** autorisation produit un enregistrement (I9).

## 4. Les deux cerveaux

| | `LLMBrain` | `HeuristicBrain` |
|---|---|---|
| Transport | HTTP loopback : Ollama `127.0.0.1:11434`, llama.cpp `127.0.0.1:8080` | aucun |
| Sortie | objet JSON conforme au schéma | déterministe, règles écrites |
| Réseau | **jamais** hors de la machine | aucun |
| Usage | production | tests, CI, démonstration, hors-ligne |

`--brain auto` (défaut) probe la loopback puis choisit. `--brain llm` force le
modèle, `--brain heuristic` force le repli.

### Protocole de décision

Le modèle ne renvoie qu'un objet JSON :

```json
{"thought": "je dois lire les notes",
 "action": "tool",
 "tool": "read_file",
 "args": {"path": "/home/chronos/user/projet/notes.md"}}
```

`action` ∈ `tool` | `answer` | `wait`. Toute réponse malformée retombe sur une
réponse texte — elle ne déclenche **aucune** action.

## 5. Budget de la boucle

| Paramètre | Défaut | Rôle |
|---|---|---|
| `max_steps` | 12 | nombre maximal de décisions par objectif |
| `timeout` | 30 s | temps total de la session |

Atteindre une limite produit un statut explicite (`budget_exceeded`) plutôt
qu'une boucle infinie : c'est l'invariant **I12**.

## 6. Frontières de confiance

```
   NON FIAble                          DE CONFIANCE (TCB)
┌─────────────────────────┐        ┌──────────────────────────────┐
│ · modèle local           │        │ · ToolRegistry                │
│ · texte de l'utilisateur │  Decision│ · PermissionManager         │
│ · fichiers lus           ├───────►│ · Sandbox                     │
│ · pages web              │ {tool, │ · AuditLog                    │
│ · sorties de commandes   │  args} │ · code des outils             │
└─────────────────────────┘        └──────────────┬───────────────┘
                                                   │ syscall
                                                   ▼
                                            système
```

**Règle centrale :** le cerveau n'est *jamais* de confiance.

## 7. Positionnement dans l'image

```
aiOS image (Chromium OS + chromeos-base/aios-agent)
│
├── /usr/bin/aios                      launcher → python3 -m aios_agent.cli
├── /usr/lib/aios/aios_agent/          le moteur (PYTHONPATH)
├── /usr/share/aios/aios-policy.json   politique déclarative (admin)
├── /etc/init/aios-agent.conf          job upstart, --no-confirm
└── /var/lib/aios/                     audit + mémoire épisodique
                 │
                 ▼  AF_UNIX 0600
          /run/aios/agent.sock  ◄──  session Ash / aios chat
```

Détails : [Construction de l'OS](Construction-de-l-OS).

---

> **Martial Zinsou** · BSD-3-Clause · 2026
