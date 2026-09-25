PYTHON ?= python3
VENV   ?= .venv
PY     := $(VENV)/bin/python
PIP    := $(VENV)/bin/pip
AIOS   := PYTHONPATH=agent/src $(PY) -m aios_agent

.PHONY: help venv deps test lint typecheck policy check-policy check \
        check-diagrams site site-check agent-chat agent-tools agent-doctor \
        overlay image vm clean

help:
	@echo "aiOS — cibles disponibles"
	@echo "  make deps          installe l'environnement de dev (pytest)"
	@echo "  make test          lance la suite de tests"
	@echo "  make lint          compilation de tous les sources Python"
	@echo "  make typecheck     mypy (optionnel, si installé)"
	@echo "  make check         test + lint + politique + diagrammes"
	@echo "  make policy        régénère la politique embarquée dans l'image"
	@echo "  make check-policy  vérifie cohérence code ↔ fichier de politique"
	@echo "  make overlay       greffe l'overlay sur le checkout Chromium OS"
	@echo "  make image         build du package puis de l'image"
	@echo "  make vm            démarre l'image dans une VM"
	@echo "  make agent-chat    démarre l'assistant en ligne de commande"

venv:
	@test -d $(VENV) || $(PYTHON) -m venv $(VENV)

deps: venv
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -e ".[dev]" || $(PIP) install -q pytest

test: venv
	$(VENV)/bin/python -m pytest

lint:
	@python3 -m compileall -q agent/src os/scripts \
		|| { echo "échec de la compilation"; exit 1; }
	@bash -n os/scripts/*.sh
	@echo "lint OK"

typecheck:
	@mypy agent/src || echo "mypy non installé (pip install mypy)"

check: test lint check-policy check-diagrams
	@echo "vérifications OK"

check-diagrams:
	@if [ -d node_modules/jsdom ]; then node tools/check_diagrams.js; \
	else echo "check-diagrams sauté (npm install pour valider la syntaxe)"; fi

site:
	$(VENV)/bin/python -c "import markdown" 2>/dev/null || $(PIP) install -q "markdown>=3.5"
	$(PY) tools/build_site.py

site-check:
	$(PY) tools/build_site.py --check

policy:
	PYTHONPATH=agent/src $(PYTHON) -c "import json;from aios_agent.security.policy import StaticPolicy;print(json.dumps(StaticPolicy.default().to_dict(),indent=2))" \
		> os/overlay/chromeos-base/aios-agent/files/aios-policy.json
	@echo "politique régénérée"

check-policy:
	PYTHONPATH=agent/src $(PYTHON) os/scripts/check_policy.py

overlay:
	os/scripts/20-prepare-overlay.sh

image:
	os/scripts/30-build-image.sh

vm:
	os/scripts/40-run-in-vm.sh

agent-chat:
	$(AIOS) chat

agent-tools:
	$(AIOS) tools

agent-doctor:
	$(AIOS) doctor

clean:
	rm -rf .pytest_cache .mypy_cache out site
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
