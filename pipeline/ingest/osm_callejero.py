"""
Descarga la red viaria de Madrid desde OpenStreetMap via osmnx y guarda el
GeoJSON en data/raw/osm/, sin filtrar (ver transform/osm_callejero.py::filter_navigable_highways).
"""
import logging
import os

import geopandas as gpd

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "../../data/raw/osm")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "madrid_calles_raw.geojson")


def download_madrid_network() -> gpd.GeoDataFrame:
    """
    Descarga la red viaria de Madrid (conducible) via osmnx.
    Retorna un GeoDataFrame con las aristas, sin filtrar.
    Puede tardar varios minutos la primera vez.
    """
    try:
        import osmnx as ox
    except ImportError:
        raise ImportError("Instala osmnx: pip install osmnx")

    logger.info("Descargando red viaria de Madrid desde OSM (puede tardar ~5 min)...")
    graph = ox.graph_from_place(
        "Madrid, Community of Madrid, Spain",
        network_type="drive",
        retain_all=False,
    )
    _, gdf_edges = ox.graph_to_gdfs(graph)
    gdf_edges = gdf_edges.reset_index()

    logger.info("Red descargada: %d aristas", len(gdf_edges))
    return gdf_edges


def save_raw(gdf: gpd.GeoDataFrame) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    gdf.to_file(OUTPUT_FILE, driver="GeoJSON")
    logger.info("Red viaria cruda guardada en %s", OUTPUT_FILE)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gdf = download_madrid_network()
    save_raw(gdf)
