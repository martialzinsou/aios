#!/usr/bin/env bash
#
# aiOS — build du package puis de l'image Chromium OS avec aiOS intégré.
#
# Variables :
#   CHROMIUMOS_DIR   défaut : <racine>/chromiumos
#   BOARD            défaut : amd64-generic
#   IMAGE_TYPE       défaut : base
#   AIOS_IMAGE_DIR   défaut : <racine>/out
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHROMIUMOS_DIR="${CHROMIUMOS_DIR:-$ROOT/chromiumos}"
BOARD="${BOARD:-amd64-generic}"
IMAGE_TYPE="${IMAGE_TYPE:-base}"
IMAGE_DIR="${AIOS_IMAGE_DIR:-$ROOT/out}"

log()  { printf '\033[36m→\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -d "$CHROMIUMOS_DIR/.repo" ] || die "pas de checkout dans $CHROMIUMOS_DIR"
cd "$CHROMIUMOS_DIR"
[ -x ./cros_sdk ] || die "cros_sdk introuvable (checkout incomplet ?)"

SDK=(./cros_sdk)

# --- 1. board -------------------------------------------------------------
if [ ! -d "chroot/var/cache/pkgroot-$BOARD" ] && [ ! -d "build/images/$BOARD" ]; then
  log "setup_board --board=$BOARD"
  "${SDK[@]}" -- ./setup_board --board="$BOARD"
else
  log "board $BOARD déjà initialisé"
fi

# --- 2. injection dans virtual/target-os ----------------------------------
# C'est le point d'entrée qui détermine le contenu des images.  On y ajoute
# chromeos-base/aios-agent de façon idempotente.
log "injection de chromeos-base/aios-agent dans virtual/target-os"
set +e
"${SDK[@]}" -- python3 - <<'PY'
import re, sys, glob

candidates = sorted(glob.glob("src/third_party/chromiumos-overlay/virtual/target-os/*.ebuild"))
if not candidates:
    print("AVERTISSEMENT : virtual/target-os introuvable.", file=sys.stderr)
    print("Ajoute manuellement chromeos-base/aios-agent à ton image profile.", file=sys.stderr)
    sys.exit(3)

path = candidates[-1]
src = open(path, encoding="utf-8").read()
if "chromeos-base/aios-agent" in src:
    print(f"déjà présent : {path}")
    sys.exit(0)

m = re.search(r'^RDEPEND="', src, re.M)
if not m:
    print(f"RDEPEND introuvable dans {path}", file=sys.stderr)
    sys.exit(1)

insert = 'RDEPEND="\n\tchromeos-base/aios-agent'
open(path, "w", encoding="utf-8").write(src[:m.start()] + insert + src[m.end():])
print(f"ajouté : {path}")
PY
INJECTION=$?
set -e
case "$INJECTION" in
  0) log "package présent dans target-os" ;;
  3) log "poursuite sans injection — ajoute le package à ton image profile" ;;
  *) die "injection a échoué (code $INJECTION)" ;;
esac

# --- 3. build du package --------------------------------------------------
log "build de chromeos-base/aios-agent"
"${SDK[@]}" -- ./cros_build_package --board="$BOARD" chromeos-base/aios-agent

# --- 4. build de l'image --------------------------------------------------
mkdir -p "$IMAGE_DIR"
log "build_image --board=$BOARD $IMAGE_TYPE  →  $IMAGE_DIR"
"${SDK[@]}" -- ./build_image --board="$BOARD" "$IMAGE_TYPE" "$IMAGE_DIR"

echo
log "images produites :"
find "$IMAGE_DIR" -maxdepth 3 \( -name '*.bin' -o -name '*.img' -o -name '*.zip' \) -print 2>/dev/null || true
echo
echo "Étape suivante : os/scripts/40-run-in-vm.sh"
