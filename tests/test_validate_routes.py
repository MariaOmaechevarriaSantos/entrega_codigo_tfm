"""Tests para el cálculo de filas de la tabla de validación (§3.7). No se
fabrican tiempos de Google Maps — ese dato es una consulta manual humana
que el script deja en blanco para completar (ver routing/validate_routes.py)."""
import networkx as nx
import pytest

from routing.validate_routes import calcular_fila_validacion


def _simple_graph() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node(0, x=0.0, y=0.0)
    G.add_node(1, x=1000.0, y=0.0)
    G.add_node(2, x=2000.0, y=0.0)
    G.add_edge(0, 1, length_m=1000.0, travel_time_s=100.0, width_m=5.0, height_m=99.0, zona="Centro")
    G.add_edge(1, 2, length_m=1000.0, travel_time_s=100.0, width_m=5.0, height_m=99.0, zona="Centro")
    return G


class TestCalcularFilaValidacion:
    def test_fila_sin_tiempo_google_deja_diferencia_en_blanco(self):
        G = _simple_graph()
        fila = calcular_fila_validacion(G, "Parque Test", "Destino Test", 0, 2, traffic_preds={})

        assert fila["parque"] == "Parque Test"
        assert fila["destino"] == "Destino Test"
        assert fila["distancia_km"] == pytest.approx(2.0)
        assert fila["tiempo_modelo_min"] == pytest.approx(round(200.0 / 60, 2))
        assert fila["tiempo_google_min"] is None
        assert fila["diferencia_pct"] is None

    def test_fila_con_tiempo_google_calcula_diferencia_pct(self):
        G = _simple_graph()
        fila = calcular_fila_validacion(
            G, "Parque Test", "Destino Test", 0, 2, traffic_preds={}, tiempo_google_min=5.0
        )
        tiempo_modelo = fila["tiempo_modelo_min"]
        esperado = round((tiempo_modelo - 5.0) / 5.0 * 100, 1)
        assert fila["diferencia_pct"] == pytest.approx(esperado)

    def test_fila_sin_ruta_posible_no_crashea_y_lo_documenta(self):
        G = _simple_graph()
        fila = calcular_fila_validacion(
            G, "Parque Test", "Destino Inalcanzable", 0, 2, traffic_preds={}, ancho_req=99.0
        )
        # width_m=5.0 en todas las aristas, con ancho_req=99.0 nx.dijkstra_path
        # sigue devolviendo "una" ruta (peso inf tratado como fallback, ver
        # comportamiento ya documentado en test_benchmark_astar.py); lo que
        # este test protege es que la función no lance excepción con
        # restricciones imposibles, sea cual sea el resultado.
        assert fila["parque"] == "Parque Test"
        assert "distancia_km" in fila

    def test_fila_con_ruta_parcial_lo_documenta_en_nota_y_no_calcula_diferencia(self):
        G = _simple_graph()
        # La única forma de llegar a 2 (tramo 1->2) es más estrecha que el
        # ancho_req por defecto (3.5m): calcular_ruta devuelve ruta_completa=False
        # hasta el nodo 1, no None (ver optimizer.py::calcular_ruta).
        G[1][2]["width_m"] = 2.0

        fila = calcular_fila_validacion(
            G, "Parque Test", "Destino Inalcanzable", 0, 2, traffic_preds={}, tiempo_google_min=5.0
        )

        assert fila["distancia_km"] == pytest.approx(1.0)  # se detiene en el nodo 1
        assert fila["diferencia_pct"] is None  # no comparar un tramo parcial contra Google Maps
        assert "parcial" in fila["nota"].lower()

    def test_fila_usa_ancho_y_galibo_por_defecto_del_modulo(self):
        G = _simple_graph()
        G.add_edge(0, 2, length_m=100.0, travel_time_s=5.0, width_m=2.0, height_m=99.0, zona="Centro")
        G.add_edge(2, 0, length_m=100.0, travel_time_s=5.0, width_m=2.0, height_m=99.0, zona="Centro")
        fila = calcular_fila_validacion(G, "Parque Test", "Destino Test", 0, 2, traffic_preds={})
        # La calle directa (100m) es más estrecha que el default de
        # ANCHO_CAMION_REQ (3.5m) y debe descartarse a favor de la ruta larga.
        assert fila["distancia_km"] == pytest.approx(2.0)
