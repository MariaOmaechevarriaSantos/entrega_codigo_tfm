"""Tests unitarios para el motor de grafo y el optimizador."""
import logging

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

import routing.graph_engine as graph_engine
from pipeline.transform.build_graph import build_graph
from routing.optimizer import calcular_ruta


def _simple_gdf() -> gpd.GeoDataFrame:
    """GeoDataFrame mínimo con 3 aristas para tests."""
    lines = [
        LineString([(0, 0), (100, 0)]),   # arista A->B
        LineString([(100, 0), (200, 0)]), # arista B->C
        LineString([(0, 0), (100, 50)]),  # arista A->D (alternativa)
    ]
    data = {
        "geometry": lines,
        "oneway": ["no", "no", "no"],
        "highway": ["primary", "primary", "residential"],
        "maxspeed": [50.0, 50.0, 30.0],
        "width": [6.0, 6.0, 2.0],  # última calle estrecha (< ANCHO_CAMION_REQ)
        "zona": ["Centro", "Centro", "Centro"],
        "junction": [None, None, None],
    }
    return gpd.GeoDataFrame(data, crs="EPSG:25830")


class TestBuildGraph:
    def test_node_count(self):
        G = build_graph(_simple_gdf())
        # 4 nodos únicos: A(0,0), B(100,0), C(200,0), D(100,50)
        assert G.number_of_nodes() == 4

    def test_edge_attributes_present(self):
        G = build_graph(_simple_gdf())
        for u, v, d in G.edges(data=True):
            assert "length_m" in d
            assert "travel_time_s" in d
            assert "width_m" in d
            assert d["travel_time_s"] > 0

    def test_bidirectional_edges(self):
        G = build_graph(_simple_gdf())
        # oneway=no → debe haber aristas en ambas direcciones
        edges = set(G.edges())
        assert len(edges) == 6  # 3 aristas * 2 direcciones


def _gdf_glorieta(junction_value, oneway_value=None) -> gpd.GeoDataFrame:
    """GeoDataFrame de un solo tramo con el junction/oneway indicados."""
    data = {
        "geometry": [LineString([(0, 0), (100, 0)])],
        "oneway": [oneway_value],
        "highway": ["residential"],
        "maxspeed": [30.0],
        "width": [4.0],
        "zona": ["Centro"],
        "junction": [junction_value],
    }
    return gpd.GeoDataFrame(data, crs="EPSG:25830")


class TestJunctionRoundabout:
    def test_junction_roundabout_es_sentido_unico_aunque_oneway_no_este_marcado(self):
        G = build_graph(_gdf_glorieta("roundabout"))
        assert set(G.edges()) == {(0, 1)}

    def test_junction_circular_es_sentido_unico_aunque_oneway_no_este_marcado(self):
        # "circular" es el valor de junction habitual en España para
        # glorietas de sentido único; el mapeador confía en esa semántica y
        # no siempre pone oneway=yes explícito (bug real detectado: 210 de
        # 623 tramos junction=circular en el callejero de Madrid tenían
        # oneway=False). Sin este caso, build_graph añadía arista en ambos
        # sentidos y las rutas podían "atajar" cortando la glorieta.
        G = build_graph(_gdf_glorieta("circular"))
        assert set(G.edges()) == {(0, 1)}

    def test_junction_circular_con_oneway_false_explicito_sigue_siendo_sentido_unico(self):
        G = build_graph(_gdf_glorieta("circular", oneway_value=False))
        assert set(G.edges()) == {(0, 1)}


class TestOptimizer:
    def _build(self):
        gdf = _simple_gdf()
        G = build_graph(gdf)
        return G

    def test_ruta_existente(self):
        G = self._build()
        # A -> C a través de B (calles anchas)
        node_A = 0
        node_C = None
        for nid, d in G.nodes(data=True):
            if round(d["x"]) == 200 and round(d["y"]) == 0:
                node_C = nid
        assert node_C is not None
        result = calcular_ruta(G, node_A, node_C)
        assert result is not None
        assert result["features"][0]["properties"]["length_m"] > 0

    def test_ruta_no_existe_por_anchura(self):
        """Ruta hacia un nodo solo accesible por calle estrecha debe devolver
        una ruta parcial marcada como incompleta, no la ruta bloqueada ni None."""
        G = self._build()
        # Encontrar nodo D (100, 50), solo accesible por calle de 2m
        node_D = None
        for nid, d in G.nodes(data=True):
            if round(d["x"]) == 100 and round(d["y"]) == 50:
                node_D = nid
        assert node_D is not None
        result = calcular_ruta(G, 0, node_D)
        # D solo es accesible por la calle de 2m (< ANCHO_CAMION_REQ), sin
        # alternativa — ver tests/test_optimizer.py::TestRutaParcialSinAlternativaValida.
        # B (100, 0) es el nodo accesible más cercano a D en línea recta (50m).
        assert result is not None
        props = result["features"][0]["properties"]
        assert props["ruta_completa"] is False
        assert props["distancia_restante_destino_m"] == pytest.approx(50.0)

    def test_propiedades_respuesta(self):
        G = self._build()
        result = calcular_ruta(G, 0, 2)  # nodo B en índice interno
        if result:
            props = result["features"][0]["properties"]
            assert "length_m" in props
            assert "time_s" in props
            assert "time_min" in props
            assert props["length_m"] > 0


class TestNodosEspeciales:
    """Cubre §3.4: parques/hospitales conectados como nodos reales en load_graph()."""

    def setup_method(self):
        graph_engine._graph = None
        graph_engine._kdtree = None
        graph_engine._node_items = None

    def teardown_method(self):
        graph_engine._graph = None
        graph_engine._kdtree = None
        graph_engine._node_items = None

    def _write_callejero(self, path):
        _simple_gdf().to_file(path, driver="GeoJSON")

    def _write_puntos(self, path, nombre: str, x: float, y: float):
        gdf = gpd.GeoDataFrame(
            {"nombre": [nombre], "direccion": ["Calle de prueba 1"]},
            geometry=[Point(x, y)],
            crs="EPSG:25830",
        )
        gdf.to_file(path, driver="GeoJSON")

    def test_nodo_especial_queda_conectado_y_localizable_por_nombre(self, tmp_path):
        callejero_path = str(tmp_path / "callejero.geojson")
        parques_path = str(tmp_path / "parques.geojson")
        hospitales_path = str(tmp_path / "hospitales_ausente.geojson")  # no se crea

        self._write_callejero(callejero_path)
        self._write_puntos(parques_path, nombre="Parque Retiro", x=50.0, y=0.0)

        G = graph_engine.load_graph(
            callejero_path=callejero_path,
            parques_path=parques_path,
            hospitales_path=hospitales_path,
        )

        nodo_parque = graph_engine.get_special_node("bomberos", "Parque Retiro")
        assert nodo_parque is not None
        assert G.nodes[nodo_parque]["tipo_nodo"] == "bomberos"

        vecinos = list(G.successors(nodo_parque))
        assert len(vecinos) >= 1, "el nodo especial debe quedar conectado a un nodo de calle"
        nodo_calle = vecinos[0]
        assert nodo_parque in G.successors(nodo_calle), "la conexión debe ser bidireccional"

    def test_get_special_node_con_nombre_inexistente_devuelve_none(self, tmp_path):
        callejero_path = str(tmp_path / "callejero.geojson")
        parques_path = str(tmp_path / "parques.geojson")
        hospitales_path = str(tmp_path / "hospitales_ausente.geojson")

        self._write_callejero(callejero_path)
        self._write_puntos(parques_path, nombre="Parque Retiro", x=50.0, y=0.0)

        graph_engine.load_graph(
            callejero_path=callejero_path,
            parques_path=parques_path,
            hospitales_path=hospitales_path,
        )

        assert graph_engine.get_special_node("bomberos", "Nombre que no existe") is None

    def test_get_nearest_node_nunca_devuelve_un_nodo_especial(self, tmp_path):
        callejero_path = str(tmp_path / "callejero.geojson")
        parques_path = str(tmp_path / "parques.geojson")
        hospitales_path = str(tmp_path / "hospitales_ausente.geojson")

        self._write_callejero(callejero_path)
        self._write_puntos(parques_path, nombre="Parque Retiro", x=50.0, y=0.0)

        graph_engine.load_graph(
            callejero_path=callejero_path,
            parques_path=parques_path,
            hospitales_path=hospitales_path,
        )

        nodo_mas_cercano = graph_engine.get_nearest_node(40.4168, -3.7038)
        # Los nodos de calle de build_graph tienen id entero; los especiales, id de texto.
        assert isinstance(nodo_mas_cercano, int)

    def test_load_graph_sin_geojson_de_equipamientos_no_rompe(self, tmp_path, caplog):
        callejero_path = str(tmp_path / "callejero.geojson")
        parques_path = str(tmp_path / "no_existe_parques.geojson")
        hospitales_path = str(tmp_path / "no_existe_hospitales.geojson")

        self._write_callejero(callejero_path)

        with caplog.at_level(logging.WARNING):
            G = graph_engine.load_graph(
                callejero_path=callejero_path,
                parques_path=parques_path,
                hospitales_path=hospitales_path,
            )

        assert G.number_of_nodes() > 0
        assert graph_engine.get_special_node("bomberos", "cualquiera") is None
        assert any("no encontrado" in rec.message.lower() for rec in caplog.records)
