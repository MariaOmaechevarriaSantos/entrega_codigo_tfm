"""
Descarga el estado EN VIVO de incidencias en vía pública de Madrid (obras,
cortes de carril, alertas/previsiones, accidentes) desde el feed XML del
Ayuntamiento. Se actualiza cada ~5 min y NO existe histórico oficial
descargable (el propio portal indica que están "estudiando la viabilidad"
de publicarlo) — por eso este módulo es deliberadamente independiente del
resto de `pipeline/ingest/`.

IMPORTANTE — este módulo NO forma parte del flow batch de `run_pipeline.py`
y no debe añadirse ahí. Al ser un feed en vivo, cualquier snapshot que P1
descargara hoy estaría obsoleto para cuando exista el motor de rutas (P4).
`fetch_incidencias_actuales()` está pensado para que P4 lo llame en el
momento de calcular una ruta (para bloquear/penalizar tramos con obras
activas), no para ingesta periódica.

Si esta URL empieza a fallar, buscar el nuevo host en
https://datos.madrid.es/dataset/202062-0-trafico-incidencias-viapublica.
"""
import logging
from xml.etree import ElementTree

import geopandas as gpd
import pandas as pd
import requests
from shapely.geometry import Point

logger = logging.getLogger(__name__)

INCIDENCIAS_URL = "https://informo.madrid.es/informo/tmadrid/incid_aytomadrid.xml"


def _xml_to_geodataframe(raw: bytes) -> gpd.GeoDataFrame:
    """
    Convierte el XML de incidencias (elemento raíz <Incidencias>, hijos
    <Incidencia>) a GeoDataFrame. Descarta registros con coordenadas
    ausentes o no numéricas (en vez de reventar todo el fetch por un dato
    puntual mal formado), deduplica por id_incidencia si el feed repite
    alguna, y parsea fh_inicio/fh_final a datetime real para que P4 pueda
    comparar contra "ahora" y saber si una obra sigue activa.
    """
    root = ElementTree.fromstring(raw)
    rows = []
    vistos = set()
    n_descartados = 0
    for inc in root.findall("Incidencia"):
        id_incidencia = inc.findtext("id_incidencia", "")
        if id_incidencia and id_incidencia in vistos:
            continue
        lat, lon = inc.findtext("latitud"), inc.findtext("longitud")
        if lat is None or lon is None:
            n_descartados += 1
            continue
        try:
            geometry = Point(float(lon), float(lat))
        except ValueError:
            n_descartados += 1
            continue
        if id_incidencia:
            vistos.add(id_incidencia)
        rows.append({
            "id_incidencia": id_incidencia,
            "cod_tipo_incidencia": inc.findtext("cod_tipo_incidencia", ""),
            "nom_tipo_incidencia": inc.findtext("nom_tipo_incidencia", ""),
            "descripcion": inc.findtext("descripcion", ""),
            "fh_inicio": inc.findtext("fh_inicio", ""),
            "fh_final": inc.findtext("fh_final", ""),
            "incid_planificada": inc.findtext("incid_planificada", "") == "S",
            "es_obras": inc.findtext("es_obras", "") == "S",
            "es_accidente": inc.findtext("es_accidente", "") == "S",
            "geometry": geometry,
        })

    if n_descartados:
        logger.warning("Incidencias con coordenadas inválidas descartadas: %d", n_descartados)

    gdf = gpd.GeoDataFrame(rows, crs="EPSG:4326")
    if not gdf.empty:
        for col in ("fh_inicio", "fh_final"):
            gdf[col] = pd.to_datetime(gdf[col], errors="coerce")
    return gdf


def fetch_incidencias_actuales() -> gpd.GeoDataFrame:
    """
    Descarga el snapshot en vivo de incidencias de vía pública.
    Retorna un GeoDataFrame con una fila por incidencia activa (obras,
    cortes, alertas, accidentes) y su ubicación. No persiste nada en disco.
    """
    logger.info("Descargando incidencias en vía pública (feed en vivo)...")
    resp = requests.get(INCIDENCIAS_URL, timeout=30)
    resp.raise_for_status()
    gdf = _xml_to_geodataframe(resp.content)
    logger.info("Incidencias descargadas: %d (obras: %d)", len(gdf), int(gdf["es_obras"].sum()) if len(gdf) else 0)
    return gdf


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    fetch_incidencias_actuales()
