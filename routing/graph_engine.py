"""
Motor de carga y acceso al grafo viario de Madrid.
Singleton: el grafo se construye una sola vez, en la primera llamada a
`load_graph()`, y queda cacheado a nivel de módulo (`_graph`). Importar
este módulo no carga nada — quien sirve la API dispara la carga
explícitamente al arrancar el proceso (ver app/server.py).
"""
import logging
import os

import geopandas as gpd
import networkx as nx
from scipy.spatial import cKDTree
from shapely.geometry import Point

logger = logging.getLogger(__name__)

PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "../data/processed")
CALLEJERO_PATH = os.path.join(PROCESSED_DIR, "madrid_callejero_filtered.geojson")
PARQUES_BOMBEROS_PATH = os.path.join(PROCESSED_DIR, "parques_bomberos.geojson")
HOSPITALES_PATH = os.path.join(PROCESSED_DIR, "hospitales.geojson")
CRS_PROJECTED = 25830

# Estado singleton del módulo
_graph: nx.DiGraph | None = None
_kdtree: cKDTree | None = None
_node_items: list | None = None


def _cargar_equipamiento(path: str, tipo_nodo: str) -> gpd.GeoDataFrame | None:
    """Carga un GeoJSON de equipamientos (parques/hospitales) en CRS proyectado, o None si no existe."""
    if not os.path.exists(path):
        logger.warning(
            "Equipamiento '%s' no encontrado en %s. El grafo se sirve sin estos nodos especiales "
            "(depende de un entregable de P1 que puede llegar tarde).",
            tipo_nodo, path,
        )
        return None
    gdf_pts = gpd.read_file(path)
    if gdf_pts.crs is None or gdf_pts.crs.to_epsg() != CRS_PROJECTED:
        gdf_pts = gdf_pts.to_crs(epsg=CRS_PROJECTED)
    return gdf_pts


def load_graph(
    callejero_path: str = CALLEJERO_PATH,
    parques_path: str = PARQUES_BOMBEROS_PATH,
    hospitales_path: str = HOSPITALES_PATH,
) -> nx.DiGraph:
    """Carga el callejero desde GeoJSON y construye el grafo. Usa caché en módulo."""
    global _graph, _kdtree, _node_items

    if _graph is not None:
        return _graph

    if not os.path.exists(callejero_path):
        raise FileNotFoundError(
            f"Callejero no encontrado en {callejero_path}. "
            "Ejecuta: python pipeline/run_pipeline.py"
        )

    logger.info("Cargando callejero desde %s...", callejero_path)
    gdf = gpd.read_file(callejero_path)
    if gdf.crs is None or gdf.crs.to_epsg() != CRS_PROJECTED:
        gdf = gdf.to_crs(epsg=CRS_PROJECTED)

    from pipeline.transform.build_graph import build_graph, build_kdtree, add_special_nodes
    _graph = build_graph(gdf)
    # KDTree de nodos de calle únicamente — se construye antes de añadir los nodos
    # especiales y no se vuelve a tocar, así get_nearest_node(lat, lon) nunca puede
    # devolver un nodo especial (esos se buscan por id vía get_special_node, no por
    # proximidad geométrica).
    _kdtree, _node_items = build_kdtree(_graph)

    for tipo_nodo, path, prefijo in [
        ("bomberos", parques_path, "bomberos"),
        ("hospitales", hospitales_path, "hospitales"),
    ]:
        gdf_pts = _cargar_equipamiento(path, tipo_nodo)
        if gdf_pts is not None:
            add_special_nodes(_graph, gdf_pts, tipo_nodo, _kdtree, _node_items, prefijo)
            logger.info("%d nodos especiales de tipo '%s' conectados.", len(gdf_pts), tipo_nodo)

    logger.info("Grafo listo: %d nodos, %d aristas", _graph.number_of_nodes(), _graph.number_of_edges())
    return _graph


def get_graph() -> nx.DiGraph:
    if _graph is None:
        load_graph()
    return _graph


def get_nearest_node(lat: float, lon: float) -> int:
    """Convierte lat/lon WGS84 a nodo más cercano del grafo."""
    if _kdtree is None:
        load_graph()
    point_wgs84 = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(epsg=CRS_PROJECTED).iloc[0]
    _, idx = _kdtree.query([point_wgs84.x, point_wgs84.y])
    return _node_items[idx][0]


def get_special_node(tipo_nodo: str, nombre: str) -> str | None:
    """Devuelve el id del nodo especial (parque/hospital) con ese tipo y nombre, o None si no existe."""
    G = get_graph()
    for nid, d in G.nodes(data=True):
        if d.get("tipo_nodo") == tipo_nodo and d.get("nombre") == nombre:
            return nid
    return None
