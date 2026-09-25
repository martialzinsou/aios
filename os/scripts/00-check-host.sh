#!/usr/bin/env bash
#
# aiOS — vérifie que cet hôte est capable de builder Chromium OS.
#
# Chromium OS n'est buildable QUE sous Linux.  Sous macOS/Windows il faut une
# VM Linux (Debian 12 / Ubuntu 22.04 recommandés) ou le SDK Docker officiel.
#
set -uo pipefail

ERRORS=0
WARNINGS=0

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32mOK \033[0m %s\n' "$*"; }
warn() { printf '  \033[33mATT\033[0m %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
bad()  { printf '  \033[31mERR\033[0m %s\n' "$*"; ERRORS=$((ERRORS + 1)); }

need_cmd() {
  if command -v "$1" >/dev/null 2>&1; then
    ok "$1 ($(command -v "$1"))"
  else
    bad "commande manquante : $1 ${2:-}"
  fi
}

echo
echo "aiOS — vérification de l'hôte de build"
echo "──────────────────────────────────────"

# --- 1. système d'exploitation ------------------------------------------
if [ "$(uname -s)" = "Linux" ]; then
  ok "Linux $(uname -r) ($(uname -m))"
else
  bad "système non supporté : $(uname -s)"
  say "Chromium OS se build uniquement sous Linux."
  say "→ lance cette arborescence dans une VM Linux ou via le SDK Docker :"
  say "     docker run -it --privileged -v \$PWD:/src debian:bookworm bash"
fi

# --- 2. outils de base ---------------------------------------------------
echo
echo "Outils requis"
need_cmd git      "(apt install git)"
need_cmd curl     "(apt install curl)"
need_cmd python3  "(apt install python3)"
need_cmd cpio     "(apt install cpio)"
need_cmd rsync    "(apt install rsync)"
need_cmd xz       "(apt install xz-utils)"
need_cmd unzip    "(apt install unzip)"
need_cmd sudo     "(apt install sudo)"
need_cmd patch    "(apt install patch)"
need_cmd zstd     "(apt install zstd)"

# --- 3. virtualisation / KVM --------------------------------------------
echo
echo "Virtualisation"
if [ -e /dev/kvm ]; then
  if [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
    ok "/dev/kvm accessible"
  else
    warn "/dev/kvm présent mais non accessible (ajoute ton utilisateur au groupe kvm)"
  fi
else
  warn "/dev/kvm absent — le build marche, mais cros_run_vm sera lent"
fi

# --- 4. ressources -------------------------------------------------------
echo
echo "Ressources"
DISK_PATH="${1:-.}"
if command -v df >/dev/null 2>&1; then
  FREE_GB=$(df -Pk "$DISK_PATH" 2>/dev/null | awk 'NR==2 {print int($4/1048576)}')
  if [ -n "${FREE_GB:-}" ]; then
    if [ "$FREE_GB" -ge 100 ]; then
      ok "disque : ${FREE_GB} Go libres (≥ 100 Go recommandés)"
    elif [ "$FREE_GB" -ge 45 ]; then
      warn "disque : ${FREE_GB} Go — suffisant pour un checkout partiel, 100 Go conseillés"
    else
      bad "disque : ${FREE_GB} Go — ≥ 45 Go minimum requis"
    fi
  fi
fi

if [ "$(uname -s)" = "Linux" ]; then
  RAM_GB=$(awk '/MemTotal/ {print int($2/1048576)}' /proc/meminfo 2>/dev/null || echo 0)
  CORES=$(nproc 2>/dev/null || echo 1)
  if [ "${RAM_GB:-0}" -ge 16 ]; then
    ok "RAM : ${RAM_GB} Go · ${CORES} cœurs"
  elif [ "${RAM_GB:-0}" -ge 8 ]; then
    warn "RAM : ${RAM_GB} Go — 16 Go recommandés"
  else
    bad "RAM : ${RAM_GB} Go — 8 Go minimum, 16 Go recommandés"
  fi
fi

# --- 5. sortie -----------------------------------------------------------
echo
if [ "$ERRORS" -gt 0 ]; then
  printf '\033[31m%s problème(s) bloquant(s), %s avertissement(s).\033[0m\n' \
    "$ERRORS" "$WARNINGS"
  exit 1
fi
if [ "$WARNINGS" -gt 0 ]; then
  printf '\033[33mHôte utilisable, %s avertissement(s).\033[0m\n' "$WARNINGS"
else
  printf '\033[32mHôte prêt pour le build.\033[0m\n'
fi
exit 0
