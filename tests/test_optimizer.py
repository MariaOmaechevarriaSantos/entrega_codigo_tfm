"""Tests específicos para la lógica de pesos dinámicos del optimizador."""
import pytest
import networkx as nx
from routing.optimizer import (
    _dynamic_weight,
    _heuristica_tiempo,
    calcular_ruta,
    ANCHO_CAMION_REQ,
    GALIBO_REQ,
)


def _edge_data(
    width_m: float, travel_time_s: float = 10.0, zona: str = "Centro", height_m: float = 99.0
) -> dict:
    return {"width_m": width_m, "travel_time_s": travel_time_s, "zona": zona, "height_m": height_m}


class TestDynamicWeight:
    def test_calle_estrecha_retorna_infinito(self):
        d = _edge_data(width_m=ANCHO_CAMION_REQ - 0.1)
        assert _dynamic_weight(0, 1, d, {}) == float("inf")

    def test_calle_justa_retorna_tiempo_base(self):
        d = _edge_data(width_m=ANCHO_CAMION_REQ)
        w = _dynamic_weight(0, 1, d, {})
        assert w == pytest.approx(10.0)

    def test_trafico_bajo_sin_multiplicador(self):
        d = _edge_data(width_m=5.0, travel_time_s=100.0, zona="Centro")
        w = _dynamic_weight(0, 1, d, {"Centro": 0})
        assert w == pytest.approx(100.0)

    def test_trafico_medio_multiplica_15(self):
        d = _edge_data(width_m=5.0, travel_time_s=100.0, zona="Centro")
        w = _dynamic_weight(0, 1, d, {"Centro": 1})
        assert w == pytest.approx(150.0)

    def test_trafico_alto_multiplica_30(self):
        d = _edge_data(width_m=5.0, travel_time_s=100.0, zona="Centro")
        w = _dynamic_weight(0, 1, d, {"Centro": 2})
        assert w == pytest.approx(300.0)

    def test_zona_desconocida_usa_factor_1(self):
        d = _edge_data(width_m=5.0, travel_time_s=50.0, zona="Desconocida")
        w = _dynamic_weight(0, 1, d, {"Centro": 2})
        assert w == pytest.approx(50.0)

    def test_altura_insuficiente_retorna_infinito(self):
        d = _edge_data(width_m=5.0, height_m=GALIBO_REQ - 0.1)
        assert _dynamic_weight(0, 1, d, {}, galibo_req=GALIBO_REQ) == float("inf")

    def test_altura_justa_retorna_tiempo_base(self):
        d = _edge_data(width_m=5.0, travel_time_s=10.0, height_m=GALIBO_REQ)
        w = _dynamic_weight(0, 1, d, {}, galibo_req=GALIBO_REQ)
        assert w == pytest.approx(10.0)

    def test_filtro_ancho_y_galibo_son_independientes(self):
        # Ancha pero baja: debe bloquear por gálibo aunque la anchura sobre.
        d_baja = _edge_data(width_m=6.0, height_m=3.0)
        assert _dynamic_weight(0, 1, d_baja, {}, ancho_req=3.5, galibo_req=4.0) == float("inf")

        # Estrecha pero alta: debe bloquear por anchura aunque el gálibo sobre.
        d_estrecha = _edge_data(width_m=2.0, height_m=99.0)
        assert _dynamic_weight(0, 1, d_estrecha, {}, ancho_req=3.5, galibo_req=4.0) == float("inf")

        # Ancha y alta: no debe bloquear por ninguno de los dos filtros.
        d_ok = _edge_data(width_m=6.0, height_m=99.0, travel_time_s=15.0)
        assert _dynamic_weight(0, 1, d_ok, {}, ancho_req=3.5, galibo_req=4.0) == pytest.approx(15.0)


class TestCalcularRuta:
    def _simple_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)
        G.add_node(2, x=200.0, y=0.0)
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
        G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
        return G

    def test_ruta_simple_sin_trafico(self):
        G = self._simple_graph()
        result = calcular_ruta(G, 0, 2)
        assert result is not None
        props = result["features"][0]["properties"]
        assert props["length_m"] == pytest.approx(200.0)

    def test_nodo_inexistente_devuelve_none(self):
        G = self._simple_graph()
        result = calcular_ruta(G, 0, 999)
        assert result is None

    def test_trafico_aumenta_tiempo(self):
        G = self._simple_graph()
        result_sin = calcular_ruta(G, 0, 2, traffic_preds={})
        result_con = calcular_ruta(G, 0, 2, traffic_preds={"Centro": 2})
        assert result_con["features"][0]["properties"]["time_s"] > \
               result_sin["features"][0]["properties"]["time_s"]


class TestRutaParcialSinAlternativaValida:
    """
    Bug real encontrado al validar contra el grafo completo de Madrid:
    nx.dijkstra_path/nx.astar_path NO descartan aristas de peso infinito
    cuando son la única forma de llegar al destino — las tratan como "muy
    caras", no como inexistentes, y devuelven esa ruta igualmente.

    Cuando no hay alternativa válida, calcular_ruta ya NO devuelve esa ruta
    inválida (bug), pero tampoco se limita a decir "sin ruta": devuelve la
    ruta hasta el nodo accesible más cercano al destino real, marcada con
    `ruta_completa=False` y `distancia_restante_destino_m` — así el equipo
    sabe hasta dónde puede llegar el camión y cuánto queda a pie/manguera.
    """

    def _graph_sin_alternativa(self, width_bloqueante: float) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)
        G.add_node(2, x=200.0, y=0.0)
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=5.0, height_m=99.0, zona="Centro")
        # única forma de llegar a 2: por una calle bloqueante, sin alternativa.
        G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=width_bloqueante, height_m=99.0, zona="Centro")
        return G

    @pytest.mark.parametrize("algoritmo", ["dijkstra", "astar"])
    def test_calle_estrecha_sin_alternativa_devuelve_ruta_parcial(self, algoritmo):
        G = self._graph_sin_alternativa(width_bloqueante=2.0)
        result = calcular_ruta(G, 0, 2, algoritmo=algoritmo)
        assert result is not None
        props = result["features"][0]["properties"]
        assert props["ruta_completa"] is False
        # Se detiene en el nodo 1 (100,0), el último accesible antes del bloqueo.
        assert props["length_m"] == pytest.approx(100.0)
        # Queda 100m en línea recta hasta el nodo 2 (200,0), el destino real.
        assert props["distancia_restante_destino_m"] == pytest.approx(100.0)

    @pytest.mark.parametrize("algoritmo", ["dijkstra", "astar"])
    def test_galibo_insuficiente_sin_alternativa_devuelve_ruta_parcial(self, algoritmo):
        G = self._graph_sin_alternativa(width_bloqueante=5.0)
        G[1][2]["height_m"] = 3.0  # ancho ok, pero gálibo insuficiente
        result = calcular_ruta(G, 0, 2, algoritmo=algoritmo)
        assert result is not None
        props = result["features"][0]["properties"]
        assert props["ruta_completa"] is False
        assert props["distancia_restante_destino_m"] == pytest.approx(100.0)

    def test_con_alternativa_valida_no_se_ve_afectado(self):
        # Mismo escenario de TestParametrizacionVehiculo: sigue devolviendo
        # la ruta válida cuando SÍ hay alternativa, el fix no debe romperlo.
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)
        G.add_node(2, x=200.0, y=0.0)
        G.add_node(3, x=100.0, y=100.0)
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=4.0, zona="Centro")
        G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=4.0, zona="Centro")
        G.add_edge(0, 3, length_m=150.0, travel_time_s=20.0, width_m=6.0, zona="Centro")
        G.add_edge(3, 2, length_m=150.0, travel_time_s=20.0, width_m=6.0, zona="Centro")
        result = calcular_ruta(G, 0, 2, ancho_req=4.5)
        assert result is not None
        props = result["features"][0]["properties"]
        assert props["length_m"] == pytest.approx(300.0)
        assert props["ruta_completa"] is True
        assert props["distancia_restante_destino_m"] == pytest.approx(0.0)

    def test_elige_el_nodo_mas_cercano_al_destino_no_el_primero_que_encuentra(self):
        """
        0 es el origen. 1 y 3 son ambos accesibles. 2 (el destino real) solo
        es alcanzable a través de 1, por una calle bloqueante. 3 está más
        cerca de 2 en línea recta que 1 — la ruta parcial debe terminar en 3,
        no en 1, aunque 1 sea el nodo "en el camino" hacia 2.
        """
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)      # en el camino "natural" hacia 2
        G.add_node(2, x=200.0, y=0.0)      # destino real, solo vía 1 (bloqueado)
        G.add_node(3, x=190.0, y=10.0)     # mucho más cerca de 2 en línea recta
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
        G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=2.0, zona="Centro")  # bloqueada
        G.add_edge(0, 3, length_m=300.0, travel_time_s=30.0, width_m=5.0, zona="Centro")

        result = calcular_ruta(G, 0, 2)
        props = result["features"][0]["properties"]
        assert props["ruta_completa"] is False
        assert props["length_m"] == pytest.approx(300.0)  # ruta hasta 3, no hasta 1
        dist_3_a_2 = ((190.0 - 200.0) ** 2 + (10.0 - 0.0) ** 2) ** 0.5
        assert props["distancia_restante_destino_m"] == pytest.approx(round(dist_3_a_2, 2))

    def test_origen_totalmente_aislado_bajo_el_filtro(self):
        """Si ni siquiera se puede salir del origen (todas las calles
        adyacentes están bloqueadas), la ruta parcial es de longitud 0 en el
        propio origen — sigue siendo información útil, no un crash."""
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=2.0, zona="Centro")

        result = calcular_ruta(G, 0, 1)
        assert result is not None
        props = result["features"][0]["properties"]
        assert props["ruta_completa"] is False
        assert props["length_m"] == pytest.approx(0.0)
        assert props["distancia_restante_destino_m"] == pytest.approx(100.0)


class TestParametrizacionVehiculo:
    """Cubre §3.1: ancho_req debe poder variar por llamada, no quedar fijado al importar."""

    def _graph_con_alternativa(self) -> nx.DiGraph:
        """
        0 -> 1 -> 2: camino corto (200m) pero estrecho (4.0m).
        0 -> 3 -> 2: camino alternativo más largo (300m) pero ancho (6.0m).
        """
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)
        G.add_node(2, x=200.0, y=0.0)
        G.add_node(3, x=100.0, y=100.0)
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=4.0, zona="Centro")
        G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=4.0, zona="Centro")
        G.add_edge(0, 3, length_m=150.0, travel_time_s=20.0, width_m=6.0, zona="Centro")
        G.add_edge(3, 2, length_m=150.0, travel_time_s=20.0, width_m=6.0, zona="Centro")
        return G

    def test_ancho_req_distinto_por_llamada_en_mismo_proceso(self):
        G = self._graph_con_alternativa()

        result_ancho_normal = calcular_ruta(G, 0, 2, ancho_req=3.5)
        result_ancho_estricto = calcular_ruta(G, 0, 2, ancho_req=4.5)

        assert result_ancho_normal["features"][0]["properties"]["length_m"] == pytest.approx(200.0)
        assert result_ancho_estricto["features"][0]["properties"]["length_m"] == pytest.approx(300.0)

    def test_sin_ancho_req_usa_default_de_modulo(self):
        G = self._graph_con_alternativa()
        result = calcular_ruta(G, 0, 2)
        assert result["features"][0]["properties"]["length_m"] == pytest.approx(200.0)


class TestFiltroGalibo:
    """Cubre §3.2: filtro de gálibo, análogo al de anchura ya existente."""

    def _graph_con_tunel(self) -> nx.DiGraph:
        """
        0 -> 1 -> 2: camino corto (200m) pero con un túnel bajo (height_m=3.5).
        0 -> 3 -> 2: camino alternativo más largo (300m) sin restricción de altura.
        """
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)
        G.add_node(2, x=200.0, y=0.0)
        G.add_node(3, x=100.0, y=100.0)
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=6.0, height_m=3.5, zona="Centro")
        G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=6.0, height_m=3.5, zona="Centro")
        G.add_edge(0, 3, length_m=150.0, travel_time_s=20.0, width_m=6.0, height_m=99.0, zona="Centro")
        G.add_edge(3, 2, length_m=150.0, travel_time_s=20.0, width_m=6.0, height_m=99.0, zona="Centro")
        return G

    def test_tunel_bajo_fuerza_desvio(self):
        G = self._graph_con_tunel()
        result = calcular_ruta(G, 0, 2, galibo_req=4.0)
        assert result["features"][0]["properties"]["length_m"] == pytest.approx(300.0)

    def test_galibo_req_bajo_permite_tunel(self):
        G = self._graph_con_tunel()
        result = calcular_ruta(G, 0, 2, galibo_req=3.0)
        assert result["features"][0]["properties"]["length_m"] == pytest.approx(200.0)


class TestAlgoritmoConfigurable:
    """Cubre §3.5: A* configurable, Dijkstra sigue siendo el default sin cambios."""

    def _simple_graph(self) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)
        G.add_node(2, x=200.0, y=0.0)
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
        G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
        return G

    def test_algoritmo_dijkstra_por_defecto_sin_cambios(self):
        G = self._simple_graph()
        result = calcular_ruta(G, 0, 2)
        assert result["features"][0]["properties"]["length_m"] == pytest.approx(200.0)

    def test_algoritmo_astar_produce_la_misma_ruta_que_dijkstra(self):
        G = self._simple_graph()
        result_dijkstra = calcular_ruta(G, 0, 2, algoritmo="dijkstra")
        result_astar = calcular_ruta(G, 0, 2, algoritmo="astar")
        assert result_astar["features"][0]["properties"]["length_m"] == pytest.approx(
            result_dijkstra["features"][0]["properties"]["length_m"]
        )
        assert result_astar["features"][0]["properties"]["time_s"] == pytest.approx(
            result_dijkstra["features"][0]["properties"]["time_s"]
        )

    def test_algoritmo_astar_respeta_filtro_de_anchura_y_galibo(self):
        G = self._simple_graph()
        G.add_edge(0, 2, length_m=50.0, travel_time_s=5.0, width_m=2.0, zona="Centro")
        G.add_edge(2, 0, length_m=50.0, travel_time_s=5.0, width_m=2.0, zona="Centro")
        result = calcular_ruta(G, 0, 2, algoritmo="astar", ancho_req=3.5)
        # La calle directa es más corta pero demasiado estrecha; debe rodear.
        assert result["features"][0]["properties"]["length_m"] == pytest.approx(200.0)

    def test_algoritmo_invalido_lanza_valueerror(self):
        G = self._simple_graph()
        with pytest.raises(ValueError):
            calcular_ruta(G, 0, 2, algoritmo="bogus")


class TestHeuristicaTiempo:
    """La heurística de A* nunca debe sobreestimar el tiempo real restante."""

    def _graph_con_alternativa(self) -> nx.DiGraph:
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=100.0, y=0.0)
        G.add_node(2, x=200.0, y=0.0)
        G.add_edge(0, 1, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
        G.add_edge(1, 2, length_m=100.0, travel_time_s=10.0, width_m=5.0, zona="Centro")
        return G

    def test_heuristica_nunca_sobreestima_el_tiempo_real(self):
        G = self._graph_con_alternativa()
        heuristica = _heuristica_tiempo(0, 2, G)
        resultado = calcular_ruta(G, 0, 2)
        tiempo_real = resultado["features"][0]["properties"]["time_s"]
        assert heuristica <= tiempo_real

    def test_heuristica_es_cero_en_el_mismo_nodo(self):
        G = self._graph_con_alternativa()
        assert _heuristica_tiempo(0, 0, G) == pytest.approx(0.0)

    def test_heuristica_disminuye_con_velocidad_max_mayor(self):
        G = self._graph_con_alternativa()
        h_lenta = _heuristica_tiempo(0, 2, G, velocidad_max_kph=50.0)
        h_rapida = _heuristica_tiempo(0, 2, G, velocidad_max_kph=100.0)
        assert h_rapida < h_lenta
