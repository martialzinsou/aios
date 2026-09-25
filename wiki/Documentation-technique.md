# Documentation technique

Référence d'implémentation : modules, formats de données, protocoles et
mécanismes. Pour les usages, voir [Documentation fonctionnelle](Documentation-fonctionnelle).

---

## 1. Arborescence

```
agent/
├── src/aios_agent/
│   ├── __init__.py
│   ├── errors.py             PolicyDenied, ConfirmationRejected, ToolNotFound…
│   ├── core/
│   │   ├── agent.py          Agent, AgentConfig
│   │   ├── loop.py           AgentLoop — boucle bornée
│   │   ├── planner.py        Decision, LLMBrain, HeuristicBrain
│   │   └── memory.py         WorkingMemory, EpisodicMemory
│   ├── security/
│   │   ├── policy.py         Risk, Decision, Rule, StaticPolicy
│   │   ├── permission.py     PermissionManager, Grant, Authorization
│   │   ├── confirmation.py   Confirmer, ConfirmationRequest, Always*
│   │   ├── sandbox.py        Sandbox, CommandSpec, classify_command
│   │   ├── audit.py          AuditLog (JSONL chaîné SHA-256)
│   │   └── redaction.py      redact, redact_object
│   ├── tools/
│   │   ├── base.py           Tool, ToolContext, ToolResult
│   │   ├── registry.py       ToolRegistry
│   │   ├── filesystem.py     read_file, write_file, list_dir, search, delete_path
│   │   ├── shell.py          run_command
│   │   ├── system.py         system_info, list_processes
│   │   └── network.py        http_get
│   ├── llm/
│   │   ├── local.py          OllamaClient, LLamaCppClient (loopback)
│   │   └── prompts.py        consigne système + schéma de décision
│   ├── ui/
│   │   ├── server.py         UIServer (HTTP loopback + jeton) + UIConfirmer
│   │   └── assets/           bureau en verre liquide (index.html, ui.css, ui.js)
│   ├── request.py            client NDJSON du service (aios-request)
│   ├── server.py             AgentServer (AF_UNIX 0600)
│   └── cli.py                8 sous-commandes
└── tests/                    130 tests
```

Zéro dépendance au runtime : Python ≥ 3.9 **standard library uniquement**.
`pytest` et `pillow` ne sont requis que pour les tests et la génération des
captures.

---

## 2. Le cœur : `Agent` et `AgentLoop`

```python
config = AgentConfig(
    jail_roots=["/home/chronos/user"],
    brain="auto",              # auto | llm | heuristic
    max_steps=8,
    max_seconds=120.0,
    audit_path="…/audit.jsonl",
    episodes_path="…/episodes.json",
)
agent = Agent(config, confirmer=CLIConfirmer())   # ou AlwaysDeny / callback
result = agent.run("liste le dossier /home/chronos/user")
```

`AgentResult` : `status`, `answer`, `brain`, `session_id`, `duration`,
`blocked_calls`, `steps`.

Chaque `StepResult` : `index`, `action` (`tool` | `answer` | `wait`),
`thought`, `tool`, `args`, `output`, `ok`, `blocked`, `duration`.

### Boucle

```
for i in 1..max_steps:
    if elapsed > max_seconds:      → status = budget_exceeded   (I12)
    decision = brain.decide(goal, memory, tools)
    if decision.action != "tool":   → answer, fin
    result = registry.call(decision.tool, decision.args, ctx)
    memory.append(result)           → le résultat devient de la donnée
```

**Le cerveau ne fait qu'observer et proposer.** Il n'a aucune référence vers
le filesystem, le réseau ou les processus.

---

## 3. `ToolRegistry` : le seul point d'exécution (I1)

```python
@dataclass
class ToolContext:
    sandbox: Sandbox
    permissions: PermissionManager
    state_dir: Optional[str] = None
    session_id: str = ""
```

Séquence de `ToolRegistry.call(name, args, ctx)` :

1. résolution de l'outil (`ToolNotFound` si inconnu) ;
2. validation des arguments (`ValueError` sur type manquant) ;
3. `permissions.authorize(action, target, risk)` → `ALLOW` / `CONFIRM` / `DENY` ;
4. `tool.run(args, ctx)` — le chemin passe par `ctx.sandbox.resolve()` ;
5. `audit.append("tool_call", …)` avec `outcome` et `detail` masqués.

```python
@dataclass
class ToolResult:
    ok: bool
    output: str
    data: Any = None
    error: str = ""
    risk: Risk = Risk.READ
```

### Plancher de risque (I5)

```python
def risk_for(self, args) -> Risk:
    return max(self.risk, _risk_from(args))   # jamais de descente
```

`_risk_from(args)` élève selon le contenu : `rm -rf` → `destructive`,
`sudo` → `privileged`, `curl|wget|ssh` → `network`.

---

## 4. Politique déclarative

### Types

```python
class Risk(str, Enum):      # ordonnés, du plus inoffensif au moins
    READ, WRITE, NETWORK, EXECUTE, DESTRUCTIVE, PRIVILEGED

class Decision(str, Enum):
    ALLOW, CONFIRM, DENY

@dataclass(frozen=True)
class Rule:
    id: str
    decision: Decision
    action: str = "*"          # glob fnmatch
    risk: Optional[Risk] = None
    target: str = "*"          # glob fnmatch (chemin, hôte, ligne…)
    reason: str = ""
```

### Parcours

`StaticPolicy.decide(action, risk, target)` :

1. parcourt les règles **dans l'ordre** ;
2. **première correspondance gagne** ;
3. sinon `DEFAULT_DECISIONS[risk]` :

```json
{"read": "allow", "write": "confirm", "network": "confirm",
 "execute": "confirm", "destructive": "confirm", "privileged": "deny"}
```

### Format du fichier

`os/overlay/…/files/aios-policy.json` est un **détachage exact** de
`StaticPolicy.default()` ; `make check-policy` échoue s'ils divergent.

```json
{
  "defaults": {"read": "allow", "write": "confirm", "network": "confirm",
               "execute": "confirm", "destructive": "confirm",
               "privileged": "deny"},
  "rules": [
    {"id": "deny-privileged", "decision": "deny",
     "action": "*", "target": "*", "risk": "privileged",
     "reason": "The agent never runs with elevated privileges."},
    {"id": "deny-shadow", "decision": "deny",
     "action": "*", "target": "/etc/shadow*", "reason": "…"}
  ]
}
```

```bash
make policy         # régénère le fichier depuis le code
make check-policy   # le vérifie (inclus dans make check)
```

---

## 5. Permissions, grants et confirmation

```python
@dataclass(frozen=True)
class Grant:
    id: str
    action: str
    target: str
    approved_by: str
    created_at: float
    ttl: float
    one_time: bool = False
    used: bool = False
```

Parcours de `PermissionManager.authorize(action, target, risk)` :

| Étape | Test | Invariant |
|---|---|---|
| 1 | un `Grant` vivant correspond à `(action, target)` exact ? | — |
| 2 | `StaticPolicy.decide(...)` | — |
| 3 | `DENY` → **retour immédiat**, sans appel au `Confirmer` | **I3** |
| 4 | `CONFIRM` → `Confirmer.ask(request)` — seul l'humain tranche | **I2** |
| 5 | `ALLOW` → accord | — |
| 6 | `audit.append("authorize", …)` dans tous les cas | **I9** |

```python
@dataclass
class Authorization:
    allowed: bool
    verdict: Decision
    rule_id: str = ""
    reason: str = ""
    approved_by: str = ""
    grant_id: str = ""

    def raise_if_denied(self) -> None: ...
        # Decision.DENY    → PolicyDenied
        # sinon décliné    → ConfirmationRejected
```

Les `Grant` sont révocables (`revoke_all`) et expirent par
`time.monotonic()`. Les clés de comparaison sont `(action, target)` **exactes** :
un grant ne peut pas s'étendre à une cible voisine.

### Implémentations de `Confirmer`

| Classe | Usage |
|---|---|
| `CLIConfirmer` | défaut interactif — pose la question sur stderr |
| `CallbackConfirmer` | branchement D-Bus / GUI (roadmap) |
| `AlwaysDeny` | `--no-confirm` : fail-closed |
| `AlwaysAllow` | `--trust` : **tests uniquement** |

---

## 6. Sandbox

### Jail de chemins (I6)

```python
def resolve(self, path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = self.root / candidate
    resolved = candidate.resolve()          # symlinks suivies
    if not self.contains(resolved):
        raise SandboxViolation(f"path … is outside the sandbox roots …")
    return resolved
```

`resolve()` est appelée **avant** toute lecture/écriture. `contains()` compare
le chemin résolu aux racines, en tenant compte de `/private/…` (macOS) et de
`/System/Volumes/Data/…`.

### Exécution bornée (I7)

`Sandbox.run(CommandSpec)` :

| Garde-fou | Détail |
|---|---|
| `shell=False` | pas d'interprétation shell, argv explicite |
| classement | `classify_command(argv)` → risque effectif |
| binaires réseau | `curl`/`wget`/`ssh` refusés si `network` désactivé |
| `cwd` | doit être dans la jail |
| `preexec_fn` | `setrlimit(RLIMIT_CPU/AS/FSIZE/NPROC/NOFILE)` |
| environnement | `_scrubbed_env()` : retire `*TOKEN*`, `*KEY*`, `*SECRET*`, `*PASSWORD*` |
| timeout | SIGTERM puis SIGKILL du groupe de processus |
| sortie | plafonnée (4 000 caractères par défaut) |

```python
def classify_command(argv) -> Risk:
    # sudo|su|doas|pkexec      → privileged
    # rm -rf|dd|mkfs|shutdown   → destructive
    # curl|wget|ssh|nc          → network
    # sinon                     → execute
```

---

## 7. Journal d'audit chaîné (I9, I10)

Format : **JSONL**, un objet canonique par ligne
(`json.dumps(..., sort_keys=True, separators=(",", ":"))`).

| Champ | Contenu |
|---|---|
| `seq` | numéro de séquence, doit valoir l'indice dans le fichier |
| `ts` | horodatage UTC ISO-8601 (millisecondes) |
| `event` | `session_start` · `authorize` · `tool_call` · `session_end` · `session_stop` |
| `action`, `target` | ce qui a été demandé |
| `actor` | `agent` par défaut |
| `verdict` | `allow` / `confirm` / `deny` |
| `approved_by` | `human`, `grant:<id>`, `trust`, … |
| `outcome` | `ok`, `blocked`, `sandbox_blocked`, `declined`, … |
| `detail` | objet **masqué** par `redact_object` |
| `prev` | hachage de l'enregistrement précédent (génésis : 64 zéros) |
| `hash` | `sha256(canonical(record sans "hash"))` |

Écriture : `os.open(..., O_WRONLY|O_APPEND|O_CREAT, 0o600)` — append-only,
fichier `0600`.

```python
def verify(self) -> bool:
    # recalcule toute la chaîne : prev, seq et hash doivent tous correspondre
```

```bash
aios audit --verify     # → "intègre ✓" (0) ou "ALTÉRÉ ✗" (2)
```

> Le journal **détecte** la falsification, il ne l'empêche pas : un attaquant
> *root* peut réécrire le fichier entier et rendre ainsi l'altération visible
> a posteriori. Voir [Modèle de sécurité](Modele-de-securite) §4.

---

## 8. Masquage des secrets (I8)

`security/redaction.py` :

- `redact(str)` applique une liste de motifs (tokens, clés AWS,
  `Bearer …`, clés privées PEM, mots de passe dans une URL…) et remplace par
  `***`;
- `redact_object(obj)` parcourt récursivement les dict/listes et masque toute
  clé faisant match sur `_SENSITIVE_KEY` (`TOKEN|KEY|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL`).

Points d'application : la mémoire de travail, l'objectif de session, le champ
`detail` de chaque enregistrement d'audit.

---

## 9. Les deux cerveaux

### `LLMBrain`

```python
OllamaClient(base_url="http://127.0.0.1:11434", model="llama3.2")
LLamaCppClient(base_url="http://127.0.0.1:8080", model="…")
```

- `http.client` vers la **loopback uniquement** ; toute autre hôte est rejeté
  avant d'ouvrir la connexion ;
- le modèle ne reçoit que : la consigne système, l'objectif, la fenêtre de
  mémoire de travail (déjà masquée) et les schémas d'outils ;
- la réponse doit être un objet JSON ; sinon on retombe sur `answer`.

### `HeuristicBrain`

Règles déterministes (regex sur l'objectif) → `Decision`. Aucun réseau, aucun
modèle. Sert de repli et de référence pour les tests.

```bash
--brain auto       # probe la loopback, sinon heuristic   (défaut)
--brain llm        # impose le modèle local
--brain heuristic  # impose le repli hors-ligne
```

---

## 10. Protocole du service

`server.py` — `socketserver.ThreadingUnixStreamServer`, flux `NDJSON` :

```
→ {"goal": "…", "session": "abc"}
← {"status": "answered", "answer": "…", "brain": "…", "session": "…",
   "duration": 0.004, "blocked": 0, "steps": [...]}

→ quit
← {"status": "bye"}
```

- `stream = 64 Ko` maximum par ligne ; au-delà, déconnexion ;
- JSON invalide ou `goal` vide → `{"status": "error"}` sans déconnexion ;
- plusieurs requêtes possibles sur la même connexion ;
- socket `0600`, dossier parent `0700` **si aiOS en est propriétaire**
  (I11) — un dossier système tel que `/tmp` n'est pas modifié ;
- `daemon_threads = True` : chaque connexion a son propre `Agent`.

```bash
aios-request --socket /run/aios/agent.sock --goal "infos système"
```

---

## 11. Exécution et état

| Élément | Emplacement |
|---|---|
| audit (par défaut) | `$AIOS_STATE_DIR/audit.jsonl` sinon `~/.local/share/aios/audit.jsonl` |
| mémoire épisodique | même répertoire, `episodes.json` |
| socket | `/run/aios/agent.sock` (`--socket`) |
| politique embarquée | `/usr/share/aios/aios-policy.json` |

Fichiers créés avec les permissions `0600`.

---

## 12. Tests

```bash
make test           # pytest
make lint           # compileall + bash -n sur os/scripts/*.sh
make check-policy   # code ↔ politique embarquée
make check          # les trois
```

| Fichier | Couvre |
|---|---|
| `test_agent.py` | boucle, budgets, brain, mémoire |
| `test_permissions.py` | verdicts, grants, I2/I3/I4 |
| `test_policy.py` | règles, premières correspondances, défauts |
| `test_sandbox.py` | jail, symlinks, rlimits, env (I6/I7) |
| `test_audit.py` | chaînage, altération, format (I9/I10) |
| `test_redaction.py` | masquage récursif (I8) |
| `test_tools.py` | chaque outil, risque effectif |
| `test_server.py` | socket, protocole, permissions (I11) |
| `test_request.py` | client `aios-request` |
| `test_cli.py` | les 7 sous-commandes |

Invariant de CI : **`make check` doit être vert** avant tout commit.

---

## 13. Générer les captures du wiki

```bash
python3 tools/make_screenshots.py      # fenêtres de terminal
python3 tools/make_ui_screenshots.py   # bureau en verre liquide (Chrome headless)
```

Le script :

1. construit une démo dans des chemins courts (`/tmp/aios-demo`) ;
2. exécute les commandes réelles avec `.venv/bin/aios` ;
3. **anonymise** la sortie : nom d'utilisateur, hostname, `$HOME`, chemins de
   démonstration → identifiants neutres (`aios`, `aios-devbox`,
   `/home/chronos/user/…`) ; les emoji non couverts par Menlo sont remplacés
   par des glyphes sûrs ;
4. **échoue** si un glyphe manquant ou une fuite d'identité subsiste
   (`assert_no_missing`, `assert_anonymous`) ;
5. rend une fenêtre terminal (Menlo, coins arrondis, ombre) et écrit
   `wiki/captures/*.png`.

`make_ui_screenshots.py` suit la même discipline pour l'interface : démo
identique, anonymisation vérifiée sur l'état JSON affiché, puis capture du
rendu réel de Chrome en mode headless (`wiki/captures/ui-0*.png`).

Aucune capture n'est modifiée à la main.

---

> **Martial Zinsou** · BSD-3-Clause · 2026
