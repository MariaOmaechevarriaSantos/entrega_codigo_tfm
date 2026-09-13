"""
Transformaciones sobre los aforos de tráfico (ver ingest/aforos_trafico.py):
limpieza, unión con coordenadas, asignación a arista y agregación por zona.
"""
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from pipeline.transform.common import (
    CRS_PROJECTED, MADRID_BOUNDARY, _stats, _avisar_baja_cobertura, descontar_fuera_limite, _requerir_crs_proyectado,
)
from pipeline.transform.open_data_madrid import marcar_equipamientos_cercanos

logger = logging.getLogger(__name__)


def clean_aforos(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Normaliza aforos entre formatos/años, descarta nulos, deduplica por
    (id_punto, fecha, hora). Acepta tanto la columna `id` (salida cruda de
    aforos_trafico.py) como `id_punto` ya normalizada.
    """
    n_input = len(df)
    df = df.copy()
    if "id" in df.columns and "id_punto" not in df.columns:
        df = df.rename(columns={"id": "id_punto"})

    mask_nulos = df["id_punto"].isna() | df["fecha"].isna()
    n_nulos = int(mask_nulos.sum())
    df = df[~mask_nulos]

    n_fuera_limite = 0

    dedup_cols = [c for c in ("id_punto", "fecha", "hora") if c in df.columns]
    n_antes_dedup = len(df)
    df = df.drop_duplicates(subset=dedup_cols)
    n_duplicados = n_antes_dedup - len(df)

    stats = _stats(n_input, len(df), n_nulos, n_fuera_limite, n_duplicados)
    logger.info("clean_aforos: %s", stats)
    if len(df):
        _avisar_baja_cobertura(df, id_col="id_punto", fuente="clean_aforos")
    return df.reset_index(drop=True), stats


def _id_punto_column(df: pd.DataFrame) -> str:
    if "id_punto" in df.columns:
        return "id_punto"
    if "id" in df.columns:
        return "id"
    raise ValueError("df_aforos debe tener columna 'id_punto' o 'id'.")


def _lonlat_columns(df: pd.DataFrame) -> tuple[str, str]:
    if "longitud" in df.columns and "latitud" in df.columns:
        return "longitud", "latitud"
    if "lon" in df.columns and "lat" in df.columns:
        return "lon", "lat"
    raise ValueError("df_aforos debe tener columnas de coordenadas ('longitud'/'latitud' o 'lon'/'lat').")


def attach_punto_coords(df_aforos: pd.DataFrame, df_puntos_medida: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Une las medidas de aforos con las coordenadas de sus puntos de medida
    (dataset aparte, ver ingest/aforos_trafico.py::download_puntos_medida),
    descartando los que caen fuera de MADRID_BOUNDARY (ni el catálogo
    oficial de sensores es 100% fiable). Retorna (df_unido,
    n_filas_descartadas) — quien no use procesar_aforos_para_zonas debe
    sumar ese segundo valor a las stats de clean_aforos a mano
    (`n_fuera_limite_descartados` +=, `n_output` -=; ver descontar_fuera_limite).
    """
    id_col_valores = _id_punto_column(df_aforos)
    id_col_puntos = _id_punto_column(df_puntos_medida)
    lon_col, lat_col = _lonlat_columns(df_puntos_medida)

    puntos = df_puntos_medida[[id_col_puntos, lon_col, lat_col]].drop_duplicates(id_col_puntos)
    gdf_puntos = gpd.GeoDataFrame(
        puntos, geometry=gpd.points_from_xy(puntos[lon_col], puntos[lat_col]), crs="EPSG:4326",
    )
    dentro = gdf_puntos.geometry.within(MADRID_BOUNDARY)
    ids_fuera = set(gdf_puntos.loc[~dentro, id_col_puntos])
    n_filas_descartadas = int(df_aforos[id_col_valores].isin(ids_fuera).sum())
    if n_filas_descartadas:
        logger.warning(
            "attach_punto_coords: %d puntos de medida fuera de MADRID_BOUNDARY (%d filas de medidas descartadas)",
            len(ids_fuera), n_filas_descartadas,
        )
    puntos = puntos[dentro].rename(columns={id_col_puntos: id_col_valores})

    df_unido = df_aforos.merge(puntos, on=id_col_valores, how="inner")
    return df_unido, n_filas_descartadas


def _puntos_a_geodataframe(df_aforos: pd.DataFrame) -> gpd.GeoDataFrame:
    """Extrae los puntos únicos (id_punto, geometry) de un df_aforos con coordenadas, en EPSG:4326."""
    id_col = _id_punto_column(df_aforos)
    lon_col, lat_col = _lonlat_columns(df_aforos)
    puntos = df_aforos[[id_col, lon_col, lat_col]].drop_duplicates(id_col)
    return gpd.GeoDataFrame(
        puntos,
        geometry=gpd.points_from_xy(puntos[lon_col], puntos[lat_col]),
        crs="EPSG:4326",
    )


def marcar_equipamientos_cercanos_en_puntos(
    df_puntos_medida: pd.DataFrame, gdf_equipamientos: gpd.GeoDataFrame, radio_m: float = 150.0,
) -> pd.DataFrame:
    """
    Marca cada punto de medida de aforos con un booleano `cerca_{tipo}` por
    tipo de equipamiento (mismo radio/criterio que
    open_data_madrid.marcar_equipamientos_cercanos, aquí aplicado a puntos
    en vez de aristas — se reutiliza la misma función, ya que un Point es su
    propio centroide).

    Pensado para persistir junto a `aforos_por_sensor` (ver
    load/save_artifacts.py::save_aforos_por_sensor): un futuro modelo de
    congestión por calle entrenaría con estas columnas + la intensidad real
    de esa tabla, y luego se aplicaría a todas las aristas del callejero
    (incluidas las que no tienen sensor).
    """
    id_col = _id_punto_column(df_puntos_medida)
    gdf_puntos = _puntos_a_geodataframe(df_puntos_medida).to_crs(epsg=CRS_PROJECTED)
    gdf_puntos = marcar_equipamientos_cercanos(gdf_puntos, gdf_equipamientos, radio_m)

    cols_cerca = [c for c in gdf_puntos.columns if c.startswith("cerca_")]
    return df_puntos_medida.merge(
        pd.DataFrame(gdf_puntos[[id_col, *cols_cerca]]), on=id_col, how="left",
    )


def merge_aforos(
    gdf_edges: gpd.GeoDataFrame,
    df_aforos: pd.DataFrame,
    radio_m: float = 200.0,
) -> gpd.GeoDataFrame:
    """
    Asocia cada punto de aforo a la arista más cercana dentro de `radio_m` y
    agrega intensidad media/mediana por arista. `df_aforos` debe incluir
    coordenadas (ver attach_punto_coords).

    Aristas sin punto cercano quedan con NaN, no 0 ni un valor estimado: a
    diferencia de meteorología (completar_meteorologia_zonas), aquí no hay
    ningún sensor cercano del que "tomar prestado" un valor, y el tráfico
    varía demasiado calle a calle como para rellenar con la media de la
    zona sin sesgar al motor de rutas (P4) hacia congestión inventada.
    """
    _requerir_crs_proyectado(gdf_edges)

    id_col = _id_punto_column(df_aforos)
    gdf_puntos = _puntos_a_geodataframe(df_aforos).to_crs(epsg=CRS_PROJECTED)

    gdf_edges = gdf_edges.reset_index(drop=True)
    edge_coords = np.array([[g.x, g.y] for g in gdf_edges.geometry.centroid])
    tree = cKDTree(edge_coords)

    punto_coords = np.array([[g.x, g.y] for g in gdf_puntos.geometry])
    dist, idx = tree.query(punto_coords, distance_upper_bound=radio_m)

    valido = np.isfinite(dist)
    gdf_puntos = gdf_puntos[valido].copy()
    gdf_puntos["edge_idx"] = idx[valido]

    id_a_edge = dict(zip(gdf_puntos[id_col], gdf_puntos["edge_idx"]))
    df_aforos = df_aforos.copy()
    df_aforos["edge_idx"] = df_aforos[id_col].map(id_a_edge)

    agg = (
        df_aforos.dropna(subset=["edge_idx"])
        .groupby("edge_idx")["intensidad"]
        .agg(intensidad_media="mean", intensidad_mediana="median")
    )

    gdf_edges = gdf_edges.copy()
    gdf_edges["intensidad_media"] = np.nan
    gdf_edges["intensidad_mediana"] = np.nan
    gdf_edges.loc[agg.index.astype(int), "intensidad_media"] = agg["intensidad_media"].values
    gdf_edges.loc[agg.index.astype(int), "intensidad_mediana"] = agg["intensidad_mediana"].values

    logger.info(
        "merge_aforos: %d/%d aristas con al menos un punto de aforo a <= %.0f m",
        gdf_edges["intensidad_media"].notna().sum(), len(gdf_edges), radio_m,
    )
    return gdf_edges


def build_aforos_zonas(df_aforos: pd.DataFrame, gdf_distritos: gpd.GeoDataFrame) -> pd.DataFrame:
    """
    Asigna distrito a cada punto de aforo y agrega intensidad media por
    (zona, fecha, hora). `df_aforos` debe incluir coordenadas (ver
    attach_punto_coords). Esta es la tabla que consume
    ml/generate_dataset.py::build_from_aforos.

    No se agrega `vmed` (velocidad media) aunque venga en `df_aforos`: solo
    la miden los sensores M-30 (~6.5% del catálogo, confirmado con datos
    reales — el resto son URB y reportan 0.0 en vez de NaN, no "no aplica").
    Promediarlo por zona diluiría casi toda la señal con ceros falsos de
    sensores sin velocímetro, y 8 de los 21 distritos (incluido Centro) no
    tienen ningún sensor M-30 cerca.
    """
    if gdf_distritos.crs is None:
        raise ValueError("gdf_distritos no tiene CRS definido.")
    if gdf_distritos.crs.to_epsg() != CRS_PROJECTED:
        gdf_distritos = gdf_distritos.to_crs(epsg=CRS_PROJECTED)

    id_col = _id_punto_column(df_aforos)
    gdf_puntos = _puntos_a_geodataframe(df_aforos).to_crs(epsg=CRS_PROJECTED)

    joined = gpd.sjoin(
        gdf_puntos[[id_col, "geometry"]],
        gdf_distritos[["geometry", "nombre"]],
        how="left",
        predicate="within",
    )
    id_a_zona = dict(zip(joined[id_col], joined["nombre"]))

    df = df_aforos.copy()
    df["zona"] = df[id_col].map(id_a_zona).fillna("Desconocida")

    agregaciones = {"intensidad": "mean"}
    renombres = {"intensidad": "intensidad_media"}
    # ocupacion (% de vía ocupada) es mejor proxy de congestión que la
    # intensidad bruta — un tramo puede tener intensidad alta circulando
    # fluido, o intensidad baja y estar completamente parado.
    if "ocupacion" in df.columns:
        agregaciones["ocupacion"] = "mean"
        renombres["ocupacion"] = "ocupacion_media"

    resultado = (
        df.groupby(["zona", "fecha", "hora"])
        .agg(agregaciones)
        .reset_index()
        .rename(columns=renombres)
    )
    logger.info("build_aforos_zonas: %d filas (zona, fecha, hora)", len(resultado))
    return resultado


def procesar_aforos_para_zonas(
    gdf_edges: gpd.GeoDataFrame, gdf_distritos: gpd.GeoDataFrame,
    df_aforos_limpio: pd.DataFrame, df_puntos_medida: pd.DataFrame, stats_aforos: dict,
    radio_m: float = 200.0,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """
    Combina las piezas que necesita el pipeline para incorporar aforos:
    une coordenadas (attach_punto_coords) y agrega por zona
    (build_aforos_zonas) — ajustando `stats_aforos` in-place cuando
    attach_punto_coords descarta sensores fuera de Madrid (ver
    descontar_fuera_limite).

    También llama a merge_aforos para loguear la cobertura por arista
    (informativo), pero no persiste `intensidad_media`/`intensidad_mediana`
    en el callejero: con solo ~6% de aristas con un sensor a menos de
    `radio_m`, esa columna sería casi toda NaN y nada la consume todavía
    (el motor de rutas usa tráfico por zona, no por arista) — ver
    "Gestión de valores nulos" en ETL.md.
    """
    df_aforos_geo, n_aforos_fuera_limite = attach_punto_coords(df_aforos_limpio, df_puntos_medida)
    descontar_fuera_limite(stats_aforos, n_aforos_fuera_limite)
    merge_aforos(gdf_edges, df_aforos_geo, radio_m=radio_m)
    df_aforos_zonas = build_aforos_zonas(df_aforos_geo, gdf_distritos)
    return gdf_edges, df_aforos_zonas
