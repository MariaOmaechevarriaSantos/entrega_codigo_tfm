"""
Orquestador del pipeline batch (Prefect).
Ejecuta ingesta + limpieza + transformación + guardado de artefactos.

Cada paso de ingesta es un @task independiente con reintentos; un fallo en
un dataset (404, timeout) se registra y se salta, no aborta el resto del
pipeline. Al final se reporta qué datasets fallaron, si los hubo. La
combinación de datos de varias fuentes (zonas, aforos, meteorología,
equipamientos) vive en transform/ (ver procesar_aforos_para_zonas,
completar_meteorologia_zonas...) — aquí solo se secuencian los pasos y se
decide qué hacer cuando uno falla.

Uso:
    python pipeline/run_pipeline.py --data-source raw
    python pipeline/run_pipeline.py --data-source drive
    python pipeline/run_pipeline.py --data-source raw --skip-osm
"""
import argparse
import inspect
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from pipeline.ingest.drive_data import bootstrap_drive_data, create_data_dirs

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv():
        return None
load_dotenv()

try:
    from prefect import flow, task
    from prefect.runtime import flow_run
except ImportError:
    class _FlowRunFallback:
        id = None

    flow_run = _FlowRunFallback()

    def task(*decorator_args, **decorator_kwargs):
        if decorator_args and callable(decorator_args[0]) and not decorator_kwargs:
            return decorator_args[0]

        def wrapper(func):
            return func

        return wrapper

    def flow(*decorator_args, **decorator_kwargs):
        if decorator_args and callable(decorator_args[0]) and not decorator_kwargs:
            return decorator_args[0]

        def wrapper(func):
            return func

        return wrapper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("pipeline")
# Alguna dependencia importada durante el flow (geopandas/osmnx/etc.) reconfigura
# el logging global y sube el nivel efectivo de "pipeline" a WARNING, silenciando
# los "=== PASO X ===". Fijar el nivel explícitamente en el logger (no solo en
# root vía basicConfig) hace que "pipeline" y todos sus hijos (pipeline.ingest.*,
# pipeline.transform.*, pipeline.load.*) mantengan INFO pase lo que pase aguas abajo.
logger.setLevel(logging.INFO)

RAW_OSM = os.path.join(os.path.dirname(__file__), "../data/raw/osm/madrid_calles_raw.geojson")
CRS_PROJECTED = 25830


def _sin_none(**kwargs) -> dict:
    """Filtra los kwargs en None — para pasar solo lo que el usuario haya
    sobreescrito explícitamente al llamar a run(), y dejar que la función
    de destino use su propio default en caso contrario (sin tener que
    duplicar aquí ese valor)."""
    return {k: v for k, v in kwargs.items() if v is not None}


def _valor_efectivo(valor, func, parametro: str):
    """Si `valor` es None, resuelve el default real de `parametro` en la
    firma de `func` por introspección — así `pipeline_runs.config` siempre
    registra el número con el que se ejecutó de verdad, sin tener que
    duplicarlo aquí a mano (y sin poder desincronizarse del código real)."""
    if valor is not None:
        return valor
    return inspect.signature(func).parameters[parametro].default


def _config_ejecucion(data_source: str, skip_osm: bool, **overrides) -> dict:
    """Config efectiva de esta ejecución (valores ya resueltos, ver `_valor_efectivo`), para reproducirla o compararla (ver save_pipeline_run)."""
    return {"data_source": data_source, "skip_osm": skip_osm, **overrides}


@task(retries=2, retry_delay_seconds=10)
def task_bootstrap_drive_data(force: bool = False):
    return bootstrap_drive_data(force=force)


@task
def task_create_data_dirs():
    create_data_dirs()


@task(retries=2, retry_delay_seconds=10)
def task_download_osm():
    from pipeline.ingest.osm_callejero import download_madrid_network, save_raw as save_raw_osm

    gdf = download_madrid_network()
    save_raw_osm(gdf)
    return gdf


@task
def task_load_osm_cache():
    import geopandas as gpd

    return gpd.read_file(RAW_OSM)


@task(retries=2, retry_delay_seconds=10)
def task_download_equipamientos() -> dict:
    from pipeline.ingest.open_data_madrid import download_all_equipamientos

    return download_all_equipamientos()


@task(retries=2, retry_delay_seconds=10)
def task_download_distritos():
    from pipeline.ingest.distritos_madrid import download_distritos, save_raw as save_raw_distritos

    gdf = download_distritos()
    save_raw_distritos(gdf)
    return gdf


@task(retries=1, retry_delay_seconds=30)
def task_download_aforos(anos: list | None = None):
    from pipeline.ingest.aforos_trafico import download_all_aforos, download_puntos_medida

    df_aforos = download_all_aforos(**_sin_none(anos=anos))
    df_puntos = download_puntos_medida(**_sin_none(anos=anos))
    return df_aforos, df_puntos


@task(retries=2, retry_delay_seconds=10)
def task_download_meteorologia(anos: list | None = None):
    from pipeline.ingest.meteorologia_madrid import download_estaciones, download_meteorologia

    df_estaciones = download_estaciones()
    df_meteo_ancho = download_meteorologia(**_sin_none(anos=anos))
    return df_estaciones, df_meteo_ancho


@task
def task_clean_equipamientos(equipamientos: dict) -> tuple[dict, list]:
    from pipeline.transform.open_data_madrid import clean_equipamientos

    limpios, stats_rows = {}, []
    for tipo, gdf in equipamientos.items():
        gdf_limpio, stats = clean_equipamientos(gdf, tipo)
        limpios[tipo] = gdf_limpio
        stats_rows.append({"fuente": f"equipamientos_{tipo}", **stats})
    return limpios, stats_rows


@task
def task_clean_aforos(df_aforos):
    from pipeline.transform.aforos_trafico import clean_aforos

    df_limpio, stats = clean_aforos(df_aforos)
    return df_limpio, {"fuente": "aforos_historicos", **stats}


@task
def task_clean_meteorologia(df_meteo_ancho):
    from pipeline.transform.meteorologia_madrid import clean_meteorologia

    df_limpio, stats = clean_meteorologia(df_meteo_ancho)
    return df_limpio, {"fuente": "meteorologia_municipal", **stats}


@flow(name="pipeline-tfm-madrid-bomberos")
def run(
    data_source: str = "raw",
    skip_osm: bool = False,
    force_osm_download: bool = False,
    force_drive_download: bool = False,
    anos_aforos: list | None = None,
    anos_meteorologia: list | None = None,
    radio_aforos_m: float | None = None,
    radio_equipamientos_cercanos_m: float | None = None,
    max_horas_interpolacion: int | None = None,
    k_zonas_cercanas_relleno: int | None = None,
) -> None:
    # Reutiliza el flow run id de Prefect como run_id — así las filas en DuckDB
    # se pueden cruzar directamente con el historial del dashboard de Prefect.
    run_id = flow_run.id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    logger.info("run_id: %s", run_id)
    fallos = []
    quality_rows = []

    if data_source not in {"raw", "drive"}:
        raise ValueError("data_source debe ser 'raw' o 'drive'.")

    # 0. Fuente de datos elegida por el usuario
    if data_source == "drive":
        logger.info("=== PASO 0: Descarga directa de artefactos desde Google Drive ===")
        task_bootstrap_drive_data(force=force_drive_download)
        logger.info("=== DATOS LISTOS DESDE DRIVE: no se ejecuta ETL raw ===")
        return

    logger.info("=== PASO 0: Preparar carpetas locales de datos ===")
    task_create_data_dirs()

    from pipeline.ingest.aforos_trafico import download_all_aforos
    from pipeline.ingest.meteorologia_madrid import download_meteorologia
    from pipeline.transform.osm_callejero import filter_navigable_highways, add_district_zones
    from pipeline.transform.aforos_trafico import (
        procesar_aforos_para_zonas, marcar_equipamientos_cercanos_en_puntos,
    )
    from pipeline.transform.open_data_madrid import merge_equipamientos, build_equipamientos_por_zona
    from pipeline.transform.meteorologia_madrid import (
        build_meteorologia_zonas, completar_meteorologia_zonas,
    )
    from pipeline.load.save_artifacts import (
        get_connection, save_aforos, save_aforos_por_sensor, save_equipamientos_tabular,
        save_equipamientos_por_zona, save_meteorologia, save_quality_report_csv, save_pipeline_run,
        save_callejero, save_equipamientos_geojson,
    )

    # 1. Ingesta red viaria OSM
    if force_osm_download or not skip_osm or not os.path.exists(RAW_OSM):
        logger.info("=== PASO 1: Ingesta red viaria OSM ===")
        try:
            gdf_edges = task_download_osm()
        except Exception as e:
            logger.error("Fallo ingesta OSM: %s", e)
            fallos.append("osm")
            if not os.path.exists(RAW_OSM):
                raise RuntimeError("Sin red viaria y sin caché disponible: no se puede continuar.") from e
            gdf_edges = task_load_osm_cache()
    else:
        logger.info("=== PASO 1: Cargando red viaria desde caché ===")
        gdf_edges = task_load_osm_cache()

    gdf_edges = filter_navigable_highways(gdf_edges)

    # 2. Ingesta equipamientos estáticos
    logger.info("=== PASO 2: Ingesta equipamientos Open Data Madrid ===")
    try:
        equipamientos_raw = task_download_equipamientos()
    except Exception as e:
        logger.error("Fallo ingesta equipamientos: %s", e)
        fallos.append("equipamientos")
        equipamientos_raw = {}

    # 3. Ingesta distritos (prerequisito de zonas/aforos)
    logger.info("=== PASO 3: Ingesta distritos de Madrid ===")
    try:
        gdf_distritos = task_download_distritos()
    except Exception as e:
        logger.error("Fallo ingesta distritos: %s", e)
        fallos.append("distritos")
        gdf_distritos = None

    # 4. Ingesta aforos históricos + puntos de medida
    logger.info("=== PASO 4: Ingesta aforos de tráfico ===")
    try:
        df_aforos_raw, df_puntos_medida = task_download_aforos(anos_aforos)
    except Exception as e:
        logger.error("Fallo ingesta aforos: %s", e)
        fallos.append("aforos")
        df_aforos_raw, df_puntos_medida = None, None

    # 5. Ingesta meteorología municipal (26 estaciones, sustituye a AEMET)
    logger.info("=== PASO 5: Ingesta meteorología municipal ===")
    try:
        df_estaciones, df_meteo_ancho = task_download_meteorologia(anos_meteorologia)
    except Exception as e:
        logger.warning("Fallo ingesta meteorología (no bloqueante): %s", e)
        fallos.append("meteorologia")
        df_estaciones, df_meteo_ancho = None, None

    # 6. Limpieza equipamientos
    equipamientos_limpios = {}
    if equipamientos_raw:
        logger.info("=== PASO 6: Limpieza equipamientos ===")
        equipamientos_limpios, stats_equip = task_clean_equipamientos(equipamientos_raw)
        quality_rows.extend(stats_equip)

    # 7. Limpieza aforos
    df_aforos_limpio, stats_aforos = None, None
    if df_aforos_raw is not None:
        logger.info("=== PASO 7: Limpieza aforos ===")
        df_aforos_limpio, stats_aforos = task_clean_aforos(df_aforos_raw)
        quality_rows.append(stats_aforos)

    # 8. Limpieza meteorología
    df_meteo_limpio = None
    if df_meteo_ancho is not None:
        logger.info("=== PASO 8: Limpieza meteorología ===")
        df_meteo_limpio, stats_meteo = task_clean_meteorologia(df_meteo_ancho)
        quality_rows.append(stats_meteo)

    # 9. Transformación — zonas + aforos + meteorología por zona
    logger.info("=== PASO 9: Transformación — zonas + aforos + meteorología ===")
    df_aforos_zonas = None
    df_meteo_zonas = None
    if gdf_distritos is None:
        logger.warning("Sin distritos: no se puede asignar zona a aristas, aforos ni meteorología.")
        if gdf_edges.crs is None or gdf_edges.crs.to_epsg() != CRS_PROJECTED:
            gdf_edges = gdf_edges.to_crs(epsg=CRS_PROJECTED)
    else:
        gdf_edges = add_district_zones(gdf_edges, gdf_distritos)

        if df_aforos_limpio is not None and df_puntos_medida is not None:
            try:
                gdf_edges, df_aforos_zonas = procesar_aforos_para_zonas(
                    gdf_edges, gdf_distritos, df_aforos_limpio, df_puntos_medida, stats_aforos,
                    **_sin_none(radio_m=radio_aforos_m),
                )
            except Exception as e:
                logger.error("Fallo en procesar_aforos_para_zonas: %s", e)
                fallos.append("merge_aforos")

        if df_meteo_limpio is not None and df_estaciones is not None:
            try:
                df_meteo_zonas = build_meteorologia_zonas(df_meteo_limpio, df_estaciones, gdf_distritos)
                df_meteo_zonas = completar_meteorologia_zonas(
                    df_meteo_zonas, gdf_distritos,
                    **_sin_none(max_horas=max_horas_interpolacion, k_zonas_cercanas=k_zonas_cercanas_relleno),
                )
            except Exception as e:
                logger.error("Fallo en build_meteorologia_zonas/completar_meteorologia_zonas: %s", e)
                fallos.append("meteorologia_zonas")

    # 10. Fusión equipamientos + conteo por zona (feature de contexto urbano para P3)
    gdf_equip_merged = None
    df_equip_por_zona = None
    if equipamientos_limpios:
        logger.info("=== PASO 10: Fusión equipamientos ===")
        gdf_equip_merged = merge_equipamientos(equipamientos_limpios)

        if gdf_distritos is not None:
            try:
                df_equip_por_zona = build_equipamientos_por_zona(gdf_equip_merged, gdf_distritos)
            except Exception as e:
                logger.error("Fallo en build_equipamientos_por_zona: %s", e)
                fallos.append("equipamientos_por_zona")

        if df_puntos_medida is not None:
            try:
                df_puntos_medida = marcar_equipamientos_cercanos_en_puntos(
                    df_puntos_medida, gdf_equip_merged, **_sin_none(radio_m=radio_equipamientos_cercanos_m),
                )
            except Exception as e:
                logger.error("Fallo en marcar_equipamientos_cercanos_en_puntos: %s", e)
                fallos.append("equipamientos_cercanos_puntos")

    # 10b. Guardar GeoJSON por tipo de equipamiento (P2/P4/P5 los leen de
    # disco, no de la tabla DuckDB — ver save_equipamientos_geojson).
    if gdf_equip_merged is not None:
        logger.info("=== PASO 10B: Guardar GeoJSON de equipamientos ===")
        try:
            save_equipamientos_geojson(gdf_equip_merged)
        except Exception as e:
            logger.error("Fallo guardando GeoJSON de equipamientos: %s", e)
            fallos.append("equipamientos_geojson")

    # 11. Guardar callejero procesado
    logger.info("=== PASO 11: Guardar callejero procesado ===")
    save_callejero(gdf_edges)

    # 12. Persistencia DuckDB + export quality report (CSV, fuera de DuckDB)
    logger.info("=== PASO 12: Persistencia DuckDB ===")
    config = _config_ejecucion(
        data_source,
        skip_osm,
        force_osm_download=force_osm_download,
        force_drive_download=force_drive_download,
        anos_aforos=_valor_efectivo(anos_aforos, download_all_aforos, "anos"),
        anos_meteorologia=_valor_efectivo(anos_meteorologia, download_meteorologia, "anos"),
        radio_aforos_m=_valor_efectivo(radio_aforos_m, procesar_aforos_para_zonas, "radio_m"),
        radio_equipamientos_cercanos_m=_valor_efectivo(
            radio_equipamientos_cercanos_m, marcar_equipamientos_cercanos_en_puntos, "radio_m",
        ),
        max_horas_interpolacion=_valor_efectivo(max_horas_interpolacion, completar_meteorologia_zonas, "max_horas"),
        k_zonas_cercanas_relleno=_valor_efectivo(
            k_zonas_cercanas_relleno, completar_meteorologia_zonas, "k_zonas_cercanas",
        ),
    )

    con = get_connection()
    try:
        if df_aforos_zonas is not None:
            save_aforos(df_aforos_zonas, con, run_id)
        if df_aforos_raw is not None and df_puntos_medida is not None:
            try:
                save_aforos_por_sensor(con, df_aforos_raw, df_puntos_medida, run_id)
            except Exception as e:
                logger.error("Fallo guardando aforos_por_sensor: %s", e)
                fallos.append("aforos_por_sensor")
        if gdf_equip_merged is not None:
            save_equipamientos_tabular(gdf_equip_merged, con, run_id)
        if df_equip_por_zona is not None:
            save_equipamientos_por_zona(df_equip_por_zona, con, run_id)
        if df_meteo_zonas is not None:
            save_meteorologia(df_meteo_zonas, con, run_id)
        save_pipeline_run(run_id, con, fallos, config)
    finally:
        con.close()
    if quality_rows:
        save_quality_report_csv(quality_rows)

    if fallos:
        logger.warning("=== PIPELINE COMPLETADO CON FALLOS EN: %s ===", fallos)
    else:
        logger.info("=== PIPELINE COMPLETADO ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pipeline datos TFM Madrid Bomberos")
    parser.add_argument(
        "--data-source",
        choices=["raw", "drive"],
        default="raw",
        help="raw = descargar/procesar fuentes oficiales; drive = descargar artefactos ya procesados de Google Drive",
    )
    parser.add_argument("--skip-osm", action="store_true", help="En modo raw, omitir descarga OSM si ya existe")
    parser.add_argument("--force-osm-download", action="store_true", help="En modo raw, forzar la descarga de OSM aunque exista la caché local")
    parser.add_argument("--force-drive-download", action="store_true", help="En modo drive, sobrescribir los artefactos locales descargados desde Drive")
    args = parser.parse_args()
    run(
        data_source=args.data_source,
        skip_osm=args.skip_osm,
        force_osm_download=args.force_osm_download,
        force_drive_download=args.force_drive_download,
    )
