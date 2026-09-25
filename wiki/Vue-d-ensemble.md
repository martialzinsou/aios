# Vue d'ensemble

## 1. Le problème

Un assistant agentique « dans » un OS pose trois questions, dans cet ordre :

1. **Qui a le droit de faire quoi ?** — un modèle peut proposer n'importe quoi.
2. **Qui dit oui ?** — un modèle ne peut jamais s'auto-autoriser.
3. **Que se passe-t-il si quelqu'un ment sur le journal ?** — il faut pouvoir
   le prouver a posteriori.

aiOS répond dans l'ordre : politique déclarative, confirmation humaine, journal
chaîné.

## 2. Périmètre

Le périmètre est **complet** : moteur agentique + couche OS + patchs d'intégration.

| Dans le périmètre | Hors périmètre (pour l'instant) |
|---|---|
| Boucle agentique bornée (étapes + temps) | Entraînement ou fine-tuning d'un modèle |
| Deux cerveaux : local (`LLMBrain`) et hors-ligne (`HeuristicBrain`) | Téléchargement de tout quelconque modèle |
| 9 outils système + registre unique d'exécution | Outils tiers installés à la demande |
| Politique `ALLOW / CONFIRM / DENY`, grants, confirmation | Gestion d'identité et annuaire |
| Jail de chemins, rlimits, environnement scrubbé | Chiffrement au repos |
| Journal d'audit JSONL chaîné en SHA-256 | Attestation matérielle (TPM) |
| Service `AF_UNIX 0600`, CLI à 7 sous-commandes | Confirmer graphique D-Bus (roadmap) |
| ebuild + upstart + scripts de build Chromium OS | Portage sur d'autres bases (Android, …) |

## 3. Arbitrages retenus

| Choix | Alternative écartée | Pourquoi |
|---|---|---|
| **IA 100 % on-device** | Appel à une API distante | Aucune donnée utilisateur ne quitte la machine ; fonctionne hors-ligne. |
| **Moindre privilège + confirmation humaine** | Modèle « de confiance » | Le modèle n'est jamais dans la TCB : sa sortie est une *proposition*. |
| **Politique déclarative, première correspondance gagne** | Évaluer les règles toutes ensemble | Lisible par un humain, déterministe, testable ligne à ligne. |
| **Risque déclaré statiquement par l'outil** | Risque estimé au moment de l'appel | Un outil ne peut qu'élever son risque, jamais l'abaisser. |
| **Python stdlib au runtime** | Dépendances lourdes | Déployable quel que soit le `python3` de la branche Chromium OS. |
| **`HeuristicBrain` en repli** | Sans modèle = inutilisable | Rend l'agent utilisable et les tests reproductibles sans modèle chargé. |
| **Service en `--no-confirm` (fail-closed)** | `--trust` en production | Un service système ne doit jamais s'auto-autoriser. |
| **Journal chaîné, pas chiffré** | Journal signé par clé | Coût/zéro-dépendance ; la falsification est *détectable*, pas *impossible*. |

## 4. Risques assumés

Le détail complet est dans [Modèle de sécurité](Modele-de-securite) §4. En résumé :

- le **code des outils est de confiance** (c'est la TCB, pas le modèle) ;
- **aucune isolation de processus** (pas de namespace/seccomp appliqué à l'agent) ;
- le journal **détecte** la falsification, il ne l'**empêche pas** ;
- la confirmation humaine n'est protectrice **que si l'humain lit** ;
- `--trust` existe pour les tests et ne doit jamais figurer dans un job livré.

## 5. Architecture en une image

```
        texte de l'utilisateur / contenu lu / page web
                          │  (non fiable)
                          ▼
                 ┌──────────────────┐
                 │  Brain            │  LLMBrain (Ollama / llama.cpp, loopback)
                 │  propose un       │  HeuristicBrain (repli hors-ligne)
                 │  Decision JSON    │
                 └────────┬─────────┘
                          │  {thought, action, tool, args}
                          ▼
                 ┌──────────────────┐
                 │  AgentLoop        │  budget : étapes + temps  (I12)
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  ToolRegistry     │  SEUL point d'exécution   (I1)
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │ PermissionManager │  policy → grants → humain (I2, I3)
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  Sandbox          │  jail, rlimits, env scrub  (I6, I7)
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │  AuditLog         │  JSONL chaîné SHA-256      (I9, I10)
                 └──────────────────┘
```

## 6. Les deux modes de fonctionnement

| | Moteur seul | Image aiOS |
|---|---|---|
| Commande | `make agent-chat` | `aios chat` dans la session |
| Dépendances | Python 3.9+ | Chromium OS + `chromeos-base/aios-agent` |
| Service | aucun | upstart `aios-agent` → `/run/aios/agent.sock` |
| Tests | `make test` (109) | idem + boot de l'image |

Toute la logique — sécurité comprise — se teste **sans** builder l'OS. Le build
n'est nécessaire que pour la mise en paquetage ([Construction de l'OS](Construction-de-l-OS)).

## 7. Prochaines étapes

Voir [Contribuer](Contribuer) pour le détail des chantiers ouverts.

---

> **Martial Zinsou** · BSD-3-Clause · 2026
