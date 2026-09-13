"""
Motor de optimización de rutas para camiones de bomberos.
Dijkstra con pesos dinámicos por tráfico predicho y
filtrado estricto de calles por anchura mínima del vehículo.
"""
import json
import logging
import os

import geopandas as gpd
import networkx as nx
from shapely.geometry import LineString

logger = logging.getLogger(__name__)

CRS_PROJECTED = 25830

# Dimensiones por defecto del camión de bomberos pesado Madrid
# Ajustables via variables de entorno
ANCHO_CAMION_REQ = float(os.environ.get("ANCHO_CAMION_REQ", "3.5"))
GALIBO_REQ = float(os.environ.get("GALIBO_REQ", "4.0"))

TRAFFIC_FACTORS = {0: 1.0, 1: 1.5, 2: 3.0}


def _dynamic_weight(
    u: int,
    v: int,
    d: dict,
    traffic_preds: dict[str, int],
    ancho_req: float = ANCHO_CAMION_REQ,
    galibo_req: float = GALIBO_REQ,
) -> float:
    """
    Función de peso para Dijkstra.
    Devuelve float('inf') si la calle es demasiado estrecha o el gálibo insuficiente.
    Multiplica el tiempo base por el factor de tráfico de la zona.
    """
    if d.get("width_m", 9.9) < ancho_req:
        return float("inf")
    if d.get("height_m", 99.0) < galibo_req:
        return float("inf")

    base = d.get("travel_time_s", 1.0)
    zona = d.get("zona", "Desconocida")
    nivel = traffic_preds.get(zona, 0)
    return base * TRAFFIC_FACTORS.get(nivel, 1.0)


def _heuristica_tiempo(u, v, G: nx.DiGraph, velocidad_max_kph: float = 100.0) -> float:
    """
    Heurística admisible para A*: tiempo de viaje en línea recta a la
    velocidad máxima del grafo. Nunca sobreestima el tiempo real (que
    siempre implica una distancia por calle >= la distancia euclídea,
    a una velocidad <= velocidad_max_kph).
    """
    dx = G.nodes[u]["x"] - G.nodes[v]["x"]
    dy = G.nodes[u]["y"] - G.nodes[v]["y"]
    return (dx**2 + dy**2) ** 0.5 / (velocidad_max_kph / 3.6)


def _distancia_recta_m(G: nx.DiGraph, u, v) -> float:
    dx = G.nodes[u]["x"] - G.nodes[v]["x"]
    dy = G.nodes[u]["y"] - G.nodes[v]["y"]
    return (dx**2 + dy**2) ** 0.5


def _ruta_parcial_hasta_nodo_mas_cercano(G: nx.DiGraph, G_filtrado, origin_node, dest_node, weight_fn) -> list:
    """
    Cuando el destino no es alcanzable respetando anchura/gálibo (ninguna
    alternativa válida), calcula la ruta hasta el nodo alcanzable más
    cercano en línea recta al destino real — no el primer nodo bloqueado
    que se encontraría siguiendo el camino "natural" hacia el destino, sino
    el mejor punto de aproximación real disponible.
    """
    _, caminos = nx.single_source_dijkstra(G_filtrado, origin_node, weight=weight_fn)
    nodo_mas_cercano = min(caminos.keys(), key=lambda nid: _distancia_recta_m(G, nid, dest_node))
    return caminos[nodo_mas_cercano]


def calcular_ruta(
    G: nx.DiGraph,
    origin_node: int,
    dest_node: int,
    traffic_preds: dict[str, int] | None = None,
    ancho_req: float = ANCHO_CAMION_REQ,
    galibo_req: float = GALIBO_REQ,
    algoritmo: str = "dijkstra",
) -> dict | None:
    """
    Calcula la ruta óptima entre dos nodos del grafo.

    Si el destino no es alcanzable respetando anchura/gálibo (no existe
    ninguna alternativa válida), devuelve la ruta hasta el nodo accesible
    más cercano al destino real, con `ruta_completa=False` y
    `distancia_restante_destino_m` (línea recta desde ese punto hasta el
    destino) — así el equipo sabe hasta dónde puede llegar el camión, en
    vez de recibir solo "sin ruta".

    Args:
        ancho_req: anchura mínima requerida (m). Default = ANCHO_CAMION_REQ,
            pero puede pasarse por llamada para no quedar fijado al importar
            el módulo (ver README.md, «Motor de rutas P4»).
        galibo_req: altura libre mínima requerida (m). Mismo criterio que ancho_req.
        algoritmo: "dijkstra" (default, ya probado en producción) o "astar"
            (alternativa configurable, ver benchmark_astar.py para la comparativa).

    Returns:
        GeoJSON dict con la ruta y propiedades, o None si origin_node/dest_node
        no existen en el grafo.
    """
    if algoritmo not in ("dijkstra", "astar"):
        raise ValueError(f"algoritmo debe ser 'dijkstra' o 'astar', recibido: {algoritmo!r}")

    if origin_node not in G or dest_node not in G:
        logger.warning("Nodo inexistente en el grafo: origin=%s, dest=%s", origin_node, dest_node)
        return None

    if traffic_preds is None:
        traffic_preds = {}

    def weight_fn(u, v, d):
        return _dynamic_weight(u, v, d, traffic_preds, ancho_req, galibo_req)

    # Vista del grafo que excluye por completo las aristas bloqueadas por
    # anchura/gálibo — así nx.dijkstra_path/nx.astar_path nunca pueden
    # devolver una ruta que las cruce (a diferencia de pasarles weight_fn
    # sobre el grafo completo, donde tratan inf como "muy cara", no como
    # inexistente, y devuelven esa ruta igual si es la única disponible).
    G_filtrado = nx.subgraph_view(G, filter_edge=lambda u, v: weight_fn(u, v, G[u][v]) != float("inf"))

    ruta_completa = True
    try:
        if algoritmo == "astar":
            def heuristic_fn(u, v):
                return _heuristica_tiempo(u, v, G)
            path = nx.astar_path(G_filtrado, origin_node, dest_node, heuristic=heuristic_fn, weight=weight_fn)
        else:
            path = nx.dijkstra_path(G_filtrado, origin_node, dest_node, weight=weight_fn)
    except nx.NetworkXNoPath:
        ruta_completa = False
        path = _ruta_parcial_hasta_nodo_mas_cercano(G, G_filtrado, origin_node, dest_node, weight_fn)
        logger.warning(
            "Destino %s no alcanzable por anchura < %.1fm o gálibo < %.1fm desde %s; "
            "devolviendo ruta parcial hasta %s.",
            dest_node, ancho_req, galibo_req, origin_node, path[-1],
        )

    # Reconstruir geometría y calcular métricas
    coords_path = []
    total_length_m = 0.0
    total_time_s = 0.0

    for i in range(len(path) - 1):
        u, v = path[i], path[i + 1]
        coords_path.append((G.nodes[u]["x"], G.nodes[u]["y"]))
        d = G[u][v]
        total_length_m += d.get("length_m", 0.0)
        total_time_s += weight_fn(u, v, d)

    last = path[-1]
    last_coords = (G.nodes[last]["x"], G.nodes[last]["y"])
    coords_path.append(last_coords)
    if len(coords_path) == 1:
        coords_path.append(last_coords)  # LineString necesita >= 2 puntos (ruta parcial de longitud 0)

    distancia_restante_destino_m = 0.0 if ruta_completa else _distancia_recta_m(G, last, dest_node)

    line = LineString(coords_path)
    gdf = gpd.GeoDataFrame(geometry=[line], crs=CRS_PROJECTED).to_crs(epsg=4326)
    geo_dict = json.loads(gdf.to_json())
    geo_dict["features"][0]["properties"] = {
        "length_m": round(total_length_m, 2),
        "time_s": round(total_time_s, 2),
        "time_min": round(total_time_s / 60, 2),
        "n_nodes": len(path),
        "ancho_min_req_m": ancho_req,
        "galibo_min_req_m": galibo_req,
        "traffic_applied": bool(traffic_preds),
        "ruta_completa": ruta_completa,
        "distancia_restante_destino_m": round(distancia_restante_destino_m, 2),
    }

    logger.info(
        "Ruta calculada: %.0fm, %.1fmin, %d nodos, completa=%s",
        total_length_m, total_time_s / 60, len(path), ruta_completa,
    )
    return geo_dict
