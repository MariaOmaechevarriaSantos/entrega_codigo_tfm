"""
Flask API — TFM Rutas Bomberos Madrid.
Endpoints: /health, /version, /metrics, /config, /ruta, /prediccion_trafico,
/isocronas, /equipamientos, /geocodificar, /meteorologia
"""
import json
import logging
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# --- sys.path: garantiza que "routing"/"ml"/"pipeline" se puedan importar
# tanto si se lanza `python app/server.py` (script, no paquete: Python
# solo añade el directorio de este fichero a sys.path, no la raíz del
# proyecto ni el cwd) como con gunicorn. Ruta absoluta resuelta desde
# este fichero -- no depende de con qué cwd se invoque ni de que el
# usuario exporte PYTHONPATH a mano. Insertado una única vez, antes de
# cualquier import propio del proyecto.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from flask import Flask, Response, g, jsonify, request
from flasgger import Swagger
from werkzeug.exceptions import HTTPException

import app.config as cfg
import app.logging_setup as logging_setup
import app.meteo_alerta as meteo_alerta
import app.metrics as metrics
from app.version import API_VERSION, build_info

app = Flask(__name__)

# Logs estructurados en JSON a stdout, nivel por LOG_LEVEL (bloque 8B). Se
# configura al importar el módulo -> `gunicorn app.wsgi:app` (que importa
# esto) y `python app/server.py` quedan igual de cubiertos. Idempotente.
logging_setup.configure_logging()
logger = logging.getLogger("server")

# Cabecera de correlación. Se respeta la entrante solo si es "sana" (evita
# que un cliente meta saltos de línea u otra basura en las trazas); si no,
# se genera una nueva.
_REQUEST_ID_HEADER = "X-Request-ID"
_RE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

PORT = int(os.environ.get("PORT", 8080))

PROCESSED_DIR = os.path.join(_PROJECT_ROOT, "data", "processed")
ARTEFACTOS_ESPERADOS = {
    "callejero": os.path.join(PROCESSED_DIR, "madrid_callejero_filtered.geojson"),
    "parques_bomberos_geojson": os.path.join(PROCESSED_DIR, "parques_bomberos.geojson"),
    "hospitales_geojson": os.path.join(PROCESSED_DIR, "hospitales.geojson"),
    "modelo_trafico_pkl": os.path.join(PROCESSED_DIR, "modelo_trafico_xgboost.pkl"),
    "modelo_trafico_metadata": os.path.join(PROCESSED_DIR, "modelo_trafico_xgboost_metadata.json"),
    "duckdb": os.path.join(PROCESSED_DIR, "tfm_madrid.duckdb"),
}

# Swagger
swagger = Swagger(app, template={
    "info": {
        "title": "TFM — Rutas Emergencia Madrid Bomberos",
        "description": "API para cálculo de rutas óptimas para camiones de bomberos en Madrid.",
        "version": API_VERSION,
    },
    "basePath": "/",
    "schemes": ["http"],
})


@app.before_request
def _asignar_request_id():
    """Un request_id por petición, propagado a TODAS las trazas de esa
    petición vía contextvars (también las de routing/ y ml/). Se respeta el
    X-Request-ID entrante si es sano; si no, se genera."""
    entrante = request.headers.get(_REQUEST_ID_HEADER, "")
    rid = entrante if _RE_REQUEST_ID.match(entrante) else logging_setup.nuevo_request_id()
    logging_setup.set_request_id(rid)
    g.request_id = rid
    g._t_inicio = time.perf_counter()


@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    # Devuelve el request_id para que el cliente pueda citarlo en una
    # incidencia y cruzarlo con el log del servidor.
    rid = g.get("request_id")
    if rid:
        response.headers[_REQUEST_ID_HEADER] = rid
    return response


@app.after_request
def _log_acceso(response):
    """Una línea JSON por petición servida (método, ruta, código, duración)
    y el registro de la misma en las métricas Prometheus. request_id lo
    añade el filtro de logging_setup."""
    t0 = g.get("_t_inicio")
    dur_s = None if t0 is None else time.perf_counter() - t0
    dur_ms = None if dur_s is None else round(dur_s * 1000, 1)
    logger.info(
        "%s %s -> %s (%s ms)",
        request.method, request.path, response.status_code, dur_ms,
        extra={
            "evento": "peticion",
            "metodo": request.method,
            "ruta": request.path,
            "status": response.status_code,
            "duracion_ms": dur_ms,
        },
    )
    # /metrics no se cuenta a sí mismo (el scrapeo periódico sería ruido en
    # el histograma). `endpoint` = regla de ruta, no el path crudo.
    if request.path != "/metrics" and dur_s is not None:
        regla = request.url_rule.rule if request.url_rule is not None else "<sin_ruta>"
        metrics.registrar_peticion(request.method, regla, response.status_code, dur_s)
    return response


def _ruta_relativa(path: str) -> str:
    try:
        return os.path.relpath(path, _PROJECT_ROOT)
    except ValueError:
        return path


_RE_FICHERO = re.compile(r"[\w./\\-]+\.(?:geojson|pkl|json|duckdb|csv)")


def _extraer_fichero(mensaje: str) -> str | None:
    """
    Best-effort: los FileNotFoundError de P1/P3/P4 (graph_engine.py,
    predict_trafico_real.py) siempre nombran el fichero que falta ANTES
    de la sugerencia de cómo regenerarlo, así que el primer match es el
    correcto. No todos los casos tienen un fichero literal (p. ej. "falta
    la tabla equipamientos_por_zona en DuckDB" no es un fichero ausente,
    es una tabla) -- en esos casos se omite el campo `fichero` en vez de
    adivinar mal.
    """
    m = _RE_FICHERO.search(mensaje)
    return _ruta_relativa(m.group(0)) if m else None


# ─────────────────────────────────────────
# Grafo: se carga UNA sola vez al arrancar el proceso (aquí, a nivel de
# módulo), nunca dentro de un endpoint. Verificado empíricamente antes de
# este cambio que el código anterior (carga perezosa en la primera
# petición, cacheada después) NO recargaba por petición -- pero sí
# cargaba en el primer request al azar que tocara el grafo, no al
# arrancar, así que /health no podía informar de nada hasta que alguien
# más disparara la carga. Ver docs/p5/README_P5_API_Streamlit.md.
# ─────────────────────────────────────────
_graph_state = {
    "G": None,
    "error": None,
    "load_seconds": None,
    "nodos_especiales": {},
}
_distritos = None


def _cargar_grafo_al_arrancar() -> None:
    from routing.graph_engine import load_graph

    t0 = time.perf_counter()
    try:
        G = load_graph()
    except Exception as e:
        # No solo FileNotFoundError: un callejero corrupto/ilegible hace
        # que pyogrio/fiona lancen sus propias excepciones, y el arranque
        # tenía que degradar a /health 503 igualmente, no tumbar el
        # proceso. El mensaje se guarda tal cual; si no nombra un fichero,
        # _extraer_fichero devolverá None y /health servirá solo el texto.
        _graph_state["error"] = str(e)
        logger.error(
            "No se pudo cargar el grafo al arrancar: %s", e,
            extra={"evento": "operacion_cara", "operacion": "carga_grafo",
                   "duracion_ms": round((time.perf_counter() - t0) * 1000, 1),
                   "ok": False},
        )
        return

    elapsed = time.perf_counter() - t0
    especiales: dict[str, int] = {}
    for _, d in G.nodes(data=True):
        tipo = d.get("tipo_nodo")
        if tipo:
            especiales[tipo] = especiales.get(tipo, 0) + 1

    _graph_state["G"] = G
    _graph_state["load_seconds"] = round(elapsed, 2)
    _graph_state["nodos_especiales"] = especiales
    logger.info(
        "Grafo cargado en %.2fs: %d nodos, %d aristas, nodos especiales=%s",
        elapsed, G.number_of_nodes(), G.number_of_edges(), especiales,
        extra={
            "evento": "operacion_cara",
            "operacion": "carga_grafo",
            "duracion_ms": round(elapsed * 1000, 1),
            "ok": True,
            "nodos": G.number_of_nodes(),
            "aristas": G.number_of_edges(),
            "nodos_especiales": especiales,
        },
    )


def get_graph():
    """
    Devuelve el grafo cargado al arrancar. NUNCA dispara una carga
    nueva: si _cargar_grafo_al_arrancar() falló, sigue fallando aquí con
    el mismo error explícito (nombra el fichero), capturado por el
    errorhandler global en vez de dejar pasar un 500 genérico de Flask.
    """
    if _graph_state["G"] is None:
        raise FileNotFoundError(
            _graph_state["error"]
            or "Grafo no disponible: revisa el log de arranque del servidor."
        )
    return _graph_state["G"]


def get_distritos():
    global _distritos
    if _distritos is None:
        from ml.generate_dataset import DISTRITOS_MADRID
        _distritos = DISTRITOS_MADRID
    return _distritos


# ─────────────────────────────────────────
# Caché de predicciones de tráfico por (fecha, hora): predecir_trafico_real
# no es gratis (carga un XGBoost + hace merge con equipamientos por zona) y
# la predicción es idéntica para todas las peticiones de esa hora exacta
# (mismas zonas, mismos festivos/día de la semana). Compartida entre /ruta
# y /prediccion_trafico -- no hay motivo para que cada endpoint la recalcule
# por separado. Solo se cachean resultados con éxito: si predecir_trafico_real
# lanza FileNotFoundError (modelo ausente), no se guarda nada y la siguiente
# petición vuelve a intentarlo (para que /health/reintentos puedan reflejar
# que el modelo ya está disponible sin reiniciar el proceso).
# ─────────────────────────────────────────
_traffic_cache: dict[tuple[str, int], dict[str, int]] = {}


def _predecir_trafico_cacheado(fecha: str, hora: int) -> dict[str, int]:
    clave = (fecha, hora)
    hit = clave in _traffic_cache
    metrics.registrar_cache("trafico", hit)
    if not hit:
        from ml.predict_trafico_real import predecir_trafico_real
        # Operación cara SOLO en el miss: carga el XGBoost + merge con
        # equipamientos por zona. El hit es un lookup de dict, no se traza.
        with logging_setup.log_duration(
            logger, "prediccion_trafico", fecha=fecha, hora=hora
        ):
            _traffic_cache[clave] = predecir_trafico_real(fecha, hora)
    return _traffic_cache[clave]


# ─────────────────────────────────────────
# Caché de isócronas por (parque, ancho_req, galibo_req, fecha, hora):
# calcular_isocronas() recorre el grafo entero con Dijkstra hasta 15 min de
# corte -- medido en real contra el grafo de Madrid (171k nodos): ~3.5s por
# parque, ~90x más que servir el GeoJSON estático que generó P4. Se cachea
# la MISMA combinación completa (incluye fecha/hora porque el tráfico
# también cambia la geometría, no solo el ancho/gálibo del vehículo) --
# bloque 4 de P5, decisión tomada con el usuario tras medir ambas
# alternativas (ver docs/p5/README_P5_API_Streamlit.md).
# ─────────────────────────────────────────
_isocronas_cache: dict[tuple, dict[float, object]] = {}

# Isócronas precomputadas (bloque 8): data/processed/isocronas_bomberos.geojson
# lo genera routing/isochrones.py::generar_geojson_bomberos() para los 13
# parques x cortes 5/10/15 CON EL VEHÍCULO DE REFERENCIA Y SIN TRÁFICO. En
# ese caso -- el que dispara el dashboard -- /isocronas sirve el polígono de
# disco en vez de recorrer el grafo con Dijkstra (~6 s medidos, bloque 8).
# Cualquier otra combinación (vehículo distinto, ancho/gálibo explícitos,
# date) se sigue calculando en vivo. El fichero queda obsoleto si se
# regenera el callejero sin rehacerlo: su sha256 lo ancla en
# artifacts.manifest.json.
_ISOCRONAS_PRECOMP_PATH = os.path.join(PROCESSED_DIR, "isocronas_bomberos.geojson")
# {nombre_parque: {corte_min: geometry}}; None = todavía no se intentó cargar.
_isocronas_precomp: dict[str, dict[float, object]] | None = None


def _cargar_isocronas_precomp() -> dict[str, dict[float, object]]:
    """Carga isocronas_bomberos.geojson a memoria una sola vez. Devuelve {}
    (nunca lanza) si el fichero falta o es ilegible -- el endpoint entonces
    calcula en vivo."""
    global _isocronas_precomp
    if _isocronas_precomp is not None:
        return _isocronas_precomp
    _isocronas_precomp = {}
    if not os.path.exists(_ISOCRONAS_PRECOMP_PATH):
        logger.info("isocronas_bomberos.geojson ausente; /isocronas calculará siempre en vivo.")
        return _isocronas_precomp
    import geopandas as gpd
    try:
        gdf = gpd.read_file(_ISOCRONAS_PRECOMP_PATH)
        for parque, sub in gdf.groupby("parque"):
            _isocronas_precomp[str(parque)] = {
                float(fila.corte_min): fila.geometry for fila in sub.itertuples()
            }
    except Exception as e:
        logger.warning("isocronas_bomberos.geojson ilegible (%s); /isocronas calculará en vivo.", e)
        _isocronas_precomp = {}
        return _isocronas_precomp
    logger.info("isocronas_bomberos.geojson: %d parques precomputados cargados.", len(_isocronas_precomp))
    return _isocronas_precomp


def _peticion_isocronas_por_defecto(ancho_req: float, galibo_req: float, fecha) -> bool:
    """True si la petición usa exactamente el vehículo de referencia y sin
    fecha -- el único caso que cubre isocronas_bomberos.geojson."""
    veh = cfg.get_vehiculo_default()
    return fecha is None and ancho_req == veh["ancho_m"] and galibo_req == veh["galibo_m"]


# ─────────────────────────────────────────
# Meteorología informativa (bloque 6). GET /meteorologia envuelve
# pipeline/ingest/meteorologia_madrid.py::fetch_meteorologia_actual (feed
# municipal en vivo, 26 estaciones, sin API key). NUNCA participa en
# calcular_ruta -- el modelo de P3 excluye la meteorología a propósito, así
# que esta alerta es solo de interfaz.
#
# Caché en memoria con TTL de 15 min + último snapshot en disco
# (cache/meteorologia_snapshot.json; cache/ ya está gitignorado). Si el
# feed en vivo no responde, se sirve el snapshot marcando origen="cache" y
# su antigüedad en minutos -- la demo no puede depender de la red.
# ─────────────────────────────────────────
_METEO_TTL_SECONDS = 900
_METEO_SNAPSHOT_PATH = os.path.join(_PROJECT_ROOT, "cache", "meteorologia_snapshot.json")
_meteo_cache: dict = {"fetched_at": None, "condiciones": None}

# Centroides aproximados (WGS84) de los 21 distritos de Madrid. Uso
# exclusivo: asignar a cada distrito la estación municipal más cercana en
# /meteorologia -- no hay ningún GeoJSON de distritos en data/processed/
# que permita un join espacial exacto en tiempo de petición. Nivel
# "cordura", no límite administrativo: con 26 estaciones para 21 distritos
# varios distritos comparten estación, la misma limitación que documenta
# pipeline/transform/meteorologia_madrid.py::build_meteorologia_zonas. Las
# claves DEBEN ser exactamente las de ml.generate_dataset.DISTRITOS_MADRID
# (tests/test_meteorologia.py lo vigila).
DISTRITO_CENTROIDES = {
    "Centro": (40.4156, -3.7074),
    "Arganzuela": (40.3980, -3.6955),
    "Retiro": (40.4089, -3.6773),
    "Salamanca": (40.4300, -3.6797),
    "Chamartin": (40.4600, -3.6770),
    "Tetuan": (40.4610, -3.6990),
    "Chamberi": (40.4350, -3.7040),
    "Fuencarral-El Pardo": (40.5100, -3.7200),
    "Moncloa-Aravaca": (40.4350, -3.7500),
    "Latina": (40.3950, -3.7450),
    "Carabanchel": (40.3800, -3.7280),
    "Usera": (40.3810, -3.7060),
    "Puente de Vallecas": (40.3900, -3.6650),
    "Moratalaz": (40.4070, -3.6450),
    "Ciudad Lineal": (40.4470, -3.6480),
    "Hortaleza": (40.4750, -3.6400),
    "Villaverde": (40.3450, -3.6900),
    "Villa de Vallecas": (40.3600, -3.6200),
    "Vicalvaro": (40.4040, -3.6080),
    "San Blas-Canillejas": (40.4300, -3.6100),
    "Barajas": (40.4720, -3.5800),
}


def _num_o_none(v):
    """float(v) tolerante: None / NaN / '' / no convertible -> None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _str_o_none(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return str(v)


def _df_a_condiciones(df) -> list[dict]:
    """
    DataFrame de fetch_meteorologia_actual -> lista de dicts por estación
    con las magnitudes que consume la alerta ya normalizadas: viento en
    km/h (el feed lo publica en m/s), None donde la estación no mide algo.
    """
    condiciones = []
    for row in df.to_dict("records"):
        viento_ms = _num_o_none(row.get("velocidad_viento"))
        condiciones.append({
            "estacion": _str_o_none(row.get("estacion")),
            "nombre": _str_o_none(row.get("nombre")),
            "lon": _num_o_none(row.get("lon")),
            "lat": _num_o_none(row.get("lat")),
            "hora": _num_o_none(row.get("hora")),
            "temperatura": _num_o_none(row.get("temperatura")),
            "humedad_relativa": _num_o_none(row.get("humedad_relativa")),
            "precipitacion_mm": _num_o_none(row.get("precipitacion")),
            "viento_kmh": None if viento_ms is None else round(viento_ms * 3.6, 1),
        })
    return condiciones


def _guardar_snapshot_meteo(fetched_at: datetime, condiciones: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_METEO_SNAPSHOT_PATH), exist_ok=True)
        with open(_METEO_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": fetched_at.isoformat(), "condiciones_por_estacion": condiciones}, f)
    except OSError as e:
        logger.warning("No se pudo guardar el snapshot de meteorología (%s): %s", _METEO_SNAPSHOT_PATH, e)


def _cargar_snapshot_meteo():
    """(fetched_at: datetime, condiciones: list) del snapshot en disco, o
    None si no existe o está ilegible."""
    try:
        with open(_METEO_SNAPSHOT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        fetched_at = datetime.fromisoformat(data["fetched_at"])
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return fetched_at, list(data["condiciones_por_estacion"])
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning("Snapshot de meteorología ausente o ilegible (%s): %s", _METEO_SNAPSHOT_PATH, e)
        return None


def _get_condiciones_meteo(ahora: datetime):
    """
    (condiciones, origen, edad_min, timestamp_lectura, motivo). Sirve la
    copia en memoria si tiene < TTL; si no, intenta el feed en vivo y, si
    falla (o responde vacío), cae al snapshot en disco con origen="cache".
    """
    fa = _meteo_cache["fetched_at"]
    fresco = fa is not None and (ahora - fa).total_seconds() < _METEO_TTL_SECONDS
    metrics.registrar_cache("meteo", fresco)
    if fresco:
        edad = int((ahora - fa).total_seconds() // 60)
        return _meteo_cache["condiciones"], "live", edad, fa, None

    try:
        from pipeline.ingest.meteorologia_madrid import fetch_meteorologia_actual
        df = fetch_meteorologia_actual()
        if df is None or len(df) == 0:
            raise RuntimeError("el feed en vivo respondió sin lecturas válidas")
        condiciones = _df_a_condiciones(df)
        _meteo_cache["fetched_at"] = ahora
        _meteo_cache["condiciones"] = condiciones
        _guardar_snapshot_meteo(ahora, condiciones)
        return condiciones, "live", 0, ahora, None
    except Exception as e:
        logger.warning("Meteorología en vivo no disponible: %s", e)
        snap = _cargar_snapshot_meteo()
        if snap is not None:
            fa_s, condiciones = snap
            edad = max(0, int((ahora - fa_s).total_seconds() // 60))
            return condiciones, "cache", edad, fa_s, (
                f"feed en vivo no disponible ({e}); se sirve el último snapshot "
                f"guardado hace {edad} min"
            )
        return [], "cache", None, None, (
            f"feed en vivo no disponible ({e}) y no hay snapshot previo en disco"
        )


def _meteorologia_por_zona(condiciones: list[dict]) -> dict:
    """A cada distrito, las magnitudes de la estación municipal más cercana a
    su centroide. Distancia con corrección de longitud por latitud -- no
    hace falta proyectar para un 'más cercano' entre 26 puntos."""
    k = math.cos(math.radians(40.42))
    salida = {}
    for distrito, (dlat, dlon) in DISTRITO_CENTROIDES.items():
        mejor, mejor_d2 = None, None
        for c in condiciones:
            if c["lon"] is None or c["lat"] is None:
                continue
            dx = (c["lon"] - dlon) * k
            dy = c["lat"] - dlat
            d2 = dx * dx + dy * dy
            if mejor_d2 is None or d2 < mejor_d2:
                mejor, mejor_d2 = c, d2
        salida[distrito] = None if mejor is None else {
            "temperatura": mejor["temperatura"],
            "humedad_relativa": mejor["humedad_relativa"],
            "precipitacion_mm": mejor["precipitacion_mm"],
            "viento_kmh": mejor["viento_kmh"],
        }
    return salida


# ─────────────────────────────────────────
# Validación de parámetros de petición. Errores de negocio (no artefactos
# ausentes): se traducen a 400 VALIDATION_ERROR / 404 NOT_FOUND en el
# endpoint que las llama, no aquí -- estas funciones solo lanzan.
# ─────────────────────────────────────────

def _validar_fecha(fecha_str: str) -> str:
    try:
        datetime.strptime(fecha_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise ValueError(f"Fecha inválida: {fecha_str!r}. Formato esperado YYYY-MM-DD.")
    return fecha_str


def _validar_hora(hora_raw) -> int:
    try:
        hora = int(hora_raw)
    except (TypeError, ValueError):
        raise ValueError(f"Hora inválida: {hora_raw!r}. Debe ser un entero entre 0 y 23.")
    if not (0 <= hora <= 23):
        raise ValueError(f"Hora fuera de rango: {hora}. Debe estar entre 0 y 23.")
    return hora


def _validar_coordenadas_madrid(lat: float, lon: float, etiqueta: str) -> None:
    b = cfg.MADRID_BBOX
    if not (b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]):
        raise ValueError(f"Coordenadas de {etiqueta} fuera de Madrid: lat={lat}, lon={lon}.")


def _resolver_vehiculo_ancho_galibo(args) -> tuple[float, float]:
    """
    Resuelve ancho_req/galibo_req de una petición: vehiculo (id de /config)
    o el default si no se especifica, con ancho_req/galibo_req explícitos
    ganando siempre sobre el vehículo (mismo criterio que documenta
    /ruta). Compartido entre /ruta e /isocronas -- ver _vehiculo_efectivo
    para el sentido inverso (ancho/gálibo -> id de vehículo).
    """
    vehiculo_id = args.get("vehiculo")
    if vehiculo_id is not None:
        vehiculo_base = cfg.get_vehiculo(vehiculo_id)
        if vehiculo_base is None:
            raise ValueError(f"Vehículo inexistente: {vehiculo_id!r}. Consulta /config para el catálogo disponible.")
    else:
        vehiculo_base = cfg.get_vehiculo_default()

    ancho_req = float(args["ancho_req"]) if "ancho_req" in args else vehiculo_base["ancho_m"]
    galibo_req = float(args["galibo_req"]) if "galibo_req" in args else vehiculo_base["galibo_m"]
    return ancho_req, galibo_req


def _vehiculo_efectivo(ancho_req: float, galibo_req: float) -> str:
    """id del vehículo del catálogo cuyo ancho/gálibo coincide exactamente con lo
    efectivamente usado, o 'personalizado' si no coincide con ninguno (p. ej.
    ancho_req/galibo_req explícitos que no son los de ningún vehículo)."""
    return next(
        (v["id"] for v in cfg.VEHICULOS if v["ancho_m"] == ancho_req and v["galibo_m"] == galibo_req),
        "personalizado",
    )


def _zona_de_nodo(G, node_id) -> str | None:
    """
    Distrito (`zona`) del nodo destino de una ruta. Las aristas del
    callejero ya llevan `zona` (ver routing/optimizer.py::_dynamic_weight);
    se toma la de cualquier arista incidente al nodo (entrante o saliente),
    ignorando 'Desconocida' y las aristas de acceso a equipamientos (que no
    llevan `zona`). None si no se puede determinar -- p. ej. destino
    resuelto por identidad a un nodo especial sin calle con zona alrededor.
    """
    if node_id not in G:
        return None
    aristas = list(G.out_edges(node_id, data=True)) + list(G.in_edges(node_id, data=True))
    for _, _, d in aristas:
        zona = d.get("zona")
        if zona and zona != "Desconocida":
            return zona
    return None


def _resolver_extremo(args, prefijo: str) -> tuple:
    """
    Resuelve un extremo de /ruta (origen o destino) a un node_id del grafo
    servido, en uno de sus dos modos mutuamente excluyentes:
      - coordenadas: {prefijo}_lat + {prefijo}_lon -> get_nearest_node
        (nunca puede devolver un nodo especial, ver graph_engine.py).
      - identidad: {prefijo}_tipo_nodo + {prefijo}_nombre -> get_special_node.

    Devuelve (node_id, tipo_nodo, nombre); tipo_nodo/nombre quedan None en
    modo coordenadas (ver schema NodoResuelto de docs/p5/openapi_p5.yaml).

    Lanza ValueError (-> 400 VALIDATION_ERROR) si los parámetros son
    ambiguos/incompletos, o LookupError (-> 404 NOT_FOUND) si el modo
    identidad no resuelve a ningún nodo especial real.
    """
    lat_raw = args.get(f"{prefijo}_lat")
    lon_raw = args.get(f"{prefijo}_lon")
    tipo_nodo = args.get(f"{prefijo}_tipo_nodo")
    nombre = args.get(f"{prefijo}_nombre")

    modo_coords = lat_raw is not None or lon_raw is not None
    modo_identidad = tipo_nodo is not None or nombre is not None

    if modo_coords and modo_identidad:
        raise ValueError(
            f"{prefijo}: usa coordenadas ({prefijo}_lat/{prefijo}_lon) o identidad "
            f"({prefijo}_tipo_nodo/{prefijo}_nombre), no ambas a la vez."
        )
    if not modo_coords and not modo_identidad:
        raise ValueError(
            f"{prefijo}: falta {prefijo}_lat/{prefijo}_lon o "
            f"{prefijo}_tipo_nodo/{prefijo}_nombre."
        )

    if modo_coords:
        if lat_raw is None or lon_raw is None:
            raise ValueError(f"{prefijo}: {prefijo}_lat y {prefijo}_lon son obligatorios juntos.")
        try:
            lat, lon = float(lat_raw), float(lon_raw)
        except ValueError:
            raise ValueError(f"{prefijo}: {prefijo}_lat/{prefijo}_lon deben ser numéricos.")
        _validar_coordenadas_madrid(lat, lon, prefijo)
        from routing.graph_engine import get_nearest_node
        return get_nearest_node(lat, lon), None, None

    if tipo_nodo is None or nombre is None:
        raise ValueError(f"{prefijo}: {prefijo}_tipo_nodo y {prefijo}_nombre son obligatorios juntos.")
    if tipo_nodo not in ("bomberos", "hospitales"):
        raise ValueError(f"{prefijo}_tipo_nodo debe ser 'bomberos' o 'hospitales', recibido: {tipo_nodo!r}.")
    from routing.graph_engine import get_special_node
    node_id = get_special_node(tipo_nodo, nombre)
    if node_id is None:
        raise LookupError(f"No existe un nodo {tipo_nodo!r} con nombre {nombre!r}.")
    return node_id, tipo_nodo, nombre


# ─────────────────────────────────────────
# Errores: cualquier artefacto ausente detectado como FileNotFoundError
# en cualquier endpoint (grafo, modelo, tabla DuckDB...) se convierte
# aquí en JSON explícito -- nunca en el 500 HTML del debugger de Flask.
# Los mensajes ya nombran el fichero y cómo regenerarlo porque así los
# escribieron P1/P3/P4 (graph_engine.py, predict_trafico_real.py); este
# handler no reimplementa ese texto, solo evita que Flask lo descarte.
# ─────────────────────────────────────────
@app.errorhandler(FileNotFoundError)
def handle_missing_artifact(e: FileNotFoundError):
    mensaje = str(e)
    logger.error("Artefacto ausente: %s", mensaje)
    body = {"error": mensaje, "code": "MISSING_ARTIFACT"}
    fichero = _extraer_fichero(mensaje)
    if fichero:
        body["fichero"] = fichero
    return jsonify(body), 503


@app.errorhandler(Exception)
def handle_unexpected_error(e: Exception):
    """
    Red de seguridad: cualquier excepción no prevista en un endpoint sale
    como JSON con code=INTERNAL_ERROR y 500, nunca el HTML del debugger de
    Flask -- Streamlit y cualquier cliente HTTP esperan JSON en TODA
    respuesta de error (regla transversal de docs/p5/openapi_p5.yaml).

    Los HTTPException de Werkzeug (404 de ruta desconocida, 405 de método...)
    se dejan pasar tal cual: ya son respuestas HTTP correctas. FileNotFoundError
    tiene su propio handler, más específico, que gana sobre este.
    """
    if isinstance(e, HTTPException):
        return e
    logger.exception("Error interno no controlado")
    return jsonify({
        "error": f"Error interno del servidor: {e}",
        "code": "INTERNAL_ERROR",
    }), 500


# ─────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────

@app.route("/health")
def health():
    """
    Diagnóstico honesto del servicio: nada de status fijo. 200 si el
    servicio puede servir rutas (grafo cargado); 503 si no.
    ---
    tags: [Sistema]
    responses:
      200:
        description: Servicio operativo, puede servir rutas.
      503:
        description: El grafo no está cargado -- no se pueden servir rutas.
    """
    G = _graph_state["G"]

    from ml import predict_trafico_real as _mlreal
    modelo_cargado = getattr(_mlreal, "_model", None) is not None

    duckdb_path = ARTEFACTOS_ESPERADOS["duckdb"]
    duckdb_accesible = False
    if os.path.exists(duckdb_path):
        try:
            import duckdb
            con = duckdb.connect(duckdb_path, read_only=True)
            con.execute("SELECT 1").fetchone()
            con.close()
            duckdb_accesible = True
        except Exception as e:
            logger.warning("DuckDB no accesible (%s): %s", duckdb_path, e)

    artefactos_faltantes = [
        _ruta_relativa(path)
        for path in ARTEFACTOS_ESPERADOS.values()
        if not os.path.exists(path)
    ]

    puede_servir_rutas = G is not None
    status_code = 200 if puede_servir_rutas else 503

    body = {
        "status": "ok" if puede_servir_rutas else "degraded",
        "grafo": {
            "cargado": puede_servir_rutas,
            "nodes": G.number_of_nodes() if G is not None else None,
            "edges": G.number_of_edges() if G is not None else None,
            "nodos_especiales": _graph_state["nodos_especiales"],
            "segundos_carga": _graph_state["load_seconds"],
            "error": _graph_state["error"] if G is None else None,
        },
        "modelo_trafico": {
            "cargado": modelo_cargado,
            "ruta_pkl": _ruta_relativa(_mlreal.MODEL_PATH),
        },
        "duckdb": {
            "accesible": duckdb_accesible,
            "ruta": _ruta_relativa(duckdb_path),
        },
        "artefactos_faltantes": artefactos_faltantes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return jsonify(body), status_code


@app.route("/version")
def version():
    """
    Identidad de la build en ejecución: versión del contrato de la API,
    SHA de git (recortado), fecha de build y sha256 del manifiesto de
    datos con el que se empaquetó la imagen. Los tres últimos se inyectan
    como build args del Dockerfile; si la imagen se construyó sin ellos
    (p. ej. `docker build` a mano), valen "desconocido". No expone rutas
    ni versiones de dependencias a propósito (ver "Escalado de seguridad"
    en docs/p5/README_P5_API_Streamlit.md).
    ---
    tags: [Sistema]
    responses:
      200:
        description: version_api + git_sha + fecha_build + sha256_manifiesto_datos.
    """
    return jsonify(build_info()), 200


@app.route("/metrics")
def metrics_endpoint():
    """
    Métricas en formato Prometheus (texto). Contadores y latencias por
    endpoint y aciertos/fallos de las cachés en memoria. No hay ningún
    Prometheus levantado: para un sistema de un usuario esto se lee con
    curl; el endpoint queda por si algún día se le apunta un scraper (ver
    docs/adr/0008).
    ---
    tags: [Sistema]
    responses:
      200:
        description: Exposición Prometheus (text/plain; version=0.0.4).
    """
    cuerpo, content_type = metrics.exposicion()
    return Response(cuerpo, status=200, mimetype=content_type)


@app.route("/config")
def config():
    """
    Catálogo compartido de vehículos, algoritmos y defaults.
    Única fuente para la API y para Streamlit (ver app/config.py).
    ---
    tags: [Configuración]
    responses:
      200:
        description: Catálogo de vehículos y valores por defecto.
    """
    body = {
        "vehiculos": [
            {
                "id": v["id"],
                "nombre": v["nombre"],
                "ancho_req_m": v["ancho_m"],
                "galibo_req_m": v["galibo_m"],
                "descripcion": v["descripcion"],
            }
            for v in cfg.VEHICULOS
        ],
        "vehiculo_default": cfg.VEHICULO_DEFAULT_ID,
        "algoritmos_disponibles": cfg.ALGORITMOS_DISPONIBLES,
        "algoritmo_default": cfg.ALGORITMO_DEFAULT,
        "cortes_isocronas_min_default": cfg.CORTES_ISOCRONAS_MIN_DEFAULT,
        "trafico_niveles": {str(k): v for k, v in cfg.TRAFICO_NIVELES.items()},
    }
    return jsonify(body)


@app.route("/prediccion_trafico")
def prediccion_trafico():
    """
    Nivel de tráfico predicho por distrito para una fecha y hora, usando el
    modelo XGBoost real de P3 (ml.predict_trafico_real). Predicciones
    cacheadas por (fecha, hora) -- ver _predecir_trafico_cacheado.
    ---
    tags: [IA]
    parameters:
      - name: date
        in: query
        type: string
        required: true
        example: "2025-06-15"
      - name: hora
        in: query
        type: integer
        required: false
        default: 8
        example: 8
    responses:
      200:
        description: parametros_efectivos + trafico_por_zona (0=Bajo,1=Medio,2=Alto)
      400:
        description: Falta date, o date/hora inválidos.
    """
    fecha = request.args.get("date")
    if not fecha:
        return jsonify({"error": "Falta parámetro date", "code": "VALIDATION_ERROR"}), 400

    try:
        fecha = _validar_fecha(fecha)
        hora = _validar_hora(request.args.get("hora", datetime.now().hour))
    except ValueError as e:
        return jsonify({"error": str(e), "code": "VALIDATION_ERROR"}), 400

    zonas = get_distritos()
    trafico_por_zona = _predecir_trafico_cacheado(fecha, hora)
    return jsonify({
        "parametros_efectivos": {"fecha": fecha, "hora": hora, "zonas": zonas},
        "trafico_por_zona": trafico_por_zona,
    })


@app.route("/ruta")
def ruta():
    """
    Calcula ruta óptima para un vehículo de emergencia entre origen y
    destino. Origen/destino admiten coordenadas o identidad de nodo
    especial (tipo + nombre); el vehículo se resuelve por id de /config o
    por ancho_req/galibo_req explícitos, que siempre ganan. Ver
    docs/p5/openapi_p5.yaml para el contrato completo.
    ---
    tags: [Rutas]
    parameters:
      - name: orig_lat
        in: query
        type: number
      - name: orig_lon
        in: query
        type: number
      - name: orig_tipo_nodo
        in: query
        type: string
      - name: orig_nombre
        in: query
        type: string
      - name: dest_lat
        in: query
        type: number
      - name: dest_lon
        in: query
        type: number
      - name: dest_tipo_nodo
        in: query
        type: string
      - name: dest_nombre
        in: query
        type: string
      - name: vehiculo
        in: query
        type: string
      - name: ancho_req
        in: query
        type: number
      - name: galibo_req
        in: query
        type: number
      - name: algoritmo
        in: query
        type: string
        default: dijkstra
      - name: date
        in: query
        type: string
        required: false
        example: "2025-06-15"
      - name: hora
        in: query
        type: integer
        required: false
        example: 8
    responses:
      200:
        description: GeoJSON FeatureCollection con la ruta, parametros_efectivos, trafico_por_zona, origen, destino y zona_destino (distrito del incidente + su nivel de tráfico previsto).
      400:
        description: Parámetros inválidos, ambiguos o incompletos.
      404:
        description: Identidad de nodo especial inexistente (NOT_FOUND), o nodo resuelto ya no en el grafo (NO_ROUTE).
    """
    # Se pide el grafo antes de resolver ningún parámetro -- si no está
    # cargado, ni get_nearest_node ni get_special_node deben intentar nada
    # (ambos tienen su propio fallback de carga perezosa en graph_engine.py,
    # que aquí queremos evitar por completo: el grafo se carga una única
    # vez al arrancar el proceso, nunca dentro de un endpoint).
    G = get_graph()

    try:
        origin_node, orig_tipo, orig_nombre = _resolver_extremo(request.args, "orig")
        dest_node, dest_tipo, dest_nombre = _resolver_extremo(request.args, "dest")

        ancho_req, galibo_req = _resolver_vehiculo_ancho_galibo(request.args)

        algoritmo = request.args.get("algoritmo", cfg.ALGORITMO_DEFAULT)
        if algoritmo not in cfg.ALGORITMOS_DISPONIBLES:
            raise ValueError(f"Algoritmo no soportado: {algoritmo!r}. Usa uno de {cfg.ALGORITMOS_DISPONIBLES}.")

        fecha = request.args.get("date")
        if fecha is not None:
            fecha = _validar_fecha(fecha)
        hora = _validar_hora(request.args.get("hora", datetime.now().hour))
    except ValueError as e:
        return jsonify({"error": str(e), "code": "VALIDATION_ERROR"}), 400
    except LookupError as e:
        return jsonify({"error": str(e), "code": "NOT_FOUND"}), 404

    from routing.optimizer import calcular_ruta

    trafico_por_zona = _predecir_trafico_cacheado(fecha, hora) if fecha is not None else {}

    # calcular_ruta ya comprueba internamente que origin_node/dest_node estén
    # en G (devuelve None si no) -- no se repite esa comprobación aquí, para
    # no replicar lógica de P4 (invariante "ningún endpoint reimplementa la
    # lógica de P1-P4": la API la invoca, no la copia; ver
    # docs/p5/README_P5_API_Streamlit.md §1). Cubre tanto un id resuelto que
    # ya no existe en el grafo servido como cualquier otro futuro motivo por
    # el que P4 decida devolver None.
    with logging_setup.log_duration(
        logger, "calculo_ruta",
        algoritmo=algoritmo, con_trafico=fecha is not None,
    ):
        geojson = calcular_ruta(
            G, origin_node, dest_node, trafico_por_zona,
            ancho_req=ancho_req, galibo_req=galibo_req, algoritmo=algoritmo,
        )
    if geojson is None:
        return jsonify({
            "error": "El nodo de origen o destino resuelto ya no existe en el grafo servido.",
            "code": "NO_ROUTE",
        }), 404

    props = geojson["features"][0]["properties"]
    nuevas_props = {
        "distancia_m": props["length_m"],
        "tiempo_min": props["time_min"],
        "n_nodes": props["n_nodes"],  # campo adicional no contractual, mantenido para Streamlit
        "ruta_completa": props["ruta_completa"],
    }
    if not props["ruta_completa"]:
        nuevas_props["distancia_sin_cubrir_m"] = props["distancia_restante_destino_m"]
        nuevas_props["motivo"] = (
            f"Destino no alcanzable con ancho_req={ancho_req}m y/o galibo_req={galibo_req}m; "
            "devuelta ruta parcial hasta el nodo accesible más próximo."
        )
    geojson["features"][0]["properties"] = nuevas_props

    geojson["parametros_efectivos"] = {
        "ancho_req": ancho_req,
        "galibo_req": galibo_req,
        "algoritmo": algoritmo,
        "fecha": fecha,
        "hora": hora,
        "vehiculo": _vehiculo_efectivo(ancho_req, galibo_req),
    }
    geojson["trafico_por_zona"] = trafico_por_zona
    geojson["origen"] = {"node_id": origin_node, "tipo_nodo": orig_tipo, "nombre": orig_nombre}
    geojson["destino"] = {"node_id": dest_node, "tipo_nodo": dest_tipo, "nombre": dest_nombre}

    # Distrito del incidente y su nivel de tráfico previsto (campo aditivo,
    # bloque 7): el dashboard lo muestra como cifra grande. `nivel_trafico`
    # es None si no se pidió tráfico (sin date) o si no se puede localizar
    # el distrito; nunca inventa un 0.
    zona_destino = _zona_de_nodo(G, dest_node)
    geojson["zona_destino"] = {
        "zona": zona_destino,
        "nivel_trafico": trafico_por_zona.get(zona_destino) if zona_destino is not None else None,
    }

    return Response(json.dumps(geojson), status=200, mimetype="application/geo+json")


@app.route("/isocronas")
def isocronas():
    """
    Polígonos de cobertura (5/10/15 min por defecto) desde un parque de
    bomberos real, calculados en vivo con routing/isochrones.py::calcular_isocronas
    (misma lógica de filtrado físico/tráfico que /ruta, nunca duplicada aquí).
    Cacheados por (parque, ancho_req, galibo_req, fecha, hora) -- ver
    _isocronas_cache, medido en ~3.5s por parque sin caché contra el grafo
    real de Madrid.
    ---
    tags: [Rutas]
    parameters:
      - name: nombre
        in: query
        type: string
        required: true
        description: Nombre exacto del parque de bomberos (ver /equipamientos?tipo=bomberos).
      - name: corte_min
        in: query
        type: number
        required: false
        description: Si se especifica, devuelve solo ese corte (uno de /config.cortes_isocronas_min_default); si se omite, devuelve todos.
      - name: vehiculo
        in: query
        type: string
      - name: ancho_req
        in: query
        type: number
      - name: galibo_req
        in: query
        type: number
      - name: date
        in: query
        type: string
        required: false
        example: "2025-06-15"
      - name: hora
        in: query
        type: integer
        required: false
        example: 8
    responses:
      200:
        description: GeoJSON FeatureCollection (una feature por corte_min), con parque, parametros_efectivos y trafico_por_zona.
      400:
        description: Parámetros inválidos (vehículo inexistente, fecha/hora inválidas, corte_min no soportado).
      404:
        description: "`nombre` no resuelve a ningún parque de bomberos real, o el nodo resuelto ya no está en el grafo servido."
    """
    G = get_graph()

    try:
        nombre = request.args.get("nombre")
        if not nombre:
            raise ValueError("Falta el parámetro nombre (nombre exacto del parque de bomberos).")

        from routing.graph_engine import get_special_node
        origin_node = get_special_node("bomberos", nombre)
        if origin_node is None:
            raise LookupError(
                f"No existe un parque de bomberos con nombre {nombre!r}. "
                "Consulta /equipamientos?tipo=bomberos para el catálogo real."
            )
        if origin_node not in G:
            raise LookupError(f"El parque {nombre!r} se resolvió a un nodo que ya no está en el grafo servido.")

        ancho_req, galibo_req = _resolver_vehiculo_ancho_galibo(request.args)

        fecha = request.args.get("date")
        if fecha is not None:
            fecha = _validar_fecha(fecha)
        hora = _validar_hora(request.args.get("hora", datetime.now().hour))

        corte_filtro = None
        corte_raw = request.args.get("corte_min")
        if corte_raw is not None:
            try:
                corte_filtro = float(corte_raw)
            except ValueError:
                raise ValueError(f"corte_min inválido: {corte_raw!r}. Debe ser numérico.")
            if corte_filtro not in cfg.CORTES_ISOCRONAS_MIN_DEFAULT:
                raise ValueError(
                    f"corte_min no soportado: {corte_filtro!r}. Usa uno de {cfg.CORTES_ISOCRONAS_MIN_DEFAULT}."
                )
    except ValueError as e:
        return jsonify({"error": str(e), "code": "VALIDATION_ERROR"}), 400
    except LookupError as e:
        return jsonify({"error": str(e), "code": "NOT_FOUND"}), 404

    trafico_por_zona = _predecir_trafico_cacheado(fecha, hora) if fecha is not None else {}

    # Petición por defecto (vehículo de referencia, sin fecha) -> sirve el
    # polígono precomputado si el parque está en isocronas_bomberos.geojson.
    # Resto de combinaciones -> Dijkstra en vivo, cacheado como hasta ahora.
    cortes_esperados = {float(c) for c in cfg.CORTES_ISOCRONAS_MIN_DEFAULT}
    poligonos_por_corte = None
    origen_isocronas = "vivo"
    if _peticion_isocronas_por_defecto(ancho_req, galibo_req, fecha):
        precomp = _cargar_isocronas_precomp().get(nombre)
        if precomp is not None and cortes_esperados.issubset(precomp):
            poligonos_por_corte = dict(precomp)
            origen_isocronas = "precomputado"

    if poligonos_por_corte is not None:
        metrics.registrar_isocronas_origen("precomputado")
    else:
        clave = (origin_node, ancho_req, galibo_req, fecha, hora)
        cache_hit = clave in _isocronas_cache
        metrics.registrar_isocronas_origen("vivo_cacheado" if cache_hit else "vivo_calculado")
        if not cache_hit:
            from routing.isochrones import calcular_isocronas
            with logging_setup.log_duration(
                logger, "calculo_isocronas", parque=nombre,
                algoritmo="dijkstra", con_trafico=fecha is not None,
            ):
                _isocronas_cache[clave] = calcular_isocronas(
                    G, origin_node, cortes_min=cfg.CORTES_ISOCRONAS_MIN_DEFAULT,
                    ancho_req=ancho_req, galibo_req=galibo_req, traffic_preds=trafico_por_zona,
                )
        poligonos_por_corte = _isocronas_cache[clave]

    if corte_filtro is not None:
        poligonos_por_corte = {c: p for c, p in poligonos_por_corte.items() if c == corte_filtro}

    import geopandas as gpd
    gdf = gpd.GeoDataFrame(
        {"corte_min": list(poligonos_por_corte.keys())},
        geometry=list(poligonos_por_corte.values()),
        crs="EPSG:4326",
    )
    geojson = json.loads(gdf.to_json())
    geojson["parque"] = {"node_id": origin_node, "tipo_nodo": "bomberos", "nombre": nombre}
    geojson["parametros_efectivos"] = {
        "ancho_req": ancho_req,
        "galibo_req": galibo_req,
        "fecha": fecha,
        "hora": hora,
        "vehiculo": _vehiculo_efectivo(ancho_req, galibo_req),
    }
    geojson["trafico_por_zona"] = trafico_por_zona
    # Señal de diagnóstico fuera del cuerpo JSON (no toca el contrato del
    # schema): "precomputado" = servido de isocronas_bomberos.geojson,
    # "vivo" = Dijkstra en el momento.
    return Response(
        json.dumps(geojson),
        status=200,
        mimetype="application/geo+json",
        headers={"X-Isocronas-Origen": origen_isocronas},
    )


@app.route("/equipamientos")
def equipamientos():
    """
    Devuelve instalaciones críticas de Madrid.
    ---
    tags: [Datos]
    parameters:
      - name: tipo
        in: query
        type: string
        required: false
        enum: [bomberos, hospitales, centros_educativos, centros_mayores]
    responses:
      200:
        description: GeoJSON FeatureCollection
    """
    tipo = request.args.get("tipo", "bomberos")
    import geopandas as gpd
    path_map = {
        "bomberos": ARTEFACTOS_ESPERADOS["parques_bomberos_geojson"],
        "hospitales": ARTEFACTOS_ESPERADOS["hospitales_geojson"],
        "centros_educativos": os.path.join(PROCESSED_DIR, "centros_educativos.geojson"),
        "centros_mayores": os.path.join(PROCESSED_DIR, "centros_mayores.geojson"),
    }
    path = path_map.get(tipo)
    if path is None:
        return jsonify({"error": f"Tipo de equipamiento '{tipo}' no reconocido.", "code": "VALIDATION_ERROR"}), 400
    if not os.path.exists(path):
        return jsonify({
            "error": f"Equipamiento '{tipo}' no disponible: {_ruta_relativa(path)} no existe.",
            "code": "MISSING_ARTIFACT",
            "fichero": _ruta_relativa(path),
        }), 404

    gdf = gpd.read_file(path)
    geojson = json.loads(gdf.to_json())
    # `tipo` a nivel superior = parámetro efectivo resuelto, igual que el
    # resto de endpoints (docs/p5/openapi_p5.yaml: required [type, features, tipo]).
    geojson["tipo"] = tipo
    return Response(json.dumps(geojson), mimetype="application/geo+json")


# Geocodificador de conveniencia: texto libre -> coordenadas. Solo para que
# el usuario del dashboard escriba una calle en vez de teclear lat/lon; el
# cálculo de /ruta sigue siendo por coordenadas. Envuelve Nominatim (OSM),
# restringido a España y filtrado al bounding box de Madrid. No participa en
# ninguna ruta ni isócrona.
_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_NOMINATIM_UA = "TFM-CentralRutasBomberos/1.0 (uso academico)"


@app.route("/geocodificar")
def geocodificar():
    """
    Traduce una dirección/calle de Madrid a coordenadas (conveniencia del
    dashboard; /ruta sigue trabajando con lat/lon). Envuelve Nominatim (OSM).
    ---
    tags: [Datos]
    parameters:
      - name: q
        in: query
        type: string
        required: true
        example: "Calle de Alcalá 100"
    responses:
      200:
        description: "{lat, lon, direccion} del primer resultado dentro de Madrid."
      400:
        description: Falta el parámetro q.
      404:
        description: Sin resultados, o el resultado cae fuera del término municipal de Madrid.
      503:
        description: El geocodificador externo (Nominatim) no está disponible.
    """
    import requests

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "Falta el parámetro q (dirección a buscar).", "code": "VALIDATION_ERROR"}), 400

    try:
        resp = requests.get(
            _NOMINATIM_URL,
            params={
                "q": f"{q}, Madrid, España", "format": "jsonv2", "limit": 1,
                "countrycodes": "es", "addressdetails": 1,
            },
            headers={"User-Agent": _NOMINATIM_UA},
            timeout=8,
        )
        resp.raise_for_status()
        resultados = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Nominatim no disponible: %s", e)
        return jsonify({
            "error": "El buscador de direcciones no está disponible ahora mismo. "
                     "Introduce las coordenadas del destino a mano.",
            "code": "GEOCODER_UNAVAILABLE",
        }), 503

    if not resultados:
        return jsonify({
            "error": f"No se ha encontrado ninguna dirección para {q!r} en Madrid.",
            "code": "DIRECCION_NO_ENCONTRADA",
        }), 404

    r0 = resultados[0]
    try:
        lat, lon = float(r0["lat"]), float(r0["lon"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "Respuesta del geocodificador no interpretable.", "code": "GEOCODER_UNAVAILABLE"}), 503

    b = cfg.MADRID_BBOX
    if not (b["lat_min"] <= lat <= b["lat_max"] and b["lon_min"] <= lon <= b["lon_max"]):
        return jsonify({
            "error": f"La dirección {q!r} se resolvió fuera del término municipal de Madrid.",
            "code": "DIRECCION_NO_ENCONTRADA",
        }), 404

    return jsonify({
        "lat": lat,
        "lon": lon,
        "direccion": r0.get("display_name", q),
    })


@app.route("/meteorologia")
def meteorologia():
    """
    Lectura meteorológica actual por distrito y alerta INFORMATIVA.

    Envuelve pipeline/ingest/meteorologia_madrid.py::fetch_meteorologia_actual
    (feed municipal en vivo). El bloque `alerta` (nivel normal/precaucion/
    adversa) lo calcula app/meteo_alerta.py -- módulo único de umbrales. Es
    puramente informativo: /ruta no lee este endpoint ni depende de él, y el
    modelo de tráfico de P3 excluye la meteorología a propósito.
    ---
    tags: [Meteorología]
    parameters:
      - name: zona
        in: query
        type: string
        required: false
        description: Filtra a un único distrito (nomenclatura de ml.generate_dataset.DISTRITOS_MADRID). Sin este parámetro, devuelve los 21.
      - name: fuente
        in: query
        type: string
        required: false
        default: municipal
        description: >
          municipal (feed en vivo de P1, por defecto) o aemet. El adaptador
          de AEMET NO está implementado en este despliegue: pedir aemet
          sirve el feed municipal igualmente y lo indica en
          `motivo`/`aemet_disponible`, sin error.
    responses:
      200:
        description: >
          informativo + aviso + disponible + origen ("live"/"cache") +
          edad_min + alerta{nivel,criterio,nota_informativa} +
          parametros_efectivos + meteorologia_por_zona. Nunca 5xx: un panel
          informativo no debe poder tumbar la petición de quien lo consume.
      400:
        description: zona o fuente no reconocidas.
    """
    try:
        fuente_req = request.args.get("fuente", "municipal")
        if fuente_req not in ("municipal", "aemet"):
            return jsonify({
                "error": f"fuente no soportada: {fuente_req!r}. Usa 'municipal' o 'aemet'.",
                "code": "VALIDATION_ERROR",
            }), 400

        zona_req = request.args.get("zona")
        if zona_req is not None and zona_req not in DISTRITO_CENTROIDES:
            return jsonify({
                "error": f"zona desconocida: {zona_req!r}. Debe ser uno de los 21 distritos de Madrid "
                         "(nomenclatura de ml.generate_dataset.DISTRITOS_MADRID).",
                "code": "VALIDATION_ERROR",
            }), 400

        ahora = datetime.now(timezone.utc)
        condiciones, origen, edad_min, ts_lectura, motivo_datos = _get_condiciones_meteo(ahora)
        disponible = len(condiciones) > 0

        por_zona = _meteorologia_por_zona(condiciones) if disponible else {}
        if zona_req is not None:
            por_zona = {zona_req: por_zona[zona_req]} if disponible else {}

        alerta = meteo_alerta.clasificar_alerta(condiciones)

        aemet_pedido = fuente_req == "aemet"
        motivo_aemet = None
        if aemet_pedido:
            if os.environ.get("AEMET_API_KEY"):
                motivo_aemet = (
                    "AEMET_API_KEY está configurada, pero el adaptador de AEMET no está "
                    "implementado en este despliegue; se sirve el feed municipal."
                )
            else:
                motivo_aemet = (
                    "AEMET no configurado (sin AEMET_API_KEY); se sirve el feed municipal, "
                    "que no requiere clave."
                )

        body = {
            "informativo": True,
            "aviso": meteo_alerta.NOTA_INFORMATIVA,
            "disponible": disponible,
            "origen": origen,
            "edad_min": edad_min,
            "alerta": alerta,
            "parametros_efectivos": {
                "fuente": "municipal",
                "zona": zona_req,
                "timestamp_lectura": ts_lectura.isoformat() if ts_lectura is not None else None,
            },
            "meteorologia_por_zona": por_zona,
        }
        motivos = [m for m in (motivo_datos, motivo_aemet) if m]
        if motivos:
            body["motivo"] = " ".join(motivos)
        if aemet_pedido:
            body["aemet_disponible"] = False
        return jsonify(body), 200

    except Exception as e:  # panel informativo: nunca 5xx
        logger.exception("Error inesperado en /meteorologia")
        return jsonify({
            "informativo": True,
            "aviso": meteo_alerta.NOTA_INFORMATIVA,
            "disponible": False,
            "origen": "cache",
            "edad_min": None,
            "alerta": {
                "nivel": meteo_alerta.NIVEL_NORMAL,
                "criterio": "no se pudo evaluar la meteorología (error interno)",
                "nota_informativa": meteo_alerta.NOTA_INFORMATIVA,
            },
            "parametros_efectivos": {
                "fuente": "municipal", "zona": request.args.get("zona"), "timestamp_lectura": None,
            },
            "meteorologia_por_zona": {},
            "motivo": f"error interno al construir la respuesta: {e}",
        }), 200


# ─────────────────────────────────────────
# Arranque
# ─────────────────────────────────────────

def run_dev():
    logger.info("Servidor de DESARROLLO en http://localhost:%d", PORT)
    logger.info("Swagger UI en http://localhost:%d/apidocs", PORT)
    # use_reloader=False: con el reloader activo, Werkzeug reimporta el
    # módulo entero al arrancar (lo comprobamos: aparece "Restarting with
    # watchdog" y todo el log de arranque se repite) -- eso dispararía
    # _cargar_grafo_al_arrancar() dos veces y duplicaría inútilmente los
    # ~17s de carga en cada arranque de --dev, ya que el grafo no cambia
    # en caliente. Se pierde el auto-reload ante cambios de código, pero
    # es aceptable en este proyecto: hay que reiniciar a mano tras editar.
    app.run(host="0.0.0.0", port=PORT, debug=True, use_reloader=False)


def run_prod():
    try:
        from gunicorn.app.base import BaseApplication
    except ImportError:
        logger.warning("Gunicorn no instalado, usando servidor de desarrollo.")
        run_dev()
        return

    class GunicornApp(BaseApplication):
        def __init__(self, application, options=None):
            self.options = options or {}
            self.application = application
            super().__init__()
        def load_config(self):
            for key, value in self.options.items():
                if key in self.cfg.settings and value is not None:
                    self.cfg.set(key.lower(), value)
        def load(self):
            return self.application

    workers = int(os.environ.get("WORKERS", 2))
    GunicornApp(app, {"bind": f"0.0.0.0:{PORT}", "workers": workers}).run()


if __name__ == "__main__":
    # Carga el grafo UNA vez, antes de aceptar la primera conexión --
    # nunca dentro de un endpoint, y solo cuando este fichero se ejecuta
    # de verdad como servidor (no al hacer `import app.server` desde un
    # test, que así puede sustituir routing.graph_engine.load_graph por
    # un doble antes de disparar la carga a mano). Asume que el proceso
    # siempre arranca por este bloque (`python app/server.py`, dev o
    # prod) -- un despliegue vía `gunicorn app.server:app` externo, que
    # no pasa por aquí, tendría que llamar a _cargar_grafo_al_arrancar()
    # por su cuenta.
    _cargar_grafo_al_arrancar()
    if "--dev" in sys.argv or os.environ.get("FLASK_ENV") == "development":
        run_dev()
    else:
        run_prod()
