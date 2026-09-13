"""
Transformaciones sobre los equipamientos críticos de Open Data Madrid (ver
ingest/open_data_madrid.py): limpieza por tipo, fusión en un único
GeoDataFrame (en memoria) y conteo por zona para el modelo de tráfico.

`merge_equipamientos` no toca el grafo de rutas (routing/graph_engine.py)
— eso es responsabilidad de P2/P4. Su salida (`gdf_equip_merged`) sí se
persiste en dos formas distintas, cada una para un consumidor distinto:
tabular en DuckDB (load/save_artifacts.py::save_equipamientos_tabular, para
P3/analítica) y GeoJSON por tipo en disco
(load/save_artifacts.py::save_equipamientos_geojson, para que
routing/graph_engine.py y app/server.py::/equipamientos no dependan de abrir
DuckDB en cada arranque/request). Bug de P1 corregido en el bloque 4 de P5:
durante bastante tiempo solo existió la persistencia en DuckDB, así que el
GeoJSON nunca se generaba pese a hacer falta desde P2/P4 (ver
docs/p5/README_P5_API_Streamlit.md).
"""
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from pipeline.transform.common import CRS_PROJECTED, MADRID_BOUNDARY, _stats, _requerir_crs_proyectado

logger = logging.getLogger(__name__)


def clean_equipamientos(gdf: gpd.GeoDataFrame, tipo: str) -> tuple[gpd.GeoDataFrame, dict]:
    """
    Descarta geometrías nulas o fuera de MADRID_BOUNDARY (límite real del
    municipio), normaliza nombres y la columna `tipo` (etiqueta categórica
    corta, ej. "bomberos"), deduplica por (nombre, tipo).
    """
    n_input = len(gdf)
    gdf = gdf.copy()

    mask_nulos = gdf.geometry.isna() | gdf.geometry.is_empty
    n_nulos = int(mask_nulos.sum())
    gdf = gdf[~mask_nulos]

    dentro = gdf.geometry.within(MADRID_BOUNDARY)
    n_fuera_limite = int((~dentro).sum())
    gdf = gdf[dentro]

    if "nombre" in gdf.columns:
        gdf["nombre"] = gdf["nombre"].astype(str).str.strip().str.upper()
    gdf["tipo"] = tipo

    n_antes_dedup = len(gdf)
    dedup_cols = [c for c in ("nombre", "tipo") if c in gdf.columns]
    gdf = gdf.drop_duplicates(subset=dedup_cols)
    n_duplicados = n_antes_dedup - len(gdf)

    stats = _stats(n_input, len(gdf), n_nulos, n_fuera_limite, n_duplicados)
    logger.info("clean_equipamientos(%s): %s", tipo, stats)
    return gdf.reset_index(drop=True), stats


def merge_equipamientos(equipamientos: dict[str, gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """
    Concatena los GeoDataFrames de equipamientos (uno por tipo, ya limpios
    por clean_equipamientos) en un único GeoDataFrame con columna `tipo`,
    en EPSG:4326.
    """
    if not equipamientos:
        raise ValueError("No hay equipamientos que fusionar.")

    gdfs = []
    for tipo, gdf in equipamientos.items():
        gdf = gdf.copy()
        if gdf.crs is None:
            raise ValueError(f"Equipamiento '{tipo}' no tiene CRS definido.")
        if gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        if "tipo" not in gdf.columns:
            gdf["tipo"] = tipo
        gdfs.append(gdf)

    gdf_merged = gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs="EPSG:4326")
    logger.info("Equipamientos fusionados: %d registros, tipos: %s", len(gdf_merged), list(equipamientos.keys()))
    return gdf_merged


def build_equipamientos_por_zona(
    gdf_equipamientos: gpd.GeoDataFrame, gdf_distritos: gpd.GeoDataFrame
) -> pd.DataFrame:
    """
    Asigna cada equipamiento a un distrito y cuenta cuántos hay de cada tipo
    por zona. Feature de contexto urbano para el modelo de tráfico (P3):
    zonas con más colegios/hospitales tienden a tener patrones de tráfico
    propios (picos de entrada/salida escolar, tráfico de emergencias...).
    """
    if gdf_distritos.crs is None:
        raise ValueError("gdf_distritos no tiene CRS definido.")
    if gdf_distritos.crs.to_epsg() != CRS_PROJECTED:
        gdf_distritos = gdf_distritos.to_crs(epsg=CRS_PROJECTED)
    if gdf_equipamientos.crs is None:
        raise ValueError("gdf_equipamientos no tiene CRS definido.")
    if gdf_equipamientos.crs.to_epsg() != CRS_PROJECTED:
        gdf_equipamientos = gdf_equipamientos.to_crs(epsg=CRS_PROJECTED)

    joined = gpd.sjoin(
        gdf_equipamientos[["tipo", "geometry"]],
        gdf_distritos[["geometry", "nombre"]],
        how="left",
        predicate="within",
    )
    joined["zona"] = joined["nombre"].fillna("Desconocida")

    resultado = joined.groupby(["zona", "tipo"]).size().reset_index(name="n_equipamientos")
    logger.info("build_equipamientos_por_zona: %d filas (zona, tipo)", len(resultado))
    return resultado


def marcar_equipamientos_cercanos(
    gdf_edges: gpd.GeoDataFrame, gdf_equipamientos: gpd.GeoDataFrame, radio_m: float = 150.0,
) -> gpd.GeoDataFrame:
    """
    Marca cada arista con un booleano `cerca_{tipo}` por tipo de
    equipamiento presente en gdf_equipamientos (bomberos, hospitales,
    centros_educativos, centros_mayores), indicando si hay alguno a
    <= radio_m. Feature de contexto muy local para un futuro modelo de
    congestión por calle (P3): el tráfico de entrada/salida de un colegio
    es un efecto de esa calle o la de al lado, no de todo el distrito — a
    diferencia de build_equipamientos_por_zona, pensado para un modelo a
    nivel de zona. Valor por defecto calibrado con datos reales — ver
    run_pipeline.py.
    """
    _requerir_crs_proyectado(gdf_edges)
    if gdf_equipamientos.crs is None:
        raise ValueError("gdf_equipamientos no tiene CRS definido.")

    gdf_edges = gdf_edges.reset_index(drop=True)
    if gdf_equipamientos.empty:
        return gdf_edges

    if gdf_equipamientos.crs.to_epsg() != CRS_PROJECTED:
        gdf_equipamientos = gdf_equipamientos.to_crs(epsg=CRS_PROJECTED)

    edge_coords = np.array([[g.x, g.y] for g in gdf_edges.geometry.centroid])
    for tipo in gdf_equipamientos["tipo"].unique():
        gdf_tipo = gdf_equipamientos[gdf_equipamientos["tipo"] == tipo]
        coords_tipo = np.array([[g.x, g.y] for g in gdf_tipo.geometry])
        tree = cKDTree(coords_tipo)
        dist, _ = tree.query(edge_coords)
        gdf_edges[f"cerca_{tipo}"] = dist <= radio_m

    logger.info(
        "marcar_equipamientos_cercanos: aristas marcadas para tipos %s (radio %.0fm)",
        list(gdf_equipamientos["tipo"].unique()), radio_m,
    )
    return gdf_edges
