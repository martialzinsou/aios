# Contribuer

---

## 1. Mettre en place

```bash
git clone <dépôt> && cd aiOs
make deps         # venv + pytest + pillow
make check        # 130 tests + lint + politique — doit être vert
```

Prérequis : Python ≥ 3.9. **Aucune dépendance au runtime** — le moteur n'utilise
que la bibliothèque standard.

---

## 2. Règles du dépôt

| Règle | Détail |
|---|---|
| **Langue** | tout le projet est en **français** : code, messages, documentation |
| **`make check` vert** | tests + `compileall` + `bash -n` + cohérence politique |
| **Aucune dépendance runtime** | `pip install` = `.[dev]` seulement |
| **Signature** | le projet et ses documents sont signés **Martial Zinsou** ; les captures masquent en revanche login, hostname et chemins système, et le générateur **échoue** s'il en détecte un |
| **`--trust` interdit** dans les artefacts livrés | réservé aux tests |
| **Politique alignée** | tout changement de `StaticPolicy.default()` impose `make policy` |

### Identité git

Le dépôt est signé par son auteur. La modification est **locale** au dépôt : ta
configuration globale n'est pas touchée.

```bash
git config --local user.name  "Martial Zinsou"
git config --local user.email "<id>+martialzinsou@users.noreply.github.com"
```

---

## 3. Avant de commencer

1. lis [Modèle de sécurité](Modele-de-securite) — en particulier les **12
   invariants** ;
2. cherche une issue ouverte ;
3. pour une faille : signale-la **avant** de proposer la correction
   ([§6](#6--signaler-une-faille)).

---

## 4. Convention de code

```
agent/src/aios_agent/
├── core/        orchestration          français, docstrings en français
├── security/    TCB                    chaque fichier = un invariant
├── tools/       capacités              risque statique obligatoire
├── llm/         modèle local           loopback uniquement
├── server.py    service                I11
└── cli.py       interface              7 sous-commandes
```

- **type hints** sur toute signature publique ;
- **docstrings** expliquant le *pourquoi*, pas le *quoi* ;
- les commentaires ne répètent pas le code ;
- pas de globale mutable, pas d'import circulaire ;
- une fonction = une responsabilité vérifiable.

### Ajouter un outil

```python
class MyTool(Tool):
    name = "my_tool"
    description = "…"                     # affiché au modèle
    risk = Risk.READ                       # plancher, ne peut que monter
    parameters = {"type": "object", "properties": {...}}

    def target(self, args) -> str: ...     # objet de la politique
    def run(self, args, ctx) -> ToolResult:
        path = ctx.jail(args["path"])      # OBLIGATOIRE (I6)
        ...
```

Puis, **dans le même commit** :

1. l'enregistrer dans `tools/__init__.py::default_registry` ;
2. ajouter ses tests dans `agent/tests/test_tools.py` ;
3. vérifier le risque effectif (`test_tools.py`) ;
4. `make check`.

---

## 5. Tester une évolution de sécurité

Toute évolution qui touche à `security/` doit :

| Étape | Commande / fichier |
|---|---|
| prouver l'invariant concerné | un test **nommé d'après** l'invariant |
| ne pas régresser les autres | `make test` (130 tests) |
| garder la politique alignée | `make check-policy` |
| rester documentée | mettre à jour `docs/SECURITY.md` **et** le wiki |

Exemple de structure de test attendue :

```python
def test_i3_deny_returns_before_prompting(audit):
    """I3 : un DENY ne doit jamais solliciter l'humain."""
    ...
```

---

## 6. Signaler une faille

Ouvre une issue **privée** (ou contacte le mainteneur) avec :

1. `aios --version` ;
2. la politique : `aios policy` ;
3. le segment d'audit : `aios audit -n 50` ;
4. les étapes de reproduction.

**N'inclus jamais de credential réel** — le journal les masque, mais vérifie
avant de coller.

Après correction :

```bash
make check
python3 tools/make_screenshots.py     # si la sortie visible a changé
```

---

## 7. Mettre à jour le wiki

Le wiki vit dans `wiki/` :

```
wiki/
├── Home.md  _Sidebar.md  _Footer.md
├── Vue-d-ensemble.md  Architecture.md  Diagrammes-UML.md
├── Documentation-fonctionnelle.md  Documentation-technique.md
├── Modele-de-securite.md  Construction-de-l-OS.md
├── Deploiement-et-exploitation.md  FAQ.md  Contribuer.md
└── captures/*.png          # générées, jamais éditées à la main
```

- les diagrammes sont en **Mermaid** : modifiables dans le dépôt, rendus par le
  wiki ;
- les captures sont régénérées par `python3 tools/make_screenshots.py` ;
- toute modification de CLI, de politique ou d'invariant **doit** être
  répercutée dans la page correspondante.

### Générer le site web

Le wiki sert de source au site *liquid glass* publié sur GitHub Pages :

```bash
npm install     # une fois : jsdom, pour valider la syntaxe Mermaid
make site       # wiki/*.md → site/ (généré, gitignoré)
make check      # tests + lint + politique + diagrammes
```

Toute nouvelle page dans `wiki/` doit aussi être déclarée dans `NAV` et
`CARDS` de `tools/build_site.py` pour apparaître dans la navigation et sur la
page d'accueil.

Les 10 diagrammes Mermaid sont validés avec le **vrai parseur**
(`tools/check_diagrams.js`) : un type inconnu — par exemple
`usecaseDiagram`, qui n'existe que chez PlantUML — fait échouer `make check`.

---

## 8. Vérification finale

```bash
make check                        # 130 tests + lint + politique + diagrammes
python3 tools/make_screenshots.py # captures à jour (échoue si fuite d'identité)
make site                         # site/ régénéré
git status                        # rien d'oublié
```

Ordre conseillé pour un commit : code → tests → politique (`make policy`) →
documentation → captures.

---

> **Martial Zinsou** · BSD-3-Clause · 2026
