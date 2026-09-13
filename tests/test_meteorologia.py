"""
Tests de la alerta meteorológica informativa — P5, bloque 6.

Cubre EXACTAMENTE lo que introduce este bloque:
  * `app/meteo_alerta.py::clasificar_alerta` — umbrales (AEMET Meteoalerta
    para Madrid), cortes inclusivos, "peor estación manda", magnitud sin
    dato != normal silencioso.
  * `GET /meteorologia` — esquema de respuesta, conversión de viento
    m/s -> km/h, caché con TTL de 15 min, snapshot en disco y camino de
    caché cuando el feed en vivo no responde, degradación de `?fuente=aemet`
    sin adaptador, filtro por `zona`, casos 400, y las invariantes
    "nunca 5xx", "no depende del grafo" y "la respuesta declara que es
    informativa".

La fuente externa (`fetch_meteorologia_actual`) está SIEMPRE mockeada:
ningún test toca la red ni `data/processed/`.
"""
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import app.meteo_alerta as meteo_alerta


# ─────────────────────────────────────────────────────────────────────────
# Fixtures y helpers
# ─────────────────────────────────────────────────────────────────────────
@pytest.fixture
def srv(monkeypatch, tmp_path):
    """(Re)importa app.server con el snapshot de meteorología redirigido a un
    tmp (nunca escribe en cache/ del repo) y la caché en memoria vacía."""
    if "app.server" in sys.modules:
        server = importlib.reload(sys.modules["app.server"])
    else:
        import app.server as server
    server.app.config["TESTING"] = False
    monkeypatch.setattr(server, "_METEO_SNAPSHOT_PATH", str(tmp_path / "meteo_snapshot.json"))
    server._meteo_cache.update(fetched_at=None, condiciones=None)
    return server


@pytest.fixture
def client(srv):
    return srv.app.test_client()


def _estacion(nombre, lon, lat, *, temp=20.0, hum=40.0, precip=0.0, viento_ms=2.0, hora=10):
    return {
        "estacion": nombre[:4], "nombre": nombre, "lon": lon, "lat": lat, "hora": hora,
        "temperatura": temp, "humedad_relativa": hum,
        "precipitacion": precip, "velocidad_viento": viento_ms,
    }


# Dos estaciones municipales reales, en extremos opuestos de la ciudad.
RETIRO = dict(nombre="Retiro", lon=-3.6824, lat=40.4143)
CASA_CAMPO = dict(nombre="Casa de Campo", lon=-3.7475, lat=40.4192)


def _feed(rows):
    """Sustituto de fetch_meteorologia_actual: función sin args -> DataFrame."""
    df = pd.DataFrame(rows)
    return lambda: df


def _feed_que_revienta(exc=RuntimeError("feed municipal 503")):
    def _f():
        raise exc
    return _f


def _patch_feed(monkeypatch, fn):
    monkeypatch.setattr("pipeline.ingest.meteorologia_madrid.fetch_meteorologia_actual", fn)


# ─────────────────────────────────────────────────────────────────────────
# app/meteo_alerta.py — clasificador de umbrales (sin Flask)
# ─────────────────────────────────────────────────────────────────────────
class TestClasificarAlerta:
    def test_sin_lecturas_es_normal_y_lo_dice_explicitamente(self):
        r = meteo_alerta.clasificar_alerta([])
        assert r["nivel"] == "normal"
        assert "sin datos" in r["criterio"].lower()

    def test_valores_suaves_son_normal(self):
        r = meteo_alerta.clasificar_alerta(
            [{"nombre": "X", "temperatura": 21.0, "precipitacion_mm": 0.0, "viento_kmh": 12.0}]
        )
        assert r["nivel"] == "normal"
        assert "sin condiciones adversas" in r["criterio"]

    def test_corte_de_lluvia_es_inclusivo(self):
        """Exactamente 15.0 mm/1h YA es precaución; 30.0, adversa (>=, no >)."""
        assert meteo_alerta.clasificar_alerta([{"nombre": "A", "precipitacion_mm": 14.9}])["nivel"] == "normal"
        assert meteo_alerta.clasificar_alerta([{"nombre": "A", "precipitacion_mm": 15.0}])["nivel"] == "precaucion"
        assert meteo_alerta.clasificar_alerta([{"nombre": "A", "precipitacion_mm": 30.0}])["nivel"] == "adversa"

    def test_temperatura_minima_bajo_cero_dispara_por_frio_no_por_calor(self):
        r = meteo_alerta.clasificar_alerta([{"nombre": "Barajas", "temperatura": -3.0}])
        assert r["nivel"] == "precaucion"
        assert "mínima" in r["criterio"]

    def test_peor_magnitud_manda_y_el_criterio_la_nombra(self):
        lecturas = [
            {"nombre": "calma", "temperatura": 20.0, "precipitacion_mm": 0.0, "viento_kmh": 5.0},
            {"nombre": "ventosa", "temperatura": 20.0, "precipitacion_mm": 0.0, "viento_kmh": 55.0},   # precaución
            {"nombre": "lluviosa", "temperatura": 20.0, "precipitacion_mm": 40.0, "viento_kmh": 0.0},  # adversa
        ]
        r = meteo_alerta.clasificar_alerta(lecturas)
        assert r["nivel"] == "adversa"
        assert "lluvia" in r["criterio"] and "lluviosa" in r["criterio"]
        assert "viento" not in r["criterio"]  # el viento solo llegaba a precaución

    def test_magnitud_sin_dato_no_se_cuenta_como_cero(self):
        """Ninguna estación mide lluvia -> no se asume 0; el viento (único con
        dato) manda."""
        r = meteo_alerta.clasificar_alerta([
            {"nombre": "A", "viento_kmh": 72.0, "temperatura": None, "precipitacion_mm": None},
        ])
        assert r["nivel"] == "adversa"
        assert "viento" in r["criterio"]

    def test_nota_informativa_dice_que_no_modifica_la_ruta(self):
        nota = meteo_alerta.clasificar_alerta([])["nota_informativa"].lower()
        assert "informativa" in nota and "ruta" in nota and "no modifica" in nota


# ─────────────────────────────────────────────────────────────────────────
# GET /meteorologia — esquema y alerta
# ─────────────────────────────────────────────────────────────────────────
class TestMeteorologiaEsquema:
    def test_esquema_exacto_y_alerta_normal(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed([
            _estacion(**RETIRO, temp=22.0, viento_ms=3.0),
            _estacion(**CASA_CAMPO, temp=21.0, viento_ms=4.0),
        ]))
        resp = client.get("/meteorologia")
        assert resp.status_code == 200
        b = resp.get_json()
        assert set(b) == {
            "informativo", "aviso", "disponible", "origen", "edad_min",
            "alerta", "parametros_efectivos", "meteorologia_por_zona",
        }
        assert b["disponible"] is True
        assert b["origen"] == "live"
        assert b["edad_min"] == 0
        assert set(b["alerta"]) == {"nivel", "criterio", "nota_informativa"}
        assert b["alerta"]["nivel"] == "normal"
        assert set(b["parametros_efectivos"]) == {"fuente", "zona", "timestamp_lectura"}
        assert b["parametros_efectivos"]["fuente"] == "municipal"
        assert len(b["meteorologia_por_zona"]) == 21
        for cond in b["meteorologia_por_zona"].values():
            assert set(cond) == {"temperatura", "humedad_relativa", "precipitacion_mm", "viento_kmh"}

    def test_viento_medio_en_ms_se_convierte_a_kmh(self, srv):
        """10 m/s -> 36.0 km/h. Sin la conversión, el umbral de viento (km/h)
        nunca se cruzaría."""
        cond = srv._df_a_condiciones(pd.DataFrame([_estacion(**RETIRO, viento_ms=10.0)]))
        assert cond[0]["viento_kmh"] == 36.0

    def test_viento_de_12ms_dispara_precaucion_no_adversa(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO, viento_ms=12.0)]))  # 43.2 km/h
        b = client.get("/meteorologia").get_json()
        assert b["alerta"]["nivel"] == "precaucion"
        assert "viento" in b["alerta"]["criterio"].lower()
        assert "43.2" in b["alerta"]["criterio"]
        assert "Retiro" in b["alerta"]["criterio"]

    def test_lluvia_de_31mm_dispara_adversa(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO, precip=31.0)]))
        b = client.get("/meteorologia").get_json()
        assert b["alerta"]["nivel"] == "adversa"
        assert "lluvia" in b["alerta"]["criterio"].lower()
        assert "31.0" in b["alerta"]["criterio"]

    def test_calor_de_40C_dispara_adversa(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO, temp=40.0)]))
        b = client.get("/meteorologia").get_json()
        assert b["alerta"]["nivel"] == "adversa"
        assert "máxima" in b["alerta"]["criterio"]

    def test_asignacion_por_cercania_a_la_estacion(self, client, monkeypatch):
        """Un distrito del oeste toma la estación del oeste; uno del este, la
        del este. Verifica que el 'más cercano' usa la distancia, no el
        orden de las filas."""
        _patch_feed(monkeypatch, _feed([
            _estacion(**RETIRO, temp=30.0),        # este
            _estacion(**CASA_CAMPO, temp=10.0),    # oeste
        ]))
        pz = client.get("/meteorologia").get_json()["meteorologia_por_zona"]
        assert pz["Moncloa-Aravaca"]["temperatura"] == 10.0   # oeste -> Casa de Campo
        assert pz["Salamanca"]["temperatura"] == 30.0         # este -> Retiro

    def test_alerta_declara_que_es_informativa_y_no_afecta_al_calculo(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO)]))
        b = client.get("/meteorologia").get_json()
        nota = b["alerta"]["nota_informativa"].lower()
        assert "informativ" in nota and "ruta" in nota and "no modifica" in nota
        assert b["aviso"]


# ─────────────────────────────────────────────────────────────────────────
# Caché (TTL 15 min) y snapshot en disco
# ─────────────────────────────────────────────────────────────────────────
class TestMeteorologiaCache:
    def test_ttl_evita_una_segunda_llamada_al_feed(self, client, monkeypatch):
        llamadas = {"n": 0}

        def _fake():
            llamadas["n"] += 1
            return pd.DataFrame([_estacion(**RETIRO)])

        _patch_feed(monkeypatch, _fake)
        r1 = client.get("/meteorologia")
        r2 = client.get("/meteorologia")
        assert llamadas["n"] == 1
        assert r1.get_json()["origen"] == r2.get_json()["origen"] == "live"

    def test_fetch_ok_persiste_el_snapshot_en_disco(self, client, srv, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO), _estacion(**CASA_CAMPO)]))
        client.get("/meteorologia")
        with open(srv._METEO_SNAPSHOT_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        assert set(snap) == {"fetched_at", "condiciones_por_estacion"}
        assert len(snap["condiciones_por_estacion"]) == 2
        assert snap["condiciones_por_estacion"][0]["nombre"] == "Retiro"

    def test_feed_caido_sin_snapshot_degrada_a_200(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed_que_revienta())
        resp = client.get("/meteorologia")
        assert resp.status_code == 200          # nunca 5xx
        b = resp.get_json()
        assert b["disponible"] is False
        assert b["origen"] == "cache"
        assert b["edad_min"] is None
        assert b["meteorologia_por_zona"] == {}
        assert b["alerta"]["nivel"] == "normal"
        assert "motivo" in b and "no hay snapshot" in b["motivo"]

    def test_feed_caido_con_snapshot_sirve_cache_con_antiguedad(self, client, srv, monkeypatch):
        # 1) primera petición OK -> escribe snapshot
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO, temp=27.0), _estacion(**CASA_CAMPO)]))
        assert client.get("/meteorologia").get_json()["origen"] == "live"

        # 2) envejece el snapshot 20 min y desactiva la caché en memoria (TTL 0)
        with open(srv._METEO_SNAPSHOT_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        hace_20 = datetime.now(timezone.utc) - timedelta(minutes=20)
        snap["fetched_at"] = hace_20.isoformat()
        with open(srv._METEO_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(snap, f)
        monkeypatch.setattr(srv, "_METEO_TTL_SECONDS", 0)
        srv._meteo_cache.update(fetched_at=None, condiciones=None)

        # 3) ahora el feed revienta -> se sirve el snapshot envejecido
        _patch_feed(monkeypatch, _feed_que_revienta())
        b = client.get("/meteorologia").get_json()
        assert b["origen"] == "cache"
        assert b["disponible"] is True
        assert b["edad_min"] == 20
        assert b["meteorologia_por_zona"]["Retiro"]["temperatura"] == 27.0
        assert "snapshot" in b["motivo"]

    def test_snapshot_corrupto_y_feed_caido_sigue_dando_200(self, client, srv, monkeypatch):
        with open(srv._METEO_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            f.write("{ esto no es json valido ")
        _patch_feed(monkeypatch, _feed_que_revienta())
        resp = client.get("/meteorologia")
        assert resp.status_code == 200
        b = resp.get_json()
        assert b["disponible"] is False
        assert b["origen"] == "cache"
        assert "motivo" in b


# ─────────────────────────────────────────────────────────────────────────
# fuente / zona / casos 400
# ─────────────────────────────────────────────────────────────────────────
class TestMeteorologiaParametros:
    def test_fuente_aemet_sin_key_sirve_municipal_sin_quejarse(self, client, monkeypatch):
        monkeypatch.delenv("AEMET_API_KEY", raising=False)
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO)]))
        resp = client.get("/meteorologia", query_string={"fuente": "aemet"})
        assert resp.status_code == 200
        b = resp.get_json()
        assert b["parametros_efectivos"]["fuente"] == "municipal"
        assert b["aemet_disponible"] is False
        assert "AEMET" in b["motivo"]
        assert len(b["meteorologia_por_zona"]) == 21   # el municipal sigue sirviendo

    def test_fuente_aemet_con_key_tampoco_usa_aemet(self, client, monkeypatch):
        monkeypatch.setenv("AEMET_API_KEY", "clave-de-prueba")
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO)]))
        b = client.get("/meteorologia", query_string={"fuente": "aemet"}).get_json()
        assert b["aemet_disponible"] is False
        assert "no está implementado" in b["motivo"]

    def test_fuente_no_reconocida_devuelve_400(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO)]))
        resp = client.get("/meteorologia", query_string={"fuente": "satelite"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"

    def test_zona_filtra_a_un_solo_distrito(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO), _estacion(**CASA_CAMPO)]))
        b = client.get("/meteorologia", query_string={"zona": "Centro"}).get_json()
        assert set(b["meteorologia_por_zona"]) == {"Centro"}
        assert b["parametros_efectivos"]["zona"] == "Centro"

    def test_zona_desconocida_devuelve_400(self, client, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO)]))
        resp = client.get("/meteorologia", query_string={"zona": "Chueca"})
        assert resp.status_code == 400
        assert resp.get_json()["code"] == "VALIDATION_ERROR"


# ─────────────────────────────────────────────────────────────────────────
# Invariantes del bloque
# ─────────────────────────────────────────────────────────────────────────
class TestMeteorologiaInvariantes:
    def test_no_depende_del_grafo(self, client, srv, monkeypatch):
        """El grafo puede no estar cargado y /meteorologia debe responder 200
        igualmente (no comparte el 503 de /ruta)."""
        def _boom():
            raise RuntimeError("no debería llamarse")
        monkeypatch.setattr(srv, "get_graph", _boom)
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO), _estacion(**CASA_CAMPO)]))
        resp = client.get("/meteorologia")
        assert resp.status_code == 200
        b = resp.get_json()
        # y además la ruta feliz se ejecutó de verdad, no la degradación del try/except
        assert b["disponible"] is True
        assert len(b["meteorologia_por_zona"]) == 21
        assert "motivo" not in b

    def test_nunca_5xx_ante_un_error_interno_inesperado(self, client, srv, monkeypatch):
        _patch_feed(monkeypatch, _feed([_estacion(**RETIRO)]))
        monkeypatch.setattr(srv, "_meteorologia_por_zona", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
        resp = client.get("/meteorologia")
        assert resp.status_code == 200
        b = resp.get_json()
        assert b["disponible"] is False
        assert "error interno" in b["motivo"]

    def test_centroides_cubren_exactamente_los_21_distritos_canonicos(self, srv):
        assert set(srv.DISTRITO_CENTROIDES) == set(srv.get_distritos())
