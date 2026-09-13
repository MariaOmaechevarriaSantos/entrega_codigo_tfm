"""
Tests de app/server.py — consolidados en P5 bloque 5.

Qué cubre esta suite (contrato de docs/p5/openapi_p5.yaml + invariantes de la
fase, docs/p5/README_P5_API_Streamlit.md §1):

  * Arranque y autodiagnóstico (bloque 2): fix de sys.path, carga del
    grafo UNA vez al arrancar (nunca por petición), /health con
    diagnóstico honesto y 200/503, errorhandler global que convierte
    cualquier FileNotFoundError en JSON con el nombre del fichero.
  * Catálogo y contrato de /ruta (bloque 3): /config sin internals,
    origen/destino por coordenadas o identidad, vehículo por id o
    ancho_req/galibo_req explícitos (con su precedencia), algoritmo,
    fecha/hora, y respuesta con parametros_efectivos + trafico_por_zona
    + origen + destino. /prediccion_trafico sobre ml.predict_trafico_real
    (NUNCA ml.predict) con caché por (fecha, hora) compartida con /ruta.
  * /isocronas (bloque 4): cálculo en vivo cacheado por
    (parque, ancho_req, galibo_req, fecha, hora), filtrado físico por
    vehículo, 400/404.
  * Esquema exacto de respuesta y casos 400/404/503 de /health, /config,
    /ruta, /prediccion_trafico, /isocronas, /equipamientos y /geocodificar.
  * Endurecimiento y follow-up (bloque 8): sobre de error uniforme (JSON +
    `code` del catálogo, incl. 500 -> INTERNAL_ERROR), destino por
    identidad, 503 al pedir tráfico sin modelo (sin degradar en silencio),
    fronteras de MADRID_BBOX, matriz de fallos de Nominatim, carga única
    del grafo a nivel de endpoint, media type real de las respuestas
    GeoJSON, y coherencia del enum `code` con docs/p5/openapi_p5.yaml.

`GET /meteorologia` NO se prueba aquí: su suite completa a nivel de
servidor (esquema, alerta, caché TTL, snapshot, ?fuente=aemet, ?zona,
"nunca 5xx", independencia del grafo) está en tests/test_meteorologia.py.
Aquí solo el invariante inverso (`/ruta` no invoca la alerta).

Grafo sintético único
---------------------
`_grafo_sintetico()` (12 nodos) sustituye a los 4 constructores de grafo
que tenía este fichero antes del bloque 5. Incluye, con atributos reales
(width_m, height_m, zona, length_m, travel_time_s):

  - 1<->2: calle de 3.0 m -> discrimina autobomba_pesada (3.5) de
    vehiculo_rescate (2.1); hay un desvío ancho 1-3-2 (500 m) para que la
    pesada tenga ruta, más larga.
  - 1<->5: gálibo 3.0 m -> bloquea la pesada (gálibo 4.0) por altura, no
    por anchura (la calle mide 4.5 m); desvío alto 1-6-5 (700 m).
  - 4<->9: calle de 1.5 m -> ningún vehículo del catálogo pasa; el par
    1->9 no tiene ruta completa posible (ruta parcial hasta el nodo 4).
  - 5<->10: calle de 3.0 m -> rama alcanzable solo por el vehículo ligero,
    para que las isócronas de dos vehículos NO coincidan.
  - parque_1: nodo especial tipo_nodo="bomberos", nombre="Parque Test",
    conectado por calle real a la red.
  - dos zonas ("Centro", "Retiro") para ejercitar el factor de tráfico.

Ningún test de la suite rápida toca data/processed/: se monkeypatchea
routing.graph_engine.load_graph. El único que carga el grafo real de
Madrid está marcado @pytest.mark.slow y queda excluido salvo
`pytest tests/ --runslow` (ver tests/conftest.py).

Nota get_nearest_node/get_special_node: graph_engine.py mantiene su PROPIO
singleton interno (_graph/_kdtree). El load_graph sintético reemplaza la
función entera y no puebla ese singleton, así que los tests que resuelven
nodos en /ruta o /isocronas parchean get_nearest_node/get_special_node
directamente (fixture `client_ruta` y helpers `_parchear_*`).
"""
import ast
import contextlib
import importlib
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb
import networkx as nx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ZONA_A = "Centro"
ZONA_B = "Retiro"

# Catálogo real de `code` de error de la API. Superset del enum de
# ErrorResponse en docs/p5/openapi_p5.yaml (que aún no lista los dos códigos
# del geocodificador, añadidos en el bloque 7) -- ver "Limitaciones" del
# registro de este bloque.
_CODES_ERROR_VALIDOS = {
    "VALIDATION_ERROR", "NOT_FOUND", "NO_ROUTE",
    "MISSING_ARTIFACT", "INTERNAL_ERROR",
    "DIRECCION_NO_ENCONTRADA", "GEOCODER_UNAVAILABLE",
}


def _assert_error(resp, status: int, code: str) -> dict:
    """Sobre común de TODO error de la API: JSON real, mimetype
    application/json, `code` del catálogo, `error` string no vacío y sin
    traza de Python cruda filtrada al cliente."""
    assert resp.status_code == status, resp.get_data(as_text=True)
    assert resp.is_json and resp.mimetype == "application/json"
    body = resp.get_json()
    assert body["code"] == code, body
    assert isinstance(body.get("error"), str) and body["error"].strip()
    assert "Traceback (most recent call last)" not in body["error"]
    return body


def _assert_linestring_4326(feature: dict) -> None:
    """La geometría de una ruta es una LineString en coordenadas de Madrid."""
    geom = feature["geometry"]
    assert geom["type"] == "LineString", geom["type"]
    coords = geom["coordinates"]
    assert len(coords) >= 2
    for lon, lat in coords:
        assert -4.0 <= lon <= -3.0, f"lon fuera de Madrid: {lon}"
        assert 40.0 <= lat <= 41.0, f"lat fuera de Madrid: {lat}"


def _assert_polygon_4326(feature: dict) -> None:
    """La geometría de una isócrona es un polígono válido, no vacío, en Madrid."""
    from shapely.geometry import shape

    geom = feature["geometry"]
    assert geom["type"] in ("Polygon", "MultiPolygon"), geom["type"]
    poly = shape(geom)
    assert not poly.is_empty
    assert poly.is_valid
    minx, miny, maxx, maxy = poly.bounds
    assert -4.0 <= minx <= maxx <= -3.0, poly.bounds
    assert 40.0 <= miny <= maxy <= 41.0, poly.bounds


# ─────────────────────────────────────────────────────────────────────────
# Grafo sintético único
# ─────────────────────────────────────────────────────────────────────────
def _grafo_sintetico() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node(1, x=440000.0, y=4470000.0)
    G.add_node(2, x=440300.0, y=4470000.0)
    G.add_node(3, x=440150.0, y=4470200.0)
    G.add_node(4, x=440600.0, y=4470000.0)
    G.add_node(5, x=440000.0, y=4470300.0)
    G.add_node(6, x=440300.0, y=4470300.0)
    G.add_node(7, x=440300.0, y=4469700.0)
    G.add_node(8, x=440600.0, y=4469700.0)
    G.add_node(9, x=441100.0, y=4470000.0)
    G.add_node(10, x=440000.0, y=4470700.0)
    G.add_node("parque_1", x=439950.0, y=4469950.0, tipo_nodo="bomberos", nombre="Parque Test")
    G.add_node("hosp_1", x=439950.0, y=4470050.0, tipo_nodo="hospitales", nombre="Hospital Test")

    def arista(u, v, length, tt, width, height, zona):
        for a, b in ((u, v), (v, u)):
            G.add_edge(a, b, length_m=length, travel_time_s=tt, width_m=width, height_m=height, zona=zona)

    arista(1, 2, 300.0, 30.0, 3.0, 99.0, ZONA_A)   # atajo estrecho: discrimina por anchura
    arista(1, 3, 250.0, 25.0, 4.5, 99.0, ZONA_A)
    arista(3, 2, 250.0, 25.0, 4.5, 99.0, ZONA_A)   # desvío ancho 1-3-2 = 500 m
    arista(1, 5, 200.0, 20.0, 4.5, 3.0, ZONA_A)    # gálibo insuficiente (h = 3.0 m), anchura sobrada
    arista(1, 6, 350.0, 35.0, 4.5, 99.0, ZONA_A)
    arista(6, 5, 350.0, 35.0, 4.5, 99.0, ZONA_A)   # desvío con gálibo libre 1-6-5 = 700 m
    arista(2, 4, 300.0, 30.0, 4.5, 99.0, ZONA_A)
    arista(4, 9, 150.0, 15.0, 1.5, 99.0, ZONA_A)   # 1.5 m: ningún vehículo del catálogo pasa
    arista(2, 7, 200.0, 20.0, 4.5, 99.0, ZONA_B)
    arista(7, 8, 200.0, 20.0, 4.5, 99.0, ZONA_B)
    arista(5, 10, 400.0, 40.0, 3.0, 99.0, ZONA_A)  # rama solo-vehículo-ligero (isócronas)
    arista("parque_1", 1, 50.0, 5.0, 5.0, 99.0, ZONA_A)
    arista("hosp_1", 1, 50.0, 5.0, 5.0, 99.0, ZONA_A)
    return G


# Coordenadas de test -> node_id (para parchear get_nearest_node).
NEAREST = {
    (40.40, -3.70): 1,
    (40.41, -3.71): 2,
    (40.42, -3.72): 4,
    (40.44, -3.74): 5,
    (40.43, -3.73): 9,
}
BASE = {"orig_lat": "40.40", "orig_lon": "-3.70", "dest_lat": "40.41", "dest_lon": "-3.71"}  # nodo 1 -> nodo 2


def _parchear_get_nearest_node(monkeypatch, mapa=NEAREST) -> None:
    import routing.graph_engine as ge

    def _nearest(lat, lon):
        return mapa[(round(lat, 6), round(lon, 6))]

    monkeypatch.setattr(ge, "get_nearest_node", _nearest)


def _parchear_get_special_node(monkeypatch, G) -> None:
    import routing.graph_engine as ge

    def _special(tipo_nodo, nombre):
        for nid, d in G.nodes(data=True):
            if d.get("tipo_nodo") == tipo_nodo and d.get("nombre") == nombre:
                return nid
        return None

    monkeypatch.setattr(ge, "get_special_node", _special)


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def srv(monkeypatch):
    """(Re)importa app.server con routing.graph_engine.load_graph ya
    monkeypatcheado al grafo sintético. _cargar_grafo_al_arrancar() NO se
    ejecuta al importar (vive en el bloque __main__ de server.py); cada
    test la llama a mano cuando quiere simular el arranque."""
    import routing.graph_engine as ge
    monkeypatch.setattr(ge, "load_graph", _grafo_sintetico)

    if "app.server" in sys.modules:
        module = importlib.reload(sys.modules["app.server"])
    else:
        import app.server as module
    # Estado global limpio y EXPLÍCITO, no como efecto colateral del reload:
    # los tests de caché dependían de que el reload rebindara estos dicts.
    module._traffic_cache.clear()
    module._isocronas_cache.clear()
    return module


@pytest.fixture
def client(srv):
    srv.app.config["TESTING"] = False  # queremos la respuesta HTTP real (200/404/503), no la excepción propagada
    return srv.app.test_client()


@pytest.fixture
def client_ruta(client, srv, monkeypatch):
    """client con el grafo sintético ya arrancado y get_nearest_node /
    get_special_node resueltos contra ese grafo. Para tests de /ruta e
    /isocronas que llegan a calcular una ruta real."""
    srv._cargar_grafo_al_arrancar()
    _parchear_get_nearest_node(monkeypatch)
    _parchear_get_special_node(monkeypatch, srv._graph_state["G"])
    return client


@pytest.fixture
def mock_trafico(monkeypatch):
    """Sustituye ml.predict_trafico_real.predecir_trafico_real por un doble
    que NO carga el XGBoost. Devuelve la lista de (fecha, hora) con que se
    ha llamado — para afirmar sobre la caché."""
    llamadas = []

    def _fake(fecha, hora, zonas=None):
        llamadas.append((fecha, hora))
        return {ZONA_A: 1, ZONA_B: 2}

    monkeypatch.setattr("ml.predict_trafico_real.predecir_trafico_real", _fake)
    return llamadas


def _artefactos_bajo(tmp_path: Path, presentes: set[str]) -> dict[str, str]:
    """ARTEFACTOS_ESPERADOS bajo tmp_path; crea en disco solo las claves de `presentes`."""
    nombres = {
        "callejero": "callejero.geojson",
        "parques_bomberos_geojson": "parques.geojson",
        "hospitales_geojson": "hospitales.geojson",
        "modelo_trafico_pkl": "modelo.pkl",
        "modelo_trafico_metadata": "modelo_metadata.json",
        "duckdb": "test.duckdb",
    }
    rutas = {}
    for clave, nombre in nombres.items():
        p = tmp_path / nombre
        if clave in presentes:
            if clave == "duckdb":
                duckdb.connect(str(p)).close()
            else:
                p.write_text("{}")
        rutas[clave] = str(p)
    return rutas


def _boom(*_a, **_k):
    raise RuntimeError("no debería llegarse hasta aquí")


# ─────────────────────────────────────────────────────────────────────────
# Bloque 2 — arranque y autodiagnóstico
# ─────────────────────────────────────────────────────────────────────────
class TestSysPathFix:
    def test_server_py_importable_como_script_directo_sin_pythonpath(self):
        """Regresión: `python app/server.py --dev` desde la raíz sin
        PYTHONPATH fallaba con ModuleNotFoundError: No module named 'routing'."""
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        proc = subprocess.Popen(
            [sys.executable, "app/server.py", "--dev"],
            cwd=str(PROJECT_ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            deadline = time.time() + 15
            salida, resultado = [], None
            while time.time() < deadline:
                line = proc.stdout.readline()
                if line:
                    salida.append(line)
                    if "ModuleNotFoundError" in line:
                        resultado = "fallo_import"
                        break
                    if "Cargando callejero" in line or "No se pudo cargar el grafo al arrancar" in line:
                        resultado = "import_ok"
                        break
                elif proc.poll() is not None:
                    break
            assert resultado == "import_ok", (
                f"routing.graph_engine no llegó a importarse con éxito (resultado={resultado});"
                f" salida:\n{''.join(salida)}"
            )
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


class TestArranqueGrafo:
    def test_carga_exitosa_puebla_el_estado_con_nodos_aristas_tiempo_y_especiales(self, srv):
        srv._cargar_grafo_al_arrancar()
        G = srv._graph_state["G"]
        assert G is not None
        # el loader no pierde ni inventa nodos/aristas respecto al grafo servido
        assert G.number_of_nodes() == 12
        assert G.number_of_edges() == 26
        # agrega los tipos de nodo especial encontrados
        assert srv._graph_state["nodos_especiales"] == {"bomberos": 1, "hospitales": 1}
        assert isinstance(srv._graph_state["load_seconds"], float) and srv._graph_state["load_seconds"] >= 0
        assert srv._graph_state["error"] is None

    def test_fallo_de_carga_guarda_el_error_con_el_fichero_y_deja_el_grafo_en_none(self, srv, monkeypatch):
        import routing.graph_engine as ge

        def _falla():
            raise FileNotFoundError(
                "Callejero no encontrado en data/processed/x.geojson. Ejecuta: python pipeline/run_pipeline.py"
            )

        monkeypatch.setattr(ge, "load_graph", _falla)
        srv._cargar_grafo_al_arrancar()
        assert srv._graph_state["G"] is None
        assert "x.geojson" in srv._graph_state["error"]
        assert srv._graph_state["load_seconds"] is None  # no llegó a medir nada

    def test_fallo_de_carga_no_filenotfound_tambien_degrada_sin_tumbar_el_proceso(self, srv, monkeypatch):
        """Bloque 8: un callejero corrupto hace que pyogrio/fiona lancen
        SUS excepciones, no FileNotFoundError. Antes eso tumbaba el arranque;
        ahora degrada a /health 503 igual que un fichero ausente."""
        import routing.graph_engine as ge

        def _falla_corrupto():
            raise ValueError("Ill-formed GeoJSON: unexpected end of file")

        monkeypatch.setattr(ge, "load_graph", _falla_corrupto)
        srv._cargar_grafo_al_arrancar()  # no debe propagar
        assert srv._graph_state["G"] is None
        assert "Ill-formed GeoJSON" in srv._graph_state["error"]


class TestGetGraph:
    def test_devuelve_el_grafo_ya_cargado_sin_copiarlo(self, srv):
        srv._cargar_grafo_al_arrancar()
        assert srv.get_graph() is srv._graph_state["G"]

    def test_lanza_filenotfounderror_si_no_se_ha_cargado(self, srv):
        with pytest.raises(FileNotFoundError):
            srv.get_graph()

    def test_no_dispara_una_carga_nueva_en_cada_llamada(self, srv, monkeypatch):
        import routing.graph_engine as ge
        llamadas = {"n": 0}

        def _contando():
            llamadas["n"] += 1
            return _grafo_sintetico()

        monkeypatch.setattr(ge, "load_graph", _contando)
        srv._cargar_grafo_al_arrancar()
        srv.get_graph()
        srv.get_graph()
        srv.get_graph()
        assert llamadas["n"] == 1


class TestExtraerFichero:
    def test_extrae_la_ruta_geojson_del_mensaje_de_callejero(self, srv):
        msg = ("Callejero no encontrado en /x/y/data/processed/madrid_callejero_filtered.geojson. "
               "Ejecuta: python pipeline/run_pipeline.py")
        assert srv._extraer_fichero(msg).endswith("madrid_callejero_filtered.geojson")

    def test_extrae_el_primer_fichero_no_la_sugerencia_de_regenerar(self, srv):
        msg = ("Modelo no encontrado en /x/data/processed/modelo_trafico_xgboost.pkl. "
               "Ejecuta notebooks/05_modelo_xgboost_densidad_trafico.ipynb para generarlo.")
        fichero = srv._extraer_fichero(msg)
        assert fichero.endswith("modelo_trafico_xgboost.pkl")
        assert "05_modelo" not in fichero

    def test_devuelve_none_si_el_mensaje_no_nombra_un_fichero(self, srv):
        msg = "Tabla 'equipamientos_por_zona' no encontrada en DuckDB. Ejecuta: python pipeline/run_pipeline.py"
        assert srv._extraer_fichero(msg) is None


class TestHealthEndpoint:
    def test_grafo_cargado_devuelve_200_y_el_esquema_completo(self, srv, client, monkeypatch, tmp_path):
        rutas = _artefactos_bajo(tmp_path, presentes=set(srv.ARTEFACTOS_ESPERADOS))
        monkeypatch.setattr(srv, "ARTEFACTOS_ESPERADOS", rutas)
        srv._cargar_grafo_al_arrancar()

        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert set(body) == {
            "status", "grafo", "modelo_trafico", "duckdb", "artefactos_faltantes", "timestamp",
        }
        assert set(body["grafo"]) == {"cargado", "nodes", "edges", "nodos_especiales", "segundos_carga", "error"}
        # sub-esquemas que openapi_p5.yaml marca required y no se comprobaban
        assert set(body["modelo_trafico"]) == {"cargado", "ruta_pkl"}
        assert set(body["duckdb"]) == {"accesible", "ruta"}
        assert body["status"] == "ok"
        assert body["grafo"]["cargado"] is True
        assert body["grafo"]["nodes"] == 12
        assert body["grafo"]["edges"] == 26
        assert body["grafo"]["nodos_especiales"] == {"bomberos": 1, "hospitales": 1}
        assert body["grafo"]["error"] is None
        assert isinstance(body["grafo"]["segundos_carga"], (int, float))
        assert body["grafo"]["segundos_carga"] >= 0
        assert body["artefactos_faltantes"] == []
        # timestamp: ISO-8601 con zona horaria (regresión a str(datetime.now()) naïve)
        assert datetime.fromisoformat(body["timestamp"]).tzinfo is not None

    def test_grafo_no_cargado_devuelve_503_y_lo_marca_degraded(self, srv, client, monkeypatch, tmp_path):
        import routing.graph_engine as ge

        def _falla():
            raise FileNotFoundError(
                "Callejero no encontrado en data/processed/madrid_callejero_filtered.geojson. "
                "Ejecuta: python pipeline/run_pipeline.py"
            )

        monkeypatch.setattr(ge, "load_graph", _falla)
        rutas = _artefactos_bajo(tmp_path, presentes=set())
        monkeypatch.setattr(srv, "ARTEFACTOS_ESPERADOS", rutas)
        srv._cargar_grafo_al_arrancar()

        resp = client.get("/health")
        assert resp.status_code == 503
        body = resp.get_json()
        assert body["status"] == "degraded"
        assert body["grafo"]["cargado"] is False
        # el error NOMBRA el fichero que falta, no es un "not None" cualquiera
        assert "madrid_callejero_filtered.geojson" in body["grafo"]["error"]
        assert set(body["artefactos_faltantes"]) == {srv._ruta_relativa(p) for p in rutas.values()}
        # NOTA: openapi_p5.yaml define este 503 como ErrorResponse {error, code};
        # la implementación devuelve el dict de /health con status="degraded".
        # Discrepancia conocida, registrada en el bloque; no se "arregla" aquí
        # porque Streamlit ya consume esta forma.
        assert set(body) == {"status", "grafo", "modelo_trafico", "duckdb", "artefactos_faltantes", "timestamp"}

    def test_artefactos_faltantes_lista_solo_los_realmente_ausentes(self, srv, client, monkeypatch, tmp_path):
        presentes = {"callejero", "duckdb"}
        rutas = _artefactos_bajo(tmp_path, presentes=presentes)
        monkeypatch.setattr(srv, "ARTEFACTOS_ESPERADOS", rutas)
        srv._cargar_grafo_al_arrancar()

        faltantes = set(client.get("/health").get_json()["artefactos_faltantes"])
        for clave in presentes:
            assert srv._ruta_relativa(rutas[clave]) not in faltantes
        for clave in set(rutas) - presentes:
            assert srv._ruta_relativa(rutas[clave]) in faltantes

    def test_duckdb_accesible_true_con_fichero_valido(self, srv, client, monkeypatch, tmp_path):
        rutas = _artefactos_bajo(tmp_path, presentes=set(srv.ARTEFACTOS_ESPERADOS))
        monkeypatch.setattr(srv, "ARTEFACTOS_ESPERADOS", rutas)
        srv._cargar_grafo_al_arrancar()
        assert client.get("/health").get_json()["duckdb"]["accesible"] is True

    def test_duckdb_corrupto_no_tumba_el_servicio(self, srv, client, monkeypatch, tmp_path):
        rutas = _artefactos_bajo(tmp_path, presentes=set(srv.ARTEFACTOS_ESPERADOS) - {"duckdb"})
        corrupto = tmp_path / "test.duckdb"
        corrupto.write_bytes(b"esto no es una base de datos duckdb valida")
        rutas["duckdb"] = str(corrupto)
        monkeypatch.setattr(srv, "ARTEFACTOS_ESPERADOS", rutas)
        srv._cargar_grafo_al_arrancar()

        resp = client.get("/health")
        assert resp.status_code == 200  # el grafo sigue cargado: puede servir rutas igualmente
        assert resp.get_json()["duckdb"]["accesible"] is False

    def test_modelo_cargado_refleja_el_estado_real_de_predict_trafico_real(self, srv, client, monkeypatch, tmp_path):
        rutas = _artefactos_bajo(tmp_path, presentes=set(srv.ARTEFACTOS_ESPERADOS))
        monkeypatch.setattr(srv, "ARTEFACTOS_ESPERADOS", rutas)
        srv._cargar_grafo_al_arrancar()

        monkeypatch.setattr("ml.predict_trafico_real._model", None)
        assert client.get("/health").get_json()["modelo_trafico"]["cargado"] is False
        monkeypatch.setattr("ml.predict_trafico_real._model", object())
        assert client.get("/health").get_json()["modelo_trafico"]["cargado"] is True

    def test_predict_trafico_real_expone_el_atributo_que_health_lee(self):
        """Contrato interno: /health decide modelo_trafico.cargado con
        getattr(ml.predict_trafico_real, '_model'). Si P3 renombra ese
        atributo, /health informaría "no cargado" para siempre en silencio.
        Este test convierte ese rename en un rojo evidente."""
        import ml.predict_trafico_real as mlreal
        assert hasattr(mlreal, "_model")

    def test_health_200_aunque_falten_todos_los_artefactos_opcionales(self, srv, client, monkeypatch, tmp_path):
        """openapi_p5.yaml /health §200: artefactos_faltantes no vacío NO
        baja el status a 503 mientras el grafo esté cargado. Solo se
        probaba todo-presente o todo-ausente+503."""
        rutas = _artefactos_bajo(tmp_path, presentes=set())  # ni uno en disco
        monkeypatch.setattr(srv, "ARTEFACTOS_ESPERADOS", rutas)
        srv._cargar_grafo_al_arrancar()  # grafo sintético -> carga OK

        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["status"] == "ok"
        assert body["grafo"]["cargado"] is True
        assert len(body["artefactos_faltantes"]) == len(rutas)
        assert body["duckdb"]["accesible"] is False

    def test_health_no_dispara_una_carga_del_grafo(self, srv, client, monkeypatch):
        import routing.graph_engine as ge
        llamadas = {"n": 0}

        def _contando():
            llamadas["n"] += 1
            return _grafo_sintetico()

        monkeypatch.setattr(ge, "load_graph", _contando)
        client.get("/health")
        client.get("/health")
        assert llamadas["n"] == 0


# ─────────────────────────────────────────────────────────────────────────
# Bloque 8B — GET /version (identidad de la build)
# ─────────────────────────────────────────────────────────────────────────
class TestVersionEndpoint:
    _CLAVES = {"version_api", "git_sha", "fecha_build", "sha256_manifiesto_datos"}

    def test_esquema_exacto_y_version_api_desde_la_fuente_unica(self, srv, client):
        resp = client.get("/version")
        assert resp.status_code == 200
        assert resp.mimetype == "application/json"
        body = resp.get_json()
        assert set(body) == self._CLAVES
        # version_api sale de app/version.API_VERSION, no de un literal suelto
        assert body["version_api"] == srv.API_VERSION

    def test_lee_git_sha_fecha_y_manifiesto_del_entorno(self, srv, client, monkeypatch):
        monkeypatch.setenv("GIT_SHA", "1a2b3c4d5e6f")
        monkeypatch.setenv("BUILD_DATE", "2026-08-30T10:11:12Z")
        monkeypatch.setenv("MANIFEST_SHA256", "deadbeef" * 8)
        body = client.get("/version").get_json()
        assert body["git_sha"] == "1a2b3c4d5e6f"
        assert body["fecha_build"] == "2026-08-30T10:11:12Z"
        assert body["sha256_manifiesto_datos"] == "deadbeef" * 8

    def test_sin_build_args_los_tres_campos_son_desconocido(self, srv, client, monkeypatch):
        for var in ("GIT_SHA", "BUILD_DATE", "MANIFEST_SHA256"):
            monkeypatch.delenv(var, raising=False)
        body = client.get("/version").get_json()
        assert body["git_sha"] == "desconocido"
        assert body["fecha_build"] == "desconocido"
        assert body["sha256_manifiesto_datos"] == "desconocido"
        assert body["version_api"] != "desconocido"  # esa SÍ está siempre

    def test_la_spec_openapi_servida_usa_la_misma_version(self, srv, client):
        """flasgger sirve el contrato en /apispec_1.json: su info.version
        tiene que ser API_VERSION, no un '1.0.0' hardcodeado aparte."""
        resp = client.get("/apispec_1.json")
        if resp.status_code != 200:
            pytest.skip("flasgger no expone /apispec_1.json en esta versión")
        assert resp.get_json()["info"]["version"] == srv.API_VERSION


# ─────────────────────────────────────────────────────────────────────────
# Bloque 8B — GET /metrics (exposición Prometheus)
# ─────────────────────────────────────────────────────────────────────────
def _muestra(nombre, etiquetas):
    """Valor actual de una serie del registro global de prometheus_client,
    0.0 si aún no se ha tocado (así los tests afirman sobre DELTAS, no
    sobre absolutos: el registro acumula durante toda la sesión)."""
    from prometheus_client import REGISTRY
    v = REGISTRY.get_sample_value(nombre, etiquetas)
    return 0.0 if v is None else v


class TestMetricsEndpoint:
    def test_200_en_formato_texto_prometheus(self, srv, client):
        client.get("/config")  # garantiza al menos una observación del histograma
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        cuerpo = resp.get_data(as_text=True)
        assert "# HELP tfm_http_requests_total" in cuerpo
        assert "# TYPE tfm_http_request_duration_seconds histogram" in cuerpo
        assert 'tfm_http_request_duration_seconds_bucket{' in cuerpo

    def test_cada_peticion_incrementa_el_contador_de_su_endpoint(self, srv, client):
        etq = {"metodo": "GET", "endpoint": "/config", "status": "200"}
        antes = _muestra("tfm_http_requests_total", etq)
        client.get("/config")
        client.get("/config")
        assert _muestra("tfm_http_requests_total", etq) == antes + 2

    def test_el_endpoint_metrics_no_se_cuenta_a_si_mismo(self, srv, client):
        etq = {"metodo": "GET", "endpoint": "/metrics", "status": "200"}
        antes = _muestra("tfm_http_requests_total", etq)
        client.get("/metrics")
        client.get("/metrics")
        assert _muestra("tfm_http_requests_total", etq) == antes  # sin cambio

    def test_hit_y_miss_de_la_cache_de_trafico_se_contabilizan(self, client_ruta, mock_trafico):
        qs = {"date": "2025-06-15", "hora": "8"}
        miss0 = _muestra("tfm_cache_events_total", {"cache": "trafico", "resultado": "miss"})
        hit0 = _muestra("tfm_cache_events_total", {"cache": "trafico", "resultado": "hit"})
        client_ruta.get("/prediccion_trafico", query_string=qs)         # miss
        client_ruta.get("/prediccion_trafico", query_string=qs)         # hit
        assert _muestra("tfm_cache_events_total", {"cache": "trafico", "resultado": "miss"}) == miss0 + 1
        assert _muestra("tfm_cache_events_total", {"cache": "trafico", "resultado": "hit"}) == hit0 + 1

    def test_isocronas_en_vivo_incrementa_el_origen_vivo_calculado(self, client_ruta, mock_trafico):
        antes = _muestra("tfm_isocronas_origen_total", {"origen": "vivo_calculado"})
        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"})
        assert resp.status_code == 200
        assert resp.headers.get("X-Isocronas-Origen") == "vivo"
        assert _muestra("tfm_isocronas_origen_total", {"origen": "vivo_calculado"}) == antes + 1


class TestArtefactoAusente:
    """errorhandler(FileNotFoundError) global: JSON con el nombre del
    fichero y 503, nunca el 500 HTML del debugger de Flask (bloque 2)."""

    def test_ruta_con_grafo_ausente_devuelve_json_limpio_con_fichero_no_500(self, srv, client, monkeypatch):
        import routing.graph_engine as ge

        def _falla():
            raise FileNotFoundError(
                "Callejero no encontrado en data/processed/madrid_callejero_filtered.geojson. "
                "Ejecuta: python pipeline/run_pipeline.py"
            )

        monkeypatch.setattr(ge, "load_graph", _falla)
        srv._cargar_grafo_al_arrancar()

        resp = client.get("/ruta?orig_lat=40.4&orig_lon=-3.7&dest_lat=40.41&dest_lon=-3.71")
        body = _assert_error(resp, 503, "MISSING_ARTIFACT")
        assert body["fichero"].endswith("madrid_callejero_filtered.geojson")
        # openapi ErrorResponse: con MISSING_ARTIFACT el fichero va TAMBIÉN en el texto
        assert "madrid_callejero_filtered.geojson" in body["error"]

    def test_mensaje_generico_sin_fichero_sigue_siendo_json_limpio(self, srv, client):
        # get_graph() sin arrancar -> FileNotFoundError con mensaje genérico (no nombra artefacto)
        resp = client.get("/ruta?orig_lat=40.4&orig_lon=-3.7&dest_lat=40.41&dest_lon=-3.71")
        body = _assert_error(resp, 503, "MISSING_ARTIFACT")
        assert "fichero" not in body
        assert "Grafo no disponible" in body["error"]  # es el fallback genérico de get_graph()


class TestErrorEnvelope:
    """Bloque 8: TODA respuesta de error de la API es JSON con `code` del
    catálogo -- incluido el 500 inesperado, que antes de este bloque salía
    como HTML del debugger de Flask (no había @app.errorhandler(Exception))."""

    def test_500_inesperado_en_ruta_sigue_siendo_json_con_code(self, client_ruta, monkeypatch):
        import routing.optimizer as opt

        def _revienta(*a, **k):
            raise RuntimeError("fallo interno simulado del optimizador")

        monkeypatch.setattr(opt, "calcular_ruta", _revienta)
        resp = client_ruta.get("/ruta", query_string=BASE)
        body = _assert_error(resp, 500, "INTERNAL_ERROR")
        assert "fallo interno simulado" in body["error"]  # el mensaje, no la traza

    def test_ruta_desconocida_sigue_dando_404_http_no_json_envelope(self, client):
        # el handler global NO debe tragarse los HTTPException de Werkzeug
        assert client.get("/no-existe-este-endpoint").status_code == 404

    @pytest.mark.parametrize("endpoint,qs", [
        ("/ruta", {"dest_lat": "40.41", "dest_lon": "-3.71"}),      # falta origen
        ("/prediccion_trafico", {}),                                 # falta date
        ("/isocronas", {}),                                          # falta nombre
        ("/equipamientos", {"tipo": "no_existe"}),                   # tipo inválido
        ("/geocodificar", {}),                                       # falta q
    ])
    def test_todo_error_400_es_json_con_code_del_catalogo(self, client_ruta, endpoint, qs):
        resp = client_ruta.get(endpoint, query_string=qs)
        _assert_error(resp, 400, "VALIDATION_ERROR")

    @pytest.mark.parametrize("endpoint,qs", [
        ("/ruta", "BASE"),
        ("/isocronas", {"nombre": "Parque Test"}),
    ])
    def test_missing_grafo_nombra_el_fichero_en_fichero_y_en_error(self, srv, client, monkeypatch, endpoint, qs):
        import routing.graph_engine as ge

        def _falla():
            raise FileNotFoundError(
                "Callejero no encontrado en data/processed/madrid_callejero_filtered.geojson. "
                "Ejecuta: python pipeline/run_pipeline.py"
            )

        monkeypatch.setattr(ge, "load_graph", _falla)
        srv._cargar_grafo_al_arrancar()
        resp = client.get(endpoint, query_string=(BASE if qs == "BASE" else qs))
        body = _assert_error(resp, 503, "MISSING_ARTIFACT")
        assert body["fichero"].endswith("madrid_callejero_filtered.geojson")
        assert "madrid_callejero_filtered.geojson" in body["error"]

    def test_missing_modelo_en_prediccion_nombra_el_fichero(self, client, monkeypatch):
        def _falla(fecha, hora, zonas=None):
            raise FileNotFoundError(
                "Modelo no encontrado en data/processed/modelo_trafico_xgboost.pkl. "
                "Ejecuta notebooks/05_modelo_xgboost_densidad_trafico.ipynb."
            )

        monkeypatch.setattr("ml.predict_trafico_real.predecir_trafico_real", _falla)
        resp = client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"})
        body = _assert_error(resp, 503, "MISSING_ARTIFACT")
        assert body["fichero"].endswith("modelo_trafico_xgboost.pkl")
        assert "modelo_trafico_xgboost.pkl" in body["error"]


# ─────────────────────────────────────────────────────────────────────────
# /config
# ─────────────────────────────────────────────────────────────────────────
class TestConfigEndpoint:
    def test_esquema_top_level_exacto_sin_internals(self, client):
        """§2 del bloque 3: /config sirve el catálogo, no un volcado de internals."""
        body = client.get("/config").get_json()
        assert set(body) == {
            "vehiculos", "vehiculo_default", "algoritmos_disponibles",
            "algoritmo_default", "cortes_isocronas_min_default", "trafico_niveles",
        }

    def test_catalogo_con_al_menos_tres_vehiculos_de_forma_valida_e_ids_unicos(self, client):
        import app.config as cfg

        vehiculos = client.get("/config").get_json()["vehiculos"]
        assert len(vehiculos) >= 3
        # openapi VehiculoTipo marca required [id, nombre, ancho_req_m, galibo_req_m];
        # `descripcion` es opcional -> se acepta, pero no se exige (añadir un
        # campo opcional al catálogo no debe romper este test).
        REQUERIDAS = {"id", "nombre", "ancho_req_m", "galibo_req_m"}
        PERMITIDAS = REQUERIDAS | {"descripcion"}
        for v in vehiculos:
            assert REQUERIDAS <= set(v) <= PERMITIDAS, set(v)
            assert re.fullmatch(r"[a-z][a-z0-9_]*", v["id"]), v["id"]
            assert isinstance(v["nombre"], str) and v["nombre"].strip()
            assert isinstance(v["ancho_req_m"], (int, float)) and v["ancho_req_m"] > 0
            assert isinstance(v["galibo_req_m"], (int, float)) and v["galibo_req_m"] > 0
        ids = [v["id"] for v in vehiculos]
        assert len(ids) == len(set(ids))
        # la respuesta no pierde ni inventa vehículos respecto a app/config.py
        assert set(ids) == {c["id"] for c in cfg.VEHICULOS}

    def test_vehiculo_pesado_coincide_con_optimizer_sanity(self, client):
        """Sanity barato. La teeth de verdad la pone el test de variable de
        entorno de abajo -- este comparaba el default consigo mismo."""
        from routing.optimizer import ANCHO_CAMION_REQ, GALIBO_REQ
        pesado = next(v for v in client.get("/config").get_json()["vehiculos"] if v["id"] == "autobomba_pesada")
        assert pesado["ancho_req_m"] == ANCHO_CAMION_REQ
        assert pesado["galibo_req_m"] == GALIBO_REQ

    def test_config_ancho_galibo_pesado_siguen_a_la_variable_de_entorno(self, monkeypatch):
        """'Prohibido duplicar constantes' (docs/adr/0006): si app/config.py
        hardcodease 3.5 en vez de importar ANCHO_CAMION_REQ de
        routing.optimizer, /config quedaría desincronizado de la variable
        de entorno. El test anterior NO podía cazarlo (comparaba el valor
        por defecto consigo mismo)."""
        import app.config
        import routing.optimizer

        monkeypatch.setenv("ANCHO_CAMION_REQ", "9.9")
        monkeypatch.setenv("GALIBO_REQ", "8.8")
        importlib.reload(routing.optimizer)
        importlib.reload(app.config)
        server = importlib.reload(sys.modules["app.server"]) if "app.server" in sys.modules else __import__("app.server", fromlist=["x"])
        try:
            pesado = next(v for v in server.app.test_client().get("/config").get_json()["vehiculos"]
                          if v["id"] == "autobomba_pesada")
            assert pesado["ancho_req_m"] == 9.9
            assert pesado["galibo_req_m"] == 8.8
        finally:
            monkeypatch.delenv("ANCHO_CAMION_REQ", raising=False)
            monkeypatch.delenv("GALIBO_REQ", raising=False)
            importlib.reload(routing.optimizer)
            importlib.reload(app.config)
            if "app.server" in sys.modules:
                importlib.reload(sys.modules["app.server"])

    def test_vehiculo_default_existe_en_el_propio_catalogo(self, client):
        body = client.get("/config").get_json()
        assert body["vehiculo_default"] in {v["id"] for v in body["vehiculos"]}

    def test_algoritmo_default_esta_entre_los_disponibles(self, client):
        body = client.get("/config").get_json()
        assert body["algoritmo_default"] in body["algoritmos_disponibles"]

    def test_cortes_isocronas_default_son_5_10_15(self, client):
        """Fuente de verdad de los cortes; TestIsocronasEndpoint compara la
        respuesta de /isocronas contra estos literales, no contra esta clave."""
        body = client.get("/config").get_json()
        assert body["cortes_isocronas_min_default"] == [5, 10, 15]


# ─────────────────────────────────────────────────────────────────────────
# /ruta — validación (400/404 antes de calcular)
# ─────────────────────────────────────────────────────────────────────────
class TestRutaValidacion:
    def test_falta_origen_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={"dest_lat": "40.41", "dest_lon": "-3.71"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_origen_con_coords_e_identidad_a_la_vez_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={
            **BASE, "orig_tipo_nodo": "bomberos", "orig_nombre": "Parque Test",
        })
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_origen_fuera_de_madrid_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={**BASE, "orig_lat": "41.38", "orig_lon": "2.17"})  # Barcelona
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_destino_fuera_de_madrid_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={**BASE, "dest_lat": "41.38", "dest_lon": "2.17"})  # Barcelona
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_vehiculo_inexistente_devuelve_400_y_nombra_el_id(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={**BASE, "vehiculo": "no_existe"})
        assert resp.status_code == 400
        body = resp.get_json()
        assert body["code"] == "VALIDATION_ERROR"
        assert "no_existe" in body["error"]

    def test_algoritmo_no_soportado_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={**BASE, "algoritmo": "bfs"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_fecha_invalida_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={**BASE, "date": "no-es-una-fecha"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_hora_fuera_de_rango_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={**BASE, "hora": "24"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_hora_no_numerica_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={**BASE, "hora": "ocho"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_destino_identidad_inexistente_devuelve_404_not_found(self, client_ruta, monkeypatch):
        import routing.graph_engine as ge
        monkeypatch.setattr(ge, "get_special_node", lambda tipo_nodo, nombre: None)
        resp = client_ruta.get("/ruta", query_string={
            "orig_lat": "40.40", "orig_lon": "-3.70",
            "dest_tipo_nodo": "hospitales", "dest_nombre": "No Existe",
        })
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "NOT_FOUND"

    def test_la_validacion_ocurre_antes_de_llamar_a_calcular_ruta(self, client_ruta, monkeypatch):
        """El contrato dice 'rechazar antes de tocar calcular_ruta'. Espía:
        calcular_ruta reventando -> una petición inválida sigue dando 400
        (no se llegó a llamar), una válida da 500 (sí se llamó)."""
        import routing.optimizer as opt
        monkeypatch.setattr(opt, "calcular_ruta", _boom)

        invalida = client_ruta.get("/ruta", query_string={**BASE, "algoritmo": "bfs"})
        assert invalida.status_code == 400

        valida = client_ruta.get("/ruta", query_string=BASE)
        assert valida.status_code == 500


class TestRutaNodoFueraDelGrafo:
    def test_nodo_resuelto_que_ya_no_esta_en_el_grafo_devuelve_404_no_route(self, srv, client, monkeypatch):
        """Guardia defensiva (docs/p5/openapi_p5.yaml): si get_nearest_node
        devolviera un id que no está en el grafo servido, /ruta corta con
        NO_ROUTE, no un 500 de calcular_ruta."""
        srv._cargar_grafo_al_arrancar()
        import routing.graph_engine as ge
        monkeypatch.setattr(ge, "get_nearest_node", lambda lat, lon: 999999)
        resp = client.get("/ruta", query_string={
            "orig_lat": "40.40", "orig_lon": "-3.70", "dest_lat": "40.41", "dest_lon": "-3.71",
        })
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "NO_ROUTE"


# ─────────────────────────────────────────────────────────────────────────
# /ruta — contrato de respuesta y comportamiento
# ─────────────────────────────────────────────────────────────────────────
class TestRutaContrato:
    def test_esquema_de_respuesta_completo_y_parametros_efectivos(self, client_ruta, mock_trafico):
        resp = client_ruta.get("/ruta", query_string={
            **BASE, "vehiculo": "vehiculo_rescate", "date": "2025-06-15", "hora": "8",
        })
        assert resp.status_code == 200
        assert resp.mimetype == "application/geo+json"  # no "application/json"
        body = resp.get_json()
        assert body["type"] == "FeatureCollection"
        assert set(body) == {
            "type", "features", "parametros_efectivos", "trafico_por_zona",
            "origen", "destino", "zona_destino",
        }
        assert len(body["features"]) == 1  # openapi: minItems=maxItems=1
        # zona_destino (bloque 7): el nodo 2 tiene aristas en ZONA_A -> "Centro",
        # y con tráfico pedido su nivel sale de trafico_por_zona.
        assert body["zona_destino"] == {"zona": ZONA_A, "nivel_trafico": 1}

        pe = body["parametros_efectivos"]
        assert set(pe) == {"ancho_req", "galibo_req", "algoritmo", "fecha", "hora", "vehiculo"}
        assert pe["ancho_req"] == 2.1
        assert pe["algoritmo"] == "dijkstra"
        assert pe["fecha"] == "2025-06-15"
        assert pe["hora"] == 8
        assert pe["vehiculo"] == "vehiculo_rescate"

        assert body["trafico_por_zona"] == {ZONA_A: 1, ZONA_B: 2}
        assert body["origen"] == {"node_id": 1, "tipo_nodo": None, "nombre": None}
        assert body["destino"] == {"node_id": 2, "tipo_nodo": None, "nombre": None}

        props = body["features"][0]["properties"]
        # el núcleo contractual es exacto; `n_nodes` está permitido pero NO
        # exigido (server.py lo marca "campo adicional no contractual"), así
        # que quitarlo no debe romper este test.
        assert {"distancia_m", "tiempo_min", "ruta_completa"} <= set(props)
        assert set(props) <= {"distancia_m", "tiempo_min", "ruta_completa", "n_nodes"}
        assert props["ruta_completa"] is True
        assert props["distancia_m"] == 300.0
        assert isinstance(props["tiempo_min"], (int, float)) and props["tiempo_min"] > 0
        _assert_linestring_4326(body["features"][0])

    def test_sin_date_no_hay_trafico_ni_llamada_al_modelo(self, client_ruta, mock_trafico):
        resp = client_ruta.get("/ruta", query_string=BASE)
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["trafico_por_zona"] == {}
        assert body["parametros_efectivos"]["fecha"] is None
        assert mock_trafico == []  # predecir_trafico_real no se llamó

    def test_zona_destino_sin_date_lleva_el_distrito_pero_nivel_null(self, client_ruta):
        """Bloque 7: sin `date` no hay predicción de tráfico, así que
        `nivel_trafico` es null -- pero el distrito del incidente sí se
        localiza (nunca se rellena un 0 por defecto)."""
        body = client_ruta.get("/ruta", query_string=BASE).get_json()
        assert body["zona_destino"] == {"zona": ZONA_A, "nivel_trafico": None}

    def test_zona_destino_nivel_coincide_con_la_zona_resuelta(self, client_ruta, mock_trafico):
        """El nivel de `zona_destino` es EXACTAMENTE el de la zona que se
        resolvió para el destino en `trafico_por_zona` -- no el de otra, ni
        un 0 por defecto. (Antes: `== "Centro"` y `!= 2`, acoplado al orden
        de inserción de aristas del grafo sintético.)"""
        body = client_ruta.get("/ruta", query_string={**BASE, "date": "2025-06-15", "hora": "8"}).get_json()
        zd = body["zona_destino"]
        assert zd["zona"] in body["trafico_por_zona"]
        assert zd["nivel_trafico"] == body["trafico_por_zona"][zd["zona"]]


class TestZonaDeNodo:
    """`_zona_de_nodo`: distrito de un nodo a partir de las aristas del
    callejero incidentes. None cuando no se puede determinar."""

    def test_toma_la_zona_de_una_arista_saliente(self, srv):
        import networkx as nx
        G = nx.DiGraph()
        G.add_edge(1, 2, zona="Salamanca")
        assert srv._zona_de_nodo(G, 1) == "Salamanca"

    def test_tambien_mira_las_aristas_entrantes(self, srv):
        import networkx as nx
        G = nx.DiGraph()
        G.add_edge(1, 2, zona="Chamberi")
        assert srv._zona_de_nodo(G, 2) == "Chamberi"

    def test_ignora_zona_desconocida_y_aristas_sin_zona(self, srv):
        import networkx as nx
        G = nx.DiGraph()
        G.add_edge(1, 2, zona="Desconocida")
        G.add_edge(1, 3)  # arista de acceso a equipamiento: sin 'zona'
        assert srv._zona_de_nodo(G, 1) is None

    def test_nodo_fuera_del_grafo_es_none(self, srv):
        import networkx as nx
        assert srv._zona_de_nodo(nx.DiGraph(), 999) is None

    def test_nodo_multizona_devuelve_una_de_sus_zonas_incidentes(self, srv):
        """Un nodo con aristas en dos distritos: el contrato de _zona_de_nodo
        es 'la de cualquier arista incidente que no sea Desconocida', no una
        en concreto. Se afirma la pertenencia, no un orden."""
        import networkx as nx
        G = nx.DiGraph()
        G.add_edge(1, 2, zona="Salamanca")
        G.add_edge(2, 3, zona="Retiro")
        assert srv._zona_de_nodo(G, 2) in {"Salamanca", "Retiro"}

    def test_salta_desconocida_y_sigue_buscando_una_zona_real(self, srv):
        """La rama 'el bucle continúa': una arista 'Desconocida' antes que
        una real no debe cortar la búsqueda."""
        import networkx as nx
        G = nx.DiGraph()
        G.add_edge(1, 2)                      # sin zona
        G.add_edge(1, 3, zona="Desconocida")
        G.add_edge(1, 4, zona="Chamartin")
        assert srv._zona_de_nodo(G, 1) == "Chamartin"


class TestRutaAlgoritmo:
    """`algoritmo=` de /ruta. (El test de astar estaba mal ubicado dentro de
    TestZonaDeNodo; aquí, además, se compara A* con Dijkstra.)"""

    def test_algoritmo_por_defecto_es_dijkstra(self, client_ruta):
        body = client_ruta.get("/ruta", query_string=BASE).get_json()
        assert body["parametros_efectivos"]["algoritmo"] == "dijkstra"

    def test_astar_coincide_con_dijkstra_en_distancia_y_geometria(self, client_ruta):
        qs = {**BASE, "vehiculo": "autobomba_pesada"}
        d = client_ruta.get("/ruta", query_string={**qs, "algoritmo": "dijkstra"}).get_json()
        a = client_ruta.get("/ruta", query_string={**qs, "algoritmo": "astar"}).get_json()
        assert d["parametros_efectivos"]["algoritmo"] == "dijkstra"
        assert a["parametros_efectivos"]["algoritmo"] == "astar"
        dp = d["features"][0]["properties"]
        ap = a["features"][0]["properties"]
        assert dp["distancia_m"] == ap["distancia_m"] == 500.0   # desvío 1-3-2
        # mismo óptimo -> misma polilínea, no solo la misma métrica
        assert a["features"][0]["geometry"]["coordinates"] == d["features"][0]["geometry"]["coordinates"]


class TestRutaVehiculo:
    """Núcleo del bloque 3: cambiar de vehículo cambia la ruta REAL cuando
    la restricción física discrimina."""

    def test_antirregresion_dos_vehiculos_distinto_ancho_y_distinta_ruta(self, client_ruta):
        r_ligero = client_ruta.get("/ruta", query_string={**BASE, "vehiculo": "vehiculo_rescate"})
        r_pesado = client_ruta.get("/ruta", query_string={**BASE, "vehiculo": "autobomba_pesada"})
        assert r_ligero.status_code == r_pesado.status_code == 200
        b_ligero, b_pesado = r_ligero.get_json(), r_pesado.get_json()

        a_ligero = b_ligero["parametros_efectivos"]["ancho_req"]
        a_pesado = b_pesado["parametros_efectivos"]["ancho_req"]
        assert a_ligero == 2.1 and a_pesado == 3.5 and a_ligero != a_pesado
        # y el id de vehículo resuelto se refleja en ambos
        assert b_ligero["parametros_efectivos"]["vehiculo"] == "vehiculo_rescate"
        assert b_pesado["parametros_efectivos"]["vehiculo"] == "autobomba_pesada"

        d_ligero = b_ligero["features"][0]["properties"]["distancia_m"]
        d_pesado = b_pesado["features"][0]["properties"]["distancia_m"]
        assert d_ligero == 300.0  # atajo 1->2 (calle de 3.0 m, vale para 2.1 m)
        assert d_pesado == 500.0  # desvío 1-3-2 (la pesada no cabe en la de 3.0 m)
        assert d_ligero != d_pesado

        # la geometría es la del desvío (más vértices), no solo "distinta"
        g_ligero = b_ligero["features"][0]["geometry"]["coordinates"]
        g_pesado = b_pesado["features"][0]["geometry"]["coordinates"]
        assert g_ligero != g_pesado
        assert len(g_pesado) > len(g_ligero)  # 1-3-2 (3 pts) frente a 1-2 (2 pts)

    def test_el_galibo_tambien_discrimina_la_ruta_por_vehiculo(self, client_ruta):
        base_galibo = {"orig_lat": "40.40", "orig_lon": "-3.70", "dest_lat": "40.44", "dest_lon": "-3.74"}  # 1 -> 5
        b_ligero = client_ruta.get("/ruta", query_string={**base_galibo, "vehiculo": "vehiculo_rescate"}).get_json()
        b_pesado = client_ruta.get("/ruta", query_string={**base_galibo, "vehiculo": "autobomba_pesada"}).get_json()
        d_ligero = b_ligero["features"][0]["properties"]["distancia_m"]
        d_pesado = b_pesado["features"][0]["properties"]["distancia_m"]
        assert d_ligero == 200.0   # 1->5 directo (gálibo 2.6 <= 3.0 m)
        assert d_pesado == 700.0   # desvío 1-6-5 (gálibo 4.0 > 3.0 m: bloqueada por altura, no por anchura)
        # el gálibo efectivo se refleja, y el desvío tiene más vértices
        assert b_ligero["parametros_efectivos"]["galibo_req"] == 2.6
        assert b_pesado["parametros_efectivos"]["galibo_req"] == 4.0
        g_ligero = b_ligero["features"][0]["geometry"]["coordinates"]
        g_pesado = b_pesado["features"][0]["geometry"]["coordinates"]
        assert len(g_pesado) > len(g_ligero)

    def test_ancho_req_explicito_gana_sobre_el_vehiculo_y_queda_como_personalizado(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={
            **BASE, "vehiculo": "autobomba_pesada", "ancho_req": "2.0",
        })
        body = resp.get_json()
        assert body["parametros_efectivos"]["ancho_req"] == 2.0
        # el eje NO sobreescrito sigue viniendo de autobomba_pesada, no se resetea
        assert body["parametros_efectivos"]["galibo_req"] == 4.0
        assert body["parametros_efectivos"]["vehiculo"] == "personalizado"
        assert body["features"][0]["properties"]["distancia_m"] == 300.0  # toma el atajo pese a vehiculo=pesada

    def test_galibo_req_explicito_gana_sobre_el_vehiculo(self, client_ruta):
        base_galibo = {"orig_lat": "40.40", "orig_lon": "-3.70", "dest_lat": "40.44", "dest_lon": "-3.74"}  # 1 -> 5
        resp = client_ruta.get("/ruta", query_string={
            **base_galibo, "vehiculo": "autobomba_pesada", "galibo_req": "2.5",
        })
        body = resp.get_json()
        assert body["parametros_efectivos"]["galibo_req"] == 2.5
        assert body["parametros_efectivos"]["ancho_req"] == 3.5           # el eje no tocado
        assert body["parametros_efectivos"]["vehiculo"] == "personalizado"  # 2.5 no es de ningún vehículo
        assert body["features"][0]["properties"]["distancia_m"] == 200.0  # cabe por la calle de gálibo 3.0 m


class TestRutaParcial:
    def test_par_imposible_devuelve_200_ruta_parcial_no_404_ni_500(self, client_ruta):
        # destino nodo 9, solo accesible por una calle de 1.5 m: ningún vehículo pasa.
        resp = client_ruta.get("/ruta", query_string={
            "orig_lat": "40.40", "orig_lon": "-3.70",
            "dest_lat": "40.43", "dest_lon": "-3.73",
            "vehiculo": "vehiculo_rescate",
        })
        assert resp.status_code == 200
        feature = resp.get_json()["features"][0]
        props = feature["properties"]
        # núcleo contractual exacto + los dos campos de ruta parcial; `n_nodes`
        # permitido pero no exigido
        assert {"distancia_m", "tiempo_min", "ruta_completa",
                "distancia_sin_cubrir_m", "motivo"} <= set(props)
        assert set(props) <= {"distancia_m", "tiempo_min", "ruta_completa",
                              "distancia_sin_cubrir_m", "motivo", "n_nodes"}
        assert props["ruta_completa"] is False
        assert isinstance(props["distancia_sin_cubrir_m"], (int, float))
        assert props["distancia_sin_cubrir_m"] == pytest.approx(500.0)  # recta nodo 4 -> nodo 9
        assert props["distancia_m"] > 0  # cubrió algo de camino, no una parcial de longitud 0
        # el motivo nombra el ancho_req efectivo (tolerante al formato) y dice "parcial"
        assert re.search(r"2\.1", props["motivo"]) and "parcial" in props["motivo"].lower()
        _assert_linestring_4326(feature)


class TestRutaModoIdentidad:
    def test_origen_por_identidad_resuelve_el_parque_y_lo_refleja_en_origen(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={
            "orig_tipo_nodo": "bomberos", "orig_nombre": "Parque Test",
            "dest_lat": "40.41", "dest_lon": "-3.71",
        })
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["origen"] == {"node_id": "parque_1", "tipo_nodo": "bomberos", "nombre": "Parque Test"}
        assert body["destino"] == {"node_id": 2, "tipo_nodo": None, "nombre": None}
        assert body["features"][0]["properties"]["ruta_completa"] is True
        # la ruta ARRANCA en el nodo del parque. Sin `vehiculo` -> default
        # autobomba_pesada (3.5 m): no cabe en la calle 1-2 (3.0 m), así que
        # es parque_1 -> 1 -> 3 -> 2 = 50 + 250 + 250.
        assert body["features"][0]["properties"]["distancia_m"] == 550.0


class TestRutaModoIdentidadDestino:
    """Bloque 8: el modo identidad para el DESTINO (hospital real por
    nombre) -- el contrato lo describe como caso de uso principal y solo
    estaba cubierto su 404, nunca un 200."""

    def test_destino_por_identidad_resuelve_hospital_y_lo_refleja(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={
            "orig_lat": "40.40", "orig_lon": "-3.70",
            "dest_tipo_nodo": "hospitales", "dest_nombre": "Hospital Test",
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["destino"] == {"node_id": "hosp_1", "tipo_nodo": "hospitales", "nombre": "Hospital Test"}
        assert body["origen"] == {"node_id": 1, "tipo_nodo": None, "nombre": None}
        assert body["features"][0]["properties"]["ruta_completa"] is True
        assert body["features"][0]["properties"]["distancia_m"] == 50.0  # 1 -> hosp_1

    def test_ambos_extremos_por_identidad(self, client_ruta):
        resp = client_ruta.get("/ruta", query_string={
            "orig_tipo_nodo": "bomberos", "orig_nombre": "Parque Test",
            "dest_tipo_nodo": "hospitales", "dest_nombre": "Hospital Test",
        })
        assert resp.status_code == 200, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["origen"]["node_id"] == "parque_1"
        assert body["destino"]["node_id"] == "hosp_1"
        assert body["features"][0]["properties"]["distancia_m"] == 100.0  # parque_1 -> 1 -> hosp_1

    def test_origen_por_identidad_inexistente_devuelve_404(self, client_ruta, monkeypatch):
        import routing.graph_engine as ge
        monkeypatch.setattr(ge, "get_special_node", lambda tipo_nodo, nombre: None)
        resp = client_ruta.get("/ruta", query_string={
            "orig_tipo_nodo": "bomberos", "orig_nombre": "No Existe",
            "dest_lat": "40.41", "dest_lon": "-3.71",
        })
        _assert_error(resp, 404, "NOT_FOUND")

    def test_destino_bogus_node_id_devuelve_404_no_route(self, srv, client, monkeypatch):
        srv._cargar_grafo_al_arrancar()
        import routing.graph_engine as ge
        # origen válido (nodo 1), destino resuelto a un id que no está en el grafo
        monkeypatch.setattr(ge, "get_nearest_node", lambda lat, lon: 1 if lat == 40.40 else 999999)
        resp = client.get("/ruta", query_string={
            "orig_lat": "40.40", "orig_lon": "-3.70", "dest_lat": "40.41", "dest_lon": "-3.71",
        })
        _assert_error(resp, 404, "NO_ROUTE")


# ─────────────────────────────────────────────────────────────────────────
# /prediccion_trafico y caché de tráfico
# ─────────────────────────────────────────────────────────────────────────
class TestPrediccionTrafico:
    def test_usa_predecir_trafico_real_nunca_el_modelo_sintetico(self, srv, client, monkeypatch):
        llamadas = {"real": 0}

        def _fake_real(fecha, hora, zonas=None):
            llamadas["real"] += 1
            return {"Centro": 2, "Retiro": 0}

        def _fake_sintetico(*a, **k):
            raise AssertionError("ml.predict.predecir_trafico no debe llamarse desde /prediccion_trafico")

        monkeypatch.setattr("ml.predict_trafico_real.predecir_trafico_real", _fake_real)
        monkeypatch.setattr("ml.predict.predecir_trafico", _fake_sintetico)

        resp = client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert llamadas["real"] == 1
        assert body["trafico_por_zona"] == {"Centro": 2, "Retiro": 0}

    def test_el_servidor_importa_predict_trafico_real_y_no_el_modelo_sintetico(self, srv):
        """Estático: recorre los import de app/server.py. `ml.predict` (el
        modelo sintético viejo) no debe aparecer en ninguna forma."""
        tree = ast.parse(Path(srv.__file__).read_text(encoding="utf-8"))
        modulos = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modulos.add(node.module)
            elif isinstance(node, ast.Import):
                modulos.update(alias.name for alias in node.names)
        assert "ml.predict_trafico_real" in modulos
        assert "ml.predict" not in modulos

    def test_esquema_de_respuesta(self, srv, client, mock_trafico):
        body = client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"}).get_json()
        assert set(body) == {"parametros_efectivos", "trafico_por_zona"}
        assert set(body["parametros_efectivos"]) == {"fecha", "hora", "zonas"}
        assert body["parametros_efectivos"]["fecha"] == "2025-06-15"
        assert body["parametros_efectivos"]["hora"] == 8
        # `zonas` es EXACTAMENTE la fuente de verdad (get_distritos), no "contiene Centro"
        assert body["parametros_efectivos"]["zonas"] == list(srv.get_distritos())
        assert body["trafico_por_zona"] == {ZONA_A: 1, ZONA_B: 2}
        # openapi TraficoPorZona: todos los niveles en {0,1,2}
        assert all(v in (0, 1, 2) for v in body["trafico_por_zona"].values())

    def test_falta_date_devuelve_400(self, client):
        resp = client.get("/prediccion_trafico")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_fecha_invalida_devuelve_400(self, client):
        resp = client.get("/prediccion_trafico", query_string={"date": "no-es-una-fecha"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_hora_fuera_de_rango_devuelve_400(self, client):
        resp = client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "99"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"


class TestCacheTrafico:
    def test_misma_fecha_hora_una_sola_llamada_al_modelo(self, srv, client, mock_trafico):
        client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"})
        client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"})
        assert mock_trafico == [("2025-06-15", 8)]
        assert list(srv._traffic_cache) == [("2025-06-15", 8)]  # exactamente una entrada

    def test_distinta_hora_recalcula(self, srv, client, mock_trafico):
        client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"})
        client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "9"})
        assert mock_trafico == [("2025-06-15", 8), ("2025-06-15", 9)]
        assert set(srv._traffic_cache) == {("2025-06-15", 8), ("2025-06-15", 9)}

    def test_distinta_fecha_recalcula(self, srv, client, mock_trafico):
        client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"})
        client.get("/prediccion_trafico", query_string={"date": "2025-06-16", "hora": "8"})
        assert mock_trafico == [("2025-06-15", 8), ("2025-06-16", 8)]
        assert set(srv._traffic_cache) == {("2025-06-15", 8), ("2025-06-16", 8)}

    def test_cache_compartida_entre_prediccion_trafico_y_ruta(self, client_ruta, mock_trafico):
        pred = client_ruta.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"}).get_json()
        resp_ruta = client_ruta.get("/ruta", query_string={**BASE, "date": "2025-06-15", "hora": "8"})
        assert resp_ruta.status_code == 200
        assert mock_trafico == [("2025-06-15", 8)]  # la ruta reusó la entrada de caché, no volvió a predecir
        # y sirve EL MISMO valor, no solo evita la llamada
        assert resp_ruta.get_json()["trafico_por_zona"] == pred["trafico_por_zona"]


class TestTraficoModeloAusente:
    """Bloque 8: si se pide tráfico (`date`) y el modelo de P3 no está, la
    API corta con 503 MISSING_ARTIFACT -- nunca sirve una ruta 'sin
    tráfico' en silencio (openapi_p5.yaml /ruta §503). Y un fallo de
    predicción NO se cachea: la siguiente petición reintenta
    (comentario de server.py:_predecir_trafico_cacheado, hasta ahora sin
    ningún test)."""

    def _modelo_falla_las_primeras(self, monkeypatch, veces: int):
        estado = {"llamadas": 0}

        def _fake(fecha, hora, zonas=None):
            estado["llamadas"] += 1
            if estado["llamadas"] <= veces:
                raise FileNotFoundError(
                    "Modelo no encontrado en data/processed/modelo_trafico_xgboost.pkl. "
                    "Ejecuta notebooks/05_modelo_xgboost_densidad_trafico.ipynb."
                )
            return {ZONA_A: 1, ZONA_B: 2}

        monkeypatch.setattr("ml.predict_trafico_real.predecir_trafico_real", _fake)
        return estado

    def test_ruta_con_date_y_modelo_ausente_devuelve_503_no_ruta_sin_trafico(self, client_ruta, monkeypatch):
        self._modelo_falla_las_primeras(monkeypatch, veces=99)
        resp = client_ruta.get("/ruta", query_string={**BASE, "date": "2025-06-15", "hora": "8"})
        body = _assert_error(resp, 503, "MISSING_ARTIFACT")
        assert body["fichero"].endswith("modelo_trafico_xgboost.pkl")
        assert "modelo_trafico_xgboost.pkl" in body["error"]

    def test_prediccion_fallida_no_se_cachea_y_la_siguiente_reintenta(self, srv, client, monkeypatch):
        estado = self._modelo_falla_las_primeras(monkeypatch, veces=1)
        qs = {"date": "2025-06-15", "hora": "8"}

        r1 = client.get("/prediccion_trafico", query_string=qs)
        _assert_error(r1, 503, "MISSING_ARTIFACT")
        assert ("2025-06-15", 8) not in srv._traffic_cache      # el fallo no se guardó

        r2 = client.get("/prediccion_trafico", query_string=qs)
        assert r2.status_code == 200
        assert r2.get_json()["trafico_por_zona"] == {ZONA_A: 1, ZONA_B: 2}
        assert estado["llamadas"] == 2                          # reintentó de verdad
        assert srv._traffic_cache[("2025-06-15", 8)] == {ZONA_A: 1, ZONA_B: 2}

    def test_cache_trafico_nunca_guarda_none_tras_un_fallo(self, srv, client, monkeypatch):
        self._modelo_falla_las_primeras(monkeypatch, veces=1)
        client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"})
        assert None not in srv._traffic_cache.values()


# ─────────────────────────────────────────────────────────────────────────
# /isocronas
# ─────────────────────────────────────────────────────────────────────────
class TestIsocronasEndpoint:
    def test_cortes_por_defecto_y_esquema_de_respuesta(self, client_ruta, srv):
        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"})
        assert resp.mimetype == "application/geo+json"
        body = resp.get_json()
        assert set(body) == {"type", "features", "parque", "parametros_efectivos", "trafico_por_zona"}
        # los cortes son 5/10/15 literales (no "lo que diga la constante"); que la
        # constante valga eso lo vigila TestConfigEndpoint por separado.
        assert sorted(f["properties"]["corte_min"] for f in body["features"]) == [5, 10, 15]
        assert body["parque"] == {"node_id": "parque_1", "tipo_nodo": "bomberos", "nombre": "Parque Test"}
        assert set(body["parametros_efectivos"]) == {"ancho_req", "galibo_req", "fecha", "hora", "vehiculo"}
        assert body["parametros_efectivos"]["vehiculo"] == "autobomba_pesada"  # default del catálogo
        assert body["trafico_por_zona"] == {}  # sin `date`
        for f in body["features"]:
            _assert_polygon_4326(f)

    @pytest.mark.parametrize("corte", [5, 10, 15])
    def test_corte_min_filtra_a_una_sola_feature(self, client_ruta, corte):
        body = client_ruta.get("/isocronas",
                               query_string={"nombre": "Parque Test", "corte_min": str(corte)}).get_json()
        assert len(body["features"]) == 1
        assert body["features"][0]["properties"]["corte_min"] == corte

    def test_corte_min_no_numerico_devuelve_400(self, client_ruta):
        # rama distinta de "corte no soportado": el float() que revienta (server.py)
        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test", "corte_min": "abc"})
        body = _assert_error(resp, 400, "VALIDATION_ERROR")
        assert "numérico" in body["error"]

    def test_falta_nombre_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/isocronas")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_nombre_inexistente_devuelve_404(self, client_ruta):
        resp = client_ruta.get("/isocronas", query_string={"nombre": "No Existe"})
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "NOT_FOUND"

    def test_corte_min_no_soportado_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test", "corte_min": "7"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_vehiculo_inexistente_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test", "vehiculo": "no_existe"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_fecha_invalida_devuelve_400(self, client_ruta):
        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test", "date": "ayer"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_el_vehiculo_cambia_el_area_de_cobertura(self, client_ruta):
        """Mismo parque, mismo corte: el vehículo pesado no cruza la calle
        de 3.0 m que lleva al nodo 10 y el de rescate sí -> el polígono
        llega más al norte (maxy mayor)."""
        from shapely.geometry import shape

        b_pesado = client_ruta.get("/isocronas", query_string={
            "nombre": "Parque Test", "corte_min": "15",
        }).get_json()
        b_ligero = client_ruta.get("/isocronas", query_string={
            "nombre": "Parque Test", "corte_min": "15", "vehiculo": "vehiculo_rescate",
        }).get_json()

        poly_pesado = shape(b_pesado["features"][0]["geometry"])
        poly_ligero = shape(b_ligero["features"][0]["geometry"])
        assert not poly_pesado.is_empty and not poly_ligero.is_empty
        # cobertura ESTRICTAMENTE mayor, no solo "un borde más al norte"
        assert poly_ligero.area > poly_pesado.area
        assert poly_ligero.bounds[3] > poly_pesado.bounds[3], "el ligero debe alcanzar más al norte"

    def test_cache_isocronas_distingue_todas_las_dimensiones_de_la_clave(self, client_ruta, mock_trafico, monkeypatch):
        """La clave es (parque, ancho_req, galibo_req, fecha, hora). El test
        anterior solo ejercitaba ancho_req; aquí, cada dimensión por separado."""
        import routing.isochrones as iso
        real = iso.calcular_isocronas
        n = {"v": 0}

        def _contando(*a, **k):
            n["v"] += 1
            return real(*a, **k)

        monkeypatch.setattr(iso, "calcular_isocronas", _contando)
        base = {"nombre": "Parque Test"}

        client_ruta.get("/isocronas", query_string=base)
        client_ruta.get("/isocronas", query_string=base)
        assert n["v"] == 1  # misma clave completa -> una sola vez

        client_ruta.get("/isocronas", query_string={**base, "vehiculo": "vehiculo_rescate"})
        assert n["v"] == 2  # cambia ancho_req (y gálibo)

        client_ruta.get("/isocronas", query_string={**base, "galibo_req": "2.5"})
        assert n["v"] == 3  # cambia solo gálibo

        client_ruta.get("/isocronas", query_string={**base, "date": "2025-06-15", "hora": "8"})
        assert n["v"] == 4  # cambia fecha/hora (el tráfico también mueve la geometría)

        client_ruta.get("/isocronas", query_string={**base, "date": "2025-06-16", "hora": "8"})
        assert n["v"] == 5  # cambia solo la fecha


class TestIsocronasPrecomputadas:
    """Bloque 8: en la petición POR DEFECTO (vehículo de referencia, sin
    fecha) /isocronas sirve data/processed/isocronas_bomberos.geojson en vez
    de recorrer el grafo con Dijkstra (~6 s medidos). Cualquier otra
    combinación (vehículo distinto, ancho/gálibo explícitos, date) sigue
    calculando en vivo. Señal fuera del cuerpo JSON: cabecera
    X-Isocronas-Origen = precomputado | vivo."""

    def _escribir_precomp(self, path, parque="Parque Test", cortes=(5, 10, 15)):
        import geopandas as gpd
        from shapely.geometry import Polygon

        filas = []
        for i, c in enumerate(cortes, start=1):
            d = 0.01 * i
            filas.append({
                "parque": parque,
                "corte_min": c,
                "geometry": Polygon([
                    (-3.70, 40.40), (-3.70 + d, 40.40),
                    (-3.70 + d, 40.40 + d), (-3.70, 40.40 + d),
                ]),
            })
        gpd.GeoDataFrame(filas, crs="EPSG:4326").to_file(path, driver="GeoJSON")

    @pytest.fixture
    def spy_calcular(self, monkeypatch):
        """Cuenta llamadas a routing.isochrones.calcular_isocronas sin
        impedir el cálculo real."""
        import routing.isochrones as iso

        real = iso.calcular_isocronas
        n = {"v": 0}

        def _spy(*a, **k):
            n["v"] += 1
            return real(*a, **k)

        monkeypatch.setattr(iso, "calcular_isocronas", _spy)
        return n

    def _usar_precomp(self, srv, monkeypatch, path):
        monkeypatch.setattr(srv, "_ISOCRONAS_PRECOMP_PATH", str(path))
        srv._isocronas_precomp = None  # fuerza recarga desde `path`

    def test_peticion_por_defecto_sirve_el_fichero_sin_recorrer_el_grafo(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc)
        self._usar_precomp(srv, monkeypatch, pc)

        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"})
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "precomputado"
        assert spy_calcular["v"] == 0, "la petición por defecto NO debe recorrer el grafo"
        assert {f["properties"]["corte_min"] for f in resp.get_json()["features"]} == {5, 10, 15}

    def test_vehiculo_no_default_ignora_el_fichero_y_calcula_en_vivo(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc)
        self._usar_precomp(srv, monkeypatch, pc)

        resp = client_ruta.get("/isocronas", query_string={
            "nombre": "Parque Test", "vehiculo": "vehiculo_rescate",
        })
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "vivo"
        assert spy_calcular["v"] == 1

    def test_ancho_req_explicito_distinto_del_default_calcula_en_vivo(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc)
        self._usar_precomp(srv, monkeypatch, pc)

        resp = client_ruta.get("/isocronas", query_string={
            "nombre": "Parque Test", "ancho_req": "2.0",
        })
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "vivo"
        assert spy_calcular["v"] == 1

    def test_ancho_galibo_explicitos_iguales_al_default_usan_el_precomputado(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        """El polígono precomputado se generó con EXACTAMENTE el ancho/gálibo
        de referencia; pedirlos a mano con ese mismo valor da el mismo
        resultado -> se sirve de disco (se decide por valor efectivo, no por
        si el parámetro venía en la query)."""
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc)
        self._usar_precomp(srv, monkeypatch, pc)

        veh = srv.cfg.get_vehiculo_default()
        resp = client_ruta.get("/isocronas", query_string={
            "nombre": "Parque Test",
            "ancho_req": str(veh["ancho_m"]),
            "galibo_req": str(veh["galibo_m"]),
        })
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "precomputado"
        assert spy_calcular["v"] == 0

    def test_con_fecha_calcula_en_vivo_aunque_el_fichero_cubra_el_parque(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular, mock_trafico
    ):
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc)
        self._usar_precomp(srv, monkeypatch, pc)

        resp = client_ruta.get("/isocronas", query_string={
            "nombre": "Parque Test", "date": "2025-06-15", "hora": "8",
        })
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "vivo"
        assert spy_calcular["v"] == 1

    def test_fichero_ausente_cae_a_calculo_en_vivo(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        self._usar_precomp(srv, monkeypatch, tmp_path / "no_existe.geojson")

        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"})
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "vivo"
        assert spy_calcular["v"] == 1

    def test_fichero_sin_ese_parque_cae_a_calculo_en_vivo(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc, parque="Parque de Otra Ciudad")
        self._usar_precomp(srv, monkeypatch, pc)

        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"})
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "vivo"
        assert spy_calcular["v"] == 1

    def test_fichero_corrupto_cae_a_calculo_en_vivo_sin_500(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        """Un isocronas_bomberos.geojson ilegible NUNCA debe tumbar /isocronas
        con un 500: se degrada a cálculo en vivo (regla de P5, 'si falta un
        artefacto: error explícito' -> aquí ni eso, es un acelerador opcional)."""
        pc = tmp_path / "isocronas_bomberos.geojson"
        pc.write_text("esto no es GeoJSON {{{")
        self._usar_precomp(srv, monkeypatch, pc)

        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"})
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "vivo"
        assert spy_calcular["v"] == 1

    def test_fichero_con_cobertura_parcial_cae_a_calculo_en_vivo(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc, cortes=(5, 10))  # falta el corte 15
        self._usar_precomp(srv, monkeypatch, pc)

        resp = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"})
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "vivo"
        assert spy_calcular["v"] == 1

    def test_corte_min_filtra_sobre_el_precomputado(
        self, client_ruta, srv, monkeypatch, tmp_path, spy_calcular
    ):
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc)
        self._usar_precomp(srv, monkeypatch, pc)

        resp = client_ruta.get("/isocronas", query_string={
            "nombre": "Parque Test", "corte_min": "10",
        })
        assert resp.status_code == 200
        assert resp.headers["X-Isocronas-Origen"] == "precomputado"
        assert [f["properties"]["corte_min"] for f in resp.get_json()["features"]] == [10]
        assert spy_calcular["v"] == 0

    def test_respuesta_precomputada_conserva_el_sobre_del_contrato(
        self, client_ruta, srv, monkeypatch, tmp_path
    ):
        """openapi_p5.yaml: required [type, features, parque,
        parametros_efectivos, trafico_por_zona]. El origen (disco vs vivo)
        no cambia la forma."""
        pc = tmp_path / "isocronas_bomberos.geojson"
        self._escribir_precomp(pc)
        self._usar_precomp(srv, monkeypatch, pc)

        body = client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"}).get_json()
        for clave in ("type", "features", "parque", "parametros_efectivos", "trafico_por_zona"):
            assert clave in body, clave
        assert body["parque"]["nombre"] == "Parque Test"
        assert body["parametros_efectivos"]["fecha"] is None
        assert body["trafico_por_zona"] == {}


# ─────────────────────────────────────────────────────────────────────────
# /equipamientos
# ─────────────────────────────────────────────────────────────────────────
class TestEquipamientos:
    def test_tipo_invalido_devuelve_400(self, client):
        resp = client.get("/equipamientos?tipo=no_existe")
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_tipo_valido_con_fichero_ausente_devuelve_404_con_fichero(self, srv, client, monkeypatch, tmp_path):
        ausente = tmp_path / "no_existe.geojson"
        monkeypatch.setitem(srv.ARTEFACTOS_ESPERADOS, "parques_bomberos_geojson", str(ausente))
        resp = client.get("/equipamientos?tipo=bomberos")
        assert resp.status_code == 404
        body = resp.get_json()
        assert body["code"] == "MISSING_ARTIFACT"
        assert body["fichero"].endswith("no_existe.geojson")

    def test_tipo_valido_con_fichero_presente_devuelve_geojson_real(self, srv, client, monkeypatch, tmp_path):
        import geopandas as gpd
        from shapely.geometry import Point

        p = tmp_path / "parques.geojson"
        gpd.GeoDataFrame(
            {"nombre": ["Parque Uno", "Parque Dos"], "geometry": [Point(-3.70, 40.40), Point(-3.71, 40.41)]},
            crs="EPSG:4326",
        ).to_file(p, driver="GeoJSON")
        monkeypatch.setitem(srv.ARTEFACTOS_ESPERADOS, "parques_bomberos_geojson", str(p))

        resp = client.get("/equipamientos?tipo=bomberos")
        assert resp.status_code == 200
        assert resp.mimetype == "application/geo+json"
        body = resp.get_json()
        assert len(body["features"]) == 2
        assert {f["properties"]["nombre"] for f in body["features"]} == {"Parque Uno", "Parque Dos"}
        # openapi_p5.yaml: 200 required [type, features, tipo]. `tipo` = el
        # parámetro efectivo resuelto, como en el resto de endpoints.
        assert body["type"] == "FeatureCollection"
        assert body["tipo"] == "bomberos"


class TestParametrosEfectivosUniversal:
    """Invariante de la fase (docs/p5/README_P5_API_Streamlit.md §1): 'Toda
    respuesta de la API devuelve los parámetros efectivos'. openapi define
    la clave por endpoint."""

    @pytest.mark.parametrize("endpoint,qs", [
        ("/ruta", "BASE_DATE"),
        ("/isocronas", {"nombre": "Parque Test"}),
        ("/prediccion_trafico", {"date": "2025-06-15", "hora": "8"}),
    ])
    def test_endpoint_200_lleva_bloque_parametros_efectivos(self, client_ruta, mock_trafico, endpoint, qs):
        if qs == "BASE_DATE":
            qs = {**BASE, "date": "2025-06-15", "hora": "8"}
        resp = client_ruta.get(endpoint, query_string=qs)
        assert resp.status_code == 200, resp.get_data(as_text=True)
        assert "parametros_efectivos" in resp.get_json()

    def test_equipamientos_200_refleja_el_tipo_efectivo(self, srv, client, monkeypatch, tmp_path):
        import geopandas as gpd
        from shapely.geometry import Point

        p = tmp_path / "hosp.geojson"
        gpd.GeoDataFrame({"nombre": ["H"], "geometry": [Point(-3.70, 40.42)]},
                         crs="EPSG:4326").to_file(p, driver="GeoJSON")
        monkeypatch.setitem(srv.ARTEFACTOS_ESPERADOS, "hospitales_geojson", str(p))
        body = client.get("/equipamientos?tipo=hospitales").get_json()
        assert body["tipo"] == "hospitales"  # el efectivo, no el default "bomberos"


# ─────────────────────────────────────────────────────────────────────────
# /geocodificar — texto libre -> coordenadas (envuelve Nominatim, mockeado)
# ─────────────────────────────────────────────────────────────────────────
class _RespNominatim:
    def __init__(self, payload, status=200, bad_json=False):
        self._payload = payload
        self.status_code = status
        self._bad_json = bad_json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self._bad_json:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


def _mock_nominatim(monkeypatch, payload=None, exc=None, status=200, bad_json=False):
    """Sustituye requests.get por un doble de Nominatim. Devuelve la lista
    de llamadas (url/params/headers) para afirmar sobre la petición saliente."""
    import requests
    llamadas = []

    def _fake_get(url, params=None, headers=None, timeout=None):
        llamadas.append({"url": url, "params": params, "headers": headers})
        if exc is not None:
            raise exc
        return _RespNominatim(payload, status=status, bad_json=bad_json)

    monkeypatch.setattr(requests, "get", _fake_get)
    return llamadas


class TestGeocodificar:
    def _mock_nominatim(self, monkeypatch, **kw):
        return _mock_nominatim(monkeypatch, **kw)

    def test_falta_q_devuelve_400(self, client):
        assert client.get("/geocodificar").status_code == 400
        assert client.get("/geocodificar?q=%20%20").get_json()["code"] == "VALIDATION_ERROR"

    def test_direccion_valida_en_madrid_devuelve_lat_lon_y_direccion(self, client, monkeypatch):
        llamadas = self._mock_nominatim(monkeypatch, payload=[{
            "lat": "40.4200", "lon": "-3.7010", "display_name": "Calle de Alcalá, 100, Madrid, España",
        }])
        resp = client.get("/geocodificar", query_string={"q": "Calle de Alcalá 100"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body == {"lat": 40.42, "lon": -3.701, "direccion": "Calle de Alcalá, 100, Madrid, España"}
        # se consulta a Nominatim con un User-Agent y sesgado a Madrid/España
        assert "nominatim" in llamadas[0]["url"]
        assert "User-Agent" in llamadas[0]["headers"]
        assert "Madrid" in llamadas[0]["params"]["q"]

    def test_sin_resultados_devuelve_404(self, client, monkeypatch):
        self._mock_nominatim(monkeypatch, payload=[])
        resp = client.get("/geocodificar", query_string={"q": "calle inventada zzz"})
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "DIRECCION_NO_ENCONTRADA"

    def test_resultado_fuera_de_madrid_devuelve_404(self, client, monkeypatch):
        # Barcelona: dentro de España, fuera del bbox de Madrid
        self._mock_nominatim(monkeypatch, payload=[{"lat": "41.3870", "lon": "2.1700", "display_name": "Barcelona"}])
        resp = client.get("/geocodificar", query_string={"q": "Plaça Catalunya"})
        assert resp.status_code == 404
        assert resp.get_json()["code"] == "DIRECCION_NO_ENCONTRADA"

    @pytest.mark.parametrize("caso", ["connection-error", "timeout", "http-500", "http-502", "json-malformado"])
    def test_nominatim_caido_o_ilegible_devuelve_503_no_5xx_generico(self, client, monkeypatch, caso):
        """Antes solo se probaba ConnectionError. El `except` real captura
        (RequestException, ValueError) -> Timeout, HTTPError (de
        raise_for_status) y JSON malformado deben dar todos 503, no 500."""
        import requests
        kwargs = {
            "connection-error": {"exc": requests.ConnectionError("boom")},
            "timeout": {"exc": requests.Timeout("timeout")},
            "http-500": {"status": 500},
            "http-502": {"status": 502},
            "json-malformado": {"bad_json": True},
        }[caso]
        self._mock_nominatim(monkeypatch, **kwargs)
        resp = client.get("/geocodificar", query_string={"q": "Puerta del Sol"})
        _assert_error(resp, 503, "GEOCODER_UNAVAILABLE")

    @pytest.mark.parametrize("payload", [
        pytest.param([{"lat": "no-es-un-numero", "lon": "-3.70"}], id="lat-no-numerica"),
        pytest.param([{"display_name": "sin lat ni lon"}], id="sin-lat-lon"),
    ])
    def test_resultado_de_nominatim_no_interpretable_devuelve_503(self, client, monkeypatch, payload):
        self._mock_nominatim(monkeypatch, payload=payload)
        resp = client.get("/geocodificar", query_string={"q": "algo"})
        _assert_error(resp, 503, "GEOCODER_UNAVAILABLE")

    def test_no_toca_el_grafo(self, srv, client, monkeypatch):
        """El geocodificador no necesita el grafo cargado."""
        import routing.graph_engine as ge
        monkeypatch.setattr(ge, "load_graph", lambda: (_ for _ in ()).throw(FileNotFoundError("sin grafo")))
        self._mock_nominatim(monkeypatch, payload=[{"lat": "40.4168", "lon": "-3.7038", "display_name": "Centro, Madrid"}])
        assert client.get("/geocodificar", query_string={"q": "Sol"}).status_code == 200


# ─────────────────────────────────────────────────────────────────────────
# Fronteras de MADRID_BBOX (bloque 8 — follow-up)
# ─────────────────────────────────────────────────────────────────────────
class TestCoordenadasBBox:
    """`_validar_coordenadas_madrid` y el filtro de /geocodificar usan
    `cfg.MADRID_BBOX` con cortes inclusivos (<= / >=). Las fronteras no se
    probaban: un `<` en vez de `<=`, o encoger la caja, pasaba
    desapercibido."""

    _FUERA = [
        ("lat-alta", {"orig_lat": "40.5701"}),
        ("lat-baja", {"orig_lat": "40.2999"}),
        ("lon-alta", {"orig_lon": "-3.4999"}),
        ("lon-baja", {"orig_lon": "-3.9001"}),
    ]
    _BORDE = [
        ("lat_max", {"orig_lat": "40.57"}),
        ("lat_min", {"orig_lat": "40.30"}),
        ("lon_max", {"orig_lon": "-3.50"}),
        ("lon_min", {"orig_lon": "-3.90"}),
    ]

    @pytest.fixture
    def client_bbox(self, srv, client, monkeypatch):
        srv._cargar_grafo_al_arrancar()
        import routing.graph_engine as ge
        monkeypatch.setattr(ge, "get_nearest_node", lambda lat, lon: 1)
        return client

    @pytest.mark.parametrize("_id,override", _FUERA, ids=[i for i, _ in _FUERA])
    def test_ruta_rechaza_coordenadas_pasado_el_borde(self, client_bbox, _id, override):
        body = _assert_error(client_bbox.get("/ruta", query_string={**BASE, **override}), 400, "VALIDATION_ERROR")
        assert "fuera de Madrid" in body["error"]

    @pytest.mark.parametrize("_id,override", _BORDE, ids=[i for i, _ in _BORDE])
    def test_ruta_acepta_el_borde_exacto(self, client_bbox, _id, override):
        resp = client_bbox.get("/ruta", query_string={**BASE, **override})
        # borde inclusivo: no puede rechazarse POR la bbox (que luego dé 200
        # o 404 por topología es otra cosa)
        assert resp.status_code != 400 or "fuera de Madrid" not in resp.get_json().get("error", "")

    def test_geocodificar_filtra_por_la_misma_caja(self, client, monkeypatch):
        _mock_nominatim(monkeypatch, payload=[{"lat": "40.5701", "lon": "-3.70", "display_name": "x"}])
        assert client.get("/geocodificar", query_string={"q": "x"}).get_json()["code"] == "DIRECCION_NO_ENCONTRADA"
        _mock_nominatim(monkeypatch, payload=[{"lat": "40.57", "lon": "-3.70", "display_name": "x"}])
        assert client.get("/geocodificar", query_string={"q": "x"}).status_code == 200

    def test_bbox_es_deliberadamente_generosa(self, client, monkeypatch):
        """Getafe (~40.305, -3.733): fuera del término municipal de Madrid
        pero DENTRO de la caja generosa -> se acepta. Fija la laxitud
        documentada en app/config.py; estrecharla debe romper este test a
        propósito."""
        _mock_nominatim(monkeypatch, payload=[{"lat": "40.3050", "lon": "-3.7330", "display_name": "Getafe"}])
        assert client.get("/geocodificar", query_string={"q": "Getafe"}).status_code == 200


# ─────────────────────────────────────────────────────────────────────────
# Carga única del grafo — a nivel de endpoint (bloque 8 — follow-up)
# ─────────────────────────────────────────────────────────────────────────
class TestCargaUnicaDelGrafo:
    """Invariante de la fase (docs/p5/README_P5_API_Streamlit.md §4): 'el
    grafo se carga una única vez al arrancar, nunca dentro de un endpoint'.
    Antes solo se comprobaba sobre `get_graph()`."""

    def _contador_load_graph(self, monkeypatch):
        import routing.graph_engine as ge
        n = {"v": 0}
        real = _grafo_sintetico

        def _contando():
            n["v"] += 1
            return real()

        monkeypatch.setattr(ge, "load_graph", _contando)
        return n

    def test_ruta_no_vuelve_a_cargar_el_grafo(self, client_ruta, monkeypatch):
        n = self._contador_load_graph(monkeypatch)
        for _ in range(3):
            assert client_ruta.get("/ruta", query_string=BASE).status_code == 200
        assert n["v"] == 0

    def test_isocronas_no_vuelve_a_cargar_el_grafo(self, client_ruta, monkeypatch):
        n = self._contador_load_graph(monkeypatch)
        for _ in range(3):
            assert client_ruta.get("/isocronas", query_string={"nombre": "Parque Test"}).status_code == 200
        assert n["v"] == 0

    def test_health_no_recarga_ni_con_el_grafo_ya_cargado(self, srv, client, monkeypatch):
        srv._cargar_grafo_al_arrancar()  # rama 200 (la existente prueba la 503)
        n = self._contador_load_graph(monkeypatch)
        client.get("/health")
        client.get("/health")
        assert n["v"] == 0

    def test_prediccion_trafico_no_toca_el_grafo(self, srv, client, monkeypatch):
        import routing.graph_engine as ge
        monkeypatch.setattr(ge, "load_graph", lambda: (_ for _ in ()).throw(FileNotFoundError("sin grafo")))
        monkeypatch.setattr("ml.predict_trafico_real.predecir_trafico_real",
                            lambda fecha, hora, zonas=None: {ZONA_A: 1})
        resp = client.get("/prediccion_trafico", query_string={"date": "2025-06-15", "hora": "8"})
        assert resp.status_code == 200


# ─────────────────────────────────────────────────────────────────────────
# Invariantes transversales de contrato (bloque 8 — follow-up)
# ─────────────────────────────────────────────────────────────────────────
class TestInvariantesContrato:
    def test_codes_de_error_del_test_coinciden_con_el_enum_de_openapi(self):
        """`_CODES_ERROR_VALIDOS` debe ser exactamente el enum
        ErrorResponse.code de docs/p5/openapi_p5.yaml -- si divergen, uno de
        los dos está desactualizado."""
        import yaml
        spec = yaml.safe_load((PROJECT_ROOT / "docs" / "p5" / "openapi_p5.yaml").read_text(encoding="utf-8"))
        enum = set(spec["components"]["schemas"]["ErrorResponse"]["properties"]["code"]["enum"])
        assert enum == _CODES_ERROR_VALIDOS

    def test_ruta_no_invoca_la_alerta_meteorologica(self, client_ruta, mock_trafico, monkeypatch):
        """docs/adr/0003: la alerta meteo es informativa y NO participa en
        el cálculo de la ruta. (`/meteorologia` tiene su suite completa en
        tests/test_meteorologia.py; aquí solo el invariante inverso.)"""
        import app.meteo_alerta as meteo_alerta
        monkeypatch.setattr(meteo_alerta, "clasificar_alerta", _boom)
        monkeypatch.setattr("app.server._get_condiciones_meteo", _boom)
        assert client_ruta.get("/ruta", query_string={**BASE, "date": "2025-06-15", "hora": "8"}).status_code == 200


# ─────────────────────────────────────────────────────────────────────────
# Bloque 8B — logs estructurados JSON + request_id + operaciones caras
# ─────────────────────────────────────────────────────────────────────────
class _CapturaLogs(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.registros: list[logging.LogRecord] = []

    def emit(self, record):
        self.registros.append(record)


@contextlib.contextmanager
def _capturar_logs():
    """Engancha un handler propio al root logger (con el MISMO filtro de
    request_id que el handler JSON de producción) y baja el nivel a DEBUG
    mientras dura el bloque."""
    import app.logging_setup as ls

    h = _CapturaLogs()
    h.addFilter(ls.RequestIdFilter())
    root = logging.getLogger()
    nivel_previo = root.level
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    try:
        yield h.registros
    finally:
        root.removeHandler(h)
        root.setLevel(nivel_previo)


def _caras(registros, operacion):
    return [
        r for r in registros
        if getattr(r, "evento", None) == "operacion_cara"
        and getattr(r, "operacion", None) == operacion
    ]


class TestLoggingEstructurado:
    def test_cada_peticion_emite_una_linea_json_de_acceso(self, srv, client):
        with _capturar_logs() as registros:
            client.get("/health")
        accesos = [r for r in registros if getattr(r, "evento", None) == "peticion"]
        assert len(accesos) == 1
        rec = accesos[0]
        assert rec.metodo == "GET" and rec.ruta == "/health"
        assert isinstance(rec.status, int)
        assert isinstance(rec.duracion_ms, float)
        # la línea es JSON real y serializable con el formatter de producción
        linea = json.loads(srv.logging_setup.JsonFormatter().format(rec))
        assert linea["evento"] == "peticion" and linea["ruta"] == "/health"

    def test_request_id_generado_viaja_a_la_traza_y_a_la_cabecera(self, srv, client):
        with _capturar_logs() as registros:
            resp = client.get("/health")
        rid = resp.headers.get("X-Request-ID")
        assert rid and len(rid) == 32  # uuid4().hex
        acceso = next(r for r in registros if getattr(r, "evento", None) == "peticion")
        assert acceso.request_id == rid

    def test_respeta_un_x_request_id_entrante_si_es_sano(self, srv, client):
        with _capturar_logs() as registros:
            resp = client.get("/health", headers={"X-Request-ID": "trace-abc_123"})
        assert resp.headers["X-Request-ID"] == "trace-abc_123"
        acceso = next(r for r in registros if getattr(r, "evento", None) == "peticion")
        assert acceso.request_id == "trace-abc_123"

    @pytest.mark.parametrize("basura", [
        "con espacios",          # espacio -> fuera del allowlist
        "punto;coma",            # separador de cabecera
        "unicode-ñ",             # no ASCII
        "x" * 200,               # más de 128 -> se descarta entero
        "",                      # vacío
    ])
    def test_descarta_un_x_request_id_que_no_cumple_el_allowlist(self, srv, client, basura):
        """Solo se acepta ^[A-Za-z0-9._-]{1,128}$. Cualquier otra cosa
        (espacios, no-ASCII, separadores, exceso de longitud) se ignora y
        se genera un id limpio -> nada raro del cliente acaba en el log."""
        with _capturar_logs() as registros:
            resp = client.get("/health", headers={"X-Request-ID": basura})
        rid = resp.headers["X-Request-ID"]
        assert rid != basura and len(rid) == 32
        acceso = next(r for r in registros if getattr(r, "evento", None) == "peticion")
        assert acceso.request_id == rid

    def test_carga_del_grafo_emite_operacion_cara_con_duracion(self, srv):
        with _capturar_logs() as registros:
            srv._cargar_grafo_al_arrancar()
        (rec,) = _caras(registros, "carga_grafo")
        assert rec.ok is True
        assert isinstance(rec.duracion_ms, float) and rec.duracion_ms >= 0
        assert rec.nodos == 12 and rec.aristas == 26

    def test_fallo_de_carga_del_grafo_emite_operacion_cara_ok_false(self, srv, monkeypatch):
        import routing.graph_engine as ge

        def _falla():
            raise FileNotFoundError("Callejero no encontrado en data/processed/x.geojson.")

        monkeypatch.setattr(ge, "load_graph", _falla)
        with _capturar_logs() as registros:
            srv._cargar_grafo_al_arrancar()
        (rec,) = _caras(registros, "carga_grafo")
        assert rec.ok is False

    def test_calculo_de_ruta_emite_operacion_cara(self, client_ruta, mock_trafico):
        with _capturar_logs() as registros:
            resp = client_ruta.get("/ruta", query_string=BASE)
        assert resp.status_code == 200
        (rec,) = _caras(registros, "calculo_ruta")
        assert rec.algoritmo == "dijkstra"
        assert isinstance(rec.duracion_ms, float)

    def test_prediccion_de_trafico_solo_traza_en_el_miss_de_cache(self, client_ruta, mock_trafico):
        qs = {"date": "2025-06-15", "hora": "8"}
        with _capturar_logs() as registros:
            client_ruta.get("/prediccion_trafico", query_string=qs)
            client_ruta.get("/prediccion_trafico", query_string=qs)  # hit de caché
        assert len(_caras(registros, "prediccion_trafico")) == 1
        assert len(mock_trafico) == 1  # coherente: el modelo se llamó una vez


# ─────────────────────────────────────────────────────────────────────────
# Suite lenta — grafo real de Madrid (excluida salvo --runslow)
# ─────────────────────────────────────────────────────────────────────────
@pytest.mark.slow
def test_ruta_real_extremo_a_extremo_sobre_el_grafo_de_madrid():
    """Ruta conocida de validacion_rutas_emergencia.csv / PARES_VALIDACION:
    PARQUE DE BOMBEROS 01. CHAMBERÍ -> Puerta del Sol (40.4169, -3.7025),
    de punta a punta por HTTP contra el grafo real (~171 k nodos)."""
    callejero = PROJECT_ROOT / "data" / "processed" / "madrid_callejero_filtered.geojson"
    if not callejero.exists():
        pytest.skip(f"falta {callejero} — regenerar con python pipeline/run_pipeline.py")

    import routing.graph_engine as ge
    ge._graph = ge._kdtree = ge._node_items = None
    try:
        if "app.server" in sys.modules:
            server = importlib.reload(sys.modules["app.server"])
        else:
            import app.server as server
        server._cargar_grafo_al_arrancar()
        assert server._graph_state["G"] is not None, server._graph_state["error"]
        client = server.app.test_client()

        qs = {
            "orig_tipo_nodo": "bomberos", "orig_nombre": "PARQUE DE BOMBEROS 01. CHAMBERÍ",
            "dest_lat": "40.4169", "dest_lon": "-3.7025", "vehiculo": "vehiculo_rescate",
        }
        r_dij = client.get("/ruta", query_string={**qs, "algoritmo": "dijkstra"})
        r_ast = client.get("/ruta", query_string={**qs, "algoritmo": "astar"})
        assert r_dij.status_code == 200, r_dij.get_data(as_text=True)
        assert r_ast.status_code == 200, r_ast.get_data(as_text=True)

        body = r_dij.get_json()
        props = body["features"][0]["properties"]
        assert props["ruta_completa"] is True
        assert isinstance(body["origen"]["node_id"], str) and body["origen"]["node_id"].startswith("bomberos")
        assert body["origen"]["nombre"] == "PARQUE DE BOMBEROS 01. CHAMBERÍ"
        assert body["parametros_efectivos"]["vehiculo"] == "vehiculo_rescate"
        assert body["parametros_efectivos"]["ancho_req"] == 2.1
        # distancia medida el 2026-08-27: 4235.6 m. Banda amplia por si P1 regenera el callejero.
        assert 3000 < props["distancia_m"] < 6000
        # A* y Dijkstra optimizan el MISMO coste sobre el MISMO grafo: el
        # camino mínimo tiene que coincidir salvo ruido de coma flotante. Un
        # 5 % de holgura enmascaraba una heurística A* no admisible.
        d_ast = r_ast.get_json()["features"][0]["properties"]["distancia_m"]
        assert d_ast == pytest.approx(props["distancia_m"], rel=1e-6)

        # cross-check opcional contra la tabla de validación, si está generada
        csv_path = PROJECT_ROOT / "validacion_rutas_emergencia.csv"
        if csv_path.exists():
            import csv as _csv
            with open(csv_path, encoding="utf-8") as f:
                filas = [
                    r for r in _csv.DictReader(f)
                    if r["parque"] == "PARQUE DE BOMBEROS 01. CHAMBERÍ" and r["destino"] == "Puerta del Sol"
                ]
            if filas and filas[0].get("distancia_km"):
                assert abs(float(filas[0]["distancia_km"]) - props["distancia_m"] / 1000) < 1.5
    finally:
        ge._graph = ge._kdtree = ge._node_items = None
