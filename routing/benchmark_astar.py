"""
Comparativa A* vs Dijkstra (ver docs/adr/0002-dijkstra-por-defecto-frente-a-astar.md):
nodos explorados, tiempo de cómputo, y verificación de que ambos devuelven
la misma distancia/tiempo total.

Decisión de implementación (nodos explorados): nx.dijkstra_path y
nx.astar_path no exponen cuántos nodos visitan internamente. Para medir
"nodos explorados" de forma comparable entre los dos algoritmos, este
script reimplementa ambas búsquedas con heapq, contando cada extracción
de la cola de prioridad (estándar en la literatura de pathfinding — un
nodo "explorado" es uno que llega a expandirse, no uno simplemente
encolado). calcular_ruta() en producción sigue usando
nx.dijkstra_path/nx.astar_path sin cambios; esta instrumentación vive
solo aquí. length_m/time_min de la tabla, en cambio, sí vienen de
calcular_ruta() real — una sola fuente de verdad para esas métricas.
"""
import csv
import heapq
import logging
import time

import networkx as nx

from routing.graph_engine import get_nearest_node, get_special_node, load_graph
from routing.optimizer import ANCHO_CAMION_REQ, GALIBO_REQ, _dynamic_weight, _heuristica_tiempo, calcular_ruta

logger = logging.getLogger(__name__)

# Nombres reales de parques de bomberos (deben coincidir exactamente con la
# columna 'nombre' de data/processed/parques_bomberos.geojson — P1 — para que
# get_special_node() los encuentre y _resolver_origen_parque no caiga al
# fallback de coordenada aproximada). 5 parques con buena dispersión
# geográfica, coordenadas de referencia solo para el fallback si P1 aún no
# ha entregado los GeoJSON.
PARQUES = {
    "PARQUE DE BOMBEROS 01. CHAMBERÍ": (40.4402, -3.7008),
    "PARQUE DE BOMBEROS 08. PUENTE DE VALLECAS": (40.3946, -3.6531),
    "PARQUE DE BOMBEROS 12. LATINA": (40.3880, -3.7651),
    "PARQUE DE BOMBEROS 11. HORTALEZA": (40.4741, -3.6651),
    "PARQUE DE BOMBEROS 10. VILLAVERDE": (40.3399, -3.7070),
}

PUNTOS_MADRID = {
    "Gran Vía": (40.4200, -3.7050),
    "Aeropuerto Barajas": (40.4936, -3.5668),
    "Vallecas": (40.3868, -3.6545),
    "Plaza España": (40.4240, -3.7122),
    "Legazpi": (40.3948, -3.6963),
}


def _dijkstra_contando_nodos(G, origin, dest, weight_fn):
    """Dijkstra con heapq, contando nodos extraídos de la cola (explorados)."""
    dist = {origin: 0.0}
    visitados = set()
    heap = [(0.0, origin)]
    nodos_explorados = 0
    while heap:
        d, u = heapq.heappop(heap)
        if u in visitados:
            continue
        visitados.add(u)
        nodos_explorados += 1
        if u == dest:
            return d, nodos_explorados
        for v, edge_d in G[u].items():
            w = weight_fn(u, v, edge_d)
            if w == float("inf"):
                continue
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(heap, (nd, v))
    return float("inf"), nodos_explorados


def _astar_contando_nodos(G, origin, dest, weight_fn, heuristic_fn):
    """A* con heapq, contando nodos extraídos de la cola (explorados)."""
    dist = {origin: 0.0}
    visitados = set()
    heap = [(heuristic_fn(origin, dest), 0.0, origin)]
    nodos_explorados = 0
    while heap:
        _, d, u = heapq.heappop(heap)
        if u in visitados:
            continue
        visitados.add(u)
        nodos_explorados += 1
        if u == dest:
            return d, nodos_explorados
        for v, edge_d in G[u].items():
            w = weight_fn(u, v, edge_d)
            if w == float("inf"):
                continue
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(heap, (nd + heuristic_fn(v, dest), nd, v))
    return float("inf"), nodos_explorados


def comparar_par(
    G, origin, dest,
    ancho_req: float = ANCHO_CAMION_REQ,
    galibo_req: float = GALIBO_REQ,
    traffic_preds: dict[str, int] | None = None,
) -> list[dict]:
    """Compara Dijkstra vs A* para un par origen-destino. length_m/time_min
    vienen de calcular_ruta() real; nodos_explorados/tiempo_ms, de la
    instrumentación heapq de este módulo (ver docstring del módulo)."""
    traffic_preds = traffic_preds or {}

    def weight_fn(u, v, d):
        return _dynamic_weight(u, v, d, traffic_preds, ancho_req, galibo_req)

    def heuristic_fn(u, v):
        return _heuristica_tiempo(u, v, G)

    filas = []
    for algoritmo in ("dijkstra", "astar"):
        t0 = time.perf_counter()
        if algoritmo == "dijkstra":
            _, nodos_explorados = _dijkstra_contando_nodos(G, origin, dest, weight_fn)
        else:
            _, nodos_explorados = _astar_contando_nodos(G, origin, dest, weight_fn, heuristic_fn)
        tiempo_ms = (time.perf_counter() - t0) * 1000

        resultado = calcular_ruta(
            G, origin, dest, traffic_preds=traffic_preds,
            ancho_req=ancho_req, galibo_req=galibo_req, algoritmo=algoritmo,
        )
        props = resultado["features"][0]["properties"] if resultado else None

        filas.append({
            "algoritmo": algoritmo,
            "nodos_explorados": nodos_explorados,
            "tiempo_ms": round(tiempo_ms, 3),
            # length_m/time_min son de la ruta real (parcial si ruta_completa=False,
            # ver calcular_ruta) — None solo si origin/dest ni siquiera existen en G.
            "length_m": props["length_m"] if props else None,
            "time_min": props["time_min"] if props else None,
            "ruta_completa": props["ruta_completa"] if props else None,
            "distancia_restante_destino_m": props["distancia_restante_destino_m"] if props else None,
        })
    return filas


def _resolver_origen_parque(nombre: str, lat: float, lon: float):
    """Usa el nodo especial real del parque si ya está conectado; si no
    (P1 aún no ha entregado los GeoJSON de equipamientos), cae a la
    coordenada aproximada — mismo criterio de degradación que graph_engine."""
    nodo = get_special_node("bomberos", nombre)
    if nodo is not None:
        return nodo
    logger.warning(
        "Nodo especial de parque '%s' no encontrado (¿faltan los GeoJSON de P1?); "
        "usando coordenada aproximada.", nombre,
    )
    return get_nearest_node(lat, lon)


def generar_pares() -> tuple[list[tuple], nx.DiGraph]:
    """Parques de bomberos × puntos conocidos de Madrid (25 pares).
    Devuelve (pares, G): la lista de (parque, destino, origin_node, dest_node)
    y el grafo ya cargado, para no volver a construirlo en el llamador."""
    G = load_graph()
    pares = []
    for nombre_parque, (p_lat, p_lon) in PARQUES.items():
        origin = _resolver_origen_parque(nombre_parque, p_lat, p_lon)
        for nombre_punto, (i_lat, i_lon) in PUNTOS_MADRID.items():
            dest = get_nearest_node(i_lat, i_lon)
            pares.append((nombre_parque, nombre_punto, origin, dest))
    return pares, G


def ejecutar_comparativa(output_csv: str = "benchmark_astar_resultados.csv") -> list[dict]:
    pares, G = generar_pares()
    filas_totales = []
    for nombre_parque, nombre_punto, origin, dest in pares:
        for fila in comparar_par(G, origin, dest):
            fila["parque"] = nombre_parque
            fila["destino"] = nombre_punto
            filas_totales.append(fila)

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "parque", "destino", "algoritmo", "nodos_explorados", "tiempo_ms", "length_m", "time_min",
            "ruta_completa", "distancia_restante_destino_m",
        ])
        writer.writeheader()
        writer.writerows(filas_totales)

    logger.info("Comparativa guardada en %s (%d filas)", output_csv, len(filas_totales))
    return filas_totales


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    filas = ejecutar_comparativa()

    por_algoritmo = {}
    for fila in filas:
        por_algoritmo.setdefault(fila["algoritmo"], []).append(fila)

    for algoritmo, datos in por_algoritmo.items():
        total_nodos = sum(f["nodos_explorados"] for f in datos)
        total_tiempo_ms = sum(f["tiempo_ms"] for f in datos)
        print(f"{algoritmo}: {total_nodos} nodos explorados (total), "
              f"{total_tiempo_ms:.2f}ms tiempo de cómputo (total), {len(datos)} pares")

    # CONCLUSIÓN (criterio explícito a rellenar tras ejecutar contra el
    # grafo real de Madrid): comparar nodos_explorados y tiempo_ms totales
    # entre "dijkstra" y "astar" arriba. Si A* explora sensiblemente menos
    # nodos pero Dijkstra ya cumple holgadamente el presupuesto de latencia
    # de /ruta, mantener Dijkstra por simplicidad (es el algoritmo ya probado
    # en producción). Si Dijkstra se acerca al límite de latencia con el
    # grafo completo de Madrid, cambiar el default a A* — requiere
    # confirmación humana explícita. La decisión vigente y su porqué están
    # en docs/adr/0002-dijkstra-por-defecto-frente-a-astar.md.
