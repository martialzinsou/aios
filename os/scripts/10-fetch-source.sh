#!/usr/bin/env bash
#
# aiOS — récupère l'arborescence Chromium OS via `repo`.
#
# Variables d'environnement :
#   CHROMIUMOS_DIR     destination du checkout   (défaut : <racine>/chromiumos)
#   CROS_MANIFEST_URL   manifeste upstream        (défaut : chromiumos/manifest)
#   CROS_BRANCH         branche / tag             (défaut : branche par défaut)
#   REPO_JOBS           parallélisme de sync      (défaut : nproc)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHROMIUMOS_DIR="${CHROMIUMOS_DIR:-$ROOT/chromiumos}"
MANIFEST_URL="${CROS_MANIFEST_URL:-https://chromium.googlesource.com/chromiumos/manifest}"
REPO_BIN="${REPO_BIN:-$ROOT/.local/bin/repo}"
REPO_JOBS="${REPO_JOBS:-$(nproc 2>/dev/null || echo 4)}"

log() { printf '\033[36m→\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "Chromium OS ne se build que sous Linux (vu : $(uname -s))."

# --- 1. outil `repo` ------------------------------------------------------
mkdir -p "$(dirname "$REPO_BIN")"
if [ ! -x "$REPO_BIN" ]; then
  log "téléchargement de l'outil repo"
  curl -fsSL https://storage.googleapis.com/git-repo-downloads/repo -o "$REPO_BIN.tmp" \
    || die "impossible de télécharger repo"
  mv "$REPO_BIN.tmp" "$REPO_BIN"
  chmod +x "$REPO_BIN"
fi
log "repo : $($REPO_BIN --version 2>/dev/null | head -1)"

# --- 2. init --------------------------------------------------------------
mkdir -p "$CHROMIUMOS_DIR"
cd "$CHROMIUMOS_DIR"

if [ ! -d .repo ]; then
  log "repo init -u $MANIFEST_URL ${CROS_BRANCH:+-b $CROS_BRANCH}"
  ARGS=(init -u "$MANIFEST_URL")
  [ -n "${CROS_BRANCH:-}" ] && ARGS+=(-b "$CROS_BRANCH")
  # --depth=1 garde le checkout gérable (~40 Go au lieu de ~150 Go).
  ARGS+=(--depth="${REPO_DEPTH:-1}")
  "$REPO_BIN" "${ARGS[@]}" || die "repo init a échoué"
else
  log "checkout existant détecté, pas de repo init"
fi

# --- 3. sync --------------------------------------------------------------
log "repo sync -j$REPO_JOBS  (plusieurs dizaines de Go, patience…)"
if [ -n "${CROS_BRANCH:-}" ]; then
  "$REPO_BIN" sync -j "$REPO_JOBS" --current-branch --force-sync
else
  "$REPO_BIN" sync -j "$REPO_JOBS" --current-branch --force-sync
fi

log "checkout prêt : $CHROMIUMOS_DIR"
echo
echo "Étape suivante : os/scripts/20-prepare-overlay.sh"
