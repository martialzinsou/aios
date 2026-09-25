# aiOS

**Un OS basé sur Chromium OS, augmenté d'un assistant agentique 100 % local.**

aiOS assemble deux choses :

1. **La couche OS** — le code source de Chromium OS, greffé d'un paquet
   `chromeos-base/aios-agent`, d'une politique de sécurité déclarative et d'un
   service upstart.
2. **Le moteur agentique** — un agent autonome (boucle observe → décide → agit)
   qui pilote des outils système **au nom de l'utilisateur**, sous une politique
   de *moindre privilège* où **chaque action non-lecture exige un humain**.

```
┌──────────────────────────────────────────────────────────────────┐
│  aiOS image (Chromium OS + chromeos-base/aios-agent)            │
│                                                                  │
│  /usr/bin/aios ──► /etc/init/aios-agent.conf                    │
│                        │  AF_UNIX 0600 · /run/aios/agent.sock   │
│                        ▼                                         │
│              ┌───────────────────────┐                           │
│              │   AgentLoop (budget)  │                           │
│              └───────────┬───────────┘                           │
│        decide            │            agit                       │
│   ┌──────────────┐       │       ┌────────────────────┐          │
│   │ Brain        │◄──────┘       │ ToolRegistry.call  │          │
│   │ · LLMBrain   │               └─────────┬──────────┘          │
│   │   (Ollama /  │                         │                     │
│   │  llama.cpp)  │               ┌─────────▼──────────┐          │
│   │ · Heuristic  │               │ PermissionManager  │          │
│   │   (offline)  │               │ policy → grants →  │          │
│   └──────────────┘               │ confirmation hum.  │          │
│                                  └─────────┬──────────┘          │
│                                 ┌──────────▼──────────┐          │
│                                 │ Sandbox (path jail, │          │
│                                 │ rlimits, timeout,   │          │
│                                 │ env scrub)          │          │
│                                 └──────────┬──────────┘          │
│                                 ┌──────────▼──────────┐          │
│                                 │ AuditLog haché      │          │
│                                 │ (JSONL, chaîné)     │          │
│                                 └────────────────────┘          │
└──────────────────────────────────────────────────────────────────┘
```

---

## Démarrage rapide (moteur seul, sans build OS)

```bash
make deps            # venv + pytest
make test            # 114 tests
make agent-doctor    # détection du modèle local
make agent-chat      # session interactive
```

Exemples de commandes reconnues (cerveau heuristique, sans modèle) :

```bash
PYTHONPATH=agent/src python3 -m aios_agent doctor
PYTHONPATH=agent/src python3 -m aios_agent run  "infos système"
PYTHONPATH=agent/src python3 -m aios_agent run  "lis le fichier ~/notes.md"
PYTHONPATH=agent/src python3 -m aios_agent chat
```

Avec un modèle local (Ollama) :

```bash
ollama serve &  &&  ollama pull llama3.2
PYTHONPATH=agent/src python3 -m aios_agent doctor   # → modèle local ✓
PYTHONPATH=agent/src python3 -m aios_agent run --brain llm "trouve les TODO dans mon projet"
```

---

## Modèle de sécurité

Le socle est **le refus par défaut**. Six mécanismes cumulatifs :

| Mécanisme | Fichier | Rôle |
|---|---|---|
| **Jail de chemins** | `security/sandbox.py` | Aucun accès hors des racines autorisées, y compris via symlink. |
| **Politique déclarative** | `security/policy.py` | Règles ordonnées `première correspondance gagne`, `ALLOW / CONFIRM / DENY`. |
| **Confirmation humaine** | `security/confirmation.py` | Un `CONFIRM` ne peut être résolu que par l'opérateur — jamais par l'agent. |
| **Journal d'audit chaîné** | `security/audit.py` | JSONL chaîné en SHA-256 ; toute altération est détectable (`aios audit --verify`). |
| **Masquage de secrets** | `security/redaction.py` | Aucun credential ne part dans un prompt ni dans un log. |
| **Exécution bornée** | `security/sandbox.py` | `shell=False`, timeout, `RLIMIT_*`, environnement scrubbé, sortie plafonnée. |

### Niveaux de risque et comportement par défaut

| Risque | Exemple | Décision |
|---|---|---|
| `read` | `read_file`, `list_dir`, `search` | **ALLOW** (la jail s'applique quand même) |
| `write` | `write_file` | **CONFIRM** |
| `network` | `http_get` | **CONFIRM** |
| `execute` | `run_command` | **CONFIRM** |
| `destructive` | `rm -rf`, `dd`, `mkfs` | **CONFIRM** (souvent refusé) |
| `privileged` | `sudo`, `su`, `doas` | **DENY** — non négociable |

Le niveau de risque est **déclaré statiquement par l'outil** et ne peut être
abaissé au moment de l'appel ; un outil ne peut que l'**élever** (`rm -rf` part
de `execute` pour atteindre `destructive`).

> **Les outils sont du code de confiance (TCB).** La politique gouverne les
> *appels* d'outils ; il revient à chaque outil de décrire honnêtement son risque
> et de passer par `ctx.sandbox` / `ctx.jail()`.

### Exemples de refus

```bash
# sudo : refusé AVANT d'interroger l'humain
$ aios run "exécute sudo rm -rf /"
⛔ policy denied run_command: The agent never runs with elevated privileges.

# écriture en mode headless : refusée
$ aios run --no-confirm "crée un fichier dans /tmp/x"
⛔ permission refused for write_file: the operator declined this action

# lecture hors jail : bloquée par le sandbox
$ aios run --jail ~/projets "lis le fichier /etc/passwd"
⛔ sandbox: path /etc/passwd is outside the sandbox roots (...)
```

---

## Architecture du moteur

```
agent/
├── src/aios_agent/
│   ├── core/
│   │   ├── agent.py      Agent, AgentConfig — câblage de tout le reste
│   │   ├── loop.py       AgentLoop — boucle bornée (étapes + temps)
│   │   ├── planner.py    Decision, LLMBrain, HeuristicBrain, protocole JSON
│   │   └── memory.py     WorkingMemory (fenêtre) + EpisodicMemory (durée)
│   ├── security/
│   │   ├── policy.py       Risk, Decision, Rule, StaticPolicy
│   │   ├── permission.py   PermissionManager, Grant, Authorization
│   │   ├── confirmation.py Confirmer (CLI / callback / Always*)
│   │   ├── sandbox.py      Sandbox, CommandSpec, classify_command
│   │   ├── audit.py        AuditLog chaîné en SHA-256
│   │   └── redaction.py    redact / redact_object
│   ├── tools/
│   │   ├── base.py       Tool, ToolContext, ToolResult
│   │   ├── registry.py   ToolRegistry — le seul point d'exécution
│   │   ├── filesystem.py read_file, write_file, list_dir, search, delete_path
│   │   ├── shell.py      run_command
│   │   ├── system.py     system_info, list_processes
│   │   └── network.py    http_get
│   ├── llm/
│   │   ├── local.py      OllamaClient, LLamaCppClient (loopback uniquement)
│   │   └── prompts.py    protocole JSON + règles système
│   ├── server.py       service AF_UNIX 0600 (NDJSON)
│   ├── request.py      aios-request — client du socket
│   └── cli.py          aios run | chat | serve | tools | policy | audit | doctor
└── tests/              114 tests
```

### Les deux cerveaux

* **`LLMBrain`** — pilote un modèle **local** (Ollama `127.0.0.1:11434` ou
  serveur OpenAI-compatible de llama.cpp `127.0.0.1:8080`). Ne parle jamais à
  l'extérieur de la machine.
* **`HeuristicBrain`** — repli déterministe, sans dépendance. Il rend l'agent
  utilisable (et les tests reproductibles) même sans modèle chargé.

`--brain auto` (défaut) probe la loopback et choisit.

### Protocole de décision

Le modèle ne renvoie qu'un objet JSON :

```json
{"thought": "…", "action": "tool", "tool": "read_file",
 "args": {"path": "/home/user/notes.md"}}
```

---

## Construire l'OS

> **Prérequis : Linux uniquement.** Chromium OS ne se build ni sous macOS ni
> sous Windows. Il faut une VM Linux (Debian 12 / Ubuntu 22.04) ou le SDK
> Docker officiel, ~100 Go de disque et 16 Go de RAM.

```bash
os/scripts/00-check-host.sh      # vérifie l'hôte
os/scripts/10-fetch-source.sh    # repo init + sync (~40 Go)
os/scripts/20-prepare-overlay.sh # greffe chromeos-base/aios-agent
os/scripts/30-build-image.sh     # build du package puis de l'image
os/scripts/40-run-in-vm.sh       # boot de l'image dans KVM
```

Détails, variables d'environnement et dépannage : **[os/README.md](os/README.md)**.

---

## Structure du dépôt

```
aiOs/
├── agent/        le moteur agentique (Python, zéro dépendance au runtime)
├── os/
│   ├── manifest/                 local manifest `repo` (optionnel)
│   ├── overlay/chromeos-base/    ebuild + politique + job upstart
│   └── scripts/                  chaîne de build Chromium OS
├── docs/
│   └── SECURITY.md               modèle de menace et invariants
├── Makefile
└── README.md
```

Le modèle de menace complet (frontières de confiance, 12 invariants, limites
assumées) est dans **[docs/SECURITY.md](docs/SECURITY.md)**.

---

## Documentation

Le wiki complet est versionné dans **[wiki/](wiki/)** :

| | |
|---|---|
| [Vue d'ensemble](wiki/Vue-d-ensemble.md) | périmètre, arbitrages, risques |
| [Architecture](wiki/Architecture.md) | modules, flux, frontières de confiance |
| [Diagrammes UML](wiki/Diagrammes-UML.md) | cas d'utilisation, classes, séquence, activité, états, composants, déploiement |
| [Documentation fonctionnelle](wiki/Documentation-fonctionnelle.md) | CLI, outils, sessions, captures |
| [Documentation technique](wiki/Documentation-technique.md) | formats, protocoles, implémentation |
| [Modèle de sécurité](wiki/Modele-de-securite.md) | 12 invariants, atténuations, limites |
| [Construction de l'OS](wiki/Construction-de-l-OS.md) | chaîne de build Chromium OS |
| [Déploiement et exploitation](wiki/Deploiement-et-exploitation.md) | service, réglages, dépannage |
| [FAQ](wiki/FAQ.md) · [Contribuer](wiki/Contribuer.md) | aide et contribution |

Les captures d'écran sont générées, jamais saisies à la main :

```bash
python3 tools/make_screenshots.py     # → wiki/captures/*.png
```

Le script anonymise les sorties et échoue s'il détecte une fuite d'identité ou
un glyphe manquant.

## Roadmap

- [x] Noyau agentique (boucle, cerveaux, mémoire, outils)
- [x] Socle de sécurité (politique, permissions, sandbox, audit, redaction)
- [x] Service local AF_UNIX + CLI complète
- [x] Couche Chromium OS (ebuild, politique, upstart, scripts de build)
- [ ] Intégration session : Confirmer graphique via D-Bus dans Ash
- [ ] Outils supplémentaires : gestionnaire de paquets, réglages ChromeOS
- [ ] Profils de politique par rôle (enfant / admin / kiosque)

## Licence

BSD-3-Clause. Le code de Chromium OS reste sous sa propre licence.
