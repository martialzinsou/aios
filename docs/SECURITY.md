# Modèle de menace aiOS

Ce document décrit **ce que le socle de sécurité protège**, **contre qui**, et
**ce qu'il ne protège pas**. Il sert de référence pour relire une évolution :
toute modification qui casse un invariant listé en §2 est un bug bloquant.

---

## 1. Frontières de confiance

```
        ┌─── non fiable ───────────────────────────────────────────┐
        │  modèle local (peut halluciner / être induit en erreur)   │
        │  texte de l'utilisateur · contenu lu sur le disque       │
        │  pages web · sorties de commandes                        │
        └───────────────────────────┬──────────────────────────────┘
                                    │  Decision {tool, args}
        ┌───────────────────────────▼──────────────────────────────┐
        │  DE CONFIANCE (TCB)                                       │
        │  · ToolRegistry  — seul point d'exécution                 │
        │  · PermissionManager — politique, grants, confirmation    │
        │  · Sandbox — jail de chemins, rlimits, environnement      │
        │  · AuditLog — journal chaîné                              │
        │  · code des outils — doit décrire honnêtement son risque  │
        └───────────────────────────┬──────────────────────────────┘
                                    │  syscall / écriture fichier
        ┌───────────────────────────▼──────────────────────────────┐
        │  système                                                 │
        └──────────────────────────────────────────────────────────┘
```

**Règle centrale :** le cerveau (modèle ou heuristique) n'est **jamais** de
confiance. Sa sortie est une *proposition*, systématiquement revalidée par le
registre d'outils.

---

## 2. Invariants

| # | Invariant | Où |
|---|---|---|
| I1 | Aucune action n'exécute sans passer par `ToolRegistry.call` | `tools/registry.py` |
| I2 | Un verdict `CONFIRM` ne peut être résolu que par un `Confirmer` — l'agent ne peut pas s'auto-approuver | `security/permission.py` |
| I3 | Un verdict `DENY` est retourné **avant** d'interroger l'humain (pas d'habillage par prompt) | `security/permission.py` |
| I4 | `Risk.PRIVILEGED` est `DENY` par défaut ; aucun outil ne peut l'abaisser | `security/policy.py`, `tools/base.py::risk_for` |
| I5 | Le niveau de risque effectif d'un appel est `max(risque déclaré, risque calculé)` | `tools/base.py::risk_for` |
| I6 | Tout chemin passe par `Sandbox.resolve()` ; une symlink vers l'extérieur est rejetée | `security/sandbox.py` |
| I7 | Aucun sous-processus ne reçoit d'environnement contenant `*TOKEN*/*KEY*/*SECRET*/*PASSWORD*` | `security/sandbox.py::_scrubbed_env` |
| I8 | Aucun credential n'atteint un prompt ni le journal (masquage récursif) | `security/redaction.py` |
| I9 | Chaque `authorize` et chaque `tool_call` produit un enregistrement d'audit chaîné | `security/permission.py`, `tools/registry.py` |
| I10 | L'altération, la suppression ou la réordonnancement d'un enregistrement est détectable | `security/audit.py::verify` |
| I11 | Le socket du service est `AF_UNIX` mode `0600` dans un répertoire `0700` | `server.py::AgentServer` |
| I12 | La boucle est bornée en étapes **et** en temps | `core/loop.py` |

### L'interface graphique (`aios ui`)

Le bureau en verre liquide **n'est pas une exception** : il ne fait que rendre
ces invariants visibles. Contrôles spécifiques, en plus d'I2 et d'I9 :

| Contrôle | Mise en œuvre |
|---|---|
| Liaison **loopback uniquement** | tout hôte hors `{127.0.0.1, localhost, ::1}` est rejeté au démarrage du serveur |
| Jeton de session par instance | exigé sur toutes les routes `/api/*`, comparaison en temps constant (`secrets.compare_digest`) |
| Vérification du champ `Host` | `Host` inattendu → `403` (relier-pour-noyer) |
| CSP stricte + `no-store` | `default-src 'self'`, `connect-src 'self'`, `base-uri 'none'`, `form-action 'none'` |
| Confirmation **fail-closed** | la file vit dans le processus du serveur ; expiration → refus |
| Aucune sortie réseau | l'interface ne parle qu'à elle-même |

Le service `aios serve` (socket `AF_UNIX 0600`, I11) reste le canal IPC de la
session Chromium OS : l'HTTP loopback est un **client supplémentaire**, pas un
remplaçant.

---

## 3. Ce que le socle protège

| Menace | Atténuation |
|---|---|
| **Le modèle décide de supprimer un fichier** | `delete_path` est `DESTRUCTIVE` → `CONFIRM` → refus si l'humain dit non ; la jail interdit les cibles hors racine. |
| **Injection de prompt via un fichier lu / une page web** | Le contenu lu n'est que de la *donnée* : il ne peut que proposer un `tool` qui repasse par la politique. Les secrets sont masqués avant tout envoi au modèle (I8). |
| **Élévation de privilèges** (`sudo`, `su`, `doas`, `pkexec`) | Risque `PRIVILEGED` → `DENY` sans jamais solliciter l'humain (I3, I4). |
| **Lecture de fichiers sensibles** | Jail de chemins (I6) + règles `deny` ciblant `/dev/*`, `/etc/shadow*`. |
| **Exfiltration de données** | `NETWORK` → `CONFIRM` ; `http_get` refuse les schémas non HTTP(S) et les cibles loopback ; `curl`/`wget`/`ssh` dans une commande sont classés `NETWORK`. |
| **Fuite de credential dans les logs/prompts** | `redact` sur la mémoire de travail, l'objectif et les détails d'audit (I8). |
| **Récursion infinie / coût déraisonnable** | Budget d'étapes et de temps (I12) ; `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`, timeout et plafond de sortie côté subprocess. |
| **Falsification du journal** | Chaînage SHA-256 + numérotation de séquence (I9, I10). |
| **Accès au service par un autre utilisateur** | Socket `0600` dans un répertoire `0700` (I11). |
| **Relier-pour-noyer vers l'interface locale** | Vérification du champ `Host` sur chaque requête ; jeton exigé ; CSP `connect-src 'self'`. |
| **Ouverture de l'interface depuis le réseau** | Liaison refusée hors loopback — le serveur ne se crée même pas. |
| **Course entre approbation et exécution** | Les `Grant` sont horodatés, TTL borné, révocables (`revoke_all`) ; ciblage par `(action, target)` exact. |

---

## 4. Ce que le socle **ne** protège **pas**

Soyons explicite — ces limites sont assumées :

1. **Le code des outils est de confiance.** Un outil mal écrit peut contourner
   la jail en appelant `os` directement. Les outils passent par
   `ctx.sandbox`, mais c'est une convention, pas une isolation matérielle.
2. **Pas de isolation entre processus.** Le service tourne avec les droits de
   l'utilisateur qui l'a lancé ; il n'y a pas de namespace/seccomp/landlock
   appliqué à l'agent lui-même.
3. **Le modèle local peut être trompé** par du contenu malveillant lu sur le
   disque. La politique est le garde-fou, pas la rectitude du modèle.
4. **Le journal détecte la falsification, il ne l'empêche pas.** Un attaquant
   *root* peut réécrire le fichier entier ; il peut seulement rendre l'altération
   détectable a posteriori par quiconque détient la valeur de hachage attendue.
5. **La confirmation humaine n'est protectrice que si l'humain lit.** Un opérateur
   qui approuve systématiquement neutralise I2.
6. **Pas de chiffrement au repos** pour la mémoire épisodique ni l'audit.
7. **Le mode `--trust` existe** (nécessaire aux tests/CI) et neutralise I2.
   Il ne doit **jamais** figurer dans un job de service livré — c'est le cas du
   job upstart fourni, qui tourne en `--no-confirm` (fail-closed).

---

## 5. Vérification

```bash
# intégrité du journal
aios audit --verify

# politique effective
aios policy

# cohérence code ↔ fichier embarqué dans l'image
make check-policy

# 130 tests dont la majorité sont des tests de sécurité
make test
```

Les tests de sécurité les plus directs :

| Test | Ce qu'il prouve |
|---|---|
| `test_permissions.py::test_privileged_is_denied_without_prompting` | I3 + I4 |
| `test_sandbox.py::test_jail_rejects_symlink_escape` | I6 |
| `test_sandbox.py::test_env_is_scrubbed_of_secrets` | I7 |
| `test_audit.py::test_verify_detects_tampering` | I10 |
| `test_redaction.py::*` | I8 |
| `test_agent.py::test_step_budget_is_enforced` | I12 |
| `test_server.py::test_socket_permissions_and_roundtrip` | I11 |
| `test_tools.py::test_read_file_outside_jail_is_blocked` | I6 |
| `test_ui.py::test_only_loopback_binding_is_accepted` | interface limitée à la loopback |
| `test_ui.py::test_api_requires_the_session_token` | jeton exigé sur `/api/*` |
| `test_ui.py::test_foreign_host_header_is_refused` | champ `Host` vérifié |
| `test_ui.py::test_confirmation_times_out_as_a_refusal` | confirmation *fail-closed* |

---

## 6. Signaler une faille

Ouvre une issue avec : la version (`aios --version`), la politique utilisée
(`aios policy`), et le segment d'audit concerné (`aios audit -n 50`).
**N'inclus jamais de credential réel** — le journal les masque, mais vérifie.

---

> **Martial Zinsou** · BSD-3-Clause · 2026
