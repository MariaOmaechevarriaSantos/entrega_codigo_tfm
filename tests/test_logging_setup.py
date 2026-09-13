"""
Tests de app/logging_setup.py — Fase P5, bloque 8B (logs estructurados).

Unidad pura: el formatter, el filtro de request_id, la idempotencia de
configure_logging y el context manager log_duration. La integración con
Flask (request_id que viaja de la cabecera a la traza, líneas
`operacion_cara` de /ruta y de la carga del grafo) está en
tests/test_server.py::TestLoggingEstructurado, que necesita el grafo
sintético.
"""
import json
import logging
import sys

import pytest

import app.logging_setup as ls


@pytest.fixture(autouse=True)
def _aislar_root_logger():
    """Cada test deja el root logger como lo encontró (nivel + handlers):
    configure_logging() toca estado global de logging."""
    root = logging.getLogger()
    nivel = root.level
    handlers = list(root.handlers)
    yield
    root.setLevel(nivel)
    root.handlers[:] = handlers
    ls.set_request_id(None)


def _record(msg="hola %s", args=("mundo",), level=logging.INFO, exc_info=None, **extra):
    rec = logging.LogRecord("test.logger", level, __file__, 10, msg, args, exc_info)
    for k, v in extra.items():
        setattr(rec, k, v)
    return rec


def _formatear(record) -> dict:
    return json.loads(ls.JsonFormatter().format(record))


# ─────────────────────────── JsonFormatter ───────────────────────────
class TestJsonFormatter:
    def test_emite_json_valido_con_los_campos_base(self):
        payload = _formatear(_record())
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test.logger"
        assert payload["message"] == "hola mundo"  # %-interpolado
        # timestamp ISO-8601 UTC con milisegundos y sufijo Z
        assert payload["timestamp"].endswith("Z")
        assert "T" in payload["timestamp"]

    def test_incluye_los_campos_pasados_por_extra(self):
        payload = _formatear(_record(operacion="calculo_ruta", duracion_ms=12.3, ok=True))
        assert payload["operacion"] == "calculo_ruta"
        assert payload["duracion_ms"] == 12.3
        assert payload["ok"] is True

    def test_no_vuelca_atributos_internos_del_logrecord(self):
        """Regresión: si el formatter iterara __dict__ sin filtrar, cada
        línea llevaría 'pathname', 'lineno', 'msecs', 'processName'..."""
        payload = _formatear(_record())
        for ruido in ("pathname", "lineno", "msecs", "processName", "args", "msg", "relativeCreated"):
            assert ruido not in payload

    def test_serializa_la_excepcion_si_hay_exc_info(self):
        try:
            raise ValueError("boom concreto")
        except ValueError:
            rec = _record(msg="fallo", args=(), level=logging.ERROR, exc_info=sys.exc_info())
        payload = _formatear(rec)
        assert "boom concreto" in payload["exception"]
        assert "ValueError" in payload["exception"]

    def test_campo_no_serializable_no_rompe_la_linea(self):
        payload = _formatear(_record(objeto=object()))
        assert isinstance(payload["objeto"], str)  # cae a str, no lanza


# ─────────────────────────── RequestIdFilter ─────────────────────────
class TestRequestIdFilter:
    def test_inyecta_el_request_id_del_contextvar_en_el_record(self):
        ls.set_request_id("abc123")
        rec = _record()
        assert ls.RequestIdFilter().filter(rec) is True
        assert rec.request_id == "abc123"
        assert _formatear(rec)["request_id"] == "abc123"

    def test_sin_request_id_la_clave_no_aparece(self):
        ls.set_request_id(None)
        rec = _record()
        ls.RequestIdFilter().filter(rec)
        assert "request_id" not in _formatear(rec)

    def test_no_pisa_un_request_id_ya_puesto_en_el_record(self):
        ls.set_request_id("del-contextvar")
        rec = _record(request_id="ya-venia")
        ls.RequestIdFilter().filter(rec)
        assert rec.request_id == "ya-venia"


# ────────────────────────── configure_logging ───────────────────────
class TestConfigureLogging:
    def test_instala_un_unico_handler_json_a_stdout(self):
        ls.configure_logging()
        ls.configure_logging()  # idempotente
        root = logging.getLogger()
        tagged = [h for h in root.handlers if getattr(h, ls._HANDLER_TAG, False)]
        assert len(tagged) == 1
        h = tagged[0]
        assert isinstance(h, logging.StreamHandler) and h.stream is sys.stdout
        assert isinstance(h.formatter, ls.JsonFormatter)

    def test_respeta_LOG_LEVEL_del_entorno(self, monkeypatch):
        monkeypatch.setenv("LOG_LEVEL", "warning")
        ls.configure_logging()
        assert logging.getLogger().level == logging.WARNING

    def test_sin_LOG_LEVEL_usa_INFO(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        ls.configure_logging()
        assert logging.getLogger().level == logging.INFO

    def test_una_segunda_llamada_no_duplica_ni_pierde_el_handler(self, monkeypatch):
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        ls.configure_logging()
        antes = list(logging.getLogger().handlers)
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        ls.configure_logging()  # solo debe reajustar el nivel
        assert list(logging.getLogger().handlers) == antes
        assert logging.getLogger().level == logging.ERROR


# ─────────────────────────── log_duration ───────────────────────────
class TestLogDuration:
    def test_emite_una_linea_operacion_cara_con_duracion_ms(self, caplog):
        logger = logging.getLogger("test.dur")
        with caplog.at_level(logging.INFO, logger="test.dur"):
            with ls.log_duration(logger, "prediccion_trafico", fecha="2025-06-15", hora=8):
                pass
        (rec,) = [r for r in caplog.records if getattr(r, "evento", None) == "operacion_cara"]
        assert rec.operacion == "prediccion_trafico"
        assert rec.ok is True
        assert isinstance(rec.duracion_ms, float)
        assert rec.fecha == "2025-06-15" and rec.hora == 8

    def test_marca_ok_false_y_re_lanza_si_el_bloque_revienta(self, caplog):
        logger = logging.getLogger("test.dur")
        with caplog.at_level(logging.INFO, logger="test.dur"):
            with pytest.raises(RuntimeError, match="fallo dentro"):
                with ls.log_duration(logger, "calculo_ruta"):
                    raise RuntimeError("fallo dentro")
        (rec,) = [r for r in caplog.records if getattr(r, "evento", None) == "operacion_cara"]
        assert rec.operacion == "calculo_ruta"
        assert rec.ok is False
