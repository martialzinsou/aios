# Construction de l'OS

Comment on produit une image bootable à partir de Chromium OS.

> **Prérequis : Linux uniquement.** Chromium OS ne se build ni sous macOS ni
> sous Windows. Il faut une VM Linux (Debian 12 / Ubuntu 22.04) ou le SDK
> Docker, ~100 Go de disque et 16 Go de RAM.

---

## 1. Prérequis

| | Minimum | Recommandé |
|---|---|---|
| OS d'hôte | Linux x86_64 | Debian 12 / Ubuntu 22.04 |
| Disque | 45 Go | **100 Go+** |
| RAM | 8 Go | 16 Go+ |
| Outils | git, curl, python3, cpio, rsync, xz, unzip, patch, zstd | — |
| VM | — | `/dev/kvm` pour `cros_run_vm` |

```bash
# sans Docker : une conteneur Debian de travail
docker run -it --privileged -v "$PWD":/src -w /src debian:bookworm bash
apt update && apt install -y git curl python3 cpio rsync xz-utils unzip sudo patch zstd
```

```bash
os/scripts/00-check-host.sh      # vérifie l'hôte avant de commencer
```

---

## 2. Chaîne de build

```bash
os/scripts/00-check-host.sh      # 1. l'hôte est-il capable de builder ?
os/scripts/10-fetch-source.sh    # 2. repo init + repo sync  (~40 Go, long)
os/scripts/20-prepare-overlay.sh # 3. greffe chromeos-base/aios-agent
os/scripts/30-build-image.sh     # 4. build_packages + build_image
os/scripts/40-run-in-vm.sh       # 5. boot KVM
```

Équivalent Make :

```bash
make overlay     # étape 3
make image       # étapes 3 + 4
make vm          # étape 5
```

Aucun fichier du checkout amont n'est modifié avant l'étape 4.

---

## 3. Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `CHROMIUMOS_DIR` | `<racine>/chromiumos` | emplacement du checkout |
| `CROS_MANIFEST_URL` | `chromiumos/manifest` | manifeste `repo` |
| `CROS_BRANCH` | branche par défaut | ex. `release-R131-62007.B` |
| `REPO_JOBS` | `nproc` | parallélisme du sync |
| `REPO_DEPTH` | `1` | clone partiel (fortement recommandé) |
| `BOARD` | `amd64-generic` | cible matérielle |
| `IMAGE_TYPE` | `base` | type d'image `build_image` |
| `AIOS_IMAGE_DIR` | `<racine>/out` | sortie des images |
| `AIOS_USE_LOCAL_MANIFEST` | `0` | installe `.repo/local_manifests/aios.xml` |

---

## 4. Ce que fait `20-prepare-overlay.sh`

1. copie `os/overlay/chromeos-base/aios-agent/` dans
   `chromiumos/src/third_party/chromiumos-overlay/chromeos-base/aios-agent/` ;
2. **vendorise** la source Python dans `files/aios_agent/` — l'ebuild a un
   `SRC_URI` vide, donc le build est **100 % hors-ligne**, sans miroir DISTDIR ;
3. rend les launchers exécutables (`aios`, `aios-request`) ;
4. vérifie que `files/aios-policy.json` est un **détachage exact** de
   `StaticPolicy.default()` (`os/scripts/check_policy.py`) — sinon l'overlay
   n'est pas greffé ;
5. (optionnel) installe le local manifest `repo`.

```bash
make check-policy     # vérification isolée, inclus dans make check
```

---

## 5. Le paquet `chromeos-base/aios-agent`

```
chromeos-base/aios-agent/
├── aios-agent-0.1.0.ebuild
└── files/
    ├── aios_agent/        # source vendorisée (copiée par le script)
    ├── aios               # launcher → python3 -m aios_agent.cli
    ├── aios-request       # client → python3 -m aios_agent.request
    ├── aios-policy.json   # politique déclarative embarquée
    ├── aios-agent.conf    # job upstart
    └── README.md
```

### Installations

| Destination | Contenu |
|---|---|
| `/usr/lib/aios/aios_agent/` | le moteur (importable via `PYTHONPATH`) |
| `/usr/bin/aios` | CLI |
| `/usr/bin/aios-request` | client du socket |
| `/usr/share/aios/aios-policy.json` | politique (modifiable par l'admin) |
| `/etc/init/aios-agent.conf` | service upstart |
| `/var/lib/aios/` | audit + mémoire épisodique |

### Pourquoi des launchers ?

Le moteur est du Python pur à zéro dépendance. Plutôt que de dépendre du chemin
`site-packages` propre à chaque branche de Chromium OS, les launchers fixent
`PYTHONPATH=/usr/lib/aios` puis `exec python3 -m …`. Le package fonctionne
donc quelle que soit la version de Python de la branche.

### Manifests

L'overlay amont utilise des manifests fins (pas de fichier `Manifest`). Si ta
branche exige un manifeste épais :

```bash
cd chromiumos/src/third_party/chromiumos-overlay
ebuild chromeos-base/aios-agent/aios-agent-0.1.0.ebuild manifest
```

---

## 6. Injection dans l'image

`30-build-image.sh` ajoute `chromeos-base/aios-agent` à `virtual/target-os`
(le point d'entrée qui détermine le contenu de toute image), de façon
**idempotente**. Si `virtual/target-os` n'existe pas sur ta branche, le script
poursuit et te demande d'ajouter le package à ton *image profile*
manuellement.

---

## 7. Build sans l'OS

Toute la logique — sécurité comprise — est testée **sans** Chromium OS :

```bash
make test          # 130 tests
make lint          # compilation Python + syntaxe bash
make check-policy  # code ↔ fichier de politique
make check         # les trois
```

Le build OS n'est nécessaire que pour la *mise en paquetage*.

---

## 8. Dépannage du build

| Symptôme | Cause probable | Fix |
|---|---|---|
| `Chromium OS ne se build que sous Linux` | hôte macOS/Windows | VM Linux ou Docker (§1) |
| `repo: command not found` / 404 | téléchargement `repo` bloqué | `curl -O https://storage.googleapis.com/git-repo-downloads/repo && chmod +x repo` |
| disque plein pendant `repo sync` | pas de `--depth=1` | relance avec `REPO_DEPTH=1` |
| `politique divergente du code` | `aios-policy.json` pas régénéré | `make policy` |
| `virtual/target-os introuvable` | branche différente | ajoute le package à ton image profile |
| `AF_UNIX path too long` | chemin de socket trop long | `/run/aios/agent.sock` |
| modèle non détecté | pas de serveur local | `ollama serve && ollama pull llama3.2` |

---

## 9. Générer une image, sans la builder

En attendant un hôte Linux, les artefacts suivants sont **vérifiables ici** :

```bash
make check                      # tests + lint + politique
os/scripts/00-check-host.sh     # échoue proprement sur macOS
bash -n os/scripts/*.sh         # syntaxe des scripts (inclus dans make lint)
```

Voir aussi [Déploiement et exploitation](Deploiement-et-exploitation).

---

> **Martial Zinsou** · BSD-3-Clause · 2026
