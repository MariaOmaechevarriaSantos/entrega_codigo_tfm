"""
Tests de la infraestructura de operabilidad — Fase P5, bloque 8B
(puntos 4 y 6): los disparadores de GitHub Actions y el Makefile.

Son ficheros de configuración, no código, pero tienen invariantes que el
usuario pidió explícitamente ("restringe los disparadores para no gastar
minutos", "cada objetivo documentado con una línea") y que conviene que se
pongan en rojo si alguien los rompe sin querer.
"""
import re
from pathlib import Path

import pytest
import yaml

RAIZ = Path(__file__).resolve().parent.parent

OBJETIVOS_MAKE = {"help", "dev", "test", "test-all", "up", "down", "data", "lint", "clean"}


@pytest.fixture(scope="module")
def makefile():
    return (RAIZ / "Makefile").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tests_wf():
    return yaml.safe_load((RAIZ / ".github/workflows/tests.yml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def build_wf():
    return yaml.safe_load((RAIZ / ".github/workflows/build.yml").read_text(encoding="utf-8"))


# ───────────────────────────── Makefile ─────────────────────────────
class TestMakefile:
    def test_estan_los_objetivos_pedidos_y_cada_uno_con_una_linea_de_ayuda(self, makefile):
        documentados = dict(re.findall(r"^([a-zA-Z_-]+):.*?## (.+)$", makefile, re.MULTILINE))
        assert OBJETIVOS_MAKE.issubset(documentados), OBJETIVOS_MAKE - set(documentados)
        for obj in OBJETIVOS_MAKE:
            assert documentados[obj].strip(), f"{obj} sin texto de ayuda"

    def test_todos_los_objetivos_estan_en_PHONY(self, makefile):
        phony = set(re.search(r"^\.PHONY:\s*(.+)$", makefile, re.MULTILINE).group(1).split())
        assert OBJETIVOS_MAKE <= phony, OBJETIVOS_MAKE - phony

    def test_up_arranca_el_compose_del_tribunal_ignorando_el_override(self, makefile):
        """`up` DEBE pasar -f docker-compose.yml (si no, aplicaría el
        override de desarrollo y no sería el arranque del tribunal)."""
        receta = re.search(r"^up:.*\n((?:\t.*\n)+)", makefile, re.MULTILINE).group(1)
        assert "-f docker-compose.yml" in receta

    def test_data_encadena_reducir_duckdb_antes_de_empaquetar(self, makefile):
        receta = re.search(r"^data:.*\n((?:\t.*\n)+)", makefile, re.MULTILINE).group(1)
        i_red = receta.find("reducir_duckdb.py")
        i_paq = receta.find("empaquetar_artefactos.py")
        assert 0 <= i_red < i_paq, "reducir_duckdb debe ir antes de empaquetar"


# ────────────────────── GitHub Actions (punto 4) ────────────────────
class TestWorkflows:
    def test_la_suite_rapida_corre_en_push_y_en_pr(self, tests_wf):
        # PyYAML parsea la clave `on:` como el booleano True
        assert set(tests_wf[True]) == {"push", "pull_request"}

    def test_la_suite_rapida_ignora_cambios_solo_de_documentacion(self, tests_wf):
        ignore = tests_wf[True]["push"]["paths-ignore"]
        assert "**.md" in ignore and "docs/**" in ignore

    def test_el_build_de_imagenes_SOLO_corre_en_pull_request(self, build_wf):
        """Invariante de coste: construir imágenes es lo caro; no debe
        dispararse en cada push a una rama de trabajo."""
        assert set(build_wf[True]) == {"pull_request"}, build_wf[True]

    def test_el_build_no_publica_en_ningun_registro(self):
        texto = (RAIZ / ".github/workflows/build.yml").read_text(encoding="utf-8")
        assert "push: false" in texto
        assert "docker/login-action" not in texto  # sin credenciales de registro

    def test_ambos_workflows_cancelan_runs_obsoletos(self, tests_wf, build_wf):
        for wf in (tests_wf, build_wf):
            assert wf["concurrency"]["cancel-in-progress"] is True


# ─────────────────── coherencia de requirements ────────────────────
def test_prometheus_client_esta_en_requirements_runtime():
    """/metrics importa prometheus_client -> la imagen de la API lo necesita."""
    runtime = (RAIZ / "requirements-runtime.txt").read_text(encoding="utf-8")
    assert re.search(r"^prometheus-client==", runtime, re.MULTILINE)
