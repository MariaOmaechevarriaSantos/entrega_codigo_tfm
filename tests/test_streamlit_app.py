"""
Tests del dashboard Streamlit (app/streamlit_app.py) -- bloque 4 de P5.

Usa streamlit.testing.v1.AppTest (ejecuta el script real en memoria, sin
navegador) con requests.get mockeado -- ningún test depende de un servidor
Flask real levantado ni de datos en data/processed/, igual de aislado que
el resto de la suite. Cubre exactamente lo que cambió en este bloque:
catálogo real de parques (ya no 4 ficticios), origen enviado como identidad
de nodo (no coordenada), vehículo/ancho/gálibo/algoritmo reenviados a
/ruta, aviso explícito de ruta parcial, y degradación legible si la API
no responde.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app" / "streamlit_app.py")
SISTEMA_PATH = str(Path(__file__).resolve().parent.parent / "app" / "pages" / "1_Sistema.py")

# /health "operativo" por defecto (bloque 7). El dashboard sondea /health al
# arrancar en TODOS los reruns; un test que no lo mockee a propósito recibe
# este cuerpo -- mismo patrón que la respuesta meteo "normal" por defecto.
HEALTH_OK = {
    "status": "ok",
    "grafo": {"cargado": True, "nodes": 171646, "edges": 235592,
              "nodos_especiales": {"bomberos": 13, "hospitales": 277},
              "segundos_carga": 16.7, "error": None},
    "modelo_trafico": {"cargado": False, "ruta_pkl": "data/processed/modelo_trafico_xgboost.pkl"},
    "duckdb": {"accesible": True, "ruta": "data/processed/tfm_madrid.duckdb"},
    "artefactos_faltantes": ["data/processed/modelo_trafico_xgboost.pkl"],
    "timestamp": "2026-08-27T10:00:00+00:00",
}


@pytest.fixture(autouse=True)
def _arranque_inmediato(monkeypatch):
    """Sin reintentos de arranque en los tests: si /health no da 200, el
    dashboard debe ir directo al panel de error (no dormir ni st.rerun en
    bucle). Los tests que ejercitan el arranque a propósito lo dejan así (0)
    y comprueban el estado visible."""
    monkeypatch.setenv("DASHBOARD_REINTENTOS_ARRANQUE", "0")
    monkeypatch.setenv("DASHBOARD_ESPERA_ARRANQUE_S", "0")


@pytest.fixture(autouse=True)
def _sin_cache_entre_tests():
    """st.cache_data(ttl=60) de _cargar_config/_cargar_parques persiste en un
    caché global de proceso -- sin esto, un test que corra después de otro que
    cacheó una respuesta 200 vería esa respuesta cacheada en vez de la suya
    propia (falso positivo/negativo cruzado entre tests)."""
    import streamlit as st
    st.cache_data.clear()
    yield
    st.cache_data.clear()


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code

    def json(self):
        return self._json_body


def _feature_collection(features):
    return {"type": "FeatureCollection", "features": features}


def _parque_feature(nombre, lon, lat):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"nombre": nombre},
    }


PARQUES_FALSOS = [
    _parque_feature("PARQUE FALSO A", -3.70, 40.40),
    _parque_feature("PARQUE FALSO B", -3.71, 40.41),
    _parque_feature("PARQUE FALSO C", -3.72, 40.42),
]

def _meteo(nivel="normal", criterio="sin condiciones adversas en las 20 estaciones que reportan",
           origen="live", edad_min=0):
    return {
        "informativo": True,
        "aviso": "Alerta meramente informativa: no modifica el cálculo de la ruta.",
        "disponible": True,
        "origen": origen,
        "edad_min": edad_min,
        "alerta": {
            "nivel": nivel, "criterio": criterio,
            "nota_informativa": "Alerta meramente informativa: no modifica el cálculo de la ruta.",
        },
        "parametros_efectivos": {"fuente": "municipal", "zona": None, "timestamp_lectura": None},
        "meteorologia_por_zona": {},
    }


# Banda meteo normal por defecto para los tests que no la ejercitan a propósito.
METEO_NORMAL = _meteo()

CONFIG_FALSO = {
    "vehiculos": [
        {"id": "autobomba_pesada", "nombre": "Autobomba Pesada (BUP)", "ancho_req_m": 3.5, "galibo_req_m": 4.0, "descripcion": "Pesado"},
        {"id": "vehiculo_rescate", "nombre": "Vehículo de Rescate (VR)", "ancho_req_m": 2.1, "galibo_req_m": 2.6, "descripcion": "Ligero"},
    ],
    "vehiculo_default": "autobomba_pesada",
    "algoritmos_disponibles": ["dijkstra", "astar"],
    "algoritmo_default": "dijkstra",
    "cortes_isocronas_min_default": [5, 10, 15],
    "trafico_niveles": {"0": "Bajo", "1": "Medio", "2": "Alto"},
}


def _ruta_feature(ruta_completa: bool, distancia_sin_cubrir_m: float = 0.0,
                  zona_destino: dict | None = None):
    props = {
        "distancia_m": 500.0, "tiempo_min": 2.0, "n_nodes": 5, "ruta_completa": ruta_completa,
    }
    if not ruta_completa:
        props["distancia_sin_cubrir_m"] = distancia_sin_cubrir_m
        props["motivo"] = "Destino no alcanzable; ruta parcial."
    return {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[-3.70, 40.40], [-3.71, 40.41]]},
            "properties": props,
        }],
        "parametros_efectivos": {
            "ancho_req": 3.5, "galibo_req": 4.0, "algoritmo": "dijkstra",
            "fecha": None, "hora": 8, "vehiculo": "autobomba_pesada",
        },
        "trafico_por_zona": {},
        "origen": {"node_id": 1, "tipo_nodo": "bomberos", "nombre": "PARQUE FALSO A"},
        "destino": {"node_id": 2, "tipo_nodo": None, "nombre": None},
        "zona_destino": zona_destino if zona_destino is not None else {"zona": "Centro", "nivel_trafico": None},
    }


def _checkbox_isocronas(at):
    return next(c for c in at.sidebar.checkbox if c.label.lower().startswith("mostrar isócronas"))


def _checkbox_aplicar_trafico(at):
    return next(c for c in at.sidebar.checkbox if c.label.lower().startswith("aplicar predicción"))


def _calcular(at):
    """El botón «Calcular ruta óptima» — por etiqueta, no por índice (la barra
    lateral tiene más botones: «Buscar dirección»)."""
    return next(b for b in at.sidebar.button if "Calcular" in b.label)


def _mock_requests_get(rutas: dict):
    """rutas: {sufijo_de_path: (json_body, status_code)} -- dispatcher por URL."""
    def _fake_get(url, params=None, timeout=None):
        for sufijo, (body, status) in rutas.items():
            if url.endswith(sufijo):
                return _FakeResponse(body, status), params
        # /health (bloque 7) y la banda meteo (bloque 6) se cargan en TODOS
        # los reruns; si un test no los mockea a propósito, se les sirve una
        # respuesta "operativo"/"normal" por defecto.
        if url.endswith("/health"):
            return _FakeResponse(HEALTH_OK, 200), params
        if url.endswith("/meteorologia"):
            return _FakeResponse(METEO_NORMAL, 200), params
        raise AssertionError(f"URL no mockeada en el test: {url}")

    calls = []

    def _get(url, params=None, timeout=None):
        resp, sent_params = _fake_get(url, params=params, timeout=timeout)
        calls.append((url, params))
        return resp

    return _get, calls


class TestCatalogoDeParquesReales:
    def test_selector_de_origen_muestra_los_parques_reales_de_la_api(self):
        """Antes de este bloque el selector tenía 4 nombres ficticios hardcodeados
        (Retiro (P1), Vallecas (P2)...) -- ahora deben ser exactamente los que
        devuelve /equipamientos?tipo=bomberos, sin importar cuántos ni cómo se llamen."""
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)

        assert at.exception == []
        selector_origen = at.sidebar.selectbox[0]
        assert selector_origen.options == ["PARQUE FALSO A", "PARQUE FALSO B", "PARQUE FALSO C"]
        assert "Retiro (P1)" not in selector_origen.options

    def test_selector_recorta_el_prefijo_parque_de_bomberos_pero_a_ruta_va_el_nombre_completo(self):
        """En pantalla el parque se lee "07. SAN BLAS" (sin el "PARQUE DE
        BOMBEROS " que comparten todos y estorba); a /ruta se envía el nombre
        completo que la API conoce."""
        parques_api = [
            _parque_feature("PARQUE DE BOMBEROS 03. CENTRO", -3.70, 40.41),
            _parque_feature("PARQUE DE BOMBEROS 07. SAN BLAS", -3.61, 40.43),
        ]
        fake_get, calls = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(parques_api), 200),
            "/ruta": (_ruta_feature(ruta_completa=True), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            assert at.exception == []
            assert at.sidebar.selectbox[0].options == ["03. CENTRO", "07. SAN BLAS"]
            at.sidebar.selectbox[0].set_value("07. SAN BLAS").run(timeout=30)
            _calcular(at).click().run(timeout=30)

        assert at.exception == []
        llamada_ruta = next(p for url, p in calls if url.endswith("/ruta"))
        assert llamada_ruta["orig_nombre"] == "PARQUE DE BOMBEROS 07. SAN BLAS"

    def test_catalogo_de_parques_inaccesible_deshabilita_el_boton_calcular(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": ({"error": "no disponible", "code": "MISSING_ARTIFACT"}, 404),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)

        assert at.exception == []
        assert any("catálogo de parques" in e.value for e in at.error)
        boton = next(b for b in at.sidebar.button if "Calcular" in b.label)
        assert boton.disabled is True


class TestOrigenPorIdentidad:
    def test_ruta_se_pide_con_identidad_de_nodo_no_con_coordenada_aproximada(self):
        fake_get, calls = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=True), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)

        assert at.exception == []
        llamada_ruta = next(params for url, params in calls if url.endswith("/ruta"))
        assert llamada_ruta["orig_tipo_nodo"] == "bomberos"
        assert llamada_ruta["orig_nombre"] == "PARQUE FALSO A"
        assert "orig_lat" not in llamada_ruta
        assert "orig_lon" not in llamada_ruta


class TestVehiculoAlgoritmoEnviadosARuta:
    def test_vehiculo_ancho_galibo_algoritmo_se_reenvian_a_ruta(self):
        fake_get, calls = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=True), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            at.sidebar.selectbox[1].set_value("Vehículo de Rescate (VR)").run(timeout=30)
            at.sidebar.selectbox[2].set_value("astar").run(timeout=30)
            _calcular(at).click().run(timeout=30)

        assert at.exception == []
        llamada_ruta = next(params for url, params in calls if url.endswith("/ruta"))
        assert llamada_ruta["vehiculo"] == "vehiculo_rescate"
        assert llamada_ruta["ancho_req"] == 2.1
        assert llamada_ruta["galibo_req"] == 2.6
        assert llamada_ruta["algoritmo"] == "astar"

    def test_sin_activar_trafico_no_se_envia_date_ni_hora(self):
        fake_get, calls = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=True), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)

        llamada_ruta = next(params for url, params in calls if url.endswith("/ruta"))
        assert "date" not in llamada_ruta
        assert "hora" not in llamada_ruta

    def test_activar_trafico_si_envia_date_y_hora(self):
        fake_get, calls = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=True), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _checkbox_aplicar_trafico(at).set_value(True).run(timeout=30)
            _calcular(at).click().run(timeout=30)

        llamada_ruta = next(params for url, params in calls if url.endswith("/ruta"))
        assert "date" in llamada_ruta
        assert llamada_ruta["hora"] == 8


class TestRutaParcial:
    def test_ruta_parcial_muestra_aviso_explicito_con_metros_sin_cubrir_y_no_success(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=False, distancia_sin_cubrir_m=287.05), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)

        assert at.exception == []
        avisos = [w.value for w in at.warning]
        assert any("no existe ruta completa" in a.lower() for a in avisos)
        assert any("287" in a for a in avisos)
        assert at.success == []

    def test_ruta_completa_muestra_success_y_ningun_aviso_de_parcial(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=True), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)

        assert at.exception == []
        assert any("correctamente" in s.value.lower() for s in at.success)
        assert at.warning == []

    def test_ruta_parcial_sigue_avisando_tras_un_rerun_no_relacionado(self):
        """El aviso de ruta parcial vive en session_state, no solo en el instante
        de pulsar el botón -- activar la capa de isócronas (rerun no relacionado)
        no debe hacerlo desaparecer ni pintarlo como completo."""
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=False, distancia_sin_cubrir_m=50.0), 200),
            "/isocronas": (_feature_collection([]), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)
            _checkbox_isocronas(at).set_value(True).run(timeout=30)

        assert at.exception == []
        assert any("no existe ruta completa" in w.value.lower() for w in at.warning)
        assert at.success == []


class TestIsocronas:
    def test_activar_isocronas_pide_al_endpoint_con_parque_y_vehiculo_seleccionados(self):
        cortes = _feature_collection([
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]},
             "properties": {"corte_min": 5}},
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 2], [2, 2], [0, 0]]]},
             "properties": {"corte_min": 10}},
        ])
        cortes["parque"] = {"node_id": 1, "tipo_nodo": "bomberos", "nombre": "PARQUE FALSO A"}
        cortes["parametros_efectivos"] = {"ancho_req": 3.5, "galibo_req": 4.0, "fecha": None, "hora": 8, "vehiculo": "autobomba_pesada"}
        cortes["trafico_por_zona"] = {}

        fake_get, calls = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/isocronas": (cortes, 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _checkbox_isocronas(at).set_value(True).run(timeout=30)

        assert at.exception == []
        llamada = next(params for url, params in calls if url.endswith("/isocronas"))
        assert llamada["nombre"] == "PARQUE FALSO A"
        assert llamada["vehiculo"] == "autobomba_pesada"

    def test_isocronas_desactivadas_por_defecto_no_llama_al_endpoint(self):
        fake_get, calls = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)

        assert at.exception == []
        assert not any(url.endswith("/isocronas") for url, _ in calls)


class TestApiCaida:
    def test_api_caida_muestra_inicializando_y_mensaje_humano_sin_traceback(self):
        """Bloque 7: con la API caída, el dashboard NO enseña el traceback ni
        el 'Connection refused' crudo -- muestra el panel 'Inicializando…' y,
        agotados los reintentos (0 en tests), un mensaje en español que dice
        qué hacer. Y se detiene: no intenta cargar config/parques encima."""
        import requests

        def _get(url, params=None, timeout=None):
            raise requests.exceptions.ConnectionError("Connection refused")

        with patch("requests.get", side_effect=_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)

        assert at.exception == []
        infos = " ".join(i.value for i in at.info)
        assert "inicializando el servicio de rutas" in infos.lower()
        errores = " ".join(e.value for e in at.error)
        assert "no hay conexión con el servicio de rutas" in errores.lower()
        assert "Connection refused" not in errores and "Traceback" not in errores
        # se detuvo antes de la barra lateral -> no hay selector de parques
        assert at.sidebar.selectbox == []

    def test_health_503_muestra_arrancando_no_pantalla_en_blanco(self):
        """/health 503 (servidor vivo, grafo aún cargando) -> panel
        'Inicializando…' + mensaje 'está arrancando o no está listo', nunca
        un st.error de conexión ni pantalla vacía."""
        fake_get, _ = _mock_requests_get({"/health": ({"status": "degraded", "grafo": {"cargado": False}}, 503)})
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)

        assert at.exception == []
        assert any("inicializando el servicio de rutas" in i.value.lower() for i in at.info)
        assert any("arrancando o no está listo" in e.value.lower() for e in at.error)

    def test_respuesta_no_json_no_provoca_traceback(self):
        class _RespuestaRota:
            status_code = 500

            def json(self):
                raise ValueError("Expecting value: line 1 column 1 (char 0)")

        def _get(url, params=None, timeout=None):
            return _RespuestaRota()

        with patch("requests.get", side_effect=_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)

        assert at.exception == []
        assert len(at.error) >= 1
        assert all("Expecting value" not in e.value for e in at.error)


class TestBandaMeteo:
    """Bloque 6: banda de alerta meteorológica INFORMATIVA en la cabecera."""

    def _run(self, meteo_body):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/meteorologia": (meteo_body, 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
        assert at.exception == []
        return at

    def test_nivel_normal_no_pinta_warning_y_nota_informativa_visible(self):
        at = self._run(_meteo(nivel="normal"))
        assert at.warning == []
        textos = " ".join(c.value for c in at.caption).lower()
        assert "no afecta al cálculo de la ruta" in textos

    def test_precaucion_pinta_warning_con_criterio_y_nota(self):
        at = self._run(_meteo(nivel="precaucion", criterio="viento medio 44.0 km/h en 'Retiro' (≥ 40 km/h)"))
        avisos = [w.value for w in at.warning]
        assert any("precaución meteorológica" in a.lower() for a in avisos)
        assert any("viento medio 44.0 km/h" in a for a in avisos)
        assert any("no afecta al cálculo de la ruta" in a.lower() for a in avisos)

    def test_adversa_pinta_warning_no_error(self):
        at = self._run(_meteo(nivel="adversa", criterio="lluvia 31.0 mm/1h en 'Casa de Campo' (≥ 30 mm/1h)"))
        assert at.error == []   # sobria: adversa NO usa st.error
        assert any("adversa" in w.value.lower() and "lluvia 31.0" in w.value for w in at.warning)

    def test_origen_cache_muestra_la_antiguedad(self):
        at = self._run(_meteo(nivel="precaucion", criterio="x", origen="cache", edad_min=22))
        assert any("hace 22 min" in w.value for w in at.warning)

    def test_meteorologia_caida_no_rompe_el_dashboard(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/meteorologia": ({"error": "boom"}, 503),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
        assert at.exception == []
        textos = " ".join(c.value for c in at.caption).lower()
        assert "meteorología no disponible" in textos
        assert at.sidebar.selectbox[0].options == ["PARQUE FALSO A", "PARQUE FALSO B", "PARQUE FALSO C"]


class TestZonaDestinoKPI:
    """Bloque 7: el panel de resultados muestra el nivel de tráfico de la
    zona de destino (campo aditivo `zona_destino` de /ruta)."""

    def _run_con_zona(self, zona_destino):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=True, zona_destino=zona_destino), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)
        assert at.exception == []
        return at

    def test_nivel_alto_de_la_zona_destino_se_muestra_como_cifra(self):
        at = self._run_con_zona({"zona": "Centro", "nivel_trafico": 2})
        metricas = {mm.label.lower(): mm.value for mm in at.metric}
        etiqueta = next(nombre for nombre in metricas if "zona destino" in nombre)
        assert metricas[etiqueta] == "Alto"

    def test_sin_nivel_la_cifra_es_marcador_no_un_cero_inventado(self):
        at = self._run_con_zona({"zona": "Centro", "nivel_trafico": None})
        metricas = {mm.label.lower(): mm.value for mm in at.metric}
        etiqueta = next(nombre for nombre in metricas if "zona destino" in nombre)
        assert metricas[etiqueta] == "—"
        assert metricas[etiqueta] not in ("Bajo", "0")

    def test_api_antigua_sin_zona_destino_no_rompe_el_panel(self):
        body = _ruta_feature(ruta_completa=True)
        del body["zona_destino"]
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (body, 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)
        assert at.exception == []
        assert any("correctamente" in s.value.lower() for s in at.success)


class TestErroresHumanos:
    """Bloque 7: los errores de la API se traducen a español operativo; el
    texto técnico (HTTP nnn crudo, traceback) no llega a la cara del operador."""

    def test_ruta_500_muestra_texto_humano_no_http_500_pelado(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": ({"error": "boom interno"}, 500),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)
        assert at.exception == []
        errores = " ".join(e.value for e in at.error)
        assert "sistema" in errores.lower() and "diagnóstico" in errores.lower()
        assert "boom interno" not in errores

    def test_ruta_404_dice_que_probar_otro_vehiculo(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": ({"error": "sin ruta", "code": "NO_ROUTE"}, 404),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            _calcular(at).click().run(timeout=30)
        assert at.exception == []
        errores = " ".join(e.value for e in at.error).lower()
        assert "vehículo más ligero" in errores


class TestMapaBloque7:
    """Bloque 7: helpers puros de dibujo del mapa (app/_ui_common.py),
    probados sin navegador. Cada uno se pone rojo si se rompe su mecanismo."""

    def test_dibujar_ruta_traza_halo_blanco_bajo_la_linea_de_color(self):
        import folium
        from app import _ui_common as ui
        m = folium.Map()
        ui.dibujar_ruta(m, [[-3.70, 40.40], [-3.71, 40.41], [-3.72, 40.42]],
                        ruta_completa=True, tooltip="x")
        lineas = [c for c in m._children.values() if isinstance(c, folium.vector_layers.PolyLine)]
        assert len(lineas) == 2, "faltan las dos capas del halo"
        halo, linea = lineas
        assert halo.options["color"] == "#ffffff"
        assert halo.options["weight"] > linea.options["weight"]
        assert linea.options["color"] == ui.RUTA_COLOR_COMPLETA

    def test_dibujar_ruta_parcial_usa_color_y_trazo_discontinuo_distintos(self):
        import folium
        from app import _ui_common as ui
        m = folium.Map()
        ui.dibujar_ruta(m, [[-3.70, 40.40], [-3.71, 40.41]], ruta_completa=False)
        lineas = [c for c in m._children.values() if isinstance(c, folium.vector_layers.PolyLine)]
        _, linea = lineas
        assert linea.options["color"] == ui.RUTA_COLOR_PARCIAL
        assert linea.options["dashArray"] is not None

    def test_isocronas_reciben_la_paleta_secuencial_en_orden_de_corte(self):
        import folium
        from app import _ui_common as ui
        feats = [
            {"type": "Feature", "properties": {"corte_min": c},
             "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}
            for c in (15, 5, 10)  # desordenados a propósito
        ]
        m = folium.Map()
        color_por_corte = ui.dibujar_isocronas(m, feats, "Parque X", 0.3)
        assert [color_por_corte[5], color_por_corte[10], color_por_corte[15]] == ui.PALETA_ISOCRONAS[:3]
        # ya no es el semáforo verde/ámbar/rojo del bloque 4
        assert "#22c55e" not in color_por_corte.values()
        assert len([c for c in m._children.values() if isinstance(c, folium.features.GeoJson)]) == 3

    def test_leyenda_incluye_entrada_de_ruta_cuando_hay_ruta(self):
        from app import _ui_common as ui
        html_con = ui.leyenda_mapa_html("Parque X", {5.0: "#ffffb2"}, con_ruta=True, ruta_completa=True)
        html_sin = ui.leyenda_mapa_html("Parque X", {5.0: "#ffffb2"}, con_ruta=False)
        assert "Ruta óptima" in html_con
        assert "Ruta óptima" not in html_sin and "Ruta parcial" not in html_sin

    def test_leyenda_vacia_no_devuelve_caja(self):
        """Sin isócronas, sin ruta y sin marcadores que anunciar: "" -- el
        llamador no añade una caja "Leyenda" sin nada debajo."""
        from app import _ui_common as ui
        assert ui.leyenda_mapa_html(None, {}, con_ruta=False) == ""

    def test_leyenda_incluye_los_iconos_de_parque_e_incidente_cuando_se_piden(self):
        from app import _ui_common as ui
        html = ui.leyenda_mapa_html(None, {}, con_ruta=False,
                                    con_parque=True, con_incidente=True, con_hospitales=True)
        assert "Parque de bomberos" in html and "Incidente" in html and "Hospital" in html
        # son los iconos Iconoir reales, no solo texto -- cada fila trae un <svg>
        assert html.count("<svg") >= 3

    def test_tabla_trafico_pinta_un_punto_de_color_junto_al_texto_del_nivel(self):
        """El color nunca sustituye al texto (§8.7): cada fila lleva el punto
        Y la palabra Bajo/Medio/Alto."""
        from app import _ui_common as ui
        html = ui.tabla_trafico_html({"Centro": 2, "Retiro": 0, "Salamanca": 1})
        assert "Alto" in html and "Bajo" in html and "Medio" in html
        assert html.count('class="inst-dot"') == 3
        assert "var(--color-error)" in html    # Alto
        assert "var(--color-success)" in html  # Bajo
        assert "var(--color-warning)" in html  # Medio

    def test_mapa_base_usa_basemap_claro_sin_api_key(self):
        from app import _ui_common as ui
        # Basemap claro que NO exige API key (DESIGN_SYSTEM §3.4).
        assert ui.TILES_BASE in {"OpenStreetMap", "CartoDB positron"}

    def test_icono_svg_con_color_explicito_colorea_los_trazos_no_solo_la_raiz(self):
        """Bug real de este bloque: cada <path> de Iconoir trae su propio
        stroke="currentColor" y NO hereda del <svg> raíz -- sin este fix, un
        `stroke` explícito no se veía (quedaba el color de texto ambiente),
        confirmado con getComputedStyle en un caso real."""
        from app import _ui_common as ui
        svg = ui.icono_svg("fire-flame", 16, stroke="#A15C00")
        assert 'stroke="currentColor"' not in svg
        assert svg.count('stroke="#A15C00"') >= 2  # la raíz + al menos un <path>

    def test_icono_svg_sin_color_explicito_sigue_heredando_currentcolor(self):
        from app import _ui_common as ui
        assert 'stroke="currentColor"' in ui.icono_svg("fire-flame", 16)

    def test_marcador_parque_usa_iconoir_en_vez_de_font_awesome(self):
        """Bloque B: folium.Icon(prefix="fa") era un tercer set de iconos,
        ajeno al resto de la app (DESIGN_SYSTEM §6.2) -- pasa a DivIcon con
        el SVG de Iconoir, coloreado con el token del marcador."""
        import folium
        from app import _ui_common as ui
        m = folium.Map()
        ui.marcador_parque(m, 40.0, -3.0, "Parque X")
        marcadores = [c for c in m._children.values() if isinstance(c, folium.map.Marker)]
        assert len(marcadores) == 1
        icono = marcadores[0].icon
        assert isinstance(icono, folium.DivIcon)
        html = icono.options["html"]
        assert "<svg" in html
        assert "#0055A0" in html  # azul de marca (§3.4) -- valor fijo, no el propio token

    def test_marcador_incidente_y_hospital_usan_colores_distintos_entre_si(self):
        import folium
        from app import _ui_common as ui
        m = folium.Map()
        ui.marcador_incidente(m, 40.0, -3.0)
        ui.marcador_hospital(m, 40.1, -3.1, "Hospital X")
        marcadores = [c for c in m._children.values() if isinstance(c, folium.map.Marker)]
        assert len(marcadores) == 2
        html_incidente, html_hospital = (mk.icon.options["html"] for mk in marcadores)
        assert ui.COLOR_MARCADOR_INCIDENTE != ui.COLOR_MARCADOR_HOSPITAL
        assert ui.COLOR_MARCADOR_INCIDENTE in html_incidente and ui.COLOR_MARCADOR_INCIDENTE not in html_hospital
        assert ui.COLOR_MARCADOR_HOSPITAL in html_hospital and ui.COLOR_MARCADOR_HOSPITAL not in html_incidente

    def test_leyenda_arriba_a_la_derecha_no_choca_con_escala_ni_atribucion(self):
        from app import _ui_common as ui
        html = ui.leyenda_mapa_html(None, {}, con_ruta=False, con_incidente=True)
        assert "top: 12px; right: 12px" in html
        assert "bottom: 24px; left: 24px" not in html


class TestParametrosEfectivosBloqueB:
    """Bloque B: los parámetros efectivos de la ruta como lista de
    definición (§2.2), no como bloque de markdown en negrita."""

    def test_lista_de_definicion_con_los_cinco_campos(self):
        from app import _ui_common as ui
        html = ui.parametros_efectivos_html(203, 3.5, "autobomba_pesada", "dijkstra", True)
        assert 'class="inst-dl"' in html
        for etiqueta in ("Nodos recorridos", "Ancho mínimo requerido", "Vehículo efectivo",
                         "Algoritmo", "Predicción de tráfico aplicada"):
            assert etiqueta in html
        assert "203" in html and "3.5 m" in html
        assert "autobomba_pesada" in html and "dijkstra" in html
        assert "<dd>sí</dd>" in html

    def test_sin_prediccion_de_trafico_dice_no(self):
        from app import _ui_common as ui
        html = ui.parametros_efectivos_html(1, 2.1, "vehiculo_rescate", "astar", False)
        assert "<dd>no</dd>" in html


class TestPaginaSistema:
    """Bloque 7: la página 'Sistema' renderiza /health en formato legible."""

    def _health(self, **over):
        base = {
            "status": "ok",
            "grafo": {"cargado": True, "nodes": 171646, "edges": 235592,
                      "nodos_especiales": {"bomberos": 13, "hospitales": 277},
                      "segundos_carga": 16.7, "error": None},
            "modelo_trafico": {"cargado": False, "ruta_pkl": "data/processed/modelo_trafico_xgboost.pkl"},
            "duckdb": {"accesible": True, "ruta": "data/processed/tfm_madrid.duckdb"},
            "artefactos_faltantes": ["data/processed/modelo_trafico_xgboost.pkl"],
            "timestamp": "2026-08-27T10:00:00+00:00",
        }
        base.update(over)
        return base

    def _run(self, health_body, status=200):
        fake_get, _ = _mock_requests_get({"/health": (health_body, status)})
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(SISTEMA_PATH)
            at.run(timeout=30)
        assert at.exception == []
        return at

    def _texto(self, at):
        piezas = [e.value for e in at.markdown] + [c.value for c in at.caption]
        piezas += [str(mm.value) for mm in at.metric] + [mm.label for mm in at.metric]
        return " ".join(piezas)

    def test_operativo_muestra_grafo_cargado_y_cifras(self):
        at = self._run(self._health())
        texto = self._texto(at)
        assert "171.646" in texto           # nodos del grafo, formateado
        assert "16.7" in texto              # segundos de carga
        assert any("green-badge" in m.value and "Operativo" in m.value for m in at.markdown)

    def test_modelo_ausente_dice_como_seguir_calculando_rutas(self):
        at = self._run(self._health())
        avisos = " ".join(w.value.lower() for w in at.warning)
        assert "sin ajuste de tráfico" in avisos

    def test_grafo_no_cargado_marca_degradado_y_lo_explica(self):
        at = self._run(self._health(status="degraded",
                                    grafo={"cargado": False, "error": "Callejero no encontrado en X.geojson"}),
                       status=503)
        assert any("orange-badge" in m.value and "Degradado" in m.value for m in at.markdown)
        assert any("no está cargado" in e.value.lower() for e in at.error)

    def test_sin_respuesta_de_health_no_revienta(self):
        import requests

        def _get(url, params=None, timeout=None):
            raise requests.exceptions.ConnectionError("nope")

        with patch("requests.get", side_effect=_get):
            at = AppTest.from_file(SISTEMA_PATH)
            at.run(timeout=30)
        assert at.exception == []
        assert any("no hay conexión" in e.value.lower() for e in at.error)


class TestBadgeEstado:
    """`badge_estado`: traduce la respuesta de /health a (texto, color, icono)."""

    def test_grafo_cargado_es_operativo_verde(self):
        from app._ui_common import badge_estado
        txt, color, _ = badge_estado(200, {"grafo": {"cargado": True}})
        assert (txt, color) == ("Operativo", "green")

    def test_503_es_degradado_naranja(self):
        from app._ui_common import badge_estado
        txt, color, _ = badge_estado(503, {"grafo": {"cargado": False}})
        assert (txt, color) == ("Degradado", "orange")

    def test_200_pero_grafo_no_cargado_no_es_operativo(self):
        from app._ui_common import badge_estado
        txt, color, _ = badge_estado(200, {"grafo": {"cargado": False}})
        assert txt != "Operativo" and color != "green"

    def test_sin_respuesta_es_sin_conexion_rojo(self):
        from app._ui_common import badge_estado
        txt, color, _ = badge_estado(None, None)
        assert (txt, color) == ("Sin conexión", "red")


class TestBuscarDireccion:
    """Búsqueda del destino por dirección: geocodifica vía /geocodificar y
    rellena las coordenadas (que siguen siendo lo que se envía a /ruta)."""

    def _text_dir(self, at):
        return next(t for t in at.sidebar.text_input if "dirección" in t.label.lower())

    def _lupa(self, at):
        # botón de la lupa, pegado al campo (sin etiqueta, key="dir_go")
        return next(b for b in at.sidebar.button if b.key == "dir_go")

    def test_direccion_encontrada_rellena_lat_lon_y_muestra_la_direccion(self):
        fake_get, calls = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/geocodificar": ({"lat": 40.4200, "lon": -3.7010,
                               "direccion": "Calle de Alcalá, 100, Madrid"}, 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            self._text_dir(at).set_value("Calle de Alcalá 100").run(timeout=30)
            self._lupa(at).click().run(timeout=30)

        assert at.exception == []
        llamada = next(params for url, params in calls if url.endswith("/geocodificar"))
        assert llamada["q"] == "Calle de Alcalá 100"
        valores = {round(n.value, 4) for n in at.sidebar.number_input}
        assert 40.42 in valores and -3.701 in valores
        assert any("Calle de Alcalá, 100, Madrid" in x.value for x in at.success)

    def test_direccion_no_encontrada_muestra_mensaje_humano_y_no_toca_coordenadas(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/geocodificar": ({"error": "nada", "code": "DIRECCION_NO_ENCONTRADA"}, 404),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            self._text_dir(at).set_value("calle que no existe zzz").run(timeout=30)
            self._lupa(at).click().run(timeout=30)

        assert at.exception == []
        errores = " ".join(e.value for e in at.error).lower()
        assert "no se ha encontrado esa dirección" in errores
        # coordenadas por defecto intactas
        valores = {round(n.value, 4) for n in at.sidebar.number_input}
        assert 40.4168 in valores and -3.7038 in valores

    def test_geocodificador_caido_dice_que_metas_las_coordenadas_a_mano(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/geocodificar": ({"error": "x", "code": "GEOCODER_UNAVAILABLE"}, 503),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            self._text_dir(at).set_value("Puerta del Sol").run(timeout=30)
            self._lupa(at).click().run(timeout=30)

        assert at.exception == []
        errores = " ".join(e.value for e in at.error).lower()
        assert "coordenadas del destino a mano" in errores


# ─────────────────────────────────────────────────────────────────────
# Bloque A — interacción y claridad de entrada
# ─────────────────────────────────────────────────────────────────────
class TestFijarDestinoPorClicEnMapa:
    """N1: además del buscador, un modo "fijar destino" en el que un clic en el
    mapa fija el punto del incidente. El manejador del clic (sección del mapa)
    deja la intención en `_dest_click` y hace rerun; la sección Destino la
    aplica ANTES de instanciar los number_input. Aquí se simula ese
    session_state (el iframe de folium no se renderiza bajo AppTest)."""

    def _mock(self):
        return _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
            "/ruta": (_ruta_feature(ruta_completa=True), 200),
        })

    def test_un_punto_fijado_por_clic_se_aplica_a_las_coordenadas_y_va_a_ruta(self):
        fake_get, calls = self._mock()
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.session_state["_dest_click"] = (40.512345, -3.612345)
            at.run(timeout=30)
            assert at.exception == []
            # el punto del clic quedó en las coordenadas del destino
            valores = {round(n.value, 6) for n in at.sidebar.number_input}
            assert 40.512345 in valores and -3.612345 in valores
            assert at.session_state["_click_fijado"] is True
            assert at.session_state["dest_dir"] == ""
            assert any("punto fijado en el mapa" in s.value.lower() for s in at.success)
            _calcular(at).click().run(timeout=30)

        llamada = next(p for url, p in calls if url.endswith("/ruta"))
        assert round(llamada["dest_lat"], 6) == 40.512345
        assert round(llamada["dest_lon"], 6) == -3.612345

    def test_el_modo_fijar_destino_se_apaga_solo_tras_fijar_el_punto(self):
        fake_get, _ = self._mock()
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.session_state["modo_fijar_destino"] = True
            at.session_state["_modo_clic_off"] = True     # lo deja el manejador del clic
            at.run(timeout=30)

        assert at.exception == []
        assert at.session_state["modo_fijar_destino"] is False
        toggle = next(t for t in at.sidebar.toggle if "Fijar el destino" in t.label)
        assert toggle.value is False

    def test_con_el_modo_activo_la_pista_del_mapa_pide_hacer_clic(self):
        fake_get, _ = self._mock()
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.session_state["modo_fijar_destino"] = True
            at.run(timeout=30)

        assert at.exception == []
        captions = " ".join(c.value.lower() for c in at.caption)
        assert "haz clic en el punto del incidente" in captions

    def test_sin_destino_y_sin_modo_la_pista_ofrece_las_dos_vias(self):
        fake_get, _ = self._mock()
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)

        assert at.exception == []
        captions = " ".join(c.value.lower() for c in at.caption)
        # pista propia del área del mapa (no la de la sección Destino)
        assert "fija el lugar del incidente" in captions
        assert "en el panel de la izquierda" in captions


class TestEstadosVaciosYAccesibilidadBloqueA:
    def _at(self):
        fake_get, _ = _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_FALSOS), 200),
        })
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
        return at

    def test_sin_ruta_el_panel_de_resultado_muestra_estado_vacio_con_guia(self):
        at = self._at()
        assert at.exception == []
        md = " ".join(m.value for m in at.markdown)
        assert "Aún no has calculado ninguna ruta" in md
        assert "Calcular ruta óptima" in md          # la guía nombra la acción
        # y ya no es el simple caption de antes
        assert not any("pulsa «calcular ruta óptima» para ver los resultados"
                       in c.value.lower() for c in at.caption)

    def test_sin_prediccion_la_seccion_de_trafico_muestra_estado_vacio(self):
        at = self._at()
        md = " ".join(m.value for m in at.markdown)
        assert "Sin predicción por distrito" in md

    def test_la_cabecera_incluye_enlace_de_salto_al_contenido(self):
        at = self._at()
        md = " ".join(m.value for m in at.markdown)
        assert 'class="skip-link"' in md and 'href="#contenido-principal"' in md


# ─────────────────────────────────────────────────────────────────────
# Bloque C — parque de origen sugerido automáticamente por cercanía al
# destino. Destino ahora es la sección 1 (antes 2) y Origen la 2 (antes
# 1); el destino se pide primero para poder sugerir el parque.
# ─────────────────────────────────────────────────────────────────────
class TestParqueMasCercanoBloqueC:
    """Helper puro (app/_ui_common.py): sin red, sin Streamlit."""

    PARQUES = [
        {"nombre": "PARQUE DE BOMBEROS 01. NORTE", "lat": 40.50, "lon": -3.70},
        {"nombre": "PARQUE DE BOMBEROS 02. SUR", "lat": 40.30, "lon": -3.70},
        {"nombre": "PARQUE DE BOMBEROS 03. CENTRO", "lat": 40.415, "lon": -3.705},
    ]

    def test_elige_el_parque_geometricamente_mas_cercano(self):
        from app._ui_common import parque_mas_cercano
        cercano = parque_mas_cercano(self.PARQUES, 40.416, -3.703)
        assert cercano["nombre"] == "PARQUE DE BOMBEROS 03. CENTRO"

    def test_devuelve_distancia_en_km_positiva_sin_mutar_la_lista_de_entrada(self):
        from app._ui_common import parque_mas_cercano
        original = [dict(p) for p in self.PARQUES]
        cercano = parque_mas_cercano(self.PARQUES, 40.416, -3.703)
        assert cercano["distancia_km"] > 0
        assert self.PARQUES == original

    def test_lista_vacia_lanza_valueerror(self):
        from app._ui_common import parque_mas_cercano
        with pytest.raises(ValueError):
            parque_mas_cercano([], 40.4, -3.7)

    def test_nombre_corto_recorta_el_prefijo_pero_respeta_nombres_sin_prefijo(self):
        from app._ui_common import nombre_corto_parque
        assert nombre_corto_parque("PARQUE DE BOMBEROS 07. SAN BLAS") == "07. SAN BLAS"
        assert nombre_corto_parque("OTRO NOMBRE") == "OTRO NOMBRE"


PARQUES_BLOQUE_C = [
    _parque_feature("PARQUE DE BOMBEROS 01. NORTE", -3.70, 40.50),
    _parque_feature("PARQUE DE BOMBEROS 02. SUR", -3.70, 40.30),
    _parque_feature("PARQUE DE BOMBEROS 03. CENTRO", -3.705, 40.415),
]


class TestSeleccionAutomaticaParqueBloqueC:
    """Integración (AppTest): fijar el destino sugiere el parque más cercano
    en línea recta; el usuario puede anularlo y solo un cambio de destino
    (no de vehículo, fecha u otro control) vuelve a recalcularlo."""

    def _selector_parque(self, at):
        return at.sidebar.selectbox[0]  # Origen sigue siendo el primer selectbox

    def _mock(self, geocodificar):
        return _mock_requests_get({
            "/config": (CONFIG_FALSO, 200),
            "/equipamientos": (_feature_collection(PARQUES_BLOQUE_C), 200),
            "/geocodificar": (geocodificar, 200),
        })

    def _fijar_destino(self, at, lat, lon, direccion):
        text_dir = next(t for t in at.sidebar.text_input if "dirección" in t.label.lower())
        lupa = next(b for b in at.sidebar.button if b.key == "dir_go")
        text_dir.set_value(direccion).run(timeout=30)
        lupa.click().run(timeout=30)

    def test_sin_destino_fijado_no_hay_sugerencia_automatica(self):
        """Antes de tocar el destino, el selector mantiene el valor por
        defecto (primer parque alfabético) y no se anuncia ninguna sugerencia."""
        fake_get, _ = self._mock({"lat": 40.416, "lon": -3.703, "direccion": "x"})
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)

        assert at.exception == []
        assert self._selector_parque(at).value == "01. NORTE"
        captions = " ".join(c.value.lower() for c in at.caption)
        assert "sugerido automáticamente" not in captions
        assert "elegido manualmente" not in captions

    def test_al_fijar_destino_por_direccion_se_sugiere_el_parque_mas_cercano(self):
        fake_get, _ = self._mock({"lat": 40.416, "lon": -3.703, "direccion": "Calle Mayor 1"})
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            self._fijar_destino(at, 40.416, -3.703, "Calle Mayor 1")

        assert at.exception == []
        assert self._selector_parque(at).value == "03. CENTRO"
        captions = " ".join(c.value.lower() for c in at.caption)
        assert "sugerido automáticamente" in captions and "km" in captions

    def test_elegir_otro_parque_a_mano_sobrevive_a_un_rerun_no_relacionado(self):
        fake_get, _ = self._mock({"lat": 40.416, "lon": -3.703, "direccion": "Calle Mayor 1"})
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            self._fijar_destino(at, 40.416, -3.703, "Calle Mayor 1")
            self._selector_parque(at).set_value("01. NORTE").run(timeout=30)
            # control sin relación con el destino (no dispara ninguna llamada
            # a la API por sí solo, a diferencia de "Mostrar isócronas")
            _checkbox_aplicar_trafico(at).set_value(True).run(timeout=30)

        assert at.exception == []
        assert self._selector_parque(at).value == "01. NORTE"
        captions = " ".join(c.value.lower() for c in at.caption)
        assert "elegido manualmente" in captions and "03. centro" in captions

    def test_cambiar_destino_de_nuevo_recalcula_la_sugerencia(self):
        fake_get, _ = self._mock({"lat": 40.416, "lon": -3.703, "direccion": "Calle Mayor 1"})
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            self._fijar_destino(at, 40.416, -3.703, "Calle Mayor 1")
            self._selector_parque(at).set_value("01. NORTE").run(timeout=30)

        fake_get2, _ = self._mock({"lat": 40.31, "lon": -3.701, "direccion": "Otra calle"})
        with patch("requests.get", side_effect=fake_get2):
            self._fijar_destino(at, 40.31, -3.701, "Otra calle")

        assert at.exception == []
        assert self._selector_parque(at).value == "02. SUR"

    def test_cambiar_vehiculo_no_toca_la_seleccion_manual_de_parque(self):
        fake_get, _ = self._mock({"lat": 40.416, "lon": -3.703, "direccion": "Calle Mayor 1"})
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            self._fijar_destino(at, 40.416, -3.703, "Calle Mayor 1")
            self._selector_parque(at).set_value("01. NORTE").run(timeout=30)
            vehiculo = next(s for s in at.sidebar.selectbox if s.label == "Tipo de vehículo")
            vehiculo.set_value("Vehículo de Rescate (VR)").run(timeout=30)

        assert at.exception == []
        assert self._selector_parque(at).value == "01. NORTE"

    def test_boton_usar_el_mas_cercano_revierte_la_eleccion_manual(self):
        fake_get, _ = self._mock({"lat": 40.416, "lon": -3.703, "direccion": "Calle Mayor 1"})
        with patch("requests.get", side_effect=fake_get):
            at = AppTest.from_file(APP_PATH)
            at.run(timeout=30)
            self._fijar_destino(at, 40.416, -3.703, "Calle Mayor 1")
            self._selector_parque(at).set_value("01. NORTE").run(timeout=30)
            boton = next(b for b in at.sidebar.button if b.key == "usar_parque_cercano")
            boton.click().run(timeout=30)

        assert at.exception == []
        assert self._selector_parque(at).value == "03. CENTRO"
        captions = " ".join(c.value.lower() for c in at.caption)
        assert "sugerido automáticamente" in captions
