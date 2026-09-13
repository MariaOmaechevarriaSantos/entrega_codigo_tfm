"""
Isócronas de cobertura (ver README.md, «Motor de rutas P4»): polígonos de
área alcanzable desde un nodo origen (parque de bomberos) en 5/10/15
minutos, reutilizando el mismo filtrado físico y de tráfico que
calcular_ruta (_dynamic_weight importado de optimizer.py, no duplicado aquí).
"""
import logging

import geopandas as gpd
import networkx as nx
from shapely import concave_hull
from shapely.geometry import MultiPoint

from routing.optimizer import ANCHO_CAMION_REQ, GALIBO_REQ, _dynamic_weight

logger = logging.getLogger(__name__)

CRS_PROJECTED = 25830

# Con menos nodos que esto, un hull (cóncavo o convexo) resulta degenerado
# o directamente falla; se devuelve un buffer circular pequeño en su lugar.
MIN_NODOS_PARA_HULL = 4
RADIO_BUFFER_DEGENERADO_M = 50.0

# ratio de shapely.concave_hull: 0 = más ajustado a la forma real de las
# calles, 1 = equivalente al convex hull. 0.3 se eligió para que el polígono
# no incluya zonas no alcanzables por la topología real de calles sin
# fragmentarse en puntos aislados con la densidad de nodos típica de un
# callejero urbano.
CONCAVE_HULL_RATIO = 0.3


def _construir_poligono(coords: list[tuple]):
    puntos = MultiPoint(coords)
    if len(coords) < MIN_NODOS_PARA_HULL:
        return puntos.centroid.buffer(RADIO_BUFFER_DEGENERADO_M)
    return concave_hull(puntos, ratio=CONCAVE_HULL_RATIO)


def calcular_isocronas(
    G: nx.DiGraph,
    origin_node,
    cortes_min: list[float] = [5, 10, 15],
    ancho_req: float = ANCHO_CAMION_REQ,
    galibo_req: float = GALIBO_REQ,
    traffic_preds: dict[str, int] | None = None,
) -> dict:
    """Devuelve {corte_min: Polygon} en EPSG:4326."""
    traffic_preds = traffic_preds or {}

    def weight_fn(u, v, d):
        return _dynamic_weight(u, v, d, traffic_preds, ancho_req, galibo_req)

    cutoff_s = max(cortes_min) * 60
    tiempos = nx.single_source_dijkstra_path_length(G, origin_node, cutoff=cutoff_s, weight=weight_fn)

    poligonos_proyectados = {}
    for corte in cortes_min:
        corte_s = corte * 60
        coords = [
            (G.nodes[nid]["x"], G.nodes[nid]["y"])
            for nid, t in tiempos.items()
            if t <= corte_s
        ]
        poligonos_proyectados[corte] = _construir_poligono(coords)

    gdf = gpd.GeoDataFrame(
        {"corte_min": list(poligonos_proyectados.keys())},
        geometry=list(poligonos_proyectados.values()),
        crs=CRS_PROJECTED,
    ).to_crs(epsg=4326)

    return dict(zip(gdf["corte_min"], gdf.geometry))


def generar_geojson_bomberos(output_path: str = "data/processed/isocronas_bomberos.geojson") -> str:
    """
    Genera las isócronas 5/10/15 min para cada parque de bomberos real
    (nodos especiales del grafo, no coordenadas aproximadas) y las guarda
    como GeoJSON, una feature por parque × corte.
    Requiere que graph_engine.load_graph() haya conectado los nodos
    especiales — depende de los GeoJSON de equipamientos de P1.
    """
    from routing.graph_engine import load_graph

    G = load_graph()
    parques = [
        (nid, d.get("nombre", str(nid)))
        for nid, d in G.nodes(data=True)
        if d.get("tipo_nodo") == "bomberos"
    ]
    if not parques:
        raise RuntimeError(
            "No hay nodos de tipo 'bomberos' en el grafo servido — "
            "¿faltan los GeoJSON de equipamientos de P1? (ver graph_engine.load_graph)"
        )

    filas = []
    for nodo_id, nombre in parques:
        isocronas = calcular_isocronas(G, nodo_id)
        for corte_min, poligono in isocronas.items():
            filas.append({"parque": nombre, "corte_min": corte_min, "geometry": poligono})

    gdf = gpd.GeoDataFrame(filas, crs="EPSG:4326")
    gdf.to_file(output_path, driver="GeoJSON")
    logger.info("Isócronas guardadas en %s (%d features)", output_path, len(gdf))
    return output_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generar_geojson_bomberos()
