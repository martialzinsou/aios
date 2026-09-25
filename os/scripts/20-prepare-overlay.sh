#!/usr/bin/env bash
#
# aiOS — greffe l'overlay aiOS sur le checkout Chromium OS.
#
#   • copie os/overlay/chromeos-base/aios-agent  →  chromiumos-overlay
#   • vendorise la source Python du moteur dans files/aios_agent (build offline)
#   • (optionnel) installe un local_manifest pour suivre le projet via git
#
# Variables :
#   CHROMIUMOS_DIR            défaut : <racine>/chromiumos
#   AIOS_USE_LOCAL_MANIFEST   1 pour ajouter .repo/local_manifests/aios.xml
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHROMIUMOS_DIR="${CHROMIUMOS_DIR:-$ROOT/chromiumos}"
OVERLAY_SRC="$ROOT/os/overlay"
OVERLAY_DST="$CHROMIUMOS_DIR/src/third_party/chromiumos-overlay"
PKG_DST="$OVERLAY_DST/chromeos-base/aios-agent"

log() { printf '\033[36m→\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -d "$CHROMIUMOS_DIR/.repo" ] || die "pas de checkout Chromium OS dans $CHROMIUMOS_DIR (lance d'abord 10-fetch-source.sh)"
[ -d "$OVERLAY_DST" ] || die "overlay introuvable : $OVERLAY_DST"

# --- 1. ebuild + fichiers du package --------------------------------------
log "installation de chromeos-base/aios-agent"
rm -rf "$PKG_DST"
mkdir -p "$PKG_DST"
cp -R "$OVERLAY_SRC/chromeos-base/aios-agent/." "$PKG_DST/"

# --- 2. vendorisation de la source de l'agent -----------------------------
# Permet un build 100% hors-ligne : SRC_URI est vide, la source vit dans
# ${FILESDIR}/aios_agent.
log "vendorisation de $ROOT/agent/src/aios_agent"
rm -rf "$PKG_DST/files/aios_agent"
mkdir -p "$PKG_DST/files/aios_agent"
cp -R "$ROOT/agent/src/aios_agent/." "$PKG_DST/files/aios_agent/"
find "$PKG_DST/files/aios_agent" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
find "$PKG_DST/files/aios_agent" -name '*.py[co]' -delete 2>/dev/null || true

# Documentation embarquée dans le package
if [ -f "$ROOT/README.md" ]; then
  cp "$ROOT/README.md" "$PKG_DST/files/README.md"
fi

chmod 0755 "$PKG_DST/files/aios" "$PKG_DST/files/aios-request"

# --- 3. local manifest optionnel ------------------------------------------
if [ "${AIOS_USE_LOCAL_MANIFEST:-0}" = "1" ]; then
  log "installation de .repo/local_manifests/aios.xml"
  mkdir -p "$CHROMIUMOS_DIR/.repo/local_manifests"
  cp "$ROOT/os/manifest/aios-local-manifest.xml" \
     "$CHROMIUMOS_DIR/.repo/local_manifests/aios.xml"
fi

# --- 4. vérifications ------------------------------------------------------
echo
log "vérifications"
test -f "$PKG_DST/aios-agent-0.1.0.ebuild" || die "ebuild manquant"
test -f "$PKG_DST/files/aios-policy.json"    || die "politique manquante"
test -f "$PKG_DST/files/aios-agent.conf"     || die "job upstart manquant"
test -f "$PKG_DST/files/aios_agent/cli.py"   || die "source de l'agent non vendorisée"
test -x "$PKG_DST/files/aios"                || die "launcher non exécutable"
test -x "$PKG_DST/files/aios-request"        || die "client aios-request non exécutable"

# La politique livrée doit être un dépouillement exact de la politique par défaut
# du code : sinon le comportement de l'image diffère de celui des tests.
if command -v python3 >/dev/null 2>&1; then
  AIOS_ROOT="$ROOT" \
  AIOS_SHIPPED_POLICY="$PKG_DST/files/aios-policy.json" \
  python3 "$ROOT/os/scripts/check_policy.py" || die "politique divergente du code"
else
  log "python3 absent : contrôle de politique ignoré"
fi

log "overlay prêt : $PKG_DST"
echo
echo "Étape suivante : os/scripts/30-build-image.sh"
