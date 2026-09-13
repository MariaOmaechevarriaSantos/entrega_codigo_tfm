"""
Transformaciones sobre la red viaria de OSM (ver ingest/osm_callejero.py):
filtrado de vías no navegables y asignación de distrito por arista.
"""
import logging

import geopandas as gpd
import numpy as np

from pipeline.transform.common import CRS_PROJECTED

logger = logging.getLogger(__name__)

# Tipos de vía excluidos (no navegables para camiones).
EXCLUDED_HIGHWAY_TYPES = {
    "footway", "pedestrian", "cycleway", "path", "steps",
    "track", "bridleway", "corridor", "proposed", "construction",
}


def filter_navigable_highways(gdf_edges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Descarta vías no aptas para vehículos pesados (peatonales, ciclistas, escaleras...)."""
    if "highway" not in gdf_edges.columns:
        return gdf_edges
    mask_excluida = gdf_edges["highway"].apply(
        lambda h: (h in EXCLUDED_HIGHWAY_TYPES) if isinstance(h, str)
        # OSM puede reportar varios tipos para una misma arista
        else any(t in EXCLUDED_HIGHWAY_TYPES for t in h) if isinstance(h, (list, np.ndarray))
        else False
    )
    resultado = gdf_edges[~mask_excluida].reset_index(drop=True)
    logger.info("filter_navigable_highways: %d/%d aristas conservadas", len(resultado), len(gdf_edges))
    return resultado


def add_district_zones(
    gdf_edges: gpd.GeoDataFrame,
    gdf_distritos: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Asigna a cada arista el distrito de Madrid al que pertenece su punto medio.
    gdf_distritos debe tener una columna 'nombre' con el nombre del distrito.
    """
    if gdf_edges.crs.to_epsg() != CRS_PROJECTED:
        gdf_edges = gdf_edges.to_crs(epsg=CRS_PROJECTED)
    if gdf_distritos.crs.to_epsg() != CRS_PROJECTED:
        gdf_distritos = gdf_distritos.to_crs(epsg=CRS_PROJECTED)

    # Calcular centroide de cada arista como punto de asignación
    gdf_centroids = gdf_edges.copy()
    gdf_centroids["geometry"] = gdf_edges.geometry.centroid

    joined = gpd.sjoin(
        gdf_centroids[["geometry"]],
        gdf_distritos[["geometry", "nombre"]],
        how="left",
        predicate="within",
    )
    gdf_edges = gdf_edges.copy()
    gdf_edges["zona"] = joined["nombre"].values
    gdf_edges["zona"] = gdf_edges["zona"].fillna("Desconocida")

    logger.info("Zonas asignadas. Distribución:\n%s", gdf_edges["zona"].value_counts().head(10))
    return gdf_edges
