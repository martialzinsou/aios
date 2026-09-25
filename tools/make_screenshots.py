#!/usr/bin/env python3
"""Génère les captures d'écran du wiki.

Chaque capture est une **vraie session** : la commande est exécutée, sa sortie
est capturée, puis rendue dans une fenêtre terminal (macOS) via Pillow.

Aucune donnée réelle n'apparaît : les chemins, l'hôte et l'utilisateur sont
remplacés par des valeurs de démonstration (`/home/chronos/user`, `aios-devbox`).

Usage :  python tools/make_screenshots.py
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
AIOS = VENV / "bin" / "aios"
AIOS_REQUEST = VENV / "bin" / "aios-request"
PY = VENV / "bin" / "python"
OUT = ROOT / "wiki" / "captures"

FONT_PATH = "/System/Library/Fonts/Menlo.ttc"
FONT_SIZE = 14
LINE_H = 20
PAD_X = 20
PAD_Y = 16
TITLE_H = 40
MAX_COLS = 100

DEFAULT_FG = (214, 216, 222)
PROMPT_FG = (78, 209, 96)
BG = (22, 24, 29)
TITLE_BG = (46, 48, 57)

_ANSI = re.compile(r"\x1b\[([0-9;]*)m")
_BASIC = {
    0: (0, 0, 0), 1: (255, 95, 86), 2: (78, 209, 96), 3: (255, 198, 46),
    4: (80, 150, 255), 5: (210, 120, 255), 6: (80, 220, 230), 7: (230, 230, 230),
}
_BRIGHT = {0: (120, 124, 132), 1: (255, 110, 100), 2: (110, 230, 130),
           3: (255, 215, 90), 4: (120, 170, 255), 5: (230, 140, 255),
           6: (110, 235, 245), 7: (255, 255, 255)}

Segment = Tuple[str, Tuple[int, int, int], bool]
Line = List[Segment]


# --------------------------------------------------------------------------
# ANSI -> segments
# --------------------------------------------------------------------------
def parse_ansi(text: str) -> List[Line]:
    lines: List[Line] = [[]]
    fg, bold = DEFAULT_FG, False

    def push(chunk: str) -> None:
        if not chunk:
            return
        parts = chunk.split("\n")
        for i, part in enumerate(parts):
            if part:
                lines[-1].append((part, fg, bold))
            if i < len(parts) - 1:
                lines.append([])

    pos = 0
    for match in _ANSI.finditer(text):
        push(text[pos:match.start()])
        pos = match.end()
        raw = match.group(1) or "0"
        codes = [int(c) if c else 0 for c in raw.split(";")]
        i = 0
        while i < len(codes):
            code = codes[i]
            if code == 0:
                fg, bold = DEFAULT_FG, False
            elif code == 1:
                bold = True
            elif code in (22, 27):
                bold = False
            elif code == 39:
                fg = DEFAULT_FG
            elif 30 <= code <= 37:
                fg = _BASIC[code - 30]
            elif 90 <= code <= 97:
                fg = _BRIGHT[code - 90]
            elif code == 38 and i + 2 < len(codes) and codes[i + 1] == 5:
                i += 2  # 256 couleurs : non utilisées ici
            i += 1
    push(text[pos:])
    return [l for l in lines] or [[]]


def hard_wrap(lines: List[Line], max_cols: int) -> List[Line]:
    out: List[Line] = []
    for segs in lines:
        current: Line = []
        width = 0
        for text, fg, bold in segs:
            for ch in text:
                if width >= max_cols:
                    out.append(current)
                    current, width = [], 0
                current.append((ch, fg, bold))
                width += 1
        out.append(current)
    return out or [[]]


def to_runs(segs: Line) -> List[Segment]:
    runs: List[Segment] = []
    for text, fg, bold in segs:
        if runs and runs[-1][1] == fg and runs[-1][2] == bold:
            runs[-1] = (runs[-1][0] + text, fg, bold)
        else:
            runs.append((text, fg, bold))
    return [(t, f, b) for t, f, b in runs if t]


# --------------------------------------------------------------------------
# Rendu
# --------------------------------------------------------------------------
def render_window(lines: List[Line], title: str) -> Image.Image:
    reg = ImageFont.truetype(FONT_PATH, FONT_SIZE, index=0)
    bold = ImageFont.truetype(FONT_PATH, FONT_SIZE, index=1)
    title_font = ImageFont.truetype(FONT_PATH, 13, index=0)

    measured = [
        sum((reg if not b else bold).getlength(t) for t, _, b in to_runs(segs))
        for segs in lines
    ]
    text_w = int(max(measured, default=400))
    width = max(text_w + 2 * PAD_X, 680)
    height = TITLE_H + PAD_Y + len(lines) * LINE_H + PAD_Y

    win = Image.new("RGBA", (width, height), BG + (255,))
    draw = ImageDraw.Draw(win)
    draw.rectangle([0, 0, width, TITLE_H], fill=TITLE_BG + (255,))
    draw.line([(0, TITLE_H), (width, TITLE_H)], fill=(0, 0, 0, 90))

    for i, color in enumerate(((255, 95, 86), (255, 190, 46), (40, 200, 64))):
        cx = 22 + i * 20
        draw.ellipse([cx - 6, TITLE_H // 2 - 6, cx + 6, TITLE_H // 2 + 6],
                     fill=color + (255,))

    tw = title_font.getlength(title)
    draw.text(((width - tw) / 2, (TITLE_H - 13) / 2 - 1), title,
              font=title_font, fill=(190, 193, 200, 255))

    y = TITLE_H + PAD_Y
    for segs in lines:
        x = float(PAD_X)
        for text, fg, b in to_runs(segs):
            font = bold if b else reg
            draw.text((x, y), text, font=font, fill=fg + (255,))
            x += font.getlength(text)
        y += LINE_H

    # coins arrondis + ombre portée
    radius = 12
    mask = Image.new("L", win.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, win.size[0] - 1, win.size[1] - 1],
                                           radius=radius, fill=255)
    win.putalpha(mask)

    margin = 16
    canvas = Image.new("RGBA", (win.size[0] + 2 * margin, win.size[1] + 2 * margin),
                       (0, 0, 0, 0))
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [margin, margin + 6, margin + win.size[0], margin + win.size[1] + 6],
        radius=radius, fill=(0, 0, 0, 130))
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(11)))
    canvas.alpha_composite(win, (margin, margin))
    return canvas.convert("RGB")


# --------------------------------------------------------------------------
# Anonymisation
# --------------------------------------------------------------------------
def build_sanitizers(paths: Dict[str, str]) -> List[Tuple[str, str]]:
    host = socket.gethostname()
    user = (os.environ.get("USER") or os.environ.get("LOGNAME")
            or Path.home().name)
    pairs = [
        (paths["demo"], "/home/chronos/user/projet"),
        (paths["state"], "/home/chronos/user/.local/share/aios"),
        (paths["sock"], "/run/aios/agent.sock"),
        (str(Path.home()), "/home/chronos/user"),
        (f"/Users/{user}" if user else "", "/home/chronos/user"),
        (host, "aios-devbox"),
        (user, "aios"),
        (" staff ", " aios  "),
        (" wheel ", " aios  "),
        (f"{user}:{user}", "aios:aios"),
    ]
    return [(a, b) for a, b in pairs if a]


#: Menlo ne couvre pas ces emoji : on les remplace par des glyphes sûrs.
GLYPH_FIX = {
    "\U0001F527": "»",   # 🔧 outil      -> »
    "⛔": "×",        # ⛔ bloqué      -> ×
    "💬": "«",    # 💬 réponse     -> «
    "⏱": "~",             # ⏱ budget     -> ~
    "💡": "?",         # 💡 idée       -> ?
    "👤": "",          # 👤            -> (retiré)
    "🤖": "@",         # 🤖 mémoire    -> @
    "💭": "·",         # 💭 pensée     -> ·
    "⟶": "->",            # ⟶            -> ->
}


def fix_glyphs(text: str) -> str:
    for src, dst in GLYPH_FIX.items():
        text = text.replace(src, dst)
    return text


def sanitize(text: str, pairs: Sequence[Tuple[str, str]]) -> str:
    text = fix_glyphs(text)
    for src, dst in pairs:
        if src:
            text = text.replace(src, dst)
    # macOS résout /tmp -> /private/tmp, /home -> /System/Volumes/Data/home
    text = re.sub(r"/private/(home|tmp|var|etc)/", r"/\1/", text)
    text = text.replace("/System/Volumes/Data", "")
    text = re.sub(r"/var/folders/[^\s'\"]+", "/tmp", text)
    # le prompt de confirmation n'a pas de retour à la ligne : on en ajoute un
    text = text.replace("Approve? [y/N] ", "Approve? [y/N]\n")
    return text


#: Mémoïse : Menlo ne couvre pas tous les caractères. On détecte ceux qui
#: retomberaient sur un glyphe manquant pour éviter les carrés dans la doc.
_MENLO = ImageFont.truetype(FONT_PATH, FONT_SIZE, index=0)
_GLYPH_OK: Dict[str, bool] = {}


def _glyph_renders(ch: str) -> bool:
    if ch not in _GLYPH_OK:
        probe = Image.new("L", (24, 24), 0)
        ImageDraw.Draw(probe).text((2, 2), ch, font=_MENLO, fill=255)
        notdef = Image.new("L", (24, 24), 0)
        ImageDraw.Draw(notdef).text((2, 2), "\ue000", font=_MENLO, fill=255)
        _GLYPH_OK[ch] = probe.tobytes() != notdef.tobytes()
    return _GLYPH_OK[ch]


def missing_glyphs(text: str) -> List[str]:
    bad = set()
    for ch in text:
        if ord(ch) > 127 and not _glyph_renders(ch):
            bad.add(ch)
    return sorted(bad)


def assert_no_missing(text: str, name: str) -> None:
    bad = missing_glyphs(text)
    if bad:
        shown = " ".join(f"U+{ord(c):04X} {c!r}" for c in bad)
        sys.exit(f"✗ {name} : glyphes absents de Menlo — {shown}")


def assert_anonymous(text: str, name: str) -> None:
    """Aucune fuite d'identité : ni nom d'utilisateur, ni chemin $HOME réel."""
    home = Path.home()
    leaks = []
    if home.name and home.name in text:
        leaks.append(f"nom d'utilisateur {home.name!r}")
    if str(home) in text or f"/Users/{home.name}" in text:
        leaks.append(f"chemin $HOME {str(home)!r}")
    if leaks:
        sys.exit(f"✗ {name} : anonymisation incomplète — {', '.join(leaks)}")


# --------------------------------------------------------------------------
# Exécution
# --------------------------------------------------------------------------
def run(argv: Sequence[str], *, env: Optional[dict] = None,
        stdin: str = "", timeout: int = 90) -> str:
    merged = os.environ.copy()
    merged["PYTHONUNBUFFERED"] = "1"
    merged.pop("AIOS_STATE_DIR", None)
    merged.pop("AIOS_JAIL", None)
    if env:
        merged.update(env)
    proc = subprocess.run(
        list(argv),
        cwd=str(ROOT),
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        env=merged,
        timeout=timeout,
    )
    return proc.stdout or ""


def prompt_of(argv: Sequence[str], override: Optional[str] = None) -> str:
    if override:
        return override
    exe = str(argv[0])
    rel = os.path.relpath(exe, ROOT) if exe.startswith(str(ROOT)) else exe
    rel = rel.replace(".venv/bin/", "").replace("venv/bin/", "")
    return "$ " + " ".join([rel] + [sh_quote(a) for a in argv[1:]])


def sh_quote(arg: str) -> str:
    return arg if re.fullmatch(r"[\w@%+=:,./-]+", arg) else "'" + arg.replace("'", "'\\''") + "'"


def truncate(text: str, max_lines: int) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    kept = lines[:max_lines]
    kept.append(f"… ({len(lines) - max_lines} lignes supplémentaires)")
    return "\n".join(kept) + "\n"


# --------------------------------------------------------------------------
# Captures
# --------------------------------------------------------------------------
def prepare_demo(root: Path) -> Dict[str, str]:
    # Chemins courts : les arguments des outils sont tronqués à l'affichage,
    # un chemin long serait coupé avant le nettoyage et fuiterait tel quel.
    demo = Path("/tmp/aios-demo")
    state = Path("/tmp/aios-state")
    shutil.rmtree(demo, ignore_errors=True)
    shutil.rmtree(state, ignore_errors=True)
    (demo / "src").mkdir(parents=True)
    state.mkdir(parents=True)
    (demo / "notes.md").write_text(
        "# Notes aiOS\n\n- valider la politique de sécurité\n- repenser la jail\n",
        "utf-8")
    (demo / "rapport.txt").write_text(
        "Rapport de build — 130 tests verts.\n", "utf-8")
    (demo / "src" / "main.py").write_text(
        'def main():\n    print("bonjour aiOS")\n', "utf-8")
    return {"demo": str(demo), "state": str(state),
            "sock": "/tmp/aios-agent.sock"}


def common_flags(paths: Dict[str, str]) -> List[str]:
    return ["--brain", "heuristic",
            "--jail", paths["demo"],
            "--audit", str(Path(paths["state"]) / "audit.jsonl")]


def capture_serve(paths: Dict[str, str], san: List[Tuple[str, str]]) -> str:
    sock = paths["sock"]
    argv = [str(AIOS), "serve", "--socket", sock, "--no-confirm"] + common_flags(paths)
    proc = subprocess.Popen(
        argv, cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1",
             "AIOS_STATE_DIR": paths["state"]})
    chunks: List[str] = [prompt_of(argv) + "\n"]
    try:
        time.sleep(1.6)
        startup = [proc.stdout.readline() for _ in range(2)]
        chunks.append("".join(x for x in startup if x))

        ls_cmd = ["ls", "-l", sock]
        chunks.append("\n" + prompt_of(ls_cmd) + "\n")
        chunks.append(run(ls_cmd))

        # l'objectif part du chemin réel : le nettoyage l'anonymise ensuite
        goal = "liste le dossier " + paths["demo"]
        chunks.append("\n$ echo '" + json_goal(goal) + "' | aios-request\n")
        chunks.append(run([str(AIOS_REQUEST), "--socket", sock],
                          stdin=json_goal(goal) + "\n"))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return "".join(chunks)


def json_goal(goal: str) -> str:
    import json as _json
    return _json.dumps({"goal": goal}, ensure_ascii=False)


def main() -> int:
    if not AIOS.exists():
        print("✗ lance d'abord : make deps", file=sys.stderr)
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="aios-shots-"))
    paths = prepare_demo(root)
    san = build_sanitizers(paths)

    # amorçage : alimente le journal d'audit avant la capture « audit »
    seed_goals = [
        "liste le dossier " + paths["demo"],
        "exécute la commande sudo rm -rf /",
        "lis le fichier /etc/passwd",
    ]
    for goal in seed_goals:
        run([str(AIOS), "run", "--no-confirm"] + common_flags(paths) + [goal])

    confirm_file = str(Path(paths["demo"]) / "sortie.txt")

    captures = [
        dict(
            name="01-doctor", title="aios doctor — diagnostic de l'environnement",
            argv=[str(AIOS), "doctor"],
            env={"AIOS_STATE_DIR": paths["state"]},
        ),
        dict(
            name="02-tools", title="aios tools — capacités exposées au modèle",
            argv=[str(AIOS), "tools"],
        ),
        dict(
            name="03-policy", title="aios policy — politique déclarative effective",
            argv=[str(AIOS), "policy"], max_lines=46,
        ),
        dict(
            name="04-run-listing", title="aios run — lecture autorisée sans interruption",
            argv=[str(AIOS), "run", "--no-confirm"] + common_flags(paths)
                 + ["liste le dossier " + paths["demo"]],
        ),
        dict(
            name="05-run-sudo-denied", title="aios run — élévation de privilèges refusée",
            argv=[str(AIOS), "run", "--no-confirm"] + common_flags(paths)
                 + ["exécute la commande sudo rm -rf /"],
        ),
        dict(
            name="06-run-sandbox", title="aios run — tentative de sortie de jail",
            argv=[str(AIOS), "run", "--no-confirm"] + common_flags(paths)
                 + ["lis le fichier /etc/passwd"],
        ),
        dict(
            name="07-confirmation", title="aios chat — confirmation humaine",
            argv=[str(AIOS), "chat", "--once",
                  f'crée un fichier dans {confirm_file} '
                  f'avec le contenu "Bonjour depuis aiOS"']
                 + ["--brain", "heuristic", "--jail", paths["demo"],
                    "--audit", str(Path(paths["state"]) / "audit.jsonl")],
            stdin="y\n",
        ),
        dict(
            name="08-audit", title="aios audit — journal chaîné en SHA-256",
            argv=[str(AIOS), "audit", "--audit",
                  str(Path(paths["state"]) / "audit.jsonl"), "-n", "16"],
        ),
        dict(
            name="08b-audit-verify", title="aios audit --verify — intégrité de la chaîne",
            argv=[str(AIOS), "audit", "--audit",
                  str(Path(paths["state"]) / "audit.jsonl"), "--verify"],
        ),
        dict(
            name="09-tests", title="make test — suite de tests",
            argv=[str(PY), "-m", "pytest", "-q"], max_lines=12,
            prompt_line="$ make test\n",
        ),
    ]

    for spec in captures:
        argv = spec["argv"]
        out = run(argv, env=spec.get("env"), stdin=spec.get("stdin", ""))
        prompt_line = spec.get("prompt_line") or (prompt_of(argv) + "\n")
        body = prompt_line + out
        if spec.get("max_lines"):
            body = truncate(body, spec["max_lines"])
        body = sanitize(body, san)
        assert_no_missing(body, spec["name"])
        assert_anonymous(body, spec["name"])
        image = render_window(hard_wrap(parse_ansi(body), MAX_COLS), spec["title"])
        target = OUT / f"{spec['name']}.png"
        image.save(target, "PNG", optimize=True)
        print(f"  ✓ {target.relative_to(ROOT)}  ({image.size[0]}×{image.size[1]})")

    # capture du service : le dossier redevient celui de la démo
    demo = Path(paths["demo"])
    (demo / "sortie.txt").unlink(missing_ok=True)
    body = sanitize(capture_serve(paths, san), san)
    assert_no_missing(body, "10-serve")
    assert_anonymous(body, "10-serve")
    image = render_window(hard_wrap(parse_ansi(body), MAX_COLS),
                          "aios serve — service AF_UNIX 0600")
    target = OUT / "10-serve.png"
    image.save(target, "PNG", optimize=True)
    print(f"  ✓ {target.relative_to(ROOT)}  ({image.size[0]}×{image.size[1]})")

    shutil.rmtree(root, ignore_errors=True)
    print(f"\n{len(list(OUT.glob('*.png')))} captures dans {OUT.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
