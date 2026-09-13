"""
Transformaciones sobre la red municipal de meteorología (ver
ingest/meteorologia_madrid.py): limpieza/reshape del formato ancho y
agregación por zona.
"""
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from pipeline.transform.common import (
    CRS_PROJECTED, _stats, _validar_columnas, _avisar_baja_cobertura, _requerir_crs_proyectado,
)
from pipeline.ingest.meteorologia_madrid import MAGNITUDES

logger = logging.getLogger(__name__)


def _deduplicar_filas_ancho(df_ancho: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Deduplica por (ESTACION, MAGNITUD, ANO, MES, DIA) — el portal puede solapar
    el mes ya cerrado con el acumulado "Desde {año}". Devuelve horas descartadas (×24)."""
    n_antes = len(df_ancho)
    df_ancho = df_ancho.drop_duplicates(subset=["ESTACION", "MAGNITUD", "ANO", "MES", "DIA"]).copy()
    n_duplicados = (n_antes - len(df_ancho)) * 24
    return df_ancho, n_duplicados


def _filtrar_magnitudes_conocidas(df_ancho: pd.DataFrame, magnitudes: dict) -> tuple[pd.DataFrame, int]:
    """Mapea el código MAGNITUD a su nombre y descarta filas con un código no reconocido."""
    df_ancho = df_ancho.copy()
    df_ancho["magnitud"] = df_ancho["MAGNITUD"].map(magnitudes)
    n_desconocida = int(df_ancho["magnitud"].isna().sum()) * 24
    df_ancho = df_ancho[df_ancho["magnitud"].notna()].copy()
    return df_ancho, n_desconocida


def _construir_fecha(df_ancho: pd.DataFrame) -> pd.DataFrame:
    """Añade la columna 'fecha' (YYYY-MM-DD) a partir de ANO/MES/DIA."""
    df_ancho = df_ancho.copy()
    df_ancho["fecha"] = pd.to_datetime(
        df_ancho[["ANO", "MES", "DIA"]].rename(columns={"ANO": "year", "MES": "month", "DIA": "day"})
    ).dt.strftime("%Y-%m-%d")
    return df_ancho


def _ancho_a_largo(df_ancho: pd.DataFrame, horas_cols: list[str], validez_cols: list[str]) -> pd.DataFrame:
    """
    Convierte de formato ancho (H01..H24/V01..V24 por fila) a largo (una fila
    por hora), con columnas estacion/magnitud/fecha/hora/valor/valido. Usa un
    índice de fila explícito (_row) para cruzar valores y validez sin
    depender del orden implícito de dos `melt` separados.
    """
    df_ancho = df_ancho.reset_index(drop=True)
    df_ancho["_row"] = df_ancho.index

    valores = df_ancho.melt(
        id_vars=["_row", "ESTACION", "magnitud", "fecha"], value_vars=horas_cols,
        var_name="H", value_name="valor",
    )
    valores["hora"] = valores["H"].str[1:].astype(int) - 1

    validez = df_ancho.melt(id_vars=["_row"], value_vars=validez_cols, var_name="V", value_name="valido")
    validez["hora"] = validez["V"].str[1:].astype(int) - 1

    return valores.merge(validez[["_row", "hora", "valido"]], on=["_row", "hora"], how="left")


def _filtrar_horas_validas(df_largo: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """
    Descarta horas marcadas inválidas por el proveedor (V != 'V'). Confirmado
    en la documentación oficial ("Contenido y estructura del fichero",
    datos.madrid.es): "ÚNICAMENTE SON VÁLIDOS LOS DATOS QUE LLEVAN EL CÓDIGO
    DE VALIDACIÓN 'V'".
    """
    n_invalidas = int((df_largo["valido"] != "V").sum())
    df_validas = df_largo[df_largo["valido"] == "V"].rename(columns={"ESTACION": "estacion"})
    return df_validas, n_invalidas


def _pivotar_a_columnas(df_largo: pd.DataFrame) -> pd.DataFrame:
    """Pivota la columna 'magnitud' a columnas con nombre (temperatura, precipitacion...)."""
    df_ancho = df_largo.pivot_table(
        index=["estacion", "fecha", "hora"], columns="magnitud", values="valor", aggfunc="first"
    ).reset_index()
    df_ancho.columns.name = None
    return df_ancho


def calcular_completitud_magnitudes(df_limpio: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula, para cada magnitud (columna), qué porcentaje de filas tiene un
    valor real (no NaN). No todas las estaciones miden las 7 magnitudes (ej.
    Plaza España solo mide temperatura) — un % bajo aquí no es un fallo de
    limpieza, es la cobertura real de sensores de la red, y quien entrene el
    modelo con esto (P3) debería saberlo antes de asumir que una magnitud
    con muchos NaN es "un problema".

    Retorna un DataFrame (magnitud, pct_con_dato), ordenado de peor a mejor.
    """
    columnas_magnitud = [c for c in df_limpio.columns if c not in ("estacion", "fecha", "hora")]
    resultado = pd.DataFrame({
        "magnitud": columnas_magnitud,
        "pct_con_dato": [round(df_limpio[c].notna().mean() * 100, 1) for c in columnas_magnitud],
    })
    return resultado.sort_values("pct_con_dato").reset_index(drop=True)


def clean_meteorologia(df_ancho: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Limpia y normaliza los datos horarios de la red municipal de
    meteorología (formato ancho de datos.madrid.es, ver
    ingest/meteorologia_madrid.py) — dedup, reshape a largo, descarte de
    horas inválidas y pivote final a columnas con nombre. Cada paso está en
    su propio helper (ver comentarios numerados más abajo). Retorna
    columnas (estacion, fecha, hora, + una por magnitud presente).
    """
    horas_cols = [f"H{h:02d}" for h in range(1, 25)]
    validez_cols = [f"V{h:02d}" for h in range(1, 25)]

    # 0. Falla rápido si el portal cambió el esquema de columnas.
    _validar_columnas(
        df_ancho, {"ESTACION", "MAGNITUD", "ANO", "MES", "DIA", *horas_cols, *validez_cols}, "meteorologia_madrid",
    )

    n_input = len(df_ancho) * 24

    # 1. Dedup por (ESTACION, MAGNITUD, ANO, MES, DIA).
    df_ancho, n_duplicados = _deduplicar_filas_ancho(df_ancho)
    # 2. Descartar códigos MAGNITUD no reconocidos.
    df_ancho, n_magnitud_desconocida = _filtrar_magnitudes_conocidas(df_ancho, MAGNITUDES)
    # 3. ANO/MES/DIA -> columna 'fecha'.
    df_ancho = _construir_fecha(df_ancho)

    # 4. Reshape ancho (H01..H24/V01..V24) -> largo (una fila por hora).
    df_largo = _ancho_a_largo(df_ancho, horas_cols, validez_cols)
    # 5. Descartar horas inválidas (V != 'V').
    df_largo, n_horas_invalidas = _filtrar_horas_validas(df_largo)
    n_nulos = n_magnitud_desconocida + n_horas_invalidas

    if df_largo.empty:
        stats = _stats(n_input, 0, n_nulos, 0, n_duplicados)
        logger.warning("clean_meteorologia: %s", stats)
        return pd.DataFrame(columns=["estacion", "fecha", "hora", *MAGNITUDES.values()]), stats

    # 6. MAGNITUD -> columnas con nombre.
    df_limpio = _pivotar_a_columnas(df_largo)

    stats = _stats(n_input, len(df_limpio), n_nulos, 0, n_duplicados)
    logger.info("clean_meteorologia: %s", stats)
    _avisar_baja_cobertura(df_limpio, id_col="estacion", fuente="clean_meteorologia")

    completitud = calcular_completitud_magnitudes(df_limpio)
    logger.info(
        "clean_meteorologia: %% de filas con dato real por magnitud:\n%s",
        completitud.to_string(index=False),
    )
    return df_limpio, stats


# Magnitudes que se excluyen de la agregación por zona: con solo 26
# estaciones para 21 distritos y "estación más cercana" (no "la que cae
# dentro"), la mayoría de distritos se quedan sin ningún dato real para
# estas 5. Confirmado con datos reales vía calcular_completitud_magnitudes
# + agregación por zona: 13-15 de los 21 distritos con 0% de cobertura para
# cada una. Solo temperatura y humedad_relativa tienen cobertura útil en
# (casi) toda la ciudad — mismo criterio que ya se aplicó a `vmed` en
# aforos_trafico.py::build_aforos_zonas (mejor no exponer una columna a
# medias que rellenarla de NaN).
MAGNITUDES_SIN_COBERTURA_SUFICIENTE = {
    "precipitacion", "velocidad_viento", "direccion_viento", "radiacion_solar", "presion_barometrica",
}


def build_meteorologia_zonas(
    df_meteo: pd.DataFrame, df_estaciones: pd.DataFrame, gdf_distritos: gpd.GeoDataFrame
) -> pd.DataFrame:
    """
    Asigna cada distrito, **por separado para cada magnitud**, a la estación
    más cercana que de verdad la mida (no "la que cae dentro" — con 26
    estaciones para 21 distritos, la mayoría no contiene ninguna). Es por
    magnitud y no una única estación para toda la zona porque la más
    cercana en general puede no medir una magnitud concreta aunque otra
    algo más lejana sí (confirmado con datos reales: Latina se quedaba al
    100% NaN en humedad_relativa por esto). Descarta las magnitudes en
    MAGNITUDES_SIN_COBERTURA_SUFICIENTE. `df_meteo` es la salida de
    clean_meteorologia; `df_estaciones`, la de download_estaciones.
    """
    if gdf_distritos.crs is None:
        raise ValueError("gdf_distritos no tiene CRS definido.")
    if gdf_distritos.crs.to_epsg() != CRS_PROJECTED:
        gdf_distritos = gdf_distritos.to_crs(epsg=CRS_PROJECTED)

    gdf_estaciones = gpd.GeoDataFrame(
        df_estaciones,
        geometry=gpd.points_from_xy(df_estaciones["LONGITUD"], df_estaciones["LATITUD"]),
        crs="EPSG:4326",
    ).to_crs(epsg=CRS_PROJECTED)
    gdf_estaciones["estacion"] = gdf_estaciones["CÓDIGO_CORTO"].astype(str)

    centroides_distritos = np.array([[g.x, g.y] for g in gdf_distritos.geometry.centroid])
    zonas = gdf_distritos["nombre"].values

    df_meteo = df_meteo.copy()
    df_meteo["estacion"] = df_meteo["estacion"].astype(str)
    df_meteo = df_meteo.drop(columns=[c for c in MAGNITUDES_SIN_COBERTURA_SUFICIENTE if c in df_meteo.columns])
    magnitudes = [c for c in df_meteo.columns if c not in ("estacion", "fecha", "hora")]

    resultado = None
    for magnitud in magnitudes:
        estaciones_con_dato = set(df_meteo.loc[df_meteo[magnitud].notna(), "estacion"])
        gdf_candidatas = gdf_estaciones[gdf_estaciones["estacion"].isin(estaciones_con_dato)]
        if gdf_candidatas.empty:
            logger.warning("build_meteorologia_zonas: ninguna estación con dato de %s, se omite.", magnitud)
            continue

        coords_candidatas = np.array([[g.x, g.y] for g in gdf_candidatas.geometry])
        tree = cKDTree(coords_candidatas)
        _, idx = tree.query(centroides_distritos)

        zona_a_estacion = pd.DataFrame({
            "zona": zonas, "estacion": gdf_candidatas["estacion"].iloc[idx].values,
        })
        columna = zona_a_estacion.merge(
            df_meteo[["estacion", "fecha", "hora", magnitud]], on="estacion", how="left"
        ).drop(columns=["estacion"])

        resultado = columna if resultado is None else resultado.merge(
            columna, on=["zona", "fecha", "hora"], how="outer"
        )

    if resultado is None:
        raise RuntimeError("build_meteorologia_zonas: ninguna magnitud tiene estaciones con dato.")

    logger.info("build_meteorologia_zonas: %d filas (zona, fecha, hora), magnitudes: %s", len(resultado), magnitudes)
    return resultado


def _interpolar_serie_huecos_cortos(serie: pd.Series, max_horas: int) -> pd.Series:
    """Interpola linealmente solo los huecos de NaN consecutivos de tamaño
    <= max_horas; los huecos más largos se dejan intactos (rellenarlos ya
    sería inventar, no interpolar)."""
    es_nan = serie.isna()
    grupo = (es_nan != es_nan.shift()).cumsum()
    tam_hueco = es_nan.groupby(grupo).transform("size")
    # limit_area="inside" evita extrapolar antes del primer dato real o
    # después del último — ahí no hay dos vecinos reales entre los que interpolar.
    interpolada = serie.interpolate(method="linear", limit_area="inside")

    resultado = serie.copy()
    mask_rellenar = es_nan & (tam_hueco <= max_horas)
    resultado[mask_rellenar] = interpolada[mask_rellenar]
    return resultado


def rellenar_huecos_cortos(
    df_zonas: pd.DataFrame, columnas: list[str], max_horas: int = 3
) -> pd.DataFrame:
    """
    Capa 2 de la estrategia de nulos (ver build_meteorologia_zonas, que
    resuelve la Capa 1: huecos estructurales por falta de cobertura).
    Aquí los huecos son bajas puntuales de sensor — la estación asignada a
    la zona existe y mide esa magnitud, solo le faltan unas pocas horas
    seguidas. Se rellenan por interpolación lineal temporal, zona a zona,
    asumiendo que temperatura/humedad no dan saltos bruscos hora a hora.
    Huecos de más de max_horas horas seguidas se dejan como NaN.
    """
    df_zonas = df_zonas.copy()
    orden_temporal = pd.to_datetime(df_zonas["fecha"]) + pd.to_timedelta(df_zonas["hora"], unit="h")
    df_zonas = df_zonas.assign(_orden=orden_temporal).sort_values(["zona", "_orden"])

    for col in columnas:
        df_zonas[col] = df_zonas.groupby("zona")[col].transform(
            lambda s: _interpolar_serie_huecos_cortos(s, max_horas)
        )

    return df_zonas.drop(columns=["_orden"]).sort_index()


def rellenar_huecos_largos(
    df_zonas: pd.DataFrame, gdf_distritos: gpd.GeoDataFrame, columnas: list[str],
    k_zonas_cercanas: int = 3,
) -> pd.DataFrame:
    """
    Capa 3: para los huecos que sobreviven a rellenar_huecos_cortos (cortes
    largos, la estación lleva horas/días sin reportar), rellena con la media
    de las k zonas más cercanas en ese mismo (fecha, hora) — el tiempo está
    correlacionado espacialmente, así que esto conserva mejor un evento real
    (ola de calor) que una media histórica o que la media de toda la ciudad.
    Si ni las k más cercanas tienen dato, cae a la media histórica de la
    propia zona para esa hora, y por último a la media global de la
    columna — garantiza cero nulos mientras exista algún dato real.
    """
    if gdf_distritos.crs is None:
        raise ValueError("gdf_distritos no tiene CRS definido.")
    if gdf_distritos.crs.to_epsg() != CRS_PROJECTED:
        gdf_distritos = gdf_distritos.to_crs(epsg=CRS_PROJECTED)

    zonas = gdf_distritos["nombre"].tolist()
    centroides = np.array([[g.x, g.y] for g in gdf_distritos.geometry.centroid])
    tree = cKDTree(centroides)
    k = min(k_zonas_cercanas + 1, len(zonas))  # +1 porque la más cercana a sí misma es ella misma
    _, idx = tree.query(centroides, k=k)
    idx = np.atleast_2d(idx)
    vecinas_por_zona = {
        zonas[i]: [zonas[j] for j in idx[i] if j != i][:k_zonas_cercanas] for i in range(len(zonas))
    }

    df_zonas = df_zonas.copy()
    for col in columnas:
        wide = df_zonas.pivot(index=["fecha", "hora"], columns="zona", values=col)
        for zona in wide.columns:
            vecinas = [z for z in vecinas_por_zona.get(zona, []) if z in wide.columns]
            if vecinas:
                media_vecinas = wide[vecinas].mean(axis=1, skipna=True)
                wide[zona] = wide[zona].fillna(media_vecinas)

        wide = wide.fillna(wide.groupby(level="hora").transform("mean"))
        wide = wide.fillna(wide.stack().mean())

        filled = wide.stack()
        filled.index = filled.index.set_names(["fecha", "hora", "zona"])
        df_zonas = df_zonas.set_index(["fecha", "hora", "zona"])
        df_zonas[col] = filled.reindex(df_zonas.index)
        df_zonas = df_zonas.reset_index()

    return df_zonas


def completar_meteorologia_zonas(
    df_zonas: pd.DataFrame, gdf_distritos: gpd.GeoDataFrame, columnas: list[str] | None = None,
    max_horas: int = 3, k_zonas_cercanas: int = 3,
) -> pd.DataFrame:
    """
    Aplica las Capas 2 y 3 (rellenar_huecos_cortos/largos) sobre la salida
    de build_meteorologia_zonas y garantiza cero NaN en `columnas` (None =
    todas las magnitudes presentes salvo zona/fecha/hora).
    """
    if columnas is None:
        columnas = [c for c in df_zonas.columns if c not in ("zona", "fecha", "hora")]

    df_zonas = df_zonas.copy()
    df_zonas = rellenar_huecos_cortos(df_zonas, columnas, max_horas=max_horas)
    df_zonas = rellenar_huecos_largos(df_zonas, gdf_distritos, columnas, k_zonas_cercanas=k_zonas_cercanas)

    for col in columnas:
        if df_zonas[col].isna().any():
            raise RuntimeError(f"completar_meteorologia_zonas: quedan NaN en '{col}' tras aplicar las 3 capas.")

    return df_zonas


def merge_meteorologia_actual(
    gdf_edges: gpd.GeoDataFrame, df_meteo_actual: pd.DataFrame, k_estaciones_cercanas: int = 3,
) -> gpd.GeoDataFrame:
    """
    Asigna a cada arista el clima AHORA MISMO (ver
    ingest/meteorologia_madrid.py::fetch_meteorologia_actual), por magnitud:
    la media de las k estaciones activas más cercanas que reportan esa
    magnitud en concreto, no una única estación para las 7 (equivalente a
    las Capas 1+3 del batch — ver build_meteorologia_zonas/
    completar_meteorologia_zonas; la Capa 2 no aplica, aquí solo hay una
    lectura por estación, no una serie temporal que interpolar). Mismo
    filtro de magnitudes que el batch (solo temperatura/humedad_relativa,
    las únicas que el modelo conoce). Si ninguna estación activa reporta una
    magnitud, esa columna se omite en vez de dejarla en NaN.
    """
    _requerir_crs_proyectado(gdf_edges)

    gdf_edges = gdf_edges.reset_index(drop=True)
    if df_meteo_actual.empty:
        return gdf_edges

    gdf_estaciones = gpd.GeoDataFrame(
        df_meteo_actual,
        geometry=gpd.points_from_xy(df_meteo_actual["lon"], df_meteo_actual["lat"]),
        crs="EPSG:4326",
    ).to_crs(epsg=CRS_PROJECTED)

    edge_coords = np.array([[g.x, g.y] for g in gdf_edges.geometry.centroid])
    columnas_magnitud = [
        c for c in df_meteo_actual.columns
        if c not in ("estacion", "nombre", "lon", "lat", "hora") and c not in MAGNITUDES_SIN_COBERTURA_SUFICIENTE
    ]

    for col in columnas_magnitud:
        gdf_candidatas = gdf_estaciones[gdf_estaciones[col].notna()]
        if gdf_candidatas.empty:
            logger.warning("merge_meteorologia_actual: ninguna estación activa reporta %s, se omite.", col)
            continue
        coords_candidatas = np.array([[g.x, g.y] for g in gdf_candidatas.geometry])
        k = min(k_estaciones_cercanas, len(gdf_candidatas))
        tree = cKDTree(coords_candidatas)
        _, idx = tree.query(edge_coords, k=k)
        if k == 1:
            idx = idx.reshape(-1, 1)
        gdf_edges[col] = gdf_candidatas[col].values[idx].mean(axis=1)

    logger.info(
        "merge_meteorologia_actual: clima asignado a %d aristas desde %d estaciones activas",
        len(gdf_edges), len(df_meteo_actual),
    )
    return gdf_edges
