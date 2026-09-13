"""
Descarga los límites administrativos de los 21 distritos de Madrid desde
OpenStreetMap vía osmnx, mismo patrón que `osm_callejero.py`.

Prerequisito de `pipeline/transform/osm_callejero.py::add_district_zones`
(espera una columna `nombre`) y de `merge_aforos`/`build_aforos_zonas`.
Los nombres se normalizan (sin tildes) para coincidir exactamente con
`ml/generate_dataset.py::DISTRITOS_MADRID` y así usar la misma
nomenclatura de zona en el grafo y en el modelo ML.
"""
import logging
import os

import geopandas as gpd

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "../../data/raw/distritos")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "madrid_distritos_raw.geojson")

# Nombre de búsqueda en OSM (con tildes, como los reconoce Nominatim) ->
# nombre canónico usado en ml/generate_dataset.py::DISTRITOS_MADRID.
DISTRITOS_MADRID = {
    "Centro": "Centro",
    "Arganzuela": "Arganzuela",
    "Retiro": "Retiro",
    "Salamanca": "Salamanca",
    "Chamartín": "Chamartin",
    "Tetuán": "Tetuan",
    "Chamberí": "Chamberi",
    "Fuencarral-El Pardo": "Fuencarral-El Pardo",
    "Moncloa-Aravaca": "Moncloa-Aravaca",
    "Latina": "Latina",
    "Carabanchel": "Carabanchel",
    "Usera": "Usera",
    "Puente de Vallecas": "Puente de Vallecas",
    "Moratalaz": "Moratalaz",
    "Ciudad Lineal": "Ciudad Lineal",
    "Hortaleza": "Hortaleza",
    "Villaverde": "Villaverde",
    "Villa de Vallecas": "Villa de Vallecas",
    "Vicálvaro": "Vicalvaro",
    "San Blas-Canillejas": "San Blas-Canillejas",
    "Barajas": "Barajas",
}


def download_distritos() -> gpd.GeoDataFrame:
    """
    Descarga los límites administrativos de los 21 distritos de Madrid.
    Retorna un GeoDataFrame con columna `nombre` (nomenclatura canónica,
    igual que DISTRITOS_MADRID de ml/generate_dataset.py) y `geometry`.
    """
    try:
        import osmnx as ox
    except ImportError:
        raise ImportError("Instala osmnx: pip install osmnx")

    logger.info("Descargando límites de los 21 distritos de Madrid desde OSM...")
    filas = []
    faltantes = []
    for osm_nombre, nombre_canonico in DISTRITOS_MADRID.items():
        try:
            gdf = ox.geocode_to_gdf(f"{osm_nombre}, Madrid, Spain")
            filas.append({"nombre": nombre_canonico, "geometry": gdf.iloc[0]["geometry"]})
        except Exception as e:
            logger.error("No se pudo geocodificar el distrito %s: %s", osm_nombre, e)
            faltantes.append(osm_nombre)

    if faltantes:
        logger.warning("Distritos no descargados (%d/21): %s", len(faltantes), faltantes)
    if not filas:
        raise RuntimeError("No se pudo descargar ningún distrito de Madrid.")

    gdf_distritos = gpd.GeoDataFrame(filas, crs="EPSG:4326")
    logger.info("Distritos descargados: %d/21", len(gdf_distritos))
    return gdf_distritos


def save_raw(gdf: gpd.GeoDataFrame) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    gdf.to_file(OUTPUT_FILE, driver="GeoJSON")
    logger.info("Distritos crudos guardados en %s", OUTPUT_FILE)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    gdf = download_distritos()
    save_raw(gdf)
