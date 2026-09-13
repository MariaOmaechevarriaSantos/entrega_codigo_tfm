"""
Capa de persistencia DuckDB para artefactos tabulares del pipeline.
La geometría (callejero, equipamientos) sigue viviendo en GeoJSON; DuckDB
solo persiste datos tabulares. El quality_report se exporta a CSV aparte.

Todas las tablas son append-only (ver `_crear_o_anadir`) y llevan un
run_id — cada ejecución añade filas, nunca borra las anteriores.
"""
import json
import logging
import os
from datetime import datetime, timezone

import duckdb
import geopandas as gpd
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_PROCESSED_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/processed"))
DUCKDB_PATH_DEFAULT = os.path.join(_PROCESSED_DIR, "tfm_madrid.duckdb")
QUALITY_REPORT_CSV_DEFAULT = os.path.join(_PROCESSED_DIR, "quality_report.csv")
CALLEJERO_PATH_DEFAULT = os.path.join(_PROCESSED_DIR, "madrid_callejero_filtered.geojson")


def get_connection(path: str | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """
    Abre (o crea) la conexión DuckDB. Ruta configurable via DUCKDB_PATH env var.
    Soporta tanto un fichero local (p.ej. data/processed/tfm_madrid.duckdb) como
    una base de datos MotherDuck compartida (DUCKDB_PATH=md:tfm_madrid), para que
    P3/P4/P5 puedan leer los mismos datos sin pasarse el fichero manualmente.
    Requiere MOTHERDUCK_TOKEN en el entorno (ver .env.example) cuando se usa md:.

    `read_only=True` abre la conexión sin permiso de escritura: DuckDB no toma el
    lock exclusivo del fichero ni crea WAL, así que le basta permiso de LECTURA
    sobre el `.duckdb`. Es lo que necesitan los consumidores de runtime que solo
    hacen SELECT (la API, vía `ml/predict_trafico_real._load_equip_pivot`) cuando
    la base la sirve un volumen o una imagen de solo lectura para el proceso
    (p. ej. el contenedor de la API como usuario no-root). Sobre un fichero que no
    existe, `read_only=True` hace que DuckDB falle en vez de crear una base vacía,
    coherente con la regla de P5 de no rellenar artefactos ausentes en silencio.
    """
    db_path = path or os.environ.get("DUCKDB_PATH", DUCKDB_PATH_DEFAULT)
    if not read_only and not db_path.startswith("md:"):
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    con = duckdb.connect(db_path, read_only=read_only)
    logger.info("Conectado a DuckDB: %s (read_only=%s)", db_path, read_only)
    return con


def _crear_o_anadir(con: duckdb.DuckDBPyConnection, tabla: str, select_sql: str, params: list | None = None) -> None:
    """Crea `tabla` si no existe y siempre añade las filas de `select_sql` (nunca reemplaza). Si `select_sql` usa parámetros (`?`), se ejecuta dos veces con los mismos `params`."""
    con.execute(f"CREATE TABLE IF NOT EXISTS {tabla} AS {select_sql} LIMIT 0", params or [])
    con.execute(f"INSERT INTO {tabla} {select_sql}", params or [])


def save_aforos(df: pd.DataFrame, con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """Añade el DataFrame de aforos (salida de clean_aforos/build_aforos_zonas) a la tabla aforos_historicos."""
    df = df.copy()
    df["run_id"] = run_id
    con.register("df_aforos_historicos", df)
    _crear_o_anadir(con, "aforos_historicos", "SELECT * FROM df_aforos_historicos")
    con.unregister("df_aforos_historicos")
    logger.info("aforos_historicos: %d filas añadidas (run_id=%s)", len(df), run_id)


def save_equipamientos_tabular(gdf: gpd.GeoDataFrame, con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """
    Añade una versión tabular de equipamientos (lon/lat en vez de geometry)
    a `equipamientos`. El catálogo apenas cambia entre ejecuciones, así que
    cada una lo reinserta casi entero — filtra por el run_id más reciente
    si solo quieres el estado actual.
    """
    df = pd.DataFrame(gdf.drop(columns="geometry"))
    df["lon"] = gdf.geometry.x
    df["lat"] = gdf.geometry.y
    df["run_id"] = run_id
    con.register("df_equipamientos", df)
    _crear_o_anadir(con, "equipamientos", "SELECT * FROM df_equipamientos")
    con.unregister("df_equipamientos")
    logger.info("equipamientos: %d filas añadidas (run_id=%s)", len(df), run_id)


def save_aforos_por_sensor(
    con: duckdb.DuckDBPyConnection, df_aforos: pd.DataFrame, df_puntos_medida: pd.DataFrame, run_id: str
) -> None:
    """
    Añade el detalle crudo de aforos por sensor/hora a `aforos_por_sensor`,
    uniendo coordenadas y, si `df_puntos_medida` las lleva (ver
    marcar_equipamientos_cercanos_en_puntos), las columnas `cerca_{tipo}`.
    Descarta las filas cuyo id de sensor no aparece en `df_puntos_medida`
    (sin coordenadas no sirven para nada aguas abajo) — ver
    `download_puntos_medida`, que ya fusiona varias fotos del catálogo para
    minimizar estos huérfanos.
    """
    id_col = "id" if "id" in df_puntos_medida.columns else "id_punto"
    lon_col = "longitud" if "longitud" in df_puntos_medida.columns else "lon"
    lat_col = "latitud" if "latitud" in df_puntos_medida.columns else "lat"
    cols_cerca = [c for c in df_puntos_medida.columns if c.startswith("cerca_")]
    puntos = df_puntos_medida[[id_col, lon_col, lat_col, *cols_cerca]].drop_duplicates(id_col)
    select_cerca = "".join(f"p.{c}, " for c in cols_cerca)

    n_input = len(df_aforos)
    con.register("df_aforos_tmp", df_aforos)
    con.register("df_puntos_medida_tmp", puntos)
    select_sql = f"""
        SELECT
            a.id AS id_punto, a.fecha, a.hora, a.intensidad, a.ocupacion,
            a.carga, a.ano, a.mes,
            p.{lon_col} AS lon, p.{lat_col} AS lat,
            {select_cerca}
            ? AS run_id
        FROM df_aforos_tmp a
        INNER JOIN df_puntos_medida_tmp p ON a.id = p.{id_col}
    """
    _crear_o_anadir(con, "aforos_por_sensor", select_sql, [run_id])
    con.unregister("df_aforos_tmp")
    con.unregister("df_puntos_medida_tmp")
    n = con.execute("SELECT COUNT(*) FROM aforos_por_sensor WHERE run_id = ?", [run_id]).fetchone()[0]
    logger.info(
        "aforos_por_sensor: %d filas añadidas (run_id=%s), %d descartadas por no tener coordenadas",
        n, run_id, n_input - n,
    )


def save_equipamientos_por_zona(df: pd.DataFrame, con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """Añade el conteo de equipamientos por (zona, tipo) — salida de open_data_madrid.build_equipamientos_por_zona."""
    df = df.copy()
    df["run_id"] = run_id
    con.register("df_equipamientos_por_zona", df)
    _crear_o_anadir(con, "equipamientos_por_zona", "SELECT * FROM df_equipamientos_por_zona")
    con.unregister("df_equipamientos_por_zona")
    logger.info("equipamientos_por_zona: %d filas añadidas (run_id=%s)", len(df), run_id)


def save_meteorologia(df: pd.DataFrame, con: duckdb.DuckDBPyConnection, run_id: str) -> None:
    """Añade el clima por zona (salida de transform/meteorologia_madrid.completar_meteorologia_zonas, sin NaN) a meteorologia_historica."""
    df = df.copy()
    df["run_id"] = run_id
    con.register("df_meteorologia_historica", df)
    _crear_o_anadir(con, "meteorologia_historica", "SELECT * FROM df_meteorologia_historica")
    con.unregister("df_meteorologia_historica")
    logger.info("meteorologia_historica: %d filas añadidas (run_id=%s)", len(df), run_id)


def save_pipeline_run(
    run_id: str, con: duckdb.DuckDBPyConnection, fallos: list[str] | None = None, config: dict | None = None,
) -> None:
    """
    Añade una fila a `pipeline_runs`: run_id, fecha/hora, qué falló (si algo
    falló) y `config` (serializado a JSON) con la configuración de esa
    ejecución — años descargados, si se saltó OSM, radios/umbrales usados...
    Sin esto, para saber con qué configuración se generó una fila de
    `aforos_historicos` habría que ir a mirar el código en ese commit.
    """
    df = pd.DataFrame([{
        "run_id": run_id,
        "fecha_ejecucion": datetime.now(timezone.utc).isoformat(),
        "fallos": ",".join(fallos) if fallos else "",
        "config": json.dumps(config or {}, default=str),
    }])
    con.register("df_pipeline_run", df)
    _crear_o_anadir(con, "pipeline_runs", "SELECT * FROM df_pipeline_run")
    con.unregister("df_pipeline_run")
    logger.info("pipeline_runs: registrada ejecución run_id=%s", run_id)


def save_callejero(gdf_edges: gpd.GeoDataFrame, output_path: str = CALLEJERO_PATH_DEFAULT) -> str:
    """
    Guarda el callejero final (reproyectado a EPSG:4326) en `output_path`,
    sobrescribiendo la ejecución anterior. `routing/graph_engine.py` lee
    siempre esta misma ruta.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    if os.path.islink(output_path) or os.path.exists(output_path):
        os.remove(output_path)
    gdf_edges.to_crs(epsg=4326).to_file(output_path, driver="GeoJSON")

    logger.info("callejero: guardado en %s", output_path)
    return output_path


def save_quality_report_csv(stats_rows: list[dict], output_path: str = QUALITY_REPORT_CSV_DEFAULT) -> None:
    """Exporta el quality report directo a CSV — material para la memoria del TFM, no vive en DuckDB."""
    df = pd.DataFrame(stats_rows)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    df.to_csv(output_path, index=False)
    logger.info("quality_report exportado a %s (%d filas)", output_path, len(df))


def save_equipamientos_geojson(gdf_merged: gpd.GeoDataFrame, output_dir: str = _PROCESSED_DIR) -> dict[str, str]:
    """
    Guarda un GeoJSON por tipo de equipamiento (una clave de
    pipeline.ingest.open_data_madrid.DATASETS por fichero) en `output_dir`,
    sobrescribiendo la ejecución anterior de cada uno.

    Bug de P1 encontrado en el bloque 4 de P5 (ver
    docs/p5/README_P5_API_Streamlit.md): esta función no existía, así que
    ningún GeoJSON de equipamientos llegaba nunca a `data/processed/` —
    `save_equipamientos_tabular` solo persiste una versión tabular (lon/lat)
    en DuckDB. `routing/graph_engine.py` (nodos especiales del grafo, P4) y
    `app/server.py::/equipamientos` (P5) necesitan la geometría en GeoJSON
    en disco, no en una tabla DuckDB.
    """
    from pipeline.ingest.open_data_madrid import DATASETS

    os.makedirs(output_dir, exist_ok=True)
    rutas = {}
    for tipo, cfg in DATASETS.items():
        gdf_tipo = gdf_merged[gdf_merged["tipo"] == tipo]
        if gdf_tipo.empty:
            continue
        output_path = os.path.join(output_dir, cfg["output"])
        if os.path.islink(output_path) or os.path.exists(output_path):
            os.remove(output_path)
        gdf_tipo.to_crs(epsg=4326).to_file(output_path, driver="GeoJSON")
        rutas[tipo] = output_path
        logger.info("equipamientos[%s]: guardado en %s (%d registros)", tipo, output_path, len(gdf_tipo))
    return rutas
