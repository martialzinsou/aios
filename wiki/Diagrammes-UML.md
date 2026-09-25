# Diagrammes UML

Tous les diagrammes sont en **Mermaid** : ils sont rendus par le wiki et restent
modifiables dans le dépôt. Les captures d'écran du terminal sont dans
[Documentation fonctionnelle](Documentation-fonctionnelle).

---

## 1. Cas d'utilisation

> Mermaid ne possède pas de type `usecaseDiagram` (c'est une syntaxe PlantUML) :
> le diagramme ci-dessous est donc exprimé en `flowchart`, avec les mêmes
> acteurs, cas et liens qu'un diagramme de cas d'utilisation UML.

```mermaid
flowchart TB
    op(["Opérateur"])
    ash(["Session Ash (UI)"])
    fs[("Système de fichiers")]
    net(("Réseau loopback"))

    subgraph aios["aiOS"]
      direction TB
      UC1["Lancer un objectif<br/>(aios run / chat)"]
      UC2["Confirmer une action<br/>non-lecture"]
      UC3["Consulter la politique<br/>(aios policy)"]
      UC4["Lire le journal<br/>(aios audit)"]
      UC5["Vérifier l'intégrité<br/>(aios audit --verify)"]
      UC6["Diagnostiquer<br/>(aios doctor)"]
      UC7["Servir la session<br/>(aios serve)"]
      UC8["Demander via le socket<br/>(aios-request)"]
      UC9["Exécuter une lecture"]
      UC10["Exécuter une écriture"]
      UC11["Interroger le modèle local"]
      UC12["Journaliser la décision"]
    end

    op --> UC1
    op --> UC2
    op --> UC3
    op --> UC4
    op --> UC5
    op --> UC6
    ash --> UC7
    ash --> UC8
    UC8 --> UC7
    UC1 --> UC9
    UC1 --> UC10
    UC1 --> UC11
    UC10 --> UC2
    UC9 --> fs
    UC10 --> fs
    UC11 --> net
    UC9 --> UC12
    UC10 --> UC12
    UC10 -->|si ALLOW| UC9
```

---

## 2. Diagramme de classes (sécurité)

```mermaid
classDiagram
    class Risk {
        <<enumeration>>
        READ
        WRITE
        NETWORK
        EXECUTE
        DESTRUCTIVE
        PRIVILEGED
        +rank() int
    }

    class Decision {
        <<enumeration>>
        ALLOW
        CONFIRM
        DENY
    }

    class Rule {
        +id str
        +decision Decision
        +action str
        +risk Risk
        +target str
        +reason str
        +matches() bool
    }

    class StaticPolicy {
        +rules list~Rule~
        +defaults dict
        +decide(action, risk, target) Decision
        +to_dict() dict
        +default() StaticPolicy
        +load(path) StaticPolicy
    }

    class PermissionManager {
        +grants dict
        +authorize(action, target, risk) Authorization
        +revoke_all() None
    }

    class Grant {
        +id str
        +action str
        +target str
        +approved_by str
        +ttl float
        +one_time bool
        +expired() bool
        +matches(action, target) bool
    }

    class Authorization {
        +allowed bool
        +verdict Decision
        +rule_id str
        +reason str
        +approved_by str
        +raise_if_denied() None
    }

    class Confirmer {
        <<interface>>
        +ask(request) str
    }

    class ConfirmationRequest {
        +action str
        +target str
        +risk Risk
        +why str
        +detail str
    }

    class Sandbox {
        +roots list~str~
        +resolve(path) Path
        +contains(path) bool
        +run(spec) CommandResult
    }

    class AuditLog {
        +records list
        +append(event, ...) dict
        +verify() bool
        +default_path() Path
    }

    Rule "0..*" --> "1" Risk : porte sur
    StaticPolicy "1" o-- "0..*" Rule : ordonnées
    StaticPolicy "1" --> "6" Decision : défauts
    PermissionManager "1" --> "1" StaticPolicy
    PermissionManager "1" o-- "0..*" Grant
    PermissionManager "1" --> "1" Confirmer : résout CONFIRM
    PermissionManager "1" --> "1" AuditLog : trace
    Confirmer "1" --> "1" ConfirmationRequest
    Authorization "1" --> "1" Decision
    Sandbox ..> PermissionManager : appelé après verdict
```

---

## 3. Diagramme de classes (moteur)

```mermaid
classDiagram
    class Agent {
        +config AgentConfig
        +run(goal) AgentResult
        +tools() list
        +policy_dump() dict
        +shutdown() None
    }

    class AgentConfig {
        +jail_roots list~str~
        +policy_path str
        +audit_path str
        +confirm bool
        +grant_ttl float
        +max_steps int
        +max_seconds float
        +brain str
        +llm_backend str
        +llm_model str
        +echo Callable
    }

    class AgentLoop {
        +max_steps int
        +max_seconds float
        +run(goal) AgentResult
    }

    class AgentResult {
        +goal str
        +status str
        +answer str
        +steps list~StepResult~
        +brain str
        +session_id str
        +duration float
        +blocked_calls() int
    }

    class StepResult {
        +index int
        +action str
        +thought str
        +tool str
        +args dict
        +output str
        +ok bool
        +blocked bool
        +duration float
    }

    class Brain {
        <<interface>>
        +decide(context) Decision
        +name str
    }

    class LLMBrain {
        +client LLMClient
        +decide(context) Decision
    }

    class HeuristicBrain {
        +decide(context) Decision
    }

    class Decision_ {
        +thought str
        +action str
        +tool str
        +args dict
    }

    class ToolRegistry {
        +tools dict
        +register(tool) ToolRegistry
        +call(name, args, ctx) ToolResult
    }

    class Tool {
        <<abstract>>
        +name str
        +description str
        +risk Risk
        +target(args) str
        +risk_for(args) Risk
        +run(args, ctx) ToolResult
    }

    Agent --> AgentConfig
    Agent --> AgentLoop
    Agent --> ToolRegistry
    Agent --> Brain
    AgentLoop --> AgentResult
    AgentLoop --> StepResult
    AgentLoop --> Brain : propose
    Brain <|.. LLMBrain
    Brain <|.. HeuristicBrain
    LLMBrain --> Decision_
    HeuristicBrain --> Decision_
    ToolRegistry --> Tool
    ToolRegistry --> ToolContext
    class ToolContext {
        +sandbox Sandbox
        +permissions PermissionManager
        +state_dir str
        +session_id str
        +jail(path) str
    }
```

---

## 4. Séquence — une écriture approuvée par l'humain

```mermaid
sequenceDiagram
    autonumber
    actor Op as Opérateur
    participant CLI as aios chat
    participant AG as Agent
    participant BR as Brain
    participant RG as ToolRegistry
    participant PM as PermissionManager
    participant CF as Confirmer
    participant SB as Sandbox
    participant AU as AuditLog

    Op->>CLI: crée un fichier …/sortie.txt
    CLI->>AG: run(goal)
    AG->>BR: decide(goal, mémoire)
    BR-->>AG: {action: tool, tool: write_file, args}
    AG->>RG: call(write_file, args, ctx)
    RG->>PM: authorize(write, cible, WRITE)
    PM->>PM: StaticPolicy.decide() → CONFIRM
    PM->>AU: append(authorize, verdict=confirm)
    PM->>CF: ask(ConfirmationRequest)
    CF->>Op: Approve? [y/N]
    Op-->>CF: y
    CF-->>PM: "human"
    PM->>PM: crée un Grant (TTL 300 s)
    PM-->>RG: Authorization(allowed=True)
    RG->>SB: resolve(chemin)
    SB-->>RG: chemin résolu dans la jail
    RG->>RG: write_file.run()
    RG->>AU: append(tool_call, outcome=ok)
    RG-->>AG: ToolResult(ok=True)
    AG-->>CLI: AgentResult(answered)
    CLI-->>Op: Voilà ce que j'ai obtenu : …
```

---

## 5. Séquence — `sudo` refusé avant toute question

```mermaid
sequenceDiagram
    autonumber
    actor Op as Opérateur
    participant CLI as aios run
    participant BR as Brain
    participant RG as ToolRegistry
    participant PM as PermissionManager
    participant CF as Confirmer
    participant AU as AuditLog

    Op->>CLI: exécute sudo rm -rf /
    CLI->>BR: decide(goal)
    BR-->>CLI: {tool: run_command, args: {command: sudo rm -rf /}}
    CLI->>RG: call(run_command, …)
    RG->>RG: risk_for(args) → PRIVILEGED
    RG->>PM: authorize(execute, "sudo …", PRIVILEGED)
    PM->>PM: règle deny-privileged → DENY
    PM->>AU: append(authorize, verdict=deny, outcome=blocked)
    Note over PM,CF: I3 — on n'interroge JAMAIS l'humain sur un DENY
    PM-->>RG: Authorization(allowed=False)
    RG->>AU: append(tool_call, outcome=policy_denied)
    RG-->>CLI: ToolResult(ok=False, error=policy denied …)
    CLI-->>Op: ⛔ policy denied run_command
```

---

## 6. Diagramme d'activité — boucle agentique

```mermaid
flowchart TD
    Start([Début : goal]) --> Mem[Charger la fenêtre de mémoire<br/>+ objectif épisodique]
    Mem --> Test{Étapes restantes<br/>et temps restant ?}
    Test -- non --> Budget([budget_exceeded])
    Test -- oui --> Decide[Brain : observer et décider]
    Decide --> Mal{Réponse conforme<br/>au schéma JSON ?}
    Mal -- non --> Answer0[Réponse texte : on n'exécute rien]
    Mal -- oui --> Act{action ?}
    Act -- answer --> Answer[Rendre la réponse]
    Act -- wait --> Test
    Act -- tool --> Call[ToolRegistry.call]
    Call --> Auth[PermissionManager.authorize]
    Auth --> Verdict{Verdict ?}
    Verdict -- DENY --> Refus[Refus immédiat<br/>+ audit]:::deny
    Verdict -- CONFIRM --> Humain[Confirmer : question à l'humain]:::confirm
    Verdict -- ALLOW --> Exec[Exécution sous sandbox]:::allow
    Humain -- oui --> Grant[Grant à TTL + audit]
    Humain -- non --> Refus
    Grant --> Exec
    Exec --> Result[ToolResult dans la mémoire]
    Refus --> Result
    Result --> Trace[audit : tool_call]
    Trace --> Test
    Answer --> Save[audit : session_end]
    Budget --> Save
    Answer0 --> Save
    Save --> End([Fin])

    classDef deny fill:#3a1414,stroke:#e05252,color:#ffd9d9
    classDef confirm fill:#3a3014,stroke:#e0b452,color:#fff1d0
    classDef allow fill:#143a1e,stroke:#4fbf6a,color:#d8ffe3
```

---

## 7. Diagramme d'états — vie d'une décision

```mermaid
stateDiagram-v2
    [*] --> Proposée : Brain.decide()

    Proposée --> Rejetée : schéma invalide
    Proposée --> Validée : JSON conforme

    Validée --> Inconnue : outil absent du registre
    Inconnue --> Terminée : ToolResult(ko)

    Validée --> Autorisée : verdict ALLOW
    Validée --> EnAttente : verdict CONFIRM
    Validée --> Refusée : verdict DENY

    EnAttente --> Autorisée : humain "oui" → Grant
    EnAttente --> Refusée : humain "non"

    Autorisée --> EnJail : Sandbox.resolve()
    EnJail --> Refusée : hors des racines
    EnJail --> Exécutée : chemin accepté

    Exécutée --> Terminée : ToolResult(ok)
    Refusée --> Terminée : audit + ToolResult(ko)
    Terminée --> [*]

    note right of EnAttente
        Un Grant expire (TTL) ou est révocable :
        l'État peut repasser en Refusée.
    end note
```

---

## 8. Diagramme de composants

```mermaid
flowchart LR
    subgraph CLI["Interface"]
        RUN["aios run / chat"]
        POL["aios policy"]
        AUD["aios audit"]
        DOC["aios doctor"]
        SER["aios serve"]
        REQ["aios-request"]
    end

    subgraph CORE["Orchestration"]
        AGT[Agent]
        LOOP[AgentLoop]
        MEM[(Mémoire<br/>fenêtre + épisodique)]
        BR1[LLMBrain]
        BR2[HeuristicBrain]
    end

    subgraph SEC["Sécurité"]
        PM[PermissionManager]
        POLI[StaticPolicy]
        CONF[Confirmer]
        SB[Sandbox]
        AU[AuditLog]
        RED[redact]
    end

    subgraph TOOLS["Outils (TCB)"]
        REG[ToolRegistry]
        FS[filesystem]
        SH[shell]
        SY[system]
        NW[network]
    end

    subgraph LLM["Modèle local"]
        OL[Ollama 127.0.0.1:11434]
        LC[llama.cpp 127.0.0.1:8080]
    end

    RUN --> AGT
    SER --> AGT
    REQ -- AF_UNIX 0600 --> SER
    POL --> POLI
    AUD --> AU
    DOC --> AGT

    AGT --> LOOP
    LOOP --> MEM
    LOOP --> BR1
    LOOP --> BR2
    BR1 --> OL
    BR1 --> LC
    BR2 -. repli .-> LOOP

    LOOP --> REG
    REG --> PM
    PM --> POLI
    PM --> CONF
    PM --> AU
    REG --> SB
    REG --> AU
    MEM --> RED
    AU --> RED

    REG --> FS
    REG --> SH
    REG --> SY
    REG --> NW
```

---

## 9. Diagramme de déploiement

```mermaid
flowchart TB
    subgraph H["Hôte Linux (build)"]
        R[repo checkout ~40 Go]
        O[overlay chromiumos-overlay]
        B[build_packages + build_image]
        R --> O --> B
    end

    subgraph VM["VM / matériel (KVM)"]
        IMG["image aiOS<br/>disque bootable"]
        subgraph CROS["Chromium OS"]
            subgraph UP["init (upstart)"]
                JOB["/etc/init/aios-agent.conf<br/>--no-confirm (fail-closed)"]
            end
            subgraph PKG["chromeos-base/aios-agent"]
                BIN["/usr/bin/aios<br/>/usr/bin/aios-request"]
                LIB["/usr/lib/aios/aios_agent/"]
                POF["/usr/share/aios/aios-policy.json"]
            end
            STATE["/var/lib/aios/<br/>audit.jsonl · episodes.json"]
            SOCK["/run/aios/agent.sock<br/>AF_UNIX 0600 · dir 0700"]
            ASH["Session Ash (UI)"]
        end
        subgraph MOD["Modèle local (optionnel)"]
            OLL["Ollama 127.0.0.1:11434"]
            LLAMA["llama.cpp 127.0.0.1:8080"]
        end
    end

    B --> IMG
    JOB --> BIN
    BIN --> LIB
    LIB --> POF
    JOB --> SOCK
    SOCK --> STATE
    ASH --> SOCK
    ASH --> BIN
    LIB -. loopback .-> OLL
    LIB -. loopback .-> LLAMA
```

> **Aucune flèche ne sort de la machine.** Le seul trafic réseau du moteur est
> la loopback vers le modèle local.

---

## 10. Diagramme de déploiement — développement

```mermaid
flowchart LR
    subgraph DEV["Poste de développeur"]
        SRC["dépôt aiOs"]
        VENV[".venv (pytest + pillow)"]
        TESTS["make check<br/>114 tests + lint + politique"]
        SHOTS["tools/make_screenshots.py<br/>→ wiki/captures/*.png"]
        SRC --> VENV
        VENV --> TESTS
        VENV --> SHOTS
    end

    SRC -->|"os/scripts/10-fetch-source.sh"| CO["checkout Chromium OS"]
    SRC -->|"os/scripts/20-prepare-overlay.sh"| OV["overlay greffé"]
    CO --> OV
    OV -->|"30-build-image.sh"| IMG["image aiOS"]
    IMG -->|"40-run-in-vm.sh"| KVM["VM KVM"]
```

---

## Note de notation

- Les **cas d'utilisation** et **diagrammes de classes** suivent UML 2.
- L'**activité** est tracée en `flowchart` (notation Mermaid équivalente à une
  activité UML : nœuds = actions, losanges = décisions, flèches = flux).
- Les **états** utilisent `stateDiagram-v2` (UML).
- Les **composants** et le **déploiement** sont en `flowchart` pour rester
  lisibles dans le wiki.

---

> **Martial Zinsou** · BSD-3-Clause · 2026
