#!/usr/bin/env bash
#
# aiOS — démarre l'image buildée dans une VM (KVM).
#
# Variables :
#   CHROMIUMOS_DIR  défaut : <racine>/chromiumos
#   BOARD           défaut : amd64-generic
#   AIOS_IMAGE_DIR  défaut : <racine>/out
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHROMIUMOS_DIR="${CHROMIUMOS_DIR:-$ROOT/chromiumos}"
BOARD="${BOARD:-amd64-generic}"
IMAGE_DIR="${AIOS_IMAGE_DIR:-$ROOT/out}"

log() { printf '\033[36m→\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

IMAGE="$(find "$IMAGE_DIR" -maxdepth 3 \( -name 'chromiumos_image.bin' -o -name '*.img' \) 2>/dev/null | head -1)"
[ -n "$IMAGE" ] || die "aucune image dans $IMAGE_DIR (lance 30-build-image.sh)"
log "image : $IMAGE"

cd "$CHROMIUMOS_DIR"
[ -x ./cros_sdk ] || die "cros_sdk introuvable"

if [ -x ./bin/cros_run_vm ]; then
  log "cros_run_vm --board=$BOARD"
  exec ./bin/cros_run_vm --board="$BOARD" --image="$IMAGE"
elif [ -x ./image_to_vm.sh ]; then
  log "image_to_vm.sh (puis lance la VM KVM résultante)"
  ./cros_sdk -- ./image_to_vm.sh --board="$BOARD" --src_image="$IMAGE"
  echo
  echo "Lance ensuite la VM produite (souvent dans build/images/$BOARD/vm/)."
else
  die "aucun outil de VM trouvé (cros_run_vm / image_to_vm.sh)"
fi
