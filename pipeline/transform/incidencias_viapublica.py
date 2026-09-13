"""
Transformación sobre incidencias en vía pública (ver
ingest/incidencias_viapublica.py::fetch_incidencias_actuales).

IMPORTANTE — igual que su ingest gemelo, este módulo es para que lo ejecute
el motor de rutas en el momento de calcular una ruta, no para el batch: no
se llama desde run_pipeline.py.
"""
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from pipeline.transform.common import CRS_PROJECTED, _requerir_crs_proyectado

logger = logging.getLogger(__name__)


def merge_incidencias(
    gdf_edges: gpd.GeoDataFrame, gdf_incidencias: gpd.GeoDataFrame, radio_m: float = 150.0,
) -> gpd.GeoDataFrame:
    """
    Asocia cada incidencia activa (obras, cortes, accidentes — ver
    fetch_incidencias_actuales()) a la arista más cercana dentro de
    `radio_m` (estimación razonada, no medida empíricamente), y marca esa
    arista como afectada.

    Añade a gdf_edges:
    - `tiene_incidencia`: bool, True si hay alguna incidencia cerca.
    - `es_obras_activa`: bool, True si alguna incidencia cercana es obra.
    - `n_incidencias_cercanas`: recuento de incidencias asociadas.
    """
    _requerir_crs_proyectado(gdf_edges)

    gdf_edges = gdf_edges.reset_index(drop=True)
    gdf_edges["tiene_incidencia"] = False
    gdf_edges["es_obras_activa"] = False
    gdf_edges["n_incidencias_cercanas"] = 0

    if gdf_incidencias.empty:
        return gdf_edges

    gdf_incidencias = gdf_incidencias.to_crs(epsg=CRS_PROJECTED)

    edge_coords = np.array([[g.x, g.y] for g in gdf_edges.geometry.centroid])
    tree = cKDTree(edge_coords)

    incid_coords = np.array([[g.x, g.y] for g in gdf_incidencias.geometry])
    dist, idx = tree.query(incid_coords, distance_upper_bound=radio_m)

    valido = np.isfinite(dist)
    gdf_incidencias = gdf_incidencias[valido].copy()
    gdf_incidencias["edge_idx"] = idx[valido]

    conteo = gdf_incidencias.groupby("edge_idx").size()
    if "es_obras" in gdf_incidencias.columns:
        obras = gdf_incidencias[gdf_incidencias["es_obras"]].groupby("edge_idx").size()
    else:
        obras = pd.Series(dtype=int)

    gdf_edges.loc[conteo.index.astype(int), "n_incidencias_cercanas"] = conteo.values
    gdf_edges.loc[conteo.index.astype(int), "tiene_incidencia"] = True
    gdf_edges.loc[obras.index.astype(int), "es_obras_activa"] = True

    logger.info(
        "merge_incidencias: %d/%d aristas con incidencia activa a <= %.0f m (%d con obras)",
        gdf_edges["tiene_incidencia"].sum(), len(gdf_edges), radio_m, gdf_edges["es_obras_activa"].sum(),
    )
    return gdf_edges
