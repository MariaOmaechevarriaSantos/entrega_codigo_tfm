"""Tests para la instrumentación de conteo de nodos explorados de §3.5.
No se testea con el grafo real de Madrid (demasiado lento/no disponible en
CI) — solo la lógica de conteo y comparación sobre grafos sintéticos."""
import networkx as nx
import pytest

from routing.benchmark_astar import comparar_par


def _graph_con_alternativa() -> nx.DiGraph:
    """
    0 -> 1 -> 2: camino corto (200m).
    0 -> 3 -> 2: camino alternativo más largo (300m).
    Igual que en test_optimizer.py, para que A* tenga margen real de explorar
    menos nodos que Dijkstra gracias a la heurística.
    """
    G = nx.DiGraph()
    G.add_node(0, x=0.0, y=0.0)
    G.add_node(1, x=100.0, y=0.0)
    G.add_node(2, x=200.0, y=0.0)
    G.add_node(3, x=100.0, y=100.0)
    G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
    G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
    G.add_edge(0, 3, length_m=150.0, travel_time_s=20.0, width_m=5.0, zona="Centro")
    G.add_edge(3, 2, length_m=150.0, travel_time_s=20.0, width_m=5.0, zona="Centro")
    return G


class TestCompararPar:
    def test_devuelve_una_fila_por_algoritmo_con_las_columnas_esperadas(self):
        G = _graph_con_alternativa()
        filas = comparar_par(G, 0, 2)
        assert {f["algoritmo"] for f in filas} == {"dijkstra", "astar"}
        for fila in filas:
            assert set(fila.keys()) == {
                "algoritmo", "nodos_explorados", "tiempo_ms", "length_m", "time_min",
                "ruta_completa", "distancia_restante_destino_m",
            }
            assert fila["nodos_explorados"] > 0
            assert fila["tiempo_ms"] >= 0

    def test_dijkstra_y_astar_coinciden_en_length_m_y_time_min(self):
        G = _graph_con_alternativa()
        filas = comparar_par(G, 0, 2)
        por_algoritmo = {f["algoritmo"]: f for f in filas}
        assert por_algoritmo["dijkstra"]["length_m"] == pytest.approx(por_algoritmo["astar"]["length_m"])
        assert por_algoritmo["dijkstra"]["time_min"] == pytest.approx(por_algoritmo["astar"]["time_min"])

    def test_sin_ruta_posible_devuelve_ruta_parcial_sin_crashear(self):
        G = _graph_con_alternativa()
        G.add_node(99, x=999.0, y=999.0)  # nodo aislado, sin aristas
        filas = comparar_par(G, 0, 99)
        for fila in filas:
            assert fila["ruta_completa"] is False
            assert fila["length_m"] is not None  # ruta parcial hasta el nodo más cercano
            assert fila["distancia_restante_destino_m"] > 0
            assert fila["nodos_explorados"] > 0
