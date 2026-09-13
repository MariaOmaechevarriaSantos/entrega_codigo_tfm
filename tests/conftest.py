"""
Configuración común de la suite.

Marca `slow`: tests que cargan el grafo real de Madrid desde
`data/processed/madrid_callejero_filtered.geojson` (~17 s, ~1 GB de RSS).
Quedan EXCLUIDOS por defecto para que `pytest tests/` sea la suite rápida
sin tocar `data/processed/`. Para incluirlos:

    pytest tests/ --runslow
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "slow: carga el grafo real de Madrid (data/processed). Excluido salvo --runslow.",
    )


def pytest_addoption(parser):
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="ejecuta también los tests marcados @pytest.mark.slow (cargan data/processed real)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return
    skip_slow = pytest.mark.skip(reason="necesita --runslow (carga el grafo real de data/processed)")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
