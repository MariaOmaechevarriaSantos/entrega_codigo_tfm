# Atajos de operación — Fase P5, bloque 8B (punto 6) y bloque 9 (docs-pdf).
#   make            -> lista los objetivos
#   make <objetivo>
#
# Los objetivos de docker asumen `docker compose` v2. Los de Python usan el
# .venv del proyecto (créalo con: python -m venv .venv && .venv/bin/pip
# install -r requirements.txt -r requirements-dev.txt).

.DEFAULT_GOAL := help
SHELL := /bin/bash
VENV_PY := .venv/bin/python

# Carpeta de PDF de documentación (FUERA del repo: junto a los PDF de las
# fases anteriores). Sobreescribible: `make docs-pdf DOCS_PDF_DIR=/otra/ruta`.
DOCS_PDF_DIR ?= $(abspath $(CURDIR)/../../Documentacion)
DOCS_PDF_SRC := docs/p5/README_P5_API_Streamlit.md

# Identidad de la build para GET /version (bloque 8B): se calcula aquí y se
# exporta, no se pide a mano. `docker compose` la recoge en build.args.
export GIT_SHA        := $(shell git rev-parse --short=12 HEAD 2>/dev/null || echo desconocido)
export BUILD_DATE     := $(shell date -u +%Y-%m-%dT%H:%M:%SZ)
export MANIFEST_SHA256 := $(shell sha256sum artifacts.manifest.json 2>/dev/null | cut -d' ' -f1 || echo desconocido)

.PHONY: help dev test test-all up down data lint clean docs-pdf

help: ## Lista los objetivos disponibles
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-9s\033[0m %s\n", $$1, $$2}'

dev: ## Levanta API + dashboard con el override de desarrollo (bind-mounts, editar sin rebuild)
	docker compose up --build

test: ## Suite rápida: pytest tests/ (no carga el grafo real de data/processed)
	$(VENV_PY) -m pytest tests/ -q

test-all: ## Suite completa, incluido el test @slow del grafo real de Madrid (--runslow)
	$(VENV_PY) -m pytest tests/ --runslow -q

up: ## Arranque EXACTO del tribunal (docker-compose.yml, ignora el override) con /version poblado
	docker compose -f docker-compose.yml up --build

down: ## Para y elimina los contenedores (conserva el volumen de datos processed_data)
	docker compose down

data: ## Regenera la DuckDB reducida y el tarball de artefactos + artifacts.manifest.json
	$(VENV_PY) scripts/reducir_duckdb.py
	$(VENV_PY) scripts/empaquetar_artefactos.py

lint: ## ruff (E9 + pyflakes) sobre el código propio: app/ scripts/ docker/ tests/
	.venv/bin/ruff check app/ scripts/ docker/ tests/

clean: ## Borra cachés de Python/pytest/ruff (no toca dist/, ni el volumen, ni las imágenes)
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage

DOCS_PDF_OUT := TFM__API_STREAMLIT.pdf

docs-pdf: ## Regenera el PDF de la fase P5 en ../../Documentacion (DOCS_PDF_DIR); necesita pandoc + xelatex
	@command -v pandoc  >/dev/null || { echo "ERROR: falta 'pandoc' (apt install pandoc)"; exit 1; }
	@command -v xelatex >/dev/null || { echo "ERROR: falta 'xelatex' (apt install texlive-xetex texlive-fonts-recommended)"; exit 1; }
	@mkdir -p "$(DOCS_PDF_DIR)"
	@set -e; \
	 sha=$$(git rev-parse --short=12 HEAD 2>/dev/null || echo desconocido); \
	 rama=$$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo desconocido); \
	 fecha=$$(date +%Y-%m-%d); \
	 tmp=$$(mktemp --suffix=.md); hdr=$$(mktemp --suffix=.tex); \
	 trap 'rm -f "$$tmp" "$$hdr"' EXIT; \
	 { printf -- '\\usepackage{fvextra}\n'; \
	   printf -- '\\DefineVerbatimEnvironment{Highlighting}{Verbatim}{breaklines,breakanywhere,fontsize=\\footnotesize,commandchars=\\\\\\{\\}}\n'; \
	   printf -- '\\DefineVerbatimEnvironment{verbatim}{Verbatim}{breaklines,breakanywhere,fontsize=\\footnotesize}\n'; \
	   printf -- '\\fvset{breaklines=true,breakanywhere=true}\n'; \
	   printf -- '\\usepackage{newunicodechar}\n'; \
	   printf -- '\\newunicodechar{\xe2\x86\x92}{$$\\rightarrow$$}\n'; \
	   printf -- '\\newunicodechar{\xe2\x86\x90}{$$\\leftarrow$$}\n'; \
	   printf -- '\\newunicodechar{\xe2\x89\xa5}{$$\\geq$$}\n'; \
	   printf -- '\\newunicodechar{\xe2\x89\xa4}{$$\\leq$$}\n'; \
	   printf -- '\\newunicodechar{\xe2\x89\x88}{$$\\approx$$}\n'; \
	   printf -- '\\newunicodechar{\xe2\x96\xb6}{\\textgreater}\n'; \
	   printf -- '\\newunicodechar{\xe2\x96\xbc}{v}\n'; \
	 } > "$$hdr"; \
	 { printf -- '---\n'; \
	   printf -- 'title: "Integración de API y dashboard, y empaquetado (P5)"\n'; \
	   printf -- 'date: "TFM en Big Data, Ciencia de Datos e IA (UCM). Rutas de emergencia, Cuerpo de Bomberos de Madrid. Documento generado el %s a partir del commit %s (rama %s)."\n' "$$fecha" "$$sha" "$$rama"; \
	   printf -- '---\n\n'; \
	   tail -n +2 "$(DOCS_PDF_SRC)" \
	     | sed -E -e 's/^(#{2,4}) [0-9]+(\.[0-9]+)*\.?[[:space:]]+/\1 /' \
	           -e 's/\xe2\x9c\x93/OK/g' -e 's/\xe2\x98\xb0/[menu]/g' \
	           -e 's/\xf0\x9f\x9f\xa1/[amarillo]/g' -e 's/\xf0\x9f\x9f\xa0/[naranja]/g'; \
	 } > "$$tmp"; \
	 pandoc "$$tmp" -o "$(DOCS_PDF_DIR)/$(DOCS_PDF_OUT)" \
	   --pdf-engine=xelatex --number-sections --shift-heading-level-by=-1 \
	   --no-highlight -H "$$hdr" \
	   -V papersize=a4 -V geometry:margin=3cm -V fontsize=11pt \
	   -V mainfont="Latin Modern Roman" -V sansfont="Latin Modern Sans" \
	   -V monofont="DejaVu Sans Mono" -V monofontoptions="Scale=0.78" \
	   -V colorlinks=true -V linkcolor=black -V urlcolor=black; \
	 echo "PDF -> $(DOCS_PDF_DIR)/$(DOCS_PDF_OUT)"
