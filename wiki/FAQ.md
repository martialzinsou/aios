# FAQ

## Le projet

### aiOS remplace-t-il Chromium OS ?

Non. aiOS **greffe** un paquet sur Chromium OS : le noyau, l'image, Ash et
Chrome restent amont. Voir [Construction de l'OS](Construction-de-l-OS).

### Tout part-il vers le cloud ?

Non. Le modèle tourne en local (Ollama ou llama.cpp sur la **loopback**) et le
cerveau `HeuristicBrain` fonctionne même sans réseau. Voir
[Vue d'ensemble §3](Vue-d-ensemble#3--arbitrages-retenus).

### Pourquoi un cerveau de repli qui n'utilise pas de LLM ?

`HeuristicBrain` rend l'agent utilisable dès l'installation, et rend les tests
**reproductibles** sans télécharger plusieurs gigaoctets de modèle. Le repli
n'est pas une démo : il passe par exactement la même politique, le même sandbox
et le même journal que `LLMBrain`.

---

## Sécurité

### Le modèle peut-il supprimer mes fichiers ?

Non directement. Le modèle ne fait que **proposer** une décision ; elle passe
par `ToolRegistry` → `PermissionManager` → `Sandbox`. `delete_path` est
`DESTRUCTIVE` → `CONFIRM` : sans ton accord explicite, rien ne se passe.

### Que se passe-t-il si je réponds « y » sans lire ?

Le système ne peut pas te protéger de toi-même — c'est explicitement
documenté comme limite (invariant I2 neutralisé par un opérateur systématique).
Le cadre de confirmation affiche `action`, `target`, `risk`, `why` et `rule`
pour rendre la lecture rapide, mais c'est à toi de la faire.

### Pourquoi `sudo` est-il refusé sans poser de question ?

Parce qu'un `DENY` est retourné **avant** d'interroger l'humain (I3). Poser la
question laisserait croire que l'action est faisable ; elle ne l'est pas.
De plus `Risk.PRIVILEGED` est `DENY` par défaut et aucun outil ne peut
l'abaisser (I4).

### Un attaquant root peut-il falsifier le journal ?

Oui — et le système le **détecte**. Le journal est chaîné en SHA-256
(`prev` + `hash` + `seq`) ; `aios audit --verify` renvoie `ALTÉRÉ` dès qu'un
octet change. Ce n'est pas de la prévention, c'est de la détection. Voir
[Modèle de sécurité §4](Modele-de-securite#4--ce-que-le-socle-ne-protege-pas).

### Le socket est-il accessible aux autres utilisateurs ?

Non : `AF_UNIX` mode `0600` dans un répertoire `0700` (I11). Le code ne
*touche* pas un répertoire qui n'appartient pas à l'utilisateur (test
`test_server_leaves_foreign_directory_alone`).

### Mon antivirus peut-il bloquer aiOS ?

aiOS n'ouvre **aucune connexion sortante**. Le seul trafic réseau est la
loopback vers le modèle local.

### Quelle est la différence entre `--no-confirm` et `--trust` ?

| | `--no-confirm` | `--trust` |
|---|---|---|
| `Confirmer` | `AlwaysDeny` | `AlwaysAllow` |
| Lecture | oui | oui |
| Écriture / réseau / exécution | **refusées** | approuvées d'avance |
| Invariant I2 | respecté | **neutralisé** |
| Usage | **service livré** | tests et CI **uniquement** |

---

## Utilisation

### Comment forcer un outil précis ?

Tu n'en as pas besoin : l'objectif est en langage naturel et le cerveau choisit
l'outil. Pour un banc de test, appelle directement le registre en Python.

### L'agent refuse une action que j'autorise ?

Vérifie l'ordre des règles — **première correspondance gagne** :

```bash
aios policy
```

Puis la jail : un chemin résolu hors des racines est refusé même avec un
`ALLOW`.

### Comment rebrancher le modèle ?

```bash
ollama serve &
ollama pull llama3.2
aios doctor                # → modèle local ✓
aios run --brain llm "…"
```

`--brain auto` (défaut) fait cette détection automatiquement.

### Puis-je utiliser aiOS sans Chromium OS ?

Oui, c'est même le cas par défaut :

```bash
make deps
make agent-chat
```

Voir [Documentation fonctionnelle §1](Documentation-fonctionnelle#1--installation-et-démarrage).

### D'où viennent les captures d'écran du wiki ?

Elles sont **régénérées à partir du code**, jamais saisies à la main :

```bash
python3 tools/make_screenshots.py
```

Le script anonymise les sorties et **échoue** si un nom d'utilisateur ou un
glyphe manquant subsiste. Voir
[Documentation technique §13](Documentation-technique#13--générer-les-captures-du-wiki).

---

## Build

### Combien de temps prend un build complet ?

`repo sync` est le maillon long : ~40 Go, souvent 30–90 minutes selon le lien.
`build_packages` + `build_image` ajoutent 20–60 minutes. Le reste (overlay,
checks) se compte en secondes.

### Puis-je builder sur macOS ?

Non. Chromium OS ne se build que sous Linux. Utilise une VM ou le SDK Docker :
[Construction de l'OS §1](Construction-de-l-OS#1--prérequis).

### Que puis-je vérifier sans builder l'OS ?

Tout ce qui touche au moteur et à la sécurité :

```bash
make check     # 130 tests + lint + cohérence de la politique
```

### `make check-policy` échoue

Le fichier `files/aios-policy.json` diverge de `StaticPolicy.default()` :

```bash
make policy      # régénère depuis le code
make check       # revérifie
```

---

## Contribuer

### Quel est le prérequis avant de proposer une modification ?

```bash
make check      # doit être vert
```

Voir [Contribuer](Contribuer).

### Comment signaler une faille ?

Issue avec `aios --version`, `aios policy` et `aios audit -n 50`.
**Jamais de credential réel** dans le ticket. Voir
[Modèle de sécurité §8](Modele-de-securite#8--signaler-une-faille).

---

> **Martial Zinsou** · BSD-3-Clause · 2026
