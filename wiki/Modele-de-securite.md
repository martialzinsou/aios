# Modèle de sécurité

Source de vérité : `docs/SECURITY.md` dans le dépôt. Ce qui suit en est la
version annotée d'images — **toute modification qui casse un invariant listé en
§2 est un bug bloquant.**

---

## 1. Frontières de confiance

```
        ┌─── non fiable ───────────────────────────────────────────┐
        │  modèle local (peut halluciner / être induit en erreur)  │
        │  texte de l'utilisateur · contenu lu sur le disque       │
        │  pages web · sorties de commandes                        │
        └───────────────────────────┬──────────────────────────────┘
                                    │  Decision {tool, args}
        ┌───────────────────────────▼──────────────────────────────┐
        │  DE CONFIANCE (TCB)                                       │
        │  · ToolRegistry     — seul point d'exécution              │
        │  · PermissionManager — politique, grants, confirmation    │
        │  · Sandbox          — jail, rlimits, environnement        │
        │  · AuditLog         — journal chaîné                      │
        │  · code des outils  — décrit honnêtement son risque       │
        └───────────────────────────┬──────────────────────────────┘
                                    │  syscall / écriture fichier
        ┌───────────────────────────▼──────────────────────────────┐
        │  système                                                 │
        └──────────────────────────────────────────────────────────┘
```

> **Règle centrale :** le cerveau (modèle ou heuristique) n'est **jamais** de
> confiance. Sa sortie est une *proposition*, systématiquement revalidée par le
> registre d'outils.

---

## 2. Les 12 invariants

| # | Invariant | Où |
|---|---|---|
| **I1** | Aucune action ne s'exécute sans passer par `ToolRegistry.call` | `tools/registry.py` |
| **I2** | Un verdict `CONFIRM` ne peut être résolu que par un `Confirmer` — l'agent ne s'auto-approuve jamais | `security/permission.py` |
| **I3** | Un verdict `DENY` est retourné **avant** d'interroger l'humain | `security/permission.py` |
| **I4** | `Risk.PRIVILEGED` est `DENY` par défaut ; aucun outil ne peut l'abaisser | `security/policy.py`, `tools/base.py::risk_for` |
| **I5** | Le risque effectif est `max(risque déclaré, risque calculé)` | `tools/base.py::risk_for` |
| **I6** | Tout chemin passe par `Sandbox.resolve()` ; une symlink vers l'extérieur est rejetée | `security/sandbox.py` |
| **I7** | Aucun sous-processus ne reçoit d'environnement contenant `*TOKEN*/*KEY*/*SECRET*/*PASSWORD*` | `security/sandbox.py::_scrubbed_env` |
| **I8** | Aucun credential n'atteint un prompt ni le journal (masquage récursif) | `security/redaction.py` |
| **I9** | Chaque `authorize` et chaque `tool_call` produit un enregistrement chaîné | `security/permission.py`, `tools/registry.py` |
| **I10** | L'altération, la suppression ou la réordonnancement d'un enregistrement est détectable | `security/audit.py::verify` |
| **I11** | Le socket est `AF_UNIX 0600`, dans un répertoire `0700` si aiOS en est propriétaire | `server.py::AgentServer` |
| **I12** | La boucle est bornée en étapes **et** en temps | `core/loop.py` |

---

## 3. Ce que le socle protège

| Menace | Atténuation |
|---|---|
| Le modèle décide de supprimer un fichier | `delete_path` est `DESTRUCTIVE` → `CONFIRM` → refus si l'humain dit non ; la jail interdit les cibles hors racine |
| Injection de prompt via un fichier lu / une page web | le contenu lu n'est que de la **donnée** : il ne peut que *proposer* un outil qui repasse par la politique ; les secrets sont masqués avant tout envoi au modèle (I8) |
| Élévation de privilèges (`sudo`, `su`, `doas`, `pkexec`) | risque `PRIVILEGED` → `DENY` sans jamais solliciter l'humain (I3, I4) |
| Lecture de fichiers sensibles | jail de chemins (I6) + règles `deny` sur `/dev/*`, `/etc/shadow*` |
| Exfiltration de données | `NETWORK` → `CONFIRM` ; `http_get` refuse les schémas non HTTP(S) et les cibles loopback ; `curl`/`wget`/`ssh` dans une commande sont classés `NETWORK` |
| Fuite de credential dans les logs/prompts | `redact` sur la mémoire de travail, l'objectif et le `detail` d'audit (I8) |
| Récursion infinie / coût déraisonnable | budget étapes + temps (I12) ; `RLIMIT_CPU`, `RLIMIT_AS`, `RLIMIT_FSIZE`, timeout et plafond de sortie |
| Falsification du journal | chaînage SHA-256 + numérotation de séquence (I9, I10) |
| Accès au service par un autre utilisateur | socket `0600` dans un répertoire `0700` (I11) |
| Course entre approbation et exécution | `Grant` horodatés, TTL borné, révocables ; ciblage `(action, target)` exact |

### Vérification visuelle

| Ce qu'on voit | Capture |
|---|---|
| `sudo` refusé avant toute question | ![refus](captures/05-run-sudo-denied.png) |
| lecture hors jail bloquée (chemin résolu) | ![jail](captures/06-run-sandbox.png) |
| cadre de confirmation : `action`, `target`, `risk`, `why`, `rule` | ![confirmation](captures/07-confirmation.png) |
| journal chaîné : `session_start`, `authorize`, `tool_call`, `session_end` | ![audit](captures/08-audit.png) |
| chaîne intègre après les sessions | ![intégrité](captures/08b-audit-verify.png) |
| politique effective, `deny-privileged` en tête | ![politique](captures/03-policy.png) |

---

## 4. Ce que le socle **ne** protège **pas**

Ces limites sont **assumées et documentées**, pas des oublis :

1. **Le code des outils est de confiance.** Un outil mal écrit peut contourner
   la jail en appelant `os` directement. Les outils passent par `ctx.sandbox`,
   mais c'est une convention, pas une isolation matérielle.
2. **Pas d'isolation entre processus.** Le service tourne avec les droits de
   l'utilisateur qui l'a lancé : aucun namespace, seccomp ou landlock n'est
   appliqué à l'agent lui-même.
3. **Le modèle local peut être trompé** par du contenu malveillant lu sur le
   disque. La politique est le garde-fou, pas la rectitude du modèle.
4. **Le journal détecte la falsification, il ne l'empêche pas.** Un attaquant
   *root* peut réécrire le fichier entier ; il peut seulement rendre
   l'altération détectable a posteriori par quiconque détient la valeur de
   hachage attendue.
5. **La confirmation humaine n'est protectrice que si l'humain lit.** Un
   opérateur qui approuve systématiquement neutralise I2.
6. **Pas de chiffrement au repos** pour la mémoire épisodique ni l'audit.
7. **Le mode `--trust` existe** (nécessaire aux tests/CI) et neutralise I2.
   Il ne doit **jamais** figurer dans un job de service livré — c'est le cas du
   job upstart fourni, qui tourne en `--no-confirm` (fail-closed).

---

## 5. Profondeur en couches

```
1. Politique déclarative      → refus par défaut, DENY sans question
2. Confirmation humaine       → le seul arbitre des actions mutantes
3. Jail de chemins            → même un ALLOW reste dans la jail
4. Exécution bornée           → rlimits, timeout, env scrubbé, sortie plafonnée
5. Journal chaîné             → tout est traçable et falsifiable-détectable
6. Masquage des secrets       → rien ne part en clair dans un prompt/log
```

Chaque couche est **indépendante** : sauter la première ne désactive pas les
autres. Exemple : un accord `ALLOW` sur `write_file` n'empêche toujours pas le
sandbox de refuser `/etc/passwd`.

---

## 6. Modes de service

| Mode | Confirmation | Usage |
|---|---|---|
| interactif (`aios chat`) | `CLIConfirmer` — pose la question | poste de travail |
| `--no-confirm` | `AlwaysDeny` — fail-closed | **job livré** |
| `--trust` | `AlwaysAllow` — neutralise I2 | tests et CI **uniquement** |

```bash
grep -n "no-confirm" os/overlay/chromeos-base/aios-agent/files/aios-agent.conf
```

Le job livré est vérifiable : il ne contient **jamais** `--trust`.

---

## 7. Vérification

```bash
aios audit --verify        # intégrité du journal
aios policy                # politique effective
make check-policy          # code ↔ fichier embarqué dans l'image
make test                  # 114 tests, dont une majorité de tests de sécurité
make check                 # les trois + lint
```

| Test | Ce qu'il prouve |
|---|---|
| `test_permissions.py::test_privileged_is_denied_without_prompting` | I3 + I4 |
| `test_sandbox.py::test_jail_rejects_symlink_escape` | I6 |
| `test_sandbox.py::test_env_is_scrubbed_of_secrets` | I7 |
| `test_audit.py::test_verify_detects_tampering` | I10 |
| `test_redaction.py::*` | I8 |
| `test_agent.py::test_step_budget_is_enforced` | I12 |
| `test_server.py::test_socket_permissions_and_roundtrip` | I11 |
| `test_server.py::test_server_leaves_foreign_directory_alone` | I11 (pas de chmod parasite) |
| `test_tools.py::test_read_file_outside_jail_is_blocked` | I6 |

---

## 8. Signaler une faille

Ouvre une issue avec :

1. la version (`aios --version`) ;
2. la politique utilisée (`aios policy`) ;
3. le segment d'audit concerné (`aios audit -n 50`).

**N'inclus jamais de credential réel** — le journal les masque, mais vérifie.
Voir [Contribuer](Contribuer).
