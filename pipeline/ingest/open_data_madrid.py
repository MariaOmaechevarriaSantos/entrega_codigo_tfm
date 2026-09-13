"""
Descarga instalaciones críticas de Madrid desde el portal Open Data Madrid.
Parques de bomberos, hospitales, centros educativos y centros de mayores.
Fuente: https://datos.madrid.es
"""
import logging
import os

import geopandas as gpd
import requests
from shapely.geometry import Point

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "../../data/raw/open_data_madrid")

# Endpoints Open Data Madrid verificados en vivo (curl -IL) el 2026-07-01.
# El portal reindexa periódicamente sus catálogos; si una URL empieza a
# devolver 404, buscar el nuevo slug en https://datos.madrid.es/portal/site/egob
# y actualizar aquí.
DATASETS = {
    "bomberos": {
        "url": "https://datos.madrid.es/egob/catalogo/211642-0-bomberos-parques.json",
        "output": "parques_bomberos.geojson",
    },
    "hospitales": {
        "url": "https://datos.madrid.es/egob/catalogo/212769-0-atencion-medica.json",
        "output": "hospitales.geojson",
    },
    "centros_educativos": {
        "url": "https://datos.madrid.es/egob/catalogo/212790-0-centros-educacion.json",
        "output": "centros_educativos.geojson",
    },
    "centros_mayores": {
        "url": "https://datos.madrid.es/egob/catalogo/200337-0-centros-mayores.json",
        "output": "centros_mayores.geojson",
    },
}


def _json_to_geodataframe(data: dict) -> gpd.GeoDataFrame:
    """Convierte la respuesta JSON del portal Madrid a GeoDataFrame."""
    records = data.get("@graph", [])
    rows = []
    for r in records:
        coords = r.get("location", {})
        lat = coords.get("latitude")
        lon = coords.get("longitude")
        if lat is None or lon is None:
            continue
        rows.append({
            "nombre": r.get("title", ""),
            "tipo": r.get("@type", ""),
            "direccion": r.get("address", {}).get("street-address", ""),
            "geometry": Point(float(lon), float(lat)),
        })
    return gpd.GeoDataFrame(rows, crs="EPSG:4326")


def download_equipamientos(tipo: str) -> gpd.GeoDataFrame:
    """Descarga un tipo de equipamiento y retorna GeoDataFrame."""
    cfg = DATASETS.get(tipo)
    if cfg is None:
        raise ValueError(f"Tipo desconocido: {tipo}. Opciones: {list(DATASETS.keys())}")

    logger.info("Descargando %s desde Open Data Madrid...", tipo)
    try:
        resp = requests.get(cfg["url"], timeout=30)
        resp.raise_for_status()
    except requests.exceptions.HTTPError:
        logger.error("%s: HTTP %s al descargar %s", tipo, resp.status_code, cfg["url"])
        raise

    gdf = _json_to_geodataframe(resp.json())
    logger.info("%s: %d registros descargados", tipo, len(gdf))
    return gdf


def download_all_equipamientos() -> dict[str, gpd.GeoDataFrame]:
    """Descarga todos los equipamientos y los guarda en data/raw/."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    result = {}
    for tipo, cfg in DATASETS.items():
        try:
            gdf = download_equipamientos(tipo)
            output_path = os.path.join(OUTPUT_DIR, cfg["output"])
            gdf.to_file(output_path, driver="GeoJSON")
            logger.info("Guardado: %s", output_path)
            result[tipo] = gdf
        except Exception as e:
            logger.error("Error descargando %s: %s", tipo, e)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_all_equipamientos()
