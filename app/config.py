"""
Catálogo compartido de vehículos y valores por defecto — P5, bloque 3.

Única definición de anchuras/gálibos de vehículo del proyecto: la API lo
expone en GET /config y Streamlit lo consume por HTTP en vez de mantener su
propio ancho_map (regla "Prohibido duplicar constantes entre Streamlit y la
API", ver docs/adr/0006-catalogo-unico-de-vehiculos.md).

Las dimensiones del camión pesado de referencia NO se redefinen aquí: se
importan de routing/optimizer.py (ANCHO_CAMION_REQ, GALIBO_REQ — ya
parametrizadas por variable de entorno en P4) para que exista un único
valor en memoria, no una copia que pueda desincronizarse si alguien cambia
ANCHO_CAMION_REQ en el entorno.
"""
from routing.optimizer import ANCHO_CAMION_REQ, GALIBO_REQ

VEHICULOS = [
    {
        "id": "autobomba_pesada",
        "nombre": "Autobomba Pesada (BUP)",
        "ancho_m": ANCHO_CAMION_REQ,
        "galibo_m": GALIBO_REQ,
        "descripcion": "Camión de bomberos pesado de referencia; requiere calles anchas.",
    },
    {
        "id": "autobomba_ligera",
        "nombre": "Autobomba Ligera (BUL)",
        "ancho_m": 2.55,
        "galibo_m": 3.2,
        "descripcion": "Vehículo de intervención rápida, apto para el casco histórico.",
    },
    {
        "id": "vehiculo_rescate",
        "nombre": "Vehículo de Rescate (VR)",
        "ancho_m": 2.1,
        "galibo_m": 2.6,
        "descripcion": "Furgón ligero de rescate; máxima accesibilidad en calles estrechas.",
    },
]

VEHICULO_DEFAULT_ID = "autobomba_pesada"

ALGORITMOS_DISPONIBLES = ["dijkstra", "astar"]
ALGORITMO_DEFAULT = "dijkstra"

CORTES_ISOCRONAS_MIN_DEFAULT = [5, 10, 15]

TRAFICO_NIVELES = {0: "Bajo", 1: "Medio", 2: "Alto"}

# Bounding box aproximado del término municipal de Madrid (WGS84). Uso
# exclusivo: validar en /ruta que unas coordenadas no llegan de otra
# ciudad por error de cliente. Deliberadamente generoso (incluye margen
# fuera del límite administrativo exacto) -- no es un filtro geográfico
# de precisión, solo una guarda de cordura.
MADRID_BBOX = {"lat_min": 40.30, "lat_max": 40.57, "lon_min": -3.90, "lon_max": -3.50}


def get_vehiculo(vehiculo_id: str) -> dict | None:
    """Busca un vehículo del catálogo por id. None si no existe."""
    return next((v for v in VEHICULOS if v["id"] == vehiculo_id), None)


def get_vehiculo_default() -> dict:
    return get_vehiculo(VEHICULO_DEFAULT_ID)
