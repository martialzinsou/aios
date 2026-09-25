#!/usr/bin/env python3
"""Génère le site statique « liquid glass » d'aiOS depuis le wiki Markdown.

Sources : wiki/*.md + wiki/captures/*.png + web/assets/*
Cibles  : site/ (gitignoré, construit en local par `make site` et en CI par
          le workflow GitHub Pages)

    python3 tools/build_site.py            # construit site/
    python3 tools/build_site.py --check    # valide les diagrammes Mermaid

Fait partie du dépôt aiOS — BSD-3-Clause.
"""
from __future__ import annotations

import html
import json
import re
import shutil
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "wiki"
WEB = ROOT / "web" / "assets"
OUT = ROOT / "site"

# Chemin de base relativement au fichier produit (site est plat).
SITE_TITLE = "aiOS"

NAV: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "Documentation",
        [
            ("Vue d'ensemble", "Vue-d-ensemble"),
            ("Architecture", "Architecture"),
            ("Documentation fonctionnelle", "Documentation-fonctionnelle"),
            ("Interface graphique", "Interface-graphique"),
            ("Documentation technique", "Documentation-technique"),
            ("Diagrammes UML", "Diagrammes-UML"),
        ],
    ),
    (
        "Sécurité",
        [
            ("Modèle de sécurité", "Modele-de-securite"),
            ("Construction de l'OS", "Construction-de-l-OS"),
            ("Déploiement et exploitation", "Deploiement-et-exploitation"),
        ],
    ),
    (
        "Projet",
        [
            ("Contribuer", "Contribuer"),
            ("FAQ", "FAQ"),
            ("Auteur", "Auteur"),
        ],
    ),
]

ORDER: list[str] = ["Home"] + [slug for _, items in NAV for _, slug in items]

CARDS: dict[str, tuple[str, str]] = {
    "Vue-d-ensemble": ("◈", "Ce qu'est aiOS, le périmètre complet, l'agent, la couche OS et la boucle de sécurité."),
    "Architecture": ("⬡", "Moteur, mémoire, outils, service, couches système et points d'intégration Chromium OS."),
    "Documentation-fonctionnelle": ("⌨", "Commandes `aios run|chat|tools|policy|doctor|serve|ui|audit` et comportements observables."),
    "Interface-graphique": ("◐", "Le bureau en verre liquide : six vues, confirmation humaine dans le navigateur, loopback + jeton."),
    "Documentation-technique": ("⚙", "Sources, build, tests, captures régénérées, conventions et IDs de code."),
    "Diagrammes-UML": ("⤫", "10 diagrammes Mermaid : classes, séquences, états, composants, déploiement."),
    "Modele-de-securite": ("⛨", "12 invariants I1–I12, moindre privilège, confirmation humaine, fail-closed."),
    "Construction-de-l-OS": ("▣", "Image Chromium OS, overlay ebuild, scripts 00→40, manifest, tests de build."),
    "Deploiement-et-exploitation": ("⌂", "Installation, service upstart, journaux, rotation, sauvegarde et dépannage."),
    "Contribuer": ("✦", "Règles du dépôt, identité git, ordre de commit et chantiers ouverts."),
    "FAQ": ("?", "Questions fréquentes sur l'installation, la sécurité, le build et les captures."),
    "Auteur": ("✦", "Parcours et philosophie de l'inventeur d'aiOS."),
}

MERMAID_RE = re.compile(r"```mermaid[ \t]*\n(.*?)```", re.S)
TOKEN_RE = re.compile(r"@@MERMAID_(\d+)@@")
HREF_RE = re.compile(r'href="([^"]+)"')
SRC_RE = re.compile(r'src="([^"]+)"')
HEADING_RE = re.compile(r"<h([23])[^>]*id=\"([^\"]+)\"[^>]*>(.*?)</h\1>", re.S)
TAG_RE = re.compile(r"<[^>]+>")

MERMAID_TYPES = {
    "sequenceDiagram", "flowchart", "graph", "classDiagram", "stateDiagram",
    "stateDiagram-v2", "erDiagram", "journey", "gantt", "pie", "quadrantChart",
    "requirementDiagram", "gitGraph", "mindmap", "timeline",
    "block-beta", "sankey-beta", "xychart-beta",
}


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", name.lower()).strip("-")


def extract_diagrams(text: str) -> tuple[str, list[str]]:
    blocks: list[str] = []

    def repl(m: re.Match) -> str:
        blocks.append(m.group(1))
        return f"\n\n@@MERMAID_{len(blocks) - 1}@@\n\n"

    return MERMAID_RE.sub(repl, text), blocks


def validate_diagrams(blocks: list[str]) -> list[str]:
    errors = []
    for i, raw in enumerate(blocks):
        body = raw.strip("\n")
        if not body.strip():
            errors.append(f"diagramme {i}: bloc vide")
            continue
        head = body.strip().split()[0]
        if head not in MERMAID_TYPES:
            errors.append(f"diagramme {i}: type inconnu « {head} »")
        if "```" in body:
            errors.append(f"diagramme {i}: ``` à l'intérieur du bloc")
        if len(body) < 12:
            errors.append(f"diagramme {i}: diagramme anormalement court")
    return errors


def diagram_html(source: str, index: int) -> str:
    safe = html.escape(source, quote=True)
    label = "UML"
    first = source.strip().split()
    if first and first[0] not in ("classDiagram", "stateDiagram", "stateDiagram-v2",
                                  "sequenceDiagram", "erDiagram"):
        label = "Schéma"
    return (
        f'<div class="diagram" id="diagramme-{index}">'
        f'<span class="dlabel">{label}</span>'
        f'<pre class="mermaid">{safe}</pre>'
        f'<pre class="source"><code>{safe}</code></pre>'
        f'<div class="dtools">'
        f'<button type="button" data-act="src">Voir la source</button>'
        f'<button type="button" data-act="dl">Télécharger SVG</button>'
        f"</div></div>"
    )


def rewrite_links(body: str) -> str:
    def href(m: re.Match) -> str:
        url = m.group(1)
        if url.startswith(("http://", "https://", "mailto:", "#", "/")):
            return m.group(0)
        anchor = ""
        if "#" in url:
            url, anchor = url.split("#", 1)
            anchor = "#" + anchor
        if url.lower().endswith(".md"):
            url = url[:-3]
        if not url:
            return f'href="{anchor or "#"}"'
        if not url.lower().endswith((".html", ".png", ".jpg", ".svg", ".css", ".js", ".json")):
            url = slugify(url) + ".html"
        return f'href="{url}{anchor}"'

    body = HREF_RE.sub(href, body)

    def src(m: re.Match) -> str:
        url = m.group(1)
        if url.startswith(("http://", "https://", "data:")):
            return m.group(0)
        return f'src="assets/captures/{url.split("/")[-1]}"'

    return SRC_RE.sub(src, body)


def build_toc(body: str) -> str:
    items = []
    for level, ident, inner in HEADING_RE.findall(body):
        text = TAG_RE.sub("", inner).strip()
        if not text:
            continue
        indent = "" if level == "2" else ' style="margin-left:18px"'
        items.append(f'<li{indent}><a href="#{ident}">{text}</a></li>')
    if len(items) < 3:
        return ""
    return (
        '<details class="tocbar"><summary>Sommaire</summary><ol>'
        + "".join(items)
        + "</ol></details>"
    )


def build_nav(current: str) -> str:
    def link(url: str, title: str) -> str:
        cls = ' class="active"' if current == url[: -len(".html")] else ""
        return f'<a href="{url}"{cls}>{title}</a>'

    parts = ["<h4>aiOS</h4><nav>", link("index.html", "Accueil"),
             link("home.html", "Présentation")]
    for section, items in NAV:
        parts.append("</nav>")
        parts.append(f"<h4>{section}</h4><nav>")
        for title, slug in items:
            parts.append(link(f"{slugify(slug)}.html", title))
    parts.append("</nav>")
    parts.append(
        '<div class="signoff">Documentation du projet.<br>'
        "<strong>Martial Zinsou</strong> · BSD-3-Clause · 2026</div>"
    )
    return "".join(parts)


def pager_html(slug: str) -> str:
    if slug not in ORDER:
        return ""
    i = ORDER.index(slug)
    prev = ORDER[i - 1] if i > 0 else None
    nxt = ORDER[i + 1] if i + 1 < len(ORDER) else None

    def label(s: str) -> str:
        if s == "Home":
            return "Accueil"
        for _, items in NAV:
            for title, sl in items:
                if slugify(sl) == slugify(s):
                    return title
        return s

    out = ['<nav class="pager">']
    if prev:
        ps = "index" if prev == "Home" else slugify(prev)
        url = "index.html" if prev == "Home" else f"{ps}.html"
        out.append(f'<a href="{url}"><small>← Précédent</small><b>{label(prev)}</b></a>')
    else:
        out.append("<span></span>")
    if nxt:
        ns = "index" if nxt == "Home" else slugify(nxt)
        url = "index.html" if nxt == "Home" else f"{ns}.html"
        out.append(f'<a class="next" href="{url}"><small>Suivant →</small><b>{label(nxt)}</b></a>')
    out.append("</nav>")
    return "".join(out)


def shell(current: str, title: str, body: str, desc: str = "") -> str:
    return f"""<!doctype html>
<html lang="fr" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — {SITE_TITLE}</title>
<meta name="description" content="{html.escape(desc or SITE_TITLE)}">
<meta name="author" content="Martial Zinsou">
<meta name="theme-color" content="#05060b">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E%3Cstop offset='0' stop-color='%237c8cff'/%3E%3Cstop offset='1' stop-color='%2337e6d4'/%3E%3C/linearGradient%3E%3C/defs%3E%3Ccircle cx='16' cy='16' r='14' fill='url(%23g)'/%3E%3C/svg%3E">
<link rel="stylesheet" href="assets/glass.css"></head>
<body>
<div class="aurora"></div>
<div class="grain"></div>

<header class="topbar glass">
  <button id="menu-toggle" class="iconbtn menubtn" type="button" aria-label="Menu">☰</button>
  <a class="brand" href="index.html">
    <span class="orb"></span>
    <span>aiOS<small>Martial Zinsou · 2026</small></span>
  </a>
  <span class="spacer"></span>
  <button id="search-btn" class="iconbtn hide-sm" type="button">Rechercher <kbd>⌘K</kbd></button>
  <a class="iconbtn hide-sm" href="https://github.com/martialzinsou/aios" target="_blank" rel="noopener">GitHub</a>
  <button id="theme-toggle" class="iconbtn" type="button" aria-label="Thème">☾</button>
</header>

<div class="shell">
  <aside class="rail glass" id="rail">{build_nav(current)}</aside>
  <main class="content glass" id="main">
{body}
  </main>
</div>

<div class="overlay" id="search-overlay">
  <div class="searchbox glass">
    <input id="search-input" type="search" placeholder="Rechercher dans la documentation…" autocomplete="off">
    <div class="results" id="search-results"></div>
  </div>
</div>

<div class="lightbox" id="lightbox">
  <button class="iconbtn close" type="button">✕</button>
  <img alt="">
</div>

<script src="assets/mermaid.min.js" onerror="var s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js';document.head.appendChild(s);"></script>
<script src="assets/app.js"></script>
</body>
</html>
"""


def read_pages() -> list[tuple[str, str, str]]:
    """(slug, nom de fichier sans extension, markdown)"""
    pages = []
    for path in sorted(WIKI.glob("*.md")):
        if path.stem.startswith("_"):
            continue
        pages.append((slugify(path.stem), path.stem, path.read_text(encoding="utf-8")))
    return pages


def convert(md_text: str) -> tuple[str, list[str], list[str]]:
    text, blocks = extract_diagrams(md_text)
    body = markdown.markdown(
        text,
        extensions=["extra", "toc", "sane_lists", "md_in_html"],
        extension_configs={"toc": {"permalink": False, "toc_depth": "2-3"}},
        output_format="html5",
    )
    errors = validate_diagrams(blocks)
    for i, raw in enumerate(blocks):
        token = f"@@MERMAID_{i}@@"
        rendered = diagram_html(raw.strip("\n"), i)
        if f"<p>{token}</p>" in body:
            body = body.replace(f"<p>{token}</p>", rendered)
        else:
            body = body.replace(token, rendered)
    body = rewrite_links(body)
    headings = [TAG_RE.sub("", t).strip() for _, _, t in HEADING_RE.findall(body)]
    return body, headings, errors


def snippet_of(body: str, limit: int = 170) -> str:
    text = TAG_RE.sub(" ", body)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def build() -> int:
    pages = read_pages()
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets" / "captures").mkdir(parents=True)

    # --- assets -------------------------------------------------------
    for name in ("glass.css", "app.js"):
        shutil.copyfile(WEB / name, OUT / "assets" / name)
    mermaid_src = WEB / "mermaid.min.js"
    if mermaid_src.exists():
        shutil.copyfile(mermaid_src, OUT / "assets" / "mermaid.min.js")
    else:
        print("! site/assets/mermaid.min.js absent — chargement CDN de secours",
              file=sys.stderr)

    caps = sorted((WIKI / "captures").glob("*.png"))
    for p in caps:
        shutil.copyfile(p, OUT / "assets" / "captures" / p.name)

    # --- pages --------------------------------------------------------
    index: list[dict] = []
    bodies: dict[str, str] = {}
    errors: list[str] = []

    for slug, stem, md_text in pages:
        body, headings, errs = convert(md_text)
        errors += [f"{stem}: {e}" for e in errs]
        bodies[slug] = body
        toc = build_toc(body)
        sig = (
            '<footer class="pagefoot">'
            '<span class="sig">Martial Zinsou</span>'
            '<span>aiOS · BSD-3-Clause · 2026</span>'
            f'<a href="https://github.com/martialzinsou/aios">github.com/martialzinsou/aios</a>'
            "</footer>"
        )
        pager = pager_html(stem if stem != "Home" else "Home")
        title = headings[0] if headings and slug != slugify(headings[0]) else (headings[0] if headings else stem)
        if headings:
            first_h1 = TAG_RE.sub("", re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S).group(1)) if "<h1" in body else stem
            title = first_h1.strip()
        if title == SITE_TITLE:
            title = "Présentation"
        page_body = toc + body + sig + pager
        html_doc = shell(slug, title, page_body, snippet_of(body, 150))
        (OUT / f"{slug}.html").write_text(html_doc, encoding="utf-8")
        index.append({
            "title": title,
            "url": f"{slug}.html",
            "headings": headings[1:8],
            "snippet": snippet_of(body),
        })

    # --- index --------------------------------------------------------
    cards = []
    for _, items in NAV:
        for label, slug in items:
            s = slugify(slug)
            glyph, desc = CARDS.get(slug, ("◇", snippet_of(bodies.get(s, ""), 120)))
            cards.append(
                f'<a class="card glass sweep" href="{s}.html">'
                f'<span class="glyph">{glyph}</span>'
                f"<h3>{label}</h3><p>{desc}</p>"
                f'<span class="more">Lire la page →</span></a>'
            )

    shots = []
    for i, p in enumerate(caps):
        name = p.stem.replace("-", " ")
        lazy = ' loading="lazy"' if i > 2 else ""
        shots.append(
            f'<figure class="shot"><img src="assets/captures/{p.name}" alt="{name}"{lazy}>'
            f"<figcaption><b>{name}</b><span>{p.name}</span></figcaption></figure>"
        )

    hero = f"""<section class="hero glass">
  <span class="eyebrow"><i></i>Documentation officielle · 100 % on-device</span>
  <h1>L'OS de demain,<br><em>assisté par un agent local.</em></h1>
  <p class="lead">aiOS est un système basé sur Chromium OS augmenté d'un assistant
  agentique : raisonnement local, moindre privilège, confirmation humaine obligatoire
  et journal d'audit inviolable. Aucune donnée ne quitte la machine.</p>
  <div class="cta">
    <a class="btn primary" href="vue-d-ensemble.html">Commencer par la vue d'ensemble</a>
    <a class="btn ghost" href="diagrammes-uml.html">Voir les 10 diagrammes UML</a>
  </div>
  <div class="stats">
    <div class="stat"><b>130</b><span>tests pytest</span></div>
    <div class="stat"><b>12</b><span>invariants I1–I12</span></div>
    <div class="stat"><b>9</b><span>outils contrôlés</span></div>
    <div class="stat"><b>10</b><span>diagrammes UML</span></div>
    <div class="stat"><b>18</b><span>captures régénérées</span></div>
    <div class="stat"><b>0</b><span>appel réseau sortant</span></div>
  </div>
</section>

<div class="section-title"><h2>Documentation</h2><span>12 pages · wiki complet</span></div>
<div class="cards">{''.join(cards)}</div>

<div class="section-title"><h2>Captures d'écran</h2><span>générées par <code>tools/make_screenshots.py</code> et <code>tools/make_ui_screenshots.py</code></span></div>
<div class="shots">{''.join(shots)}</div>

<footer class="pagefoot">
  <span class="sig">Martial Zinsou</span>
  <span>aiOS · BSD-3-Clause · 2026 · Tout reste sur la machine.</span>
  <a href="https://github.com/martialzinsou/aios">github.com/martialzinsou/aios</a>
</footer>"""

    (OUT / "index.html").write_text(shell("index", "Documentation", hero,
                                          "Documentation d'aiOS — OS Chromium OS + agent IA local, par Martial Zinsou"),
                                     encoding="utf-8")

    (OUT / "search-index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")

    print(f"site/ : {len(pages)} pages, {len(caps)} captures, "
          f"{sum(len(HEADING_RE.findall(b)) for b in bodies.values())} titres")
    if errors:
        print("\n".join("ERREUR " + e for e in errors), file=sys.stderr)
        return 1
    return 0


def check() -> int:
    errors = []
    total = 0
    for path in sorted(WIKI.glob("*.md")):
        if path.stem.startswith("_"):
            continue
        _, blocks = extract_diagrams(path.read_text(encoding="utf-8"))
        total += len(blocks)
        errors += [f"{path.name}: {e}" for e in validate_diagrams(blocks)]
    print(f"{total} diagrammes Mermaid contrôlés (structure)")
    if errors:
        print("\n".join("ERREUR " + e for e in errors), file=sys.stderr)
        return 1

    # Validation syntaxique réelle avec le parseur Mermaid (node + jsdom).
    node = shutil.which("node")
    script = ROOT / "tools" / "check_diagrams.js"
    if node and script.exists() and (ROOT / "node_modules" / "jsdom").exists():
        import subprocess
        proc = subprocess.run([node, str(script)], cwd=str(ROOT))
        if proc.returncode != 0:
            return proc.returncode
    else:
        print("(parseur Mermaid absent : `npm install` pour la validation syntaxique)")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(check())
    sys.exit(build())
