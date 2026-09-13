"""
Construye el grafo dirigido de la red viaria de Madrid.
"""
import logging
import re

import geopandas as gpd
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree
from shapely.geometry import LineString, Point

logger = logging.getLogger(__name__)

CRS_PROJECTED = 25830   # ETRS89 / UTM zone 30N (metros)

# Anchuras estimadas por tipo de vía (metros)
DEFAULT_WIDTH_BY_HIGHWAY = {
    "motorway": 7.2, "trunk": 7.0, "primary": 6.0, "secondary": 5.5,
    "tertiary": 5.0, "unclassified": 4.0, "residential": 3.5,
    "service": 3.0, "living_street": 3.0, "track": 2.5,
    "pedestrian": 1.4, "footway": 1.4, "cycleway": 2.0, "path": 1.0,
}

DEFAULT_SPEED_BY_HIGHWAY = {
    "motorway": 100.0, "trunk": 80.0, "primary": 50.0, "secondary": 50.0,
    "tertiary": 40.0, "unclassified": 30.0, "residential": 30.0,
    "service": 20.0, "living_street": 15.0,
}

# Alturas para gálibo (metros)
ALTURA_LIBRE_SIN_RESTRICCION = 99.0
ALTURA_TUNEL_CONSERVADORA = 3.5
ALTURA_PASO_EDIFICIO = 3.0

# Acceso a un equipamiento (arista corta que cuelga el nodo especial de la
# calle más cercana, ver add_special_nodes): ni la anchura ni el gálibo deben
# bloquear la salida del parque, y se asume paso a 20 km/h.
ANCHO_ACCESO_EQUIPAMIENTO_M = 6.0
VELOCIDAD_ACCESO_EQUIPAMIENTO_KPH = 20.0


def _parse_numeric(val) -> float | None:
    if val is None:
        return None
    clean = re.sub(r"[^\d.]", "", str(val).lower().replace("km/h", "").replace("m", ""))
    try:
        return float(clean) if clean else None
    except ValueError:
        return None


def _normalize_highway(row) -> str:
    hw = row.get("highway", "unclassified")
    if isinstance(hw, (list, np.ndarray)):
        hw = hw[0] if len(hw) > 0 else "unclassified"
    hw = str(hw)
    if hw.strip().startswith("["):
        m = re.findall(r"[A-Za-z_]+", hw)
        hw = m[0] if m else "unclassified"
    return hw


def _get_width(row) -> float:
    w = _parse_numeric(row.get("width") or row.get("maxwidth"))
    if w:
        return w
    hw = _normalize_highway(row)
    return DEFAULT_WIDTH_BY_HIGHWAY.get(hw, 3.5)


def _get_speed(row) -> float:
    ms = _parse_numeric(row.get("maxspeed"))
    if ms:
        return ms
    hw = _normalize_highway(row)
    return DEFAULT_SPEED_BY_HIGHWAY.get(hw, 30.0)


def _get_height(row) -> float:
    h = _parse_numeric(row.get("maxheight"))
    if h:
        return h
    tunnel = row.get("tunnel")
    if isinstance(tunnel, (list, np.ndarray)):
        tunnel = tunnel[0] if len(tunnel) > 0 else None
    if tunnel is not None:
        if str(tunnel) == "building_passage":
            return ALTURA_PASO_EDIFICIO
        return ALTURA_TUNEL_CONSERVADORA
    return ALTURA_LIBRE_SIN_RESTRICCION


def _interpret_oneway(val, junction=None) -> str:
    # "circular" es el valor de junction habitual en España para glorietas de
    # sentido único; igual que "roundabout", implica oneway aunque el
    # mapeador no lo haya etiquetado explícitamente.
    if str(junction).lower() in ("roundabout", "circular"):
        return "yes"
    if val is None:
        return "no"
    v = str(val).strip().lower()
    if v in ("yes", "true", "1", "y", "only"):
        return "yes"
    if v == "-1":
        return "-1"
    return "no"


def build_graph(gdf_edges: gpd.GeoDataFrame) -> nx.DiGraph:
    """
    Construye nx.DiGraph desde un GeoDataFrame de aristas (EPSG proyectado).
    Cada arista incluye: length_m, travel_time_s, width_m, zona, highway.
    """
    if gdf_edges.crs is None or gdf_edges.crs.to_epsg() != CRS_PROJECTED:
        raise ValueError(
            f"GeoDataFrame debe estar en EPSG:{CRS_PROJECTED}. "
            f"CRS actual: {gdf_edges.crs}"
        )

    G = nx.DiGraph()
    node_id_map: dict[tuple, int] = {}

    def get_node_id(coord):
        key = (round(coord[0], 3), round(coord[1], 3))
        if key not in node_id_map:
            nid = len(node_id_map)
            node_id_map[key] = nid
            G.add_node(nid, x=coord[0], y=coord[1])
        return node_id_map[key]

    for _, row in gdf_edges.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        speed_kph = _get_speed(row)
        width_m = _get_width(row)
        height_m = _get_height(row)
        zona = str(row.get("zona", "Desconocida"))
        highway = _normalize_highway(row)
        oneway = _interpret_oneway(row.get("oneway"), row.get("junction"))

        lines = [geom] if geom.geom_type == "LineString" else list(geom.geoms)
        for ls in lines:
            coords = list(ls.coords)
            for i in range(len(coords) - 1):
                a, b = coords[i], coords[i + 1]
                u, v = get_node_id(a), get_node_id(b)
                seg_len = LineString([a, b]).length
                seg_time = seg_len / (speed_kph / 3.6)

                attr = {
                    "length_m": seg_len,
                    "travel_time_s": seg_time,
                    "width_m": width_m,
                    "height_m": height_m,
                    "zona": zona,
                    "highway": highway,
                }
                if oneway == "yes":
                    G.add_edge(u, v, **attr)
                elif oneway == "-1":
                    G.add_edge(v, u, **attr)
                else:
                    G.add_edge(u, v, **attr)
                    G.add_edge(v, u, **attr)

    logger.info("Grafo construido: %d nodos, %d aristas", G.number_of_nodes(), G.number_of_edges())
    return G


def build_kdtree(G: nx.DiGraph):
    """Retorna (kdtree, node_items) para búsqueda de nodo más cercano."""
    node_items = list(G.nodes(data=True))
    coords = np.array([[d["x"], d["y"]] for _, d in node_items])
    return cKDTree(coords), node_items


def nearest_node(kdtree, node_items: list, point: Point) -> int:
    """Retorna el node_id del nodo más cercano a un shapely Point (en CRS proyectado)."""
    _, idx = kdtree.query([point.x, point.y])
    return node_items[idx][0]


def add_special_nodes(G: nx.DiGraph, gdf_points: gpd.GeoDataFrame, tipo_nodo: str,
                      kdtree, node_items: list, prefijo: str) -> nx.DiGraph:
    """
    Añade equipamientos (parques de bomberos, hospitales) como nodos especiales,
    conectados mediante una arista corta al nodo de calle más cercano.
    gdf_points debe estar en el mismo CRS proyectado que el grafo (EPSG:25830).
    """
    for i, row in gdf_points.iterrows():
        punto = row.geometry
        nodo_especial_id = f"{prefijo}_{i}"

        G.add_node(
            nodo_especial_id,
            x=punto.x, y=punto.y,
            tipo_nodo=tipo_nodo,
            nombre=row.get("nombre", ""),
            direccion=row.get("direccion", ""),
        )

        nodo_cercano_id = nearest_node(kdtree, node_items, punto)
        cercano = G.nodes[nodo_cercano_id]
        distancia = punto.distance(Point(cercano["x"], cercano["y"]))

        attr_conexion = {
            "length_m": distancia,
            "travel_time_s": distancia / (VELOCIDAD_ACCESO_EQUIPAMIENTO_KPH / 3.6),
            "width_m": ANCHO_ACCESO_EQUIPAMIENTO_M,
            "height_m": ALTURA_LIBRE_SIN_RESTRICCION,
            "highway": "equipment_access",
        }
        G.add_edge(nodo_especial_id, nodo_cercano_id, **attr_conexion)
        G.add_edge(nodo_cercano_id, nodo_especial_id, **attr_conexion)

    return G
