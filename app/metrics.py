"""
Métricas en formato Prometheus — Fase P5, bloque 8B (punto 3).

Un endpoint `GET /metrics` en texto Prometheus. NO se levanta ningún
Prometheus ni Grafana: para un sistema de un host y un usuario, ejecutado
bajo demanda en la defensa, el stack de observabilidad sería
desproporcionado (misma línea que el rechazo a la sobre-ingeniería de P1
— ver docs/adr/0008 y "Escalado de seguridad" en el README_P5). El
endpoint queda por si alguien quiere apuntarle un Prometheus más adelante;
mientras tanto se lee con `curl` o a ojo.

Series expuestas (prefijo `tfm_`):
  - tfm_http_requests_total{metodo,endpoint,status}      contador
  - tfm_http_request_duration_seconds{metodo,endpoint}   histograma
  - tfm_cache_events_total{cache,resultado}              contador  (trafico|meteo x hit|miss)
  - tfm_isocronas_origen_total{origen}                   contador  (precomputado|vivo_cacheado|vivo_calculado)

`endpoint` es la REGLA de la ruta (`request.url_rule`), no el path crudo:
cardinalidad acotada aunque algún día haya parámetros en el path.
"""
from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

HTTP_REQUESTS = Counter(
    "tfm_http_requests_total",
    "Peticiones HTTP servidas, por método, endpoint (regla de ruta) y código.",
    ["metodo", "endpoint", "status"],
)

HTTP_DURACION = Histogram(
    "tfm_http_request_duration_seconds",
    "Latencia de las peticiones HTTP servidas, por método y endpoint.",
    ["metodo", "endpoint"],
)

CACHE_EVENTOS = Counter(
    "tfm_cache_events_total",
    "Aciertos y fallos de las cachés en memoria de la API.",
    ["cache", "resultado"],  # cache: trafico|meteo   resultado: hit|miss
)

ISOCRONAS_ORIGEN = Counter(
    "tfm_isocronas_origen_total",
    "De dónde salió cada respuesta de /isocronas.",
    ["origen"],  # precomputado | vivo_cacheado | vivo_calculado
)


def registrar_peticion(metodo: str, endpoint: str, status: int, duracion_s: float) -> None:
    HTTP_REQUESTS.labels(metodo=metodo, endpoint=endpoint, status=str(status)).inc()
    HTTP_DURACION.labels(metodo=metodo, endpoint=endpoint).observe(duracion_s)


def registrar_cache(cache: str, hit: bool) -> None:
    CACHE_EVENTOS.labels(cache=cache, resultado="hit" if hit else "miss").inc()


def registrar_isocronas_origen(origen: str) -> None:
    ISOCRONAS_ORIGEN.labels(origen=origen).inc()


def exposicion() -> tuple[bytes, str]:
    """(cuerpo, content-type) para servir en GET /metrics."""
    return generate_latest(), CONTENT_TYPE_LATEST
