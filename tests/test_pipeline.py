"""Tests unitarios para el pipeline de datos (ingesta, limpieza, transformación, persistencia)."""
import json
import os
from unittest.mock import patch, Mock

import numpy as np
import pandas as pd
import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point

from pipeline.transform.common import detectar_huecos, merge_quality_stats, CRS_PROJECTED
from pipeline.transform.osm_callejero import filter_navigable_highways
from pipeline.transform.aforos_trafico import (
    clean_aforos, attach_punto_coords, merge_aforos, build_aforos_zonas, marcar_equipamientos_cercanos_en_puntos,
)
from pipeline.transform.open_data_madrid import (
    clean_equipamientos, merge_equipamientos, build_equipamientos_por_zona, marcar_equipamientos_cercanos,
)
from pipeline.transform.meteorologia_madrid import (
    clean_meteorologia, build_meteorologia_zonas, merge_meteorologia_actual, calcular_completitud_magnitudes,
    rellenar_huecos_cortos, rellenar_huecos_largos, completar_meteorologia_zonas,
)
from pipeline.transform.incidencias_viapublica import merge_incidencias
from pipeline.load.save_artifacts import (
    get_connection, save_aforos, save_aforos_por_sensor, save_equipamientos_tabular,
    save_meteorologia, save_quality_report_csv, save_pipeline_run, save_callejero,
    save_equipamientos_geojson,
)
from pipeline.ingest.open_data_madrid import download_equipamientos, DATASETS


# ======================================================================
# INGEST — descarga de datos crudos (mockeando requests.get, nunca red real)
# ======================================================================

class TestDownloadEquipamientos:
    def test_tipo_desconocido_lanza_error(self):
        with pytest.raises(ValueError):
            download_equipamientos("tipo_inexistente")

    @patch("pipeline.ingest.open_data_madrid.requests.get")
    def test_descarga_json_mockeada(self, mock_get):
        mock_resp = Mock()
        mock_resp.json.return_value = {
            "@graph": [{
                "title": "Parque de Bomberos 01",
                "@type": "ParquesBomberos",
                "address": {"street-address": "Calle Falsa 123"},
                "location": {"latitude": 40.42, "longitude": -3.70},
            }]
        }
        mock_resp.raise_for_status = Mock()
        mock_get.return_value = mock_resp

        gdf = download_equipamientos("bomberos")
        assert len(gdf) == 1
        assert gdf.iloc[0]["nombre"] == "Parque de Bomberos 01"
        mock_get.assert_called_once_with(DATASETS["bomberos"]["url"], timeout=30)

    @patch("pipeline.ingest.open_data_madrid.requests.get")
    def test_http_error_se_propaga(self, mock_get):
        import requests
        mock_resp = Mock()
        mock_resp.status_code = 404
        mock_resp.raise_for_status = Mock(side_effect=requests.exceptions.HTTPError("404"))
        mock_get.return_value = mock_resp

        with pytest.raises(requests.exceptions.HTTPError):
            download_equipamientos("bomberos")


class TestDownloadAforosTrafico:
    @patch("pipeline.ingest.aforos_trafico.requests.get")
    def test_download_puntos_medida_mockeado(self, mock_get):
        from pipeline.ingest.aforos_trafico import download_puntos_medida

        package_resp = Mock()
        package_resp.json.return_value = {
            "result": {"resources": [
                {"format": "CSV", "description": "Ubicación de los puntos de medida del tráfico. Diciembre 2024",
                 "created": "2024-12-01", "url": "http://fake/puntos.csv"},
            ]}
        }
        package_resp.raise_for_status = Mock()

        csv_bytes = "tipo_elem;distrito;id;longitud;latitud\r\nURB;1;5902;-3.70;40.42\r\n".encode("latin-1")
        csv_resp = Mock()
        csv_resp.content = csv_bytes
        csv_resp.raise_for_status = Mock()

        mock_get.side_effect = [package_resp, csv_resp]

        df = download_puntos_medida()
        assert len(df) == 1
        assert df.iloc[0]["id"] == 5902

    @patch("pipeline.ingest.aforos_trafico.requests.get")
    def test_monthly_resource_urls_parsea_descripciones(self, mock_get):
        from pipeline.ingest.aforos_trafico import _monthly_resource_urls

        package_resp = Mock()
        package_resp.json.return_value = {
            "result": {"resources": [
                {"format": "ZIP", "description": "Histórico de datos del tráfico. Diciembre 2023", "url": "http://fake/dic2023.zip"},
                {"format": "ZIP", "description": "Tráfico. Histórico de datos del tráfico desde 2013. 2021. junio", "url": "http://fake/jun2021.zip"},
                {"format": "PDF", "description": "Documentación", "url": "http://fake/doc.pdf"},
                {"format": "ZIP", "description": "Histórico de datos del tráfico. 2014 (año completo)", "url": "http://fake/2014.zip"},
            ]}
        }
        package_resp.raise_for_status = Mock()
        mock_get.return_value = package_resp

        urls = _monthly_resource_urls()
        assert urls[(2023, 12)] == "http://fake/dic2023.zip"
        assert urls[(2021, 6)] == "http://fake/jun2021.zip"
        assert (2014, None) not in urls
        assert len(urls) == 2


# ======================================================================
# TRANSFORM — limpieza, uniones espaciales, agregación por zona
# ======================================================================

def _df_aforos_crudo() -> pd.DataFrame:
    return pd.DataFrame({
        "id": [1, 1, 1, 2, None],
        "fecha": ["2024-01-01", "2024-01-01", "2024-01-01", "2024-01-01", "2024-01-01"],
        "hora": [0, 0, 1, 0, 0],
        "intensidad": [100, 100, 200, 300, 50],
    })


def _gdf_equipamientos(n_validos: int = 2) -> gpd.GeoDataFrame:
    """GeoDataFrame con un duplicado (mismo nombre normalizado y coordenadas),
    un punto fuera del límite de Madrid y uno con geometría nula."""
    geoms = [Point(-3.70, 40.42), Point(-3.70, 40.42), Point(10.0, 50.0), None]
    nombres = ["Parque A", " parque a ", "Fuera de Madrid", "Nulo"]
    return gpd.GeoDataFrame({"nombre": nombres[: n_validos + 2], "geometry": geoms[: n_validos + 2]}, crs="EPSG:4326")


class TestCleanAforos:
    def test_descarta_nulos(self):
        df_limpio, stats = clean_aforos(_df_aforos_crudo())
        assert stats["n_nulos_descartados"] == 1
        assert df_limpio["id_punto"].isna().sum() == 0

    def test_deduplica_por_id_fecha_hora(self):
        df_limpio, stats = clean_aforos(_df_aforos_crudo())
        assert stats["n_duplicados"] == 1
        assert len(df_limpio) == 3

    def test_renombra_id_a_id_punto(self):
        df_limpio, _ = clean_aforos(_df_aforos_crudo())
        assert "id_punto" in df_limpio.columns
        assert "id" not in df_limpio.columns

    def test_stats_son_serializables(self):
        _, stats = clean_aforos(_df_aforos_crudo())
        assert set(stats) == {
            "n_input", "n_output", "n_nulos_descartados",
            "n_fuera_limite_descartados", "n_duplicados",
        }
        assert all(isinstance(v, int) for v in stats.values())

    def test_no_descarta_intensidad_alta_legitima(self):
        """La detección estadística de outliers es análisis (P3), no limpieza de ingesta."""
        df = pd.DataFrame({
            "id_punto": [1, 1], "fecha": ["2024-01-01"] * 2, "hora": [0, 1],
            "intensidad": [50, 50_000],
        })
        df_limpio, stats = clean_aforos(df)
        assert stats["n_fuera_limite_descartados"] == 0
        assert len(df_limpio) == 2


class TestCleanEquipamientos:
    def test_descarta_geometria_nula(self):
        gdf_limpio, stats = clean_equipamientos(_gdf_equipamientos(), "bomberos")
        assert stats["n_nulos_descartados"] == 1

    def test_descarta_fuera_del_limite_real(self):
        gdf_limpio, stats = clean_equipamientos(_gdf_equipamientos(), "bomberos")
        assert stats["n_fuera_limite_descartados"] == 1
        assert "Fuera de Madrid" not in gdf_limpio["nombre"].values

    def test_descarta_municipio_vecino(self):
        """MADRID_BOUNDARY (polígono real) debe rechazar un municipio vecino
        que un bounding box rectangular sí dejaría pasar."""
        gdf = gpd.GeoDataFrame({
            "nombre": ["Pozuelo de Alarcón"], "geometry": [Point(-3.81, 40.43)],
        }, crs="EPSG:4326")
        gdf_limpio, stats = clean_equipamientos(gdf, "bomberos")
        assert stats["n_fuera_limite_descartados"] == 1
        assert len(gdf_limpio) == 0

    def test_normaliza_nombre_y_dedup(self):
        gdf_limpio, stats = clean_equipamientos(_gdf_equipamientos(), "bomberos")
        assert stats["n_duplicados"] == 1
        assert len(gdf_limpio) == 1
        assert gdf_limpio.iloc[0]["nombre"] == "PARQUE A"

    def test_asigna_columna_tipo(self):
        gdf_limpio, _ = clean_equipamientos(_gdf_equipamientos(), "hospitales")
        assert (gdf_limpio["tipo"] == "hospitales").all()


class TestMergeQualityStats:
    def test_agrega_por_fuente(self):
        s1 = {"n_input": 10, "n_output": 8, "n_nulos_descartados": 1, "n_fuera_limite_descartados": 1, "n_duplicados": 0}
        s2 = {"n_input": 5, "n_output": 5, "n_nulos_descartados": 0, "n_fuera_limite_descartados": 0, "n_duplicados": 0}
        merged = merge_quality_stats([s1, s2], "aforos")
        assert merged["fuente"] == "aforos"
        assert merged["n_input"] == 15
        assert merged["n_output"] == 13


def _punto_proyectado(lon: float, lat: float):
    return gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(epsg=CRS_PROJECTED).iloc[0]


class TestMergeAforos:
    def test_error_si_crs_no_proyectado(self):
        gdf_edges = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
        df_aforos = pd.DataFrame({"id_punto": [1], "longitud": [-3.7], "latitud": [40.4], "intensidad": [100]})
        with pytest.raises(ValueError):
            merge_aforos(gdf_edges, df_aforos)

    def test_asigna_por_cercania_y_nan_si_lejos(self):
        lon, lat = -3.70, 40.42
        p = _punto_proyectado(lon, lat)
        gdf_edges = gpd.GeoDataFrame({"geometry": [
            LineString([(p.x - 5, p.y), (p.x + 5, p.y)]),
            LineString([(p.x + 5000, p.y), (p.x + 5010, p.y)]),
        ]}, crs=f"EPSG:{CRS_PROJECTED}")
        df_aforos = pd.DataFrame({
            "id_punto": [1, 1, 2, 2],
            "longitud": [lon, lon, lon + 1.0, lon + 1.0],
            "latitud": [lat, lat, lat + 1.0, lat + 1.0],
            "intensidad": [100, 200, 50, 60],
        })
        result = merge_aforos(gdf_edges, df_aforos, radio_m=50)
        assert result.loc[0, "intensidad_media"] == pytest.approx(150.0)
        assert result.loc[0, "intensidad_mediana"] == pytest.approx(150.0)
        assert np.isnan(result.loc[1, "intensidad_media"])


class TestMergeIncidencias:
    def test_error_si_crs_no_proyectado(self):
        gdf_edges = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
        gdf_incidencias = gpd.GeoDataFrame({"geometry": [Point(-3.7, 40.4)]}, crs="EPSG:4326")
        with pytest.raises(ValueError):
            merge_incidencias(gdf_edges, gdf_incidencias)

    def test_marca_arista_cercana_y_deja_el_resto_sin_marcar(self):
        lon, lat = -3.70, 40.42
        p = _punto_proyectado(lon, lat)
        gdf_edges = gpd.GeoDataFrame({"geometry": [
            LineString([(p.x - 5, p.y), (p.x + 5, p.y)]),
            LineString([(p.x + 5000, p.y), (p.x + 5010, p.y)]),
        ]}, crs=f"EPSG:{CRS_PROJECTED}")
        gdf_incidencias = gpd.GeoDataFrame({
            "es_obras": [True], "geometry": [Point(lon, lat)],
        }, crs="EPSG:4326")

        result = merge_incidencias(gdf_edges, gdf_incidencias, radio_m=50)
        assert result.loc[0, "tiene_incidencia"] == True
        assert result.loc[0, "es_obras_activa"] == True
        assert result.loc[0, "n_incidencias_cercanas"] == 1
        assert result.loc[1, "tiene_incidencia"] == False

    def test_sin_incidencias_no_marca_nada(self):
        gdf_edges = gpd.GeoDataFrame(
            {"geometry": [LineString([(0, 0), (1, 1)])]}, crs=f"EPSG:{CRS_PROJECTED}",
        )
        gdf_incidencias = gpd.GeoDataFrame({"geometry": []}, crs="EPSG:4326")
        result = merge_incidencias(gdf_edges, gdf_incidencias)
        assert not result["tiene_incidencia"].any()


class TestBuildAforosZonas:
    def _gdf_distritos(self, lon, lat):
        from shapely.geometry import box
        return gpd.GeoDataFrame(
            {"nombre": ["Centro"], "geometry": [box(lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1)]},
            crs="EPSG:4326",
        )

    def test_agrega_por_zona_fecha_hora(self):
        lon, lat = -3.70, 40.42
        df_aforos = pd.DataFrame({
            "id_punto": [1, 1],
            "longitud": [lon, lon],
            "latitud": [lat, lat],
            "fecha": ["2024-01-01", "2024-01-01"],
            "hora": [0, 0],
            "intensidad": [100, 200],
        })
        resultado = build_aforos_zonas(df_aforos, self._gdf_distritos(lon, lat))
        assert len(resultado) == 1
        assert resultado.iloc[0]["zona"] == "Centro"
        assert resultado.iloc[0]["intensidad_media"] == pytest.approx(150.0)

    def test_punto_fuera_de_distrito_es_desconocida(self):
        lon, lat = -3.70, 40.42
        df_aforos = pd.DataFrame({
            "id_punto": [1], "longitud": [lon + 10], "latitud": [lat + 10],
            "fecha": ["2024-01-01"], "hora": [0], "intensidad": [100],
        })
        resultado = build_aforos_zonas(df_aforos, self._gdf_distritos(lon, lat))
        assert resultado.iloc[0]["zona"] == "Desconocida"

    def test_agrega_ocupacion_si_esta_presente(self):
        lon, lat = -3.70, 40.42
        df_aforos = pd.DataFrame({
            "id_punto": [1, 1], "longitud": [lon, lon], "latitud": [lat, lat],
            "fecha": ["2024-01-01"] * 2, "hora": [0, 0],
            "intensidad": [100, 200], "ocupacion": [2.0, 4.0], "vmed": [40.0, 60.0],
        })
        resultado = build_aforos_zonas(df_aforos, self._gdf_distritos(lon, lat))
        assert resultado.iloc[0]["ocupacion_media"] == pytest.approx(3.0)

    def test_no_agrega_vmed(self):
        """vmed solo lo miden los sensores M-30 (~6.5% del catálogo); el resto
        reporta 0.0 en vez de NaN, así que promediarlo por zona daría un dato
        engañoso — se decidió no exponer la columna en vez de rellenarla de NaN."""
        lon, lat = -3.70, 40.42
        df_aforos = pd.DataFrame({
            "id_punto": [1], "longitud": [lon], "latitud": [lat],
            "fecha": ["2024-01-01"], "hora": [0], "intensidad": [100], "vmed": [40.0],
        })
        resultado = build_aforos_zonas(df_aforos, self._gdf_distritos(lon, lat))
        assert "vmed_media" not in resultado.columns


class TestBuildEquipamientosPorZona:
    def _gdf_distritos(self, lon, lat):
        from shapely.geometry import box
        return gpd.GeoDataFrame(
            {"nombre": ["Centro"], "geometry": [box(lon - 0.1, lat - 0.1, lon + 0.1, lat + 0.1)]},
            crs="EPSG:4326",
        )

    def test_cuenta_por_zona_y_tipo(self):
        lon, lat = -3.70, 40.42
        gdf_equip = gpd.GeoDataFrame({
            "tipo": ["bomberos", "hospitales", "hospitales"],
            "geometry": [Point(lon, lat), Point(lon + 0.01, lat), Point(lon - 0.01, lat)],
        }, crs="EPSG:4326")
        resultado = build_equipamientos_por_zona(gdf_equip, self._gdf_distritos(lon, lat))
        resultado = resultado.set_index("tipo")["n_equipamientos"]
        assert resultado["bomberos"] == 1
        assert resultado["hospitales"] == 2

    def test_error_si_falta_crs(self):
        gdf_equip = gpd.GeoDataFrame({"tipo": ["bomberos"], "geometry": [Point(-3.7, 40.4)]})
        gdf_equip.crs = None
        with pytest.raises(ValueError):
            build_equipamientos_por_zona(gdf_equip, self._gdf_distritos(-3.7, 40.4))


class TestMarcarEquipamientosCercanos:
    def test_marca_arista_cercana_y_deja_lejana_sin_marcar(self):
        lon, lat = -3.70, 40.42
        p = _punto_proyectado(lon, lat)
        gdf_edges = gpd.GeoDataFrame({"geometry": [
            LineString([(p.x - 5, p.y), (p.x + 5, p.y)]),
            LineString([(p.x + 5000, p.y), (p.x + 5010, p.y)]),
        ]}, crs=f"EPSG:{CRS_PROJECTED}")
        gdf_equip = gpd.GeoDataFrame({
            "tipo": ["centros_educativos"], "geometry": [Point(lon, lat)],
        }, crs="EPSG:4326")

        resultado = marcar_equipamientos_cercanos(gdf_edges, gdf_equip, radio_m=50)
        assert resultado.loc[0, "cerca_centros_educativos"] == True
        assert resultado.loc[1, "cerca_centros_educativos"] == False

    def test_una_columna_por_tipo(self):
        lon, lat = -3.70, 40.42
        p = _punto_proyectado(lon, lat)
        gdf_edges = gpd.GeoDataFrame({"geometry": [
            LineString([(p.x - 5, p.y), (p.x + 5, p.y)]),
        ]}, crs=f"EPSG:{CRS_PROJECTED}")
        gdf_equip = gpd.GeoDataFrame({
            "tipo": ["centros_educativos", "bomberos"],
            "geometry": [Point(lon, lat), Point(lon + 1.0, lat + 1.0)],
        }, crs="EPSG:4326")

        resultado = marcar_equipamientos_cercanos(gdf_edges, gdf_equip, radio_m=50)
        assert resultado.loc[0, "cerca_centros_educativos"] == True
        assert resultado.loc[0, "cerca_bomberos"] == False

    def test_sin_equipamientos_no_falla(self):
        gdf_edges = gpd.GeoDataFrame(
            {"geometry": [LineString([(0, 0), (1, 1)])]}, crs=f"EPSG:{CRS_PROJECTED}",
        )
        gdf_equip = gpd.GeoDataFrame({"tipo": [], "geometry": []}, crs="EPSG:4326")
        resultado = marcar_equipamientos_cercanos(gdf_edges, gdf_equip)
        assert len(resultado) == 1
        assert "cerca_centros_educativos" not in resultado.columns

    def test_error_si_crs_no_proyectado(self):
        gdf_edges = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
        gdf_equip = gpd.GeoDataFrame({"tipo": ["bomberos"], "geometry": [Point(-3.7, 40.4)]}, crs="EPSG:4326")
        with pytest.raises(ValueError):
            marcar_equipamientos_cercanos(gdf_edges, gdf_equip)


def _fila_meteo_ancha(estacion=102, magnitud=83, ano=2024, mes=1, dia=1, valor=10.0, hora_invalida_desde=25):
    """Fila del CSV ancho de meteorología municipal (H01..H24/V01..V24).
    Horas >= hora_invalida_desde se marcan inválidas (V='N')."""
    fila = {
        "PROVINCIA": 28, "MUNICIPIO": 79, "ESTACION": estacion, "MAGNITUD": magnitud,
        "PUNTO_MUESTREO": f"2807{estacion}_{magnitud}_98", "ANO": ano, "MES": mes, "DIA": dia,
    }
    for h in range(1, 25):
        fila[f"H{h:02d}"] = valor
        fila[f"V{h:02d}"] = "V" if h < hora_invalida_desde else "N"
    return fila


class TestCleanMeteorologia:
    def test_reshape_y_pivot_magnitud(self):
        df_ancho = pd.DataFrame([
            _fila_meteo_ancha(estacion=102, magnitud=83, valor=10.0),   # temperatura
            _fila_meteo_ancha(estacion=102, magnitud=89, valor=0.0),    # precipitacion
        ])
        df_limpio, stats = clean_meteorologia(df_ancho)
        assert {"estacion", "fecha", "hora", "temperatura", "precipitacion"} <= set(df_limpio.columns)
        assert len(df_limpio) == 24
        assert df_limpio.iloc[0]["temperatura"] == pytest.approx(10.0)
        assert df_limpio.iloc[0]["precipitacion"] == pytest.approx(0.0)

    def test_descarta_horas_invalidas(self):
        df_ancho = pd.DataFrame([_fila_meteo_ancha(hora_invalida_desde=13)])
        df_limpio, stats = clean_meteorologia(df_ancho)
        assert len(df_limpio) == 12
        assert stats["n_nulos_descartados"] == 12

    def test_deduplica_filas_repetidas(self):
        fila = _fila_meteo_ancha()
        df_ancho = pd.DataFrame([fila, fila])
        df_limpio, stats = clean_meteorologia(df_ancho)
        assert stats["n_duplicados"] == 24
        assert len(df_limpio) == 24

    def test_ignora_magnitud_desconocida(self):
        df_ancho = pd.DataFrame([_fila_meteo_ancha(magnitud=999)])
        df_limpio, _ = clean_meteorologia(df_ancho)
        assert len(df_limpio) == 0

    def test_error_si_faltan_columnas_esperadas(self):
        with pytest.raises(ValueError):
            clean_meteorologia(pd.DataFrame({"ESTACION": [102]}))


class TestCalcularCompletitudMagnitudes:
    def test_calcula_pct_con_dato_por_magnitud(self):
        df_limpio = pd.DataFrame({
            "estacion": ["1", "2"], "fecha": ["2024-01-01"] * 2, "hora": [0, 0],
            "temperatura": [10.0, 12.0], "precipitacion": [0.0, None],
        })
        resultado = calcular_completitud_magnitudes(df_limpio).set_index("magnitud")
        assert resultado.loc["temperatura", "pct_con_dato"] == pytest.approx(100.0)
        assert resultado.loc["precipitacion", "pct_con_dato"] == pytest.approx(50.0)

    def test_ordena_de_peor_a_mejor(self):
        df_limpio = pd.DataFrame({
            "estacion": ["1"], "fecha": ["2024-01-01"], "hora": [0],
            "temperatura": [10.0], "precipitacion": [None],
        })
        resultado = calcular_completitud_magnitudes(df_limpio)
        assert resultado.iloc[0]["magnitud"] == "precipitacion"
        assert resultado.iloc[-1]["magnitud"] == "temperatura"


class TestDetectarHuecos:
    def test_cobertura_completa_es_100_pct(self):
        df = pd.DataFrame({"id_punto": [1] * 24, "fecha": ["2024-01-01"] * 24, "hora": list(range(24))})
        resultado = detectar_huecos(df, id_col="id_punto")
        assert resultado.iloc[0]["pct_cobertura"] == pytest.approx(100.0)

    def test_detecta_hueco_parcial(self):
        df = pd.DataFrame({"id_punto": [1, 1], "fecha": ["2024-01-01", "2024-01-02"], "hora": [0, 0]})
        resultado = detectar_huecos(df, id_col="id_punto")
        assert resultado.iloc[0]["n_esperado"] == 48
        assert resultado.iloc[0]["n_presente"] == 2
        assert resultado.iloc[0]["pct_cobertura"] == pytest.approx(2 / 48 * 100, abs=0.1)

    def test_compara_dos_ids_con_cobertura_distinta(self):
        df = pd.DataFrame({
            "id_punto": [1] * 24 + [2],
            "fecha": ["2024-01-01"] * 24 + ["2024-01-01"],
            "hora": list(range(24)) + [0],
        })
        resultado = detectar_huecos(df, id_col="id_punto").set_index("id_punto")
        assert resultado.loc[1, "pct_cobertura"] == pytest.approx(100.0)
        assert resultado.loc[2, "pct_cobertura"] == pytest.approx(1 / 24 * 100, abs=0.1)


class TestFilterNavigableHighways:
    def test_descarta_tipos_no_navegables(self):
        gdf = gpd.GeoDataFrame({
            "highway": ["residential", "footway", "primary", "cycleway"],
            "geometry": [LineString([(0, 0), (1, 1)])] * 4,
        }, crs="EPSG:4326")
        resultado = filter_navigable_highways(gdf)
        assert set(resultado["highway"]) == {"residential", "primary"}

    def test_lista_con_algun_tipo_excluido_se_descarta(self):
        gdf = gpd.GeoDataFrame({
            "highway": [["residential", "footway"]],
            "geometry": [LineString([(0, 0), (1, 1)])],
        }, crs="EPSG:4326")
        assert len(filter_navigable_highways(gdf)) == 0

    def test_lista_sin_tipos_excluidos_se_conserva(self):
        gdf = gpd.GeoDataFrame({
            "highway": [["residential", "primary"]],
            "geometry": [LineString([(0, 0), (1, 1)])],
        }, crs="EPSG:4326")
        assert len(filter_navigable_highways(gdf)) == 1

    def test_sin_columna_highway_no_falla(self):
        gdf = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
        assert len(filter_navigable_highways(gdf)) == 1

    def test_ndarray_con_algun_tipo_excluido_se_descarta(self):
        """geopandas/pyogrio deserializa listas de un GeoJSON como numpy.ndarray,
        no como list de Python — ver madrid_calles_raw.geojson real, donde el
        100% de las 61852 aristas llegan así, incluso con un único valor."""
        gdf = gpd.GeoDataFrame({
            "highway": [np.array(["residential", "footway"])],
            "geometry": [LineString([(0, 0), (1, 1)])],
        }, crs="EPSG:4326")
        assert len(filter_navigable_highways(gdf)) == 0


class TestBuildMeteorologiaZonas:
    def test_asigna_estacion_mas_cercana_por_distrito(self):
        from shapely.geometry import box
        gdf_distritos = gpd.GeoDataFrame({
            "nombre": ["Centro", "Norte"],
            "geometry": [box(-3.71, 40.41, -3.69, 40.43), box(-3.71, 40.51, -3.69, 40.53)],
        }, crs="EPSG:4326")
        df_estaciones = pd.DataFrame({"CÓDIGO_CORTO": [1, 2], "LONGITUD": [-3.70, -3.70], "LATITUD": [40.42, 40.52]})
        df_meteo = pd.DataFrame({
            "estacion": [1, 2], "fecha": ["2024-01-01", "2024-01-01"], "hora": [0, 0],
            "temperatura": [10.0, 20.0],
        })
        resultado = build_meteorologia_zonas(df_meteo, df_estaciones, gdf_distritos).set_index("zona")
        assert resultado.loc["Centro", "temperatura"] == pytest.approx(10.0)
        assert resultado.loc["Norte", "temperatura"] == pytest.approx(20.0)

    def test_error_si_falta_crs_distritos(self):
        gdf_distritos = gpd.GeoDataFrame({"nombre": ["Centro"], "geometry": [Point(-3.7, 40.4)]})
        gdf_distritos.crs = None
        with pytest.raises(ValueError):
            build_meteorologia_zonas(pd.DataFrame(), pd.DataFrame(), gdf_distritos)

    def test_no_incluye_magnitudes_sin_cobertura_suficiente(self):
        """precipitacion/velocidad_viento/direccion_viento/radiacion_solar/presion_barometrica
        dejan sin dato a 13-15 de los 21 distritos reales — se descartan, no se guardan a medias."""
        gdf_distritos = gpd.GeoDataFrame(
            {"nombre": ["Centro"], "geometry": [Point(-3.7, 40.4)]}, crs=f"EPSG:{CRS_PROJECTED}",
        )
        df_estaciones = pd.DataFrame({"CÓDIGO_CORTO": [1], "LONGITUD": [-3.70], "LATITUD": [40.42]})
        df_meteo = pd.DataFrame({
            "estacion": [1], "fecha": ["2024-01-01"], "hora": [0],
            "temperatura": [10.0], "precipitacion": [0.0], "presion_barometrica": [940.0],
        })
        resultado = build_meteorologia_zonas(df_meteo, df_estaciones, gdf_distritos)
        assert "temperatura" in resultado.columns
        assert "precipitacion" not in resultado.columns
        assert "presion_barometrica" not in resultado.columns


class TestRellenarHuecosCortos:
    def _df_zona(self, zona: str, valores: list) -> pd.DataFrame:
        n = len(valores)
        return pd.DataFrame({
            "zona": [zona] * n, "fecha": ["2024-01-01"] * n, "hora": list(range(n)), "temperatura": valores,
        })

    def test_rellena_hueco_corto_por_interpolacion_lineal(self):
        df = self._df_zona("Centro", [10.0, np.nan, np.nan, 13.0])
        resultado = rellenar_huecos_cortos(df, ["temperatura"], max_horas=3)
        assert resultado["temperatura"].tolist() == pytest.approx([10.0, 11.0, 12.0, 13.0])

    def test_no_rellena_hueco_mas_largo_que_max_horas(self):
        df = self._df_zona("Centro", [10.0, np.nan, np.nan, np.nan, np.nan, 15.0])
        resultado = rellenar_huecos_cortos(df, ["temperatura"], max_horas=3)
        assert resultado["temperatura"].iloc[1:5].isna().all()

    def test_no_extrapola_en_los_bordes(self):
        df = self._df_zona("Centro", [np.nan, 10.0, 11.0, np.nan])
        resultado = rellenar_huecos_cortos(df, ["temperatura"], max_horas=3)
        assert pd.isna(resultado["temperatura"].iloc[0])
        assert pd.isna(resultado["temperatura"].iloc[3])

    def test_interpola_zona_a_zona_sin_mezclar(self):
        df = pd.concat([
            self._df_zona("Centro", [10.0, np.nan, 12.0]),
            self._df_zona("Norte", [100.0, np.nan, 102.0]),
        ], ignore_index=True)
        resultado = rellenar_huecos_cortos(df, ["temperatura"], max_horas=3)
        centro = resultado[resultado["zona"] == "Centro"]["temperatura"]
        norte = resultado[resultado["zona"] == "Norte"]["temperatura"]
        assert centro.tolist() == pytest.approx([10.0, 11.0, 12.0])
        assert norte.tolist() == pytest.approx([100.0, 101.0, 102.0])


class TestRellenarHuecosLargos:
    def _gdf_distritos_en_linea(self, nombres_x: list) -> gpd.GeoDataFrame:
        return gpd.GeoDataFrame({
            "nombre": [n for n, _ in nombres_x],
            "geometry": [Point(x, 0) for _, x in nombres_x],
        }, crs=f"EPSG:{CRS_PROJECTED}")

    def test_rellena_con_la_zona_mas_cercana_en_el_mismo_instante(self):
        gdf_distritos = self._gdf_distritos_en_linea([("A", 0), ("B", 1), ("C", 2)])
        df = pd.DataFrame({
            "zona": ["A", "B", "C"], "fecha": ["2024-01-01"] * 3, "hora": [0, 0, 0],
            "temperatura": [np.nan, 20.0, 22.0],
        })
        resultado = rellenar_huecos_largos(df, gdf_distritos, ["temperatura"], k_zonas_cercanas=1)
        valor_a = resultado.loc[resultado["zona"] == "A", "temperatura"].item()
        assert valor_a == pytest.approx(20.0)  # B (dist 1) es más cercana a A que C (dist 2)

    def test_fallback_a_media_historica_si_las_vecinas_tambien_faltan(self):
        gdf_distritos = self._gdf_distritos_en_linea([("A", 0), ("B", 1)])
        df = pd.DataFrame({
            "zona": ["A", "B", "A", "B"],
            "fecha": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"],
            "hora": [0, 0, 0, 0],
            "temperatura": [10.0, np.nan, np.nan, np.nan],
        })
        resultado = rellenar_huecos_largos(df, gdf_distritos, ["temperatura"], k_zonas_cercanas=1)
        assert not resultado["temperatura"].isna().any()
        valor_a_dia2 = resultado.loc[
            (resultado["zona"] == "A") & (resultado["fecha"] == "2024-01-02"), "temperatura"
        ].item()
        assert valor_a_dia2 == pytest.approx(10.0)  # única medida real disponible para esa hora


class TestCompletarMeteorologiaZonas:
    def test_no_deja_ningun_nan(self):
        gdf_distritos = gpd.GeoDataFrame({
            "nombre": ["A", "B"], "geometry": [Point(0, 0), Point(1, 0)],
        }, crs=f"EPSG:{CRS_PROJECTED}")
        df = pd.DataFrame({
            "zona": ["A", "B", "A", "B"],
            "fecha": ["2024-01-01", "2024-01-01", "2024-01-02", "2024-01-02"],
            "hora": [0, 0, 0, 0],
            "temperatura": [10.0, 20.0, np.nan, 21.0],
        })
        resultado = completar_meteorologia_zonas(df, gdf_distritos, ["temperatura"])
        assert not resultado["temperatura"].isna().any()
        assert "temperatura_imputado" not in resultado.columns


class TestMergeMeteorologiaActual:
    def test_error_si_crs_no_proyectado(self):
        gdf_edges = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs="EPSG:4326")
        with pytest.raises(ValueError):
            merge_meteorologia_actual(gdf_edges, pd.DataFrame())

    def test_asigna_estacion_mas_cercana_a_cada_arista(self):
        p1 = _punto_proyectado(-3.70, 40.42)
        p2 = _punto_proyectado(-3.70, 40.52)
        gdf_edges = gpd.GeoDataFrame({"geometry": [
            LineString([(p1.x - 5, p1.y), (p1.x + 5, p1.y)]),
            LineString([(p2.x - 5, p2.y), (p2.x + 5, p2.y)]),
        ]}, crs=f"EPSG:{CRS_PROJECTED}")
        df_meteo_actual = pd.DataFrame({
            "estacion": ["1", "2"], "nombre": ["A", "B"], "lon": [-3.70, -3.70], "lat": [40.42, 40.52],
            "hora": [12, 12], "temperatura": [10.0, 20.0],
        })
        resultado = merge_meteorologia_actual(gdf_edges, df_meteo_actual, k_estaciones_cercanas=1)
        assert resultado.loc[0, "temperatura"] == pytest.approx(10.0)
        assert resultado.loc[1, "temperatura"] == pytest.approx(20.0)

    def test_promedia_las_k_estaciones_mas_cercanas_por_defecto(self):
        """Por defecto (k=3) se promedian varias estaciones cercanas, no solo la más
        próxima — más robusto ante la lectura anómala de un único sensor."""
        p_arista = _punto_proyectado(-3.70, 40.42)
        gdf_edges = gpd.GeoDataFrame({"geometry": [
            LineString([(p_arista.x - 5, p_arista.y), (p_arista.x + 5, p_arista.y)]),
        ]}, crs=f"EPSG:{CRS_PROJECTED}")
        df_meteo_actual = pd.DataFrame({
            "estacion": ["1", "2", "3"], "nombre": ["A", "B", "C"],
            "lon": [-3.70, -3.701, -3.699], "lat": [40.42, 40.42, 40.42],
            "hora": [12, 12, 12], "temperatura": [10.0, 20.0, 30.0],
        })
        resultado = merge_meteorologia_actual(gdf_edges, df_meteo_actual)
        assert resultado.loc[0, "temperatura"] == pytest.approx(20.0)  # media de las 3

    def test_sin_estaciones_activas_no_falla(self):
        gdf_edges = gpd.GeoDataFrame(
            {"geometry": [LineString([(0, 0), (1, 1)])]}, crs=f"EPSG:{CRS_PROJECTED}",
        )
        resultado = merge_meteorologia_actual(gdf_edges, pd.DataFrame())
        assert len(resultado) == 1
        assert "temperatura" not in resultado.columns

    def test_usa_la_mas_cercana_que_mide_esa_magnitud_aunque_no_sea_la_mas_cercana_global(self):
        """Confirmado en vivo: de 25 estaciones activas, humedad_relativa falta en el
        12% — si la más cercana en general no la mide, hay que buscar la siguiente."""
        p_arista = _punto_proyectado(-3.70, 40.42)
        gdf_edges = gpd.GeoDataFrame({"geometry": [
            LineString([(p_arista.x - 5, p_arista.y), (p_arista.x + 5, p_arista.y)]),
        ]}, crs=f"EPSG:{CRS_PROJECTED}")
        df_meteo_actual = pd.DataFrame({
            "estacion": ["1", "2"], "nombre": ["A", "B"], "lon": [-3.70, -3.65], "lat": [40.42, 40.42],
            "hora": [12, 12], "temperatura": [10.0, 12.0], "humedad_relativa": [np.nan, 60.0],
        })
        resultado = merge_meteorologia_actual(gdf_edges, df_meteo_actual, k_estaciones_cercanas=1)
        assert resultado.loc[0, "temperatura"] == pytest.approx(10.0)  # estación 1 (más cercana global)
        assert resultado.loc[0, "humedad_relativa"] == pytest.approx(60.0)  # estación 2 (única con dato)

    def test_omite_magnitud_sin_ninguna_estacion_activa(self):
        gdf_edges = gpd.GeoDataFrame(
            {"geometry": [LineString([(0, 0), (1, 1)])]}, crs=f"EPSG:{CRS_PROJECTED}",
        )
        df_meteo_actual = pd.DataFrame({
            "estacion": ["1"], "nombre": ["A"], "lon": [-3.70], "lat": [40.42],
            "hora": [12], "temperatura": [10.0], "humedad_relativa": [np.nan],
        })
        resultado = merge_meteorologia_actual(gdf_edges, df_meteo_actual)
        assert "temperatura" in resultado.columns
        assert "humedad_relativa" not in resultado.columns

    def test_omite_magnitudes_sin_cobertura_suficiente_aunque_tengan_dato(self):
        """El modelo (P3) solo se entrena con temperatura/humedad_relativa (ver
        MAGNITUDES_SIN_COBERTURA_SUFICIENTE) — las otras 5 se descartan siempre aquí,
        aunque una estación activa sí las reporte, porque no hay quien las consuma."""
        gdf_edges = gpd.GeoDataFrame(
            {"geometry": [LineString([(0, 0), (1, 1)])]}, crs=f"EPSG:{CRS_PROJECTED}",
        )
        df_meteo_actual = pd.DataFrame({
            "estacion": ["1"], "nombre": ["A"], "lon": [-3.70], "lat": [40.42], "hora": [12],
            "temperatura": [10.0], "precipitacion": [0.0], "velocidad_viento": [5.0],
        })
        resultado = merge_meteorologia_actual(gdf_edges, df_meteo_actual)
        assert "temperatura" in resultado.columns
        assert "precipitacion" not in resultado.columns
        assert "velocidad_viento" not in resultado.columns


class TestAttachPuntoCoords:
    def test_une_por_id(self):
        df_aforos = pd.DataFrame({"id": [1, 2], "fecha": ["2024-01-01"] * 2, "hora": [0, 1], "intensidad": [10, 20]})
        df_puntos = pd.DataFrame({
            "id": [1, 2, 3], "longitud": [-3.70, -3.69, -3.68], "latitud": [40.42, 40.41, 40.45],
        })
        result, n_descartadas = attach_punto_coords(df_aforos, df_puntos)
        assert len(result) == 2
        assert set(result.columns) >= {"id", "longitud", "latitud", "intensidad"}
        assert n_descartadas == 0

    def test_descarta_puntos_fuera_de_madrid(self):
        df_aforos = pd.DataFrame({"id": [1, 1, 2], "fecha": ["2024-01-01"] * 3, "hora": [0, 1, 0], "intensidad": [10, 15, 20]})
        df_puntos = pd.DataFrame({
            "id": [1, 2], "longitud": [-3.70, -3.81], "latitud": [40.42, 40.43],  # id 2 = Pozuelo
        })
        result, n_descartadas = attach_punto_coords(df_aforos, df_puntos)
        assert len(result) == 2
        assert (result["id"] == 1).all()
        assert n_descartadas == 1  # 1 fila de medidas del sensor 2 (fuera de Madrid)


class TestMarcarEquipamientosCercanosEnPuntos:
    def test_marca_punto_cercano_y_deja_lejano_sin_marcar(self):
        df_puntos = pd.DataFrame({
            "id": [1001, 1002], "longitud": [-3.70, -3.70], "latitud": [40.42, 41.42],
        })
        gdf_equip = gpd.GeoDataFrame({
            "tipo": ["centros_educativos"], "geometry": [Point(-3.70, 40.42)],
        }, crs="EPSG:4326")
        resultado = marcar_equipamientos_cercanos_en_puntos(df_puntos, gdf_equip).set_index("id")
        assert resultado.loc[1001, "cerca_centros_educativos"] == True
        assert resultado.loc[1002, "cerca_centros_educativos"] == False

    def test_preserva_columnas_originales_de_df_puntos_medida(self):
        df_puntos = pd.DataFrame({"id": [1001], "longitud": [-3.70], "latitud": [40.42]})
        gdf_equip = gpd.GeoDataFrame({"tipo": ["bomberos"], "geometry": [Point(-3.70, 40.42)]}, crs="EPSG:4326")
        resultado = marcar_equipamientos_cercanos_en_puntos(df_puntos, gdf_equip)
        assert set(resultado.columns) >= {"id", "longitud", "latitud", "cerca_bomberos"}


class TestMergeEquipamientos:
    def test_concatena_con_columna_tipo(self):
        gdf_a = gpd.GeoDataFrame({"nombre": ["A"], "geometry": [Point(-3.7, 40.4)]}, crs="EPSG:4326")
        gdf_b = gpd.GeoDataFrame({"nombre": ["B"], "geometry": [Point(-3.6, 40.3)]}, crs="EPSG:4326")
        result = merge_equipamientos({"bomberos": gdf_a, "hospitales": gdf_b})
        assert len(result) == 2
        assert set(result["tipo"]) == {"bomberos", "hospitales"}
        assert result.crs.to_epsg() == 4326

    def test_reproyecta_a_4326(self):
        p = _punto_proyectado(-3.7, 40.4)
        gdf_proj = gpd.GeoDataFrame({"nombre": ["A"], "geometry": [Point(p.x, p.y)]}, crs=f"EPSG:{CRS_PROJECTED}")
        result = merge_equipamientos({"bomberos": gdf_proj})
        assert result.crs.to_epsg() == 4326

    def test_error_si_diccionario_vacio(self):
        with pytest.raises(ValueError):
            merge_equipamientos({})


# ======================================================================
# LOAD — persistencia en DuckDB y ficheros de salida
# ======================================================================

class TestSaveArtifacts:
    def test_roundtrip_duckdb(self, tmp_path):
        db_path = str(tmp_path / "test.duckdb")
        con = get_connection(db_path)

        df_aforos = pd.DataFrame({"zona": ["Centro"], "fecha": ["2024-01-01"], "hora": [0], "intensidad_media": [123.0]})
        save_aforos(df_aforos, con, run_id="run-1")

        gdf_equip = gpd.GeoDataFrame({"nombre": ["Parque 1"], "tipo": ["bomberos"], "geometry": [Point(-3.7, 40.4)]}, crs="EPSG:4326")
        save_equipamientos_tabular(gdf_equip, con, run_id="run-1")

        tablas = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
        assert tablas == {"aforos_historicos", "equipamientos"}
        assert con.execute("SELECT COUNT(*) FROM aforos_historicos").fetchone()[0] == 1
        lon, lat, run_id = con.execute("SELECT lon, lat, run_id FROM equipamientos").fetchone()
        assert (lon, lat) == pytest.approx((-3.7, 40.4))
        assert run_id == "run-1"
        con.close()

        # Reabrir el fichero y confirmar persistencia real a disco
        con2 = get_connection(db_path)
        assert con2.execute("SELECT COUNT(*) FROM equipamientos").fetchone()[0] == 1
        con2.close()

    def test_get_connection_read_only_lee_pero_no_escribe(self, tmp_path):
        """`read_only=True` da una conexión de solo lectura: SELECT sí, DML/DDL no."""
        import duckdb

        db_path = str(tmp_path / "test.duckdb")
        con = get_connection(db_path)
        con.execute("CREATE TABLE t AS SELECT 1 AS x")
        con.close()

        ro = get_connection(db_path, read_only=True)
        try:
            assert ro.execute("SELECT x FROM t").fetchone()[0] == 1
            with pytest.raises(duckdb.Error):
                ro.execute("INSERT INTO t VALUES (2)")
        finally:
            ro.close()

    def test_get_connection_read_only_no_crea_la_base_si_falta(self, tmp_path):
        """Sobre un fichero inexistente, `read_only=True` falla en vez de crear una
        base vacía: no se rellenan artefactos ausentes en silencio (regla de P5)."""
        import duckdb

        falta = str(tmp_path / "no_existe.duckdb")
        with pytest.raises(duckdb.Error):
            get_connection(falta, read_only=True)
        assert not os.path.exists(falta)

    def test_ejecuciones_sucesivas_no_borran_las_anteriores(self, tmp_path):
        """Cada save_* debe AÑADIR, no reemplazar — el histórico de ejecuciones se
        conserva y se distingue por run_id, no por quedarse solo con la última."""
        con = get_connection(str(tmp_path / "test.duckdb"))
        df_run1 = pd.DataFrame({"zona": ["Centro"], "fecha": ["2024-01-01"], "hora": [0], "intensidad_media": [100.0]})
        df_run2 = pd.DataFrame({"zona": ["Retiro"], "fecha": ["2024-01-02"], "hora": [5], "intensidad_media": [200.0]})
        save_aforos(df_run1, con, run_id="run-1")
        save_aforos(df_run2, con, run_id="run-2")

        result = con.execute("SELECT zona, run_id FROM aforos_historicos ORDER BY run_id").fetchdf()
        con.close()
        assert len(result) == 2
        assert result["run_id"].tolist() == ["run-1", "run-2"]
        assert result["zona"].tolist() == ["Centro", "Retiro"]

    def test_save_aforos_por_sensor(self, tmp_path):
        df_aforos = pd.DataFrame({
            "id": [1001, 1001, 1002], "fecha": ["2025-01-01"] * 3, "hora": [0, 1, 0],
            "intensidad": [100, 200, 50], "ocupacion": [1.0, 2.0, 0.5], "carga": [0, 0, 0],
            "vmed": [50, 55, 40], "ano": [2025] * 3, "mes": [1] * 3,
        })
        df_puntos = pd.DataFrame({"id": [1001, 1002], "longitud": [-3.70, -3.68], "latitud": [40.42, 40.40]})

        con = get_connection(str(tmp_path / "test.duckdb"))
        save_aforos_por_sensor(con, df_aforos, df_puntos, run_id="run-1")

        result = con.execute("SELECT * FROM aforos_por_sensor ORDER BY id_punto, hora").fetchdf()
        con.close()
        assert len(result) == 3
        assert result.iloc[0]["lon"] == pytest.approx(-3.70)
        assert result.iloc[0]["lat"] == pytest.approx(40.42)
        assert (result["run_id"] == "run-1").all()

    def test_save_aforos_por_sensor_descarta_filas_sin_coordenadas(self, tmp_path):
        df_aforos = pd.DataFrame({
            "id": [1001, 1002], "fecha": ["2025-01-01"] * 2, "hora": [0, 0],
            "intensidad": [100, 50], "ocupacion": [1.0, 0.5], "carga": [0, 0],
            "vmed": [50, 40], "ano": [2025] * 2, "mes": [1] * 2,
        })
        df_puntos = pd.DataFrame({"id": [1001], "longitud": [-3.70], "latitud": [40.42]})

        con = get_connection(str(tmp_path / "test.duckdb"))
        save_aforos_por_sensor(con, df_aforos, df_puntos, run_id="run-1")

        result = con.execute("SELECT * FROM aforos_por_sensor").fetchdf()
        con.close()
        assert len(result) == 1
        assert result.iloc[0]["id_punto"] == 1001
        assert not result["lon"].isna().any()

    def test_save_aforos_por_sensor_incluye_columnas_cerca_si_estan_presentes(self, tmp_path):
        df_aforos = pd.DataFrame({
            "id": [1001, 1002], "fecha": ["2025-01-01"] * 2, "hora": [0, 0],
            "intensidad": [100, 50], "ocupacion": [1.0, 0.5], "carga": [0, 0],
            "vmed": [50, 40], "ano": [2025] * 2, "mes": [1] * 2,
        })
        df_puntos = pd.DataFrame({
            "id": [1001, 1002], "longitud": [-3.70, -3.68], "latitud": [40.42, 40.40],
            "cerca_centros_educativos": [True, False], "cerca_bomberos": [False, False],
        })

        con = get_connection(str(tmp_path / "test.duckdb"))
        save_aforos_por_sensor(con, df_aforos, df_puntos, run_id="run-1")

        result = con.execute("SELECT * FROM aforos_por_sensor ORDER BY id_punto").fetchdf()
        con.close()
        assert result.iloc[0]["cerca_centros_educativos"] == True
        assert result.iloc[1]["cerca_centros_educativos"] == False
        assert (result["cerca_bomberos"] == False).all()

    def test_save_meteorologia(self, tmp_path):
        df_meteo = pd.DataFrame({
            "zona": ["Centro"], "fecha": ["2025-01-01"], "hora": [0], "temperatura": [10.0],
        })
        con = get_connection(str(tmp_path / "test.duckdb"))
        save_meteorologia(df_meteo, con, run_id="run-1")

        result = con.execute("SELECT * FROM meteorologia_historica").fetchdf()
        con.close()
        assert len(result) == 1
        assert result.iloc[0]["temperatura"] == pytest.approx(10.0)
        assert result.iloc[0]["run_id"] == "run-1"

    def test_save_pipeline_run_es_append_only(self, tmp_path):
        con = get_connection(str(tmp_path / "test.duckdb"))
        save_pipeline_run("run-1", con, fallos=["aforos"])
        save_pipeline_run("run-2", con, fallos=None)

        df = con.execute("SELECT * FROM pipeline_runs ORDER BY run_id").fetchdf()
        con.close()
        assert len(df) == 2
        assert df.iloc[0]["run_id"] == "run-1"
        assert df.iloc[0]["fallos"] == "aforos"
        assert df.iloc[1]["fallos"] == ""

    def test_save_pipeline_run_guarda_config_como_json(self, tmp_path):
        con = get_connection(str(tmp_path / "test.duckdb"))
        config = {"skip_osm": True, "anos_aforos": [2024, 2025], "radio_aforos_m": 200.0}
        save_pipeline_run("run-1", con, config=config)

        row = con.execute("SELECT config FROM pipeline_runs WHERE run_id = 'run-1'").fetchone()
        con.close()
        assert json.loads(row[0]) == config

    def test_save_pipeline_run_sin_config_guarda_json_vacio(self, tmp_path):
        con = get_connection(str(tmp_path / "test.duckdb"))
        save_pipeline_run("run-1", con)
        row = con.execute("SELECT config FROM pipeline_runs WHERE run_id = 'run-1'").fetchone()
        con.close()
        assert json.loads(row[0]) == {}

    def test_save_callejero_sobrescribe_ejecucion_anterior(self, tmp_path):
        output_path = str(tmp_path / "callejero.geojson")
        gdf1 = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])]}, crs=f"EPSG:{CRS_PROJECTED}")
        gdf2 = gpd.GeoDataFrame({"geometry": [LineString([(0, 0), (1, 1)])] * 2}, crs=f"EPSG:{CRS_PROJECTED}")

        save_callejero(gdf1, output_path)
        save_callejero(gdf2, output_path)

        assert not os.path.islink(output_path)
        assert len(gpd.read_file(output_path)) == 2

    def test_save_equipamientos_geojson_un_fichero_por_tipo_presente(self, tmp_path):
        """Bug de P1 (bloque 4): antes de save_equipamientos_geojson, ningún
        GeoJSON de equipamientos llegaba a disco. Solo debe escribirse un
        fichero por tipo que tenga al menos una fila -- no uno vacío por cada
        clave de DATASETS que no venga en el gdf fusionado."""
        gdf = gpd.GeoDataFrame({
            "nombre": ["Parque 1", "Parque 2", "Hospital 1"],
            "tipo": ["bomberos", "bomberos", "hospitales"],
            "geometry": [Point(-3.70, 40.40), Point(-3.71, 40.41), Point(-3.68, 40.42)],
        }, crs="EPSG:4326")

        rutas = save_equipamientos_geojson(gdf, output_dir=str(tmp_path))

        assert set(rutas) == {"bomberos", "hospitales"}
        assert set(os.listdir(tmp_path)) == {"parques_bomberos.geojson", "hospitales.geojson"}
        gdf_bomberos = gpd.read_file(rutas["bomberos"])
        assert len(gdf_bomberos) == 2
        assert set(gdf_bomberos["nombre"]) == {"Parque 1", "Parque 2"}
        assert len(gpd.read_file(rutas["hospitales"])) == 1

    def test_save_equipamientos_geojson_sobrescribe_ejecucion_anterior(self, tmp_path):
        gdf_run1 = gpd.GeoDataFrame({
            "nombre": ["Parque Viejo"], "tipo": ["bomberos"], "geometry": [Point(-3.70, 40.40)],
        }, crs="EPSG:4326")
        gdf_run2 = gpd.GeoDataFrame({
            "nombre": ["Parque Nuevo A", "Parque Nuevo B"], "tipo": ["bomberos", "bomberos"],
            "geometry": [Point(-3.70, 40.40), Point(-3.71, 40.41)],
        }, crs="EPSG:4326")

        save_equipamientos_geojson(gdf_run1, output_dir=str(tmp_path))
        rutas = save_equipamientos_geojson(gdf_run2, output_dir=str(tmp_path))

        gdf_leido = gpd.read_file(rutas["bomberos"])
        assert len(gdf_leido) == 2
        assert set(gdf_leido["nombre"]) == {"Parque Nuevo A", "Parque Nuevo B"}

    def test_save_quality_report_csv(self, tmp_path):
        output_path = str(tmp_path / "quality_report.csv")
        stats_rows = [{"fuente": "bomberos", "n_input": 13, "n_output": 13, "n_nulos_descartados": 0, "n_fuera_limite_descartados": 0, "n_duplicados": 0}]
        save_quality_report_csv(stats_rows, output_path)

        df = pd.read_csv(output_path)
        assert df.iloc[0]["fuente"] == "bomberos"
        assert df.iloc[0]["n_input"] == 13
