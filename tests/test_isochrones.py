"""Tests para las isócronas de cobertura (§3.6). Usa grafos sintéticos (una
'rueda' de nodos concéntricos) — no el grafo real de Madrid, demasiado lento
para CI."""
import geopandas as gpd
import networkx as nx

from routing.isochrones import calcular_isocronas


def _grafo_rueda(n_radios: int = 36, radios_m: tuple = (30, 60, 90, 120, 150, 180, 210)) -> nx.DiGraph:
    """
    Origen en (0,0), con `n_radios` direcciones y nodos a distancias
    crecientes `radios_m` en cada una. Velocidad uniforme de 1 m/s
    (travel_time_s == distancia recorrida), así el tiempo acumulado desde
    el origen a un nodo es exactamente su radio en segundos — útil para
    construir isócronas con formas concéntricas bien comportadas.
    `n_radios` alto (36) evita que el concave_hull "muerda" hacia dentro
    entre direcciones angularmente separadas — con pocos radios el hull
    tiende a zigzaguear entre anillos en vez de seguir el anillo exterior.
    """
    import math

    G = nx.DiGraph()
    G.add_node(0, x=0.0, y=0.0)
    nodo_id = 1
    for i in range(n_radios):
        angulo = 2 * math.pi * i / n_radios
        anterior = 0
        radio_anterior = 0.0
        for radio in radios_m:
            x = radio * math.cos(angulo)
            y = radio * math.sin(angulo)
            G.add_node(nodo_id, x=x, y=y)
            dist = radio - radio_anterior
            attrs = {"length_m": dist, "travel_time_s": dist, "width_m": 6.0, "height_m": 99.0, "zona": "Centro"}
            G.add_edge(anterior, nodo_id, **attrs)
            G.add_edge(nodo_id, anterior, **attrs)
            anterior = nodo_id
            radio_anterior = radio
            nodo_id += 1
    return G


class TestCalcularIsocronas:
    def test_isocronas_anidadas_por_tiempo_creciente(self):
        G = _grafo_rueda()
        isocronas = calcular_isocronas(G, 0, cortes_min=[1, 2, 3])

        assert set(isocronas.keys()) == {1, 2, 3}

        # Reproyectar de vuelta a metros para comparar áreas con tolerancia real.
        poligonos_m = {
            corte: gpd.GeoSeries([poly], crs="EPSG:4326").to_crs(epsg=25830).iloc[0]
            for corte, poly in isocronas.items()
        }

        area_fuera_1_de_2 = poligonos_m[1].difference(poligonos_m[2]).area
        area_fuera_2_de_3 = poligonos_m[2].difference(poligonos_m[3]).area
        assert area_fuera_1_de_2 < 1.0, "la isócrona de 1 min debe quedar contenida en la de 2 min"
        assert area_fuera_2_de_3 < 1.0, "la isócrona de 2 min debe quedar contenida en la de 3 min"

        assert poligonos_m[3].area > poligonos_m[2].area > poligonos_m[1].area

    def test_devuelve_poligonos_en_epsg_4326(self):
        G = _grafo_rueda()
        isocronas = calcular_isocronas(G, 0, cortes_min=[1])
        poligono = isocronas[1]
        # Coordenadas en grados, no en metros UTM.
        minx, miny, maxx, maxy = poligono.bounds
        assert -180 <= minx <= 180 and -180 <= maxx <= 180
        assert -90 <= miny <= 90 and -90 <= maxy <= 90

    def test_pocos_nodos_alcanzables_devuelve_buffer_en_vez_de_hull_degenerado(self):
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=10.0, y=0.0)
        G.add_edge(0, 1, length_m=10.0, travel_time_s=10.0, width_m=6.0, height_m=99.0, zona="Centro")
        # Solo 2 nodos alcanzables — por debajo de MIN_NODOS_PARA_HULL.
        isocronas = calcular_isocronas(G, 0, cortes_min=[1])
        poligono_m = gpd.GeoSeries([isocronas[1]], crs="EPSG:4326").to_crs(epsg=25830).iloc[0]
        assert poligono_m.area > 0

    def test_reutiliza_dynamic_weight_de_optimizer_sin_duplicar_logica(self):
        from routing.isochrones import _dynamic_weight as isochrones_weight_fn
        from routing.optimizer import _dynamic_weight as optimizer_weight_fn
        assert isochrones_weight_fn is optimizer_weight_fn

    def test_respeta_filtro_de_anchura(self):
        """Un nodo solo alcanzable por una calle demasiado estrecha no debe
        aparecer en la isócrona."""
        G = nx.DiGraph()
        G.add_node(0, x=0.0, y=0.0)
        G.add_node(1, x=10.0, y=0.0)  # alcanzable, calle ancha
        G.add_node(2, x=1000.0, y=0.0)  # inalcanzable de otro modo, calle estrecha
        G.add_edge(0, 1, length_m=10.0, travel_time_s=5.0, width_m=6.0, height_m=99.0, zona="Centro")
        G.add_edge(1, 2, length_m=10.0, travel_time_s=5.0, width_m=2.0, height_m=99.0, zona="Centro")

        isocronas = calcular_isocronas(G, 0, cortes_min=[5], ancho_req=3.5)
        poligono_m = gpd.GeoSeries([isocronas[5]], crs="EPSG:4326").to_crs(epsg=25830).iloc[0]
        # El polígono debe quedar pegado al origen (radio ~10m), no extenderse a 1000m.
        minx, miny, maxx, maxy = poligono_m.bounds
        assert maxx < 100.0
