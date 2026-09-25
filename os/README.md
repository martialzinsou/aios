# aiOS — couche Chromium OS

Comment le moteur agentique devient un **service du système**, et comment on
produit une image bootable.

---

## 1. Prérequis

| | Minimum | Recommandé |
|---|---|---|
| OS d'hôte | Linux (x86_64) | Debian 12 / Ubuntu 22.04 |
| Disque | 45 Go | **100 Go+** |
| RAM | 8 Go | 16 Go+ |
| Outils | git, curl, python3, cpio, rsync, xz, unzip, patch, zstd | — |
| VM | — | `/dev/kvm` pour `cros_run_vm` |

> **macOS / Windows :** Chromium OS ne se build pas. Lance l'arborescence dans
> une VM Linux ou via le SDK Docker :
> ```bash
> docker run -it --privileged -v "$PWD":/src -w /src debian:bookworm bash
> apt update && apt install -y git curl python3 cpio rsync xz-utils unzip sudo patch zstd
> ```

Vérifie l'hôte :

```bash
os/scripts/00-check-host.sh
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

Équivalent avec Make : `make check` (tests + lint + politique), `make overlay`,
`make image`, `make vm`.

### Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `CHROMIUMOS_DIR` | `<racine>/chromiumos` | emplacement du checkout |
| `CROS_MANIFEST_URL` | `chromiumos/manifest` | manifeste `repo` |
| `CROS_BRANCH` | (branche par défaut) | ex. `release-R131-62007.B` |
| `REPO_JOBS` | `nproc` | parallélisme du sync |
| `REPO_DEPTH` | `1` | clone partiel (fortement recommandé) |
| `BOARD` | `amd64-generic` | cible matérielle |
| `IMAGE_TYPE` | `base` | type d'image `build_image` |
| `AIOS_IMAGE_DIR` | `<racine>/out` | sortie des images |
| `AIOS_USE_LOCAL_MANIFEST` | `0` | installe `.repo/local_manifests/aios.xml` |

---

## 3. Ce que fait `20-prepare-overlay.sh`

1. Copie `os/overlay/chromeos-base/aios-agent/` dans
   `chromiumos/src/third_party/chromiumos-overlay/chromeos-base/aios-agent/`.
2. **Vendorise** la source Python dans `files/aios_agent/` — l'ebuild a un
   `SRC_URI` vide, donc le build est **100 % hors-ligne**, sans miroir DISTDIR.
3. Vérifie que `files/aios-policy.json` est **identique** à
   `StaticPolicy.default()` (`os/scripts/check_policy.py`). Si le code et
   l'image divergent, l'overlay n'est pas greffé.
4. (optionnel) installe le local manifest `repo`.

Aucun fichier du checkout amont n'est modifié à cette étape.

---

## 4. Le paquet `chromeos-base/aios-agent`

```
chromeos-base/aios-agent/
├── aios-agent-0.1.0.ebuild
└── files/
    ├── aios_agent/        # source vendorisée (copiée par le script)
    ├── aios               # launcher → python3 -m aios_agent.cli
    ├── aios-policy.json   # politique déclarative embarquée
    ├── aios-agent.conf    # job upstart
    └── README.md
```

Installations :

| Destination | Contenu |
|---|---|
| `/usr/lib/aios/aios_agent/` | le moteur (importable via `PYTHONPATH`) |
| `/usr/bin/aios` | launcher CLI |
| `/usr/share/aios/aios-policy.json` | politique (modifiable par l'admin) |
| `/etc/init/aios-agent.conf` | service upstart |
| `/var/lib/aios/` | audit + mémoire épisodique |

### Pourquoi un launcher ?

Le moteur est du Python pur à zéro dépendance. Plutôt que de dépendre du chemin
`site-packages` propre à chaque branche de Chromium OS, `/usr/bin/aios` fixe
`PYTHONPATH=/usr/lib/aios` puis exec `python3 -m aios_agent.cli`. Le package
fonctionne donc quelle que soit la version de Python de la branche.

### Si ton overlay utilise des Manifests épais

L'overlay amont de Chromium OS utilise des manifests fins (pas de fichier
`Manifest`). Si ta branche exige un manifeste épais :

```bash
cd chromiumos/src/third_party/chromiumos-overlay
ebuild chromeos-base/aios-agent/aios-agent-0.1.0.ebuild manifest
```

---

## 5. Le service

```bash
# dans la chroot / la VM
start aios-agent
status aios-agent
log -t aios-agent -f          # journal en direct

# du côté utilisateur
aios chat
aios tools
aios policy
aios audit -n 40
aios audit --verify
```

Le service écoute sur **`/run/aios/agent.sock`** — un `AF_UNIX` de mode
**0600** dans un répertoire **0700** : seul le propriétaire peut l'atteindre.
Protocole : JSON ligne à ligne.

```bash
echo '{"goal": "liste le dossier /home/chronos/user"}' | \
  python3 -c 'import socket,sys; s=socket.socket(socket.AF_UNIX); s.connect("/run/aios/agent.sock"); s.sendall(sys.stdin.buffer.read()+b"\n"); print(s.makefile("r").readline())'
```

### Mode par défaut : **fail-closed**

Le job livré tourne avec `--no-confirm` : seules les opérations **lecture**
s'exécutent sans humain dans la boucle. C'est volontaire — un service système
ne doit jamais s'auto-autoriser.

Pour ouvrir l'écriture, il faut brancher un **Confirmer graphique** (D-Bus vers
la session Ash) puis retirer `--no-confirm` dans
`/etc/init/aios-agent.conf`. En attendant, utilise `aios chat` en interactif,
qui confirme toujours.

---

## 6. Injection dans l'image

`30-build-image.sh` ajoute `chromeos-base/aios-agent` à
`virtual/target-os` (le point d'entrée qui détermine le contenu de toute image),
de façon **idempotente**. Si `virtual/target-os` n'existe pas sur ta branche,
le script poursuit et te demande d'ajouter le package à ton *image profile*
manuellement.

---

## 7. Dépannage

| Symptôme | Cause probable | Fix |
|---|---|---|
| `Chromium OS ne se build que sous Linux` | hôte macOS/Windows | VM Linux ou Docker (voir §1) |
| `repo: command not found` / 404 | téléchargement `repo` bloqué | `curl -O https://storage.googleapis.com/git-repo-downloads/repo && chmod +x repo` |
| disque plein pendant `repo sync` | pas de `--depth=1` | relance avec `REPO_DEPTH=1` |
| `politique divergente du code` | `aios-policy.json` pas régénéré | `make policy` |
| `virtual/target-os introuvable` | branche différente | ajoute le package à ton image profile |
| `AF_UNIX path too long` | chemin de socket trop long | utilise un chemin court (`/run/aios/agent.sock`) |
| modèle non détecté | pas de serveur local | `ollama serve && ollama pull llama3.2` |

---

## 8. Vérifier sans builder l'OS

Toute la logique — sécurité comprise — est testée **sans** Chromium OS :

```bash
make test          # 114 tests
make lint          # compilation + syntaxe bash
make check-policy  # code ↔ fichier de politique
```

Le build OS n'est nécessaire que pour la *mise empaquetage*.
