"""
Validación con rutas reales (ver README.md, «Motor de rutas P4»): parque de
bomberos real -> destino conocido de Madrid, con Dijkstra + tráfico real de
una fecha/hora representativa. El origen es el nodo especial real del
parque, no una coordenada aproximada.

IMPORTANTE — tiempo_google_min NO se fabrica aquí: es una consulta manual
a Google Maps que un humano debe rellenar en el CSV de salida antes de
usar la tabla en la memoria. Un camión de bomberos no es asimilable a un
coche particular; la comparación es una aproximación, no una validación
estricta.
"""
import csv
import logging

from routing.benchmark_astar import PARQUES, _resolver_origen_parque
from routing.graph_engine import get_nearest_node, load_graph
from routing.optimizer import ANCHO_CAMION_REQ, GALIBO_REQ, calcular_ruta

logger = logging.getLogger(__name__)

FECHA_REPRESENTATIVA = "2025-06-16"  # lunes laborable
HORA_REPRESENTATIVA = 8  # hora punta mañana

# Destinos conocidos de Madrid (hospitales, monumentos) — mismo criterio de
# coordenadas aproximadas que PARQUES/PUNTOS_MADRID en benchmark_astar.py.
DESTINOS = {
    "Puerta del Sol": (40.4169, -3.7025),
    "Palacio Real": (40.4180, -3.7144),
    "Santiago Bernabéu": (40.4531, -3.6883),
    "Hospital La Paz": (40.4839, -3.6890),
    "Hospital 12 de Octubre": (40.3729, -3.6963),
    "Estación de Atocha": (40.4067, -3.6907),
}

# 12 pares parque × destino. Nombres de parque = claves reales de
# benchmark_astar.PARQUES (ver fix "usa nombres reales de parques de
# bomberos en PARQUES").
PARES_VALIDACION = [
    ("PARQUE DE BOMBEROS 01. CHAMBERÍ", "Puerta del Sol"),
    ("PARQUE DE BOMBEROS 01. CHAMBERÍ", "Hospital La Paz"),
    ("PARQUE DE BOMBEROS 08. PUENTE DE VALLECAS", "Hospital 12 de Octubre"),
    ("PARQUE DE BOMBEROS 08. PUENTE DE VALLECAS", "Estación de Atocha"),
    ("PARQUE DE BOMBEROS 12. LATINA", "Palacio Real"),
    ("PARQUE DE BOMBEROS 12. LATINA", "Puerta del Sol"),
    ("PARQUE DE BOMBEROS 11. HORTALEZA", "Santiago Bernabéu"),
    ("PARQUE DE BOMBEROS 11. HORTALEZA", "Hospital La Paz"),
    ("PARQUE DE BOMBEROS 10. VILLAVERDE", "Hospital 12 de Octubre"),
    ("PARQUE DE BOMBEROS 10. VILLAVERDE", "Palacio Real"),
    ("PARQUE DE BOMBEROS 01. CHAMBERÍ", "Estación de Atocha"),
    ("PARQUE DE BOMBEROS 12. LATINA", "Hospital 12 de Octubre"),
]


def calcular_fila_validacion(
    G, parque_nombre: str, destino_nombre: str, origin_node, dest_node,
    traffic_preds: dict[str, int],
    ancho_req: float = ANCHO_CAMION_REQ,
    galibo_req: float = GALIBO_REQ,
    tiempo_google_min: float | None = None,
) -> dict:
    """Calcula distancia/tiempo del modelo para un par parque->destino. No
    calcula tiempo_google_min — ese valor se pasa ya conocido (consulta
    manual) o se deja en blanco para rellenar después."""
    resultado = calcular_ruta(
        G, origin_node, dest_node, traffic_preds=traffic_preds,
        ancho_req=ancho_req, galibo_req=galibo_req,
    )

    if resultado is None:
        return {
            "parque": parque_nombre,
            "destino": destino_nombre,
            "distancia_km": None,
            "tiempo_modelo_min": None,
            "tiempo_google_min": tiempo_google_min,
            "diferencia_pct": None,
            "nota": "Sin ruta posible con las restricciones de anchura/gálibo actuales.",
        }

    props = resultado["features"][0]["properties"]
    distancia_km = round(props["length_m"] / 1000, 2)
    tiempo_modelo_min = props["time_min"]
    ruta_completa = props.get("ruta_completa", True)

    diferencia_pct = None
    if tiempo_google_min and ruta_completa:
        diferencia_pct = round((tiempo_modelo_min - tiempo_google_min) / tiempo_google_min * 100, 1)

    nota = ""
    if not ruta_completa:
        nota = (
            f"Ruta parcial: no alcanza el destino real por anchura/gálibo, se "
            f"detiene a {props.get('distancia_restante_destino_m', 0):.0f}m de "
            f"él. No comparar tiempo_modelo_min contra Google Maps."
        )

    return {
        "parque": parque_nombre,
        "destino": destino_nombre,
        "distancia_km": distancia_km,
        "tiempo_modelo_min": tiempo_modelo_min,
        "tiempo_google_min": tiempo_google_min,
        "diferencia_pct": diferencia_pct,
        "nota": nota,
    }


def _obtener_traffic_preds() -> dict[str, int]:
    try:
        from ml.predict_trafico_real import predecir_trafico_real
        return predecir_trafico_real(FECHA_REPRESENTATIVA, HORA_REPRESENTATIVA)
    except FileNotFoundError as e:
        logger.warning(
            "No se pudo cargar el modelo de tráfico real (%s); "
            "la tabla se genera sin tráfico aplicado.", e,
        )
        return {}


def ejecutar_validacion(output_csv: str = "validacion_rutas_emergencia.csv") -> list[dict]:
    G = load_graph()
    traffic_preds = _obtener_traffic_preds()

    filas = []
    for parque_nombre, destino_nombre in PARES_VALIDACION:
        p_lat, p_lon = PARQUES[parque_nombre]
        origin = _resolver_origen_parque(parque_nombre, p_lat, p_lon)
        d_lat, d_lon = DESTINOS[destino_nombre]
        dest = get_nearest_node(d_lat, d_lon)

        fila = calcular_fila_validacion(G, parque_nombre, destino_nombre, origin, dest, traffic_preds)
        filas.append(fila)

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "parque", "destino", "distancia_km", "tiempo_modelo_min",
            "tiempo_google_min", "diferencia_pct", "nota",
        ])
        writer.writeheader()
        writer.writerows(filas)

    logger.info(
        "Validación guardada en %s (%d filas). Completa 'tiempo_google_min' "
        "manualmente consultando Google Maps antes de usar la tabla en la memoria.",
        output_csv, len(filas),
    )
    return filas


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ejecutar_validacion()
