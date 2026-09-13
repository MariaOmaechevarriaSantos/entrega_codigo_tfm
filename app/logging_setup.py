"""
Logs estructurados en JSON — Fase P5, bloque 8B (operabilidad).

Una línea = un objeto JSON en stdout. Objetivo: que `docker compose logs
api` sea legible por máquina (jq, Loki, lo que sea) sin parsear texto libre,
y que cada línea de una misma petición lleve el mismo `request_id` para
poder seguir una traza de punta a punta.

Piezas:
  - `JsonFormatter`: serializa cada `LogRecord` a JSON. Incluye los campos
    `extra=` que se le pasen al logger (así una operación cara puede añadir
    `duracion_ms` sin que el formato lo sepa de antemano).
  - `RequestIdFilter`: inyecta el `request_id` del `contextvars` en CADA
    registro, también los que emiten `routing/` o `ml/` por debajo.
  - `configure_logging()`: instala UN handler a stdout con lo anterior.
    Idempotente (los tests reimportan `app.server` en bucle) y nivel por
    `LOG_LEVEL` (default INFO).
  - `log_duration(...)`: context manager que cronometra un bloque y emite
    una línea `evento=operacion_cara` con `operacion` y `duracion_ms`.

Sin dependencias nuevas: `json` + `logging` de la stdlib.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone

# request_id de la petición en curso. `None` fuera de una petición (arranque,
# carga del grafo en el máster de gunicorn, tests unitarios directos).
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "tfm_request_id", default=None
)

# Atributos que `logging` ya pone en todo LogRecord: lo que NO sea uno de
# estos y no empiece por "_" es un campo `extra=` del llamante y va al JSON.
_RESERVADOS = frozenset(
    logging.makeLogRecord({}).__dict__
) | {"message", "asctime", "taskName"}


def nuevo_request_id() -> str:
    return uuid.uuid4().hex


def set_request_id(rid: str | None) -> None:
    _request_id.set(rid)


def get_request_id() -> str | None:
    return _request_id.get()


class RequestIdFilter(logging.Filter):
    """Copia el request_id del contextvar al registro. Como filtro (no como
    parte del formatter) para que esté disponible aunque alguien cambie el
    handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        return True


class JsonFormatter(logging.Formatter):
    """LogRecord -> línea JSON. Claves fijas: timestamp, level, logger,
    message. Opcionales: request_id (si lo hay), exception (si exc_info), y
    cualquier campo pasado con `extra=`."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        payload: dict[str, object] = {
            "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        rid = getattr(record, "request_id", None)
        if rid is not None:
            payload["request_id"] = rid

        for clave, valor in record.__dict__.items():
            if clave in _RESERVADOS or clave.startswith("_") or clave == "request_id":
                continue
            payload[clave] = _json_safe(valor)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def _json_safe(v: object) -> object:
    """Deja pasar lo serializable directo; el resto a str (default=str del
    dump cubre el caso, esto solo evita sorpresas con contenedores)."""
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_safe(x) for k, x in v.items()}
    return str(v)


_HANDLER_TAG = "_tfm_json_handler"


def configure_logging() -> None:
    """Instala el handler JSON a stdout en el root logger. Idempotente:
    si ya está puesto, solo reajusta el nivel (los tests reimportan
    `app.server`, y `LOG_LEVEL` puede cambiar entre tests)."""
    nivel = os.environ.get("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.setLevel(nivel)

    for h in root.handlers:
        if getattr(h, _HANDLER_TAG, False):
            h.setLevel(nivel)
            return

    # Primera vez: fuera los handlers de texto que haya podido dejar
    # logging.basicConfig() (Flask/Werkzeug) para que stdout sea SOLO JSON.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestIdFilter())
    handler.setLevel(nivel)
    setattr(handler, _HANDLER_TAG, True)
    root.addHandler(handler)


@contextlib.contextmanager
def log_duration(logger: logging.Logger, operacion: str, **campos: object):
    """Cronometra el bloque y emite una línea estructurada al salir:
    `evento=operacion_cara`, `operacion=<nombre>`, `duracion_ms=<float>`,
    `ok=<bool>` y los `campos` extra que se pasen. Re-lanza la excepción
    si la hay (deja `ok=false` en la traza antes de propagar)."""
    t0 = time.perf_counter()
    ok = True
    try:
        yield
    except Exception:
        ok = False
        raise
    finally:
        dur_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "operacion cara: %s (%.1f ms)",
            operacion,
            dur_ms,
            extra={
                "evento": "operacion_cara",
                "operacion": operacion,
                "duracion_ms": dur_ms,
                "ok": ok,
                **campos,
            },
        )
