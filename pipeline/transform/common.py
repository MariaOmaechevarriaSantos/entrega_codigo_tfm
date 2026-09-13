"""
Utilidades compartidas por todos los módulos de transform/ — nada aquí es
específico de una fuente de datos. Cada fuente tiene su propio fichero
gemelo al de ingest/ (aforos_trafico.py, open_data_madrid.py,
osm_callejero.py, meteorologia_madrid.py); este es el único que no
corresponde a ninguna fuente en particular.
"""
import logging
import os

import geopandas as gpd
import pandas as pd
from shapely import wkt

logger = logging.getLogger(__name__)

CRS_PROJECTED = 25830

# Umbral de cobertura horaria por debajo del cual se avisa de posibles
# huecos (averías de sensor, festivos sin publicar, etc.) — ver detectar_huecos.
UMBRAL_COBERTURA_AVISO_PCT = 80.0

# Límite real del municipio de Madrid: unión de los 21 distritos,
# simplificada a ~50m de tolerancia (321 puntos en vez de 1716) y guardada
# en disco — no depende de descargar distritos en cada ejecución. La usan
# equipamientos y aforos para descartar puntos mal geolocalizados (ninguna
# fuente es 100% fiable: hemos encontrado casos reales en ambas).
with open(os.path.join(os.path.dirname(__file__), "madrid_boundary.wkt")) as f:
    MADRID_BOUNDARY = wkt.loads(f.read())


def _stats(n_input: int, n_output: int, n_nulos: int = 0, n_fuera_limite: int = 0, n_duplicados: int = 0) -> dict:
    return {
        "n_input": n_input,
        "n_output": n_output,
        "n_nulos_descartados": n_nulos,
        "n_fuera_limite_descartados": n_fuera_limite,
        "n_duplicados": n_duplicados,
    }


def _requerir_crs_proyectado(gdf: gpd.GeoDataFrame, nombre: str = "gdf_edges") -> None:
    """Lanza ValueError si `gdf` no está en CRS_PROJECTED — lo necesita cualquier función que mida distancias (cKDTree, buffers en metros)."""
    if gdf.crs is None or gdf.crs.to_epsg() != CRS_PROJECTED:
        raise ValueError(f"{nombre} debe estar en EPSG:{CRS_PROJECTED}. CRS actual: {gdf.crs}")


def _validar_columnas(df: pd.DataFrame, columnas_esperadas: set[str], fuente: str) -> None:
    """Falla rápido y con mensaje claro si el portal cambió el esquema de columnas."""
    faltantes = columnas_esperadas - set(df.columns)
    if faltantes:
        raise ValueError(
            f"{fuente}: faltan columnas esperadas {sorted(faltantes)} — "
            f"¿cambió el formato del dataset en origen? Columnas recibidas: {sorted(df.columns)}"
        )


def detectar_huecos(df: pd.DataFrame, id_col: str, fecha_col: str = "fecha", hora_col: str = "hora") -> pd.DataFrame:
    """
    Calcula la cobertura horaria real de cada id_col (sensor/estación)
    frente al rango completo de fechas/horas presente en el dataset. No
    rellena huecos (sería inventar datos) — solo los cuantifica: un sensor
    con una avería larga o un festivo sin publicar no genera ninguna fila
    con valores nulos (simplemente no existe esa fila), así que clean_*
    nunca lo vería si no se compara contra el calendario completo esperado.

    Retorna un DataFrame (id_col, n_presente, n_esperado, pct_cobertura),
    ordenado de peor a mejor cobertura.
    """
    df = df.copy()
    df[fecha_col] = pd.to_datetime(df[fecha_col]).dt.date
    fechas = pd.date_range(df[fecha_col].min(), df[fecha_col].max(), freq="D").date
    n_esperado = len(fechas) * 24

    presentes = df.drop_duplicates(subset=[id_col, fecha_col, hora_col]).groupby(id_col).size()
    resultado = presentes.reindex(df[id_col].unique(), fill_value=0).rename("n_presente").reset_index()
    resultado.columns = [id_col, "n_presente"]
    resultado["n_esperado"] = n_esperado
    resultado["pct_cobertura"] = (resultado["n_presente"] / resultado["n_esperado"] * 100).round(1)
    return resultado.sort_values("pct_cobertura").reset_index(drop=True)


def _avisar_baja_cobertura(df: pd.DataFrame, id_col: str, fuente: str) -> None:
    """
    Registra siempre un resumen de cobertura horaria (min/media/max) — antes
    esta información solo se veía si nadie bajaba del umbral, así que una
    fuente con 89% de cobertura mínima podía pasar completamente
    desapercibida en los logs. Además, avisa por separado (warning) si algún
    id_col cae por debajo del umbral.
    """
    cobertura = detectar_huecos(df, id_col=id_col)
    logger.info(
        "%s: cobertura horaria de %d %s — mínima %.1f%%, media %.1f%%, máxima %.1f%%",
        fuente, len(cobertura), id_col,
        cobertura["pct_cobertura"].min(), cobertura["pct_cobertura"].mean(), cobertura["pct_cobertura"].max(),
    )
    baja = cobertura[cobertura["pct_cobertura"] < UMBRAL_COBERTURA_AVISO_PCT]
    if len(baja):
        logger.warning(
            "%s: %d/%d %s con cobertura horaria < %.0f%% (posibles huecos por avería/festivo). Peor caso: %s",
            fuente, len(baja), len(cobertura), id_col, UMBRAL_COBERTURA_AVISO_PCT, baja.iloc[0].to_dict(),
        )


def merge_quality_stats(stats_list: list[dict], label: str) -> dict:
    """Agrega una lista de stats (una por fuente/lote) en un único dict etiquetado."""
    return {
        "fuente": label,
        "n_input": sum(s["n_input"] for s in stats_list),
        "n_output": sum(s["n_output"] for s in stats_list),
        "n_nulos_descartados": sum(s["n_nulos_descartados"] for s in stats_list),
        "n_fuera_limite_descartados": sum(s["n_fuera_limite_descartados"] for s in stats_list),
        "n_duplicados": sum(s["n_duplicados"] for s in stats_list),
    }


def descontar_fuera_limite(stats: dict, n_descartados: int) -> dict:
    """
    Ajusta in-place un dict de _stats cuando se descubren descartes por
    límite geográfico DESPUÉS de que ya se generaron las stats originales
    (ver aforos_trafico.py::attach_punto_coords, que descarta sensores fuera
    de Madrid en un paso posterior a clean_aforos, cuando ya hay coordenadas).
    """
    if n_descartados:
        stats["n_fuera_limite_descartados"] += n_descartados
        stats["n_output"] -= n_descartados
    return stats
