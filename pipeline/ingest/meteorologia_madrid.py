"""
Descarga la red municipal de estaciones meteorológicas de Madrid
(datos.madrid.es). Tiene 26 estaciones repartidas por toda la ciudad,
lo que permite asignar clima por zona en vez de un único valor para toda
Madrid.

El portal publica los años ya cerrados mes a mes (un CSV independiente por
mes), y el año en curso en un único recurso acumulado "Desde {año}" que se
va actualizando — por eso `download_meteorologia` combina ambos mecanismos.

Los datos horarios vienen en formato ANCHO — una fila por
(estación, magnitud, día), con columnas H01..H24 (valor por hora) y
V01..V24 (validez por hora), mismo esquema que usa el Ayuntamiento para su
red de calidad del aire.

IMPORTANTE — `fetch_meteorologia_actual()` es distinta al resto: consulta
el feed EN VIVO (observación real, no predicción, actualizado cada ~20 min)
y está pensada para que el routing engine consulte el tiempo real en la estación más
cercana a una ruta en el momento de calcularla, igual que
`incidencias_viapublica.py` para obras. No forma parte del batch de run_pipeline.
"""
import io
import logging
import re

import pandas as pd
import requests

logger = logging.getLogger(__name__)

CKAN_PACKAGE_SHOW = "https://datos.madrid.es/api/action/package_show"
ESTACIONES_PACKAGE_ID = "300360-0-meteorologicos-estaciones"
HORARIOS_PACKAGE_ID = "300352-0-meteorologicos-horarios"
METEO_TIEMPO_REAL_URL = "https://ciudadesabiertas.madrid.es/dynamicAPI/API/query/meteo_tiemporeal.json"

# Código de magnitud (columna MAGNITUD del CSV horario) -> nombre legible.
MAGNITUDES = {
    81: "velocidad_viento",
    82: "direccion_viento",
    83: "temperatura",
    86: "humedad_relativa",
    87: "presion_barometrica",
    88: "radiacion_solar",
    89: "precipitacion",
}

_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_PAT_MES_ANO = re.compile(r"horarios\.\s*(\w+)\s+(\d{4})\s*$")


def _parse_mes_ano(descripcion: str) -> tuple[int, int] | None:
    """Extrae (año, mes) de una descripción tipo 'Datos meteorológicos horarios. Marzo 2024'."""
    m = _PAT_MES_ANO.search(descripcion)
    if not m:
        return None
    mes_nombre, ano = m.group(1).lower(), int(m.group(2))
    mes = _MESES_ES.get(mes_nombre)
    return (ano, mes) if mes else None


def _fetch_ckan_resources(package_id: str) -> list[dict]:
    resp = requests.get(CKAN_PACKAGE_SHOW, params={"id": package_id}, timeout=30)
    resp.raise_for_status()
    return resp.json()["result"]["resources"]


def _download_csv(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return pd.read_csv(io.BytesIO(resp.content), sep=";", encoding="utf-8")


def download_estaciones() -> pd.DataFrame:
    """
    Descarga el catálogo de las 26 estaciones municipales: código, nombre,
    dirección, lat/lon (columnas LATITUD/LONGITUD, WGS84) y qué magnitudes
    mide cada una (no todas miden las 7).
    """
    resources = _fetch_ckan_resources(ESTACIONES_PACKAGE_ID)
    resource = next((r for r in resources if r.get("format") == "CSV"), None)
    if resource is None:
        raise RuntimeError("No se encontró el CSV de estaciones meteorológicas municipales.")
    df = _download_csv(resource["url"])
    logger.info("Estaciones meteorológicas municipales: %d", len(df))
    return df


def download_meteorologia(anos: list[int] = [2024, 2025, 2026]) -> pd.DataFrame:
    """
    Descarga los datos horarios de la red municipal para `anos`, en el
    formato ancho tal como lo publica el portal (una fila por
    estación/magnitud/día). Los años ya cerrados se descargan mes a mes; el
    año en curso se cubre con el recurso acumulado "Desde {año}" (un único
    recurso cubre todos los años sin cerrar, no hace falta repetir la
    búsqueda por año). No muta `anos`, así que el default compartido es seguro.
    """
    resources = _fetch_ckan_resources(HORARIOS_PACKAGE_ID)
    csvs = [r for r in resources if r.get("format") == "CSV"]

    frames = []
    anos_cubiertos = set()
    for r in csvs:
        parsed = _parse_mes_ano(r.get("description", ""))
        if parsed and parsed[0] in anos:
            try:
                frames.append(_download_csv(r["url"]))
                anos_cubiertos.add(parsed[0])
            except Exception as e:
                logger.error("Error descargando meteorología %s: %s", r.get("description"), e)

    if any(ano not in anos_cubiertos for ano in anos):
        bulk = next(
            (r for r in csvs if r.get("description", "").startswith("Datos meteorológicos horarios. Desde")),
            None,
        )
        if bulk is not None:
            try:
                frames.append(_download_csv(bulk["url"]))
            except Exception as e:
                logger.error("Error descargando recurso acumulado '%s': %s", bulk.get("description"), e)
        else:
            logger.warning("No se encontró recurso acumulado para los años sin publicación mensual.")

    if not frames:
        raise RuntimeError("No se pudo descargar ningún dato de la red meteorológica municipal.")

    df_all = pd.concat(frames, ignore_index=True)
    logger.info("Meteorología municipal consolidada: %d filas (formato ancho)", len(df_all))
    return df_all


def _fetch_registros_tiempo_real() -> list[dict]:
    """Descarga los registros crudos del feed en vivo (JSON, uno por estación/magnitud)."""
    resp = requests.get(METEO_TIEMPO_REAL_URL, params={"pageSize": 5000}, timeout=30)
    resp.raise_for_status()
    return resp.json()["records"]


def _extraer_ultima_lectura_valida(registros: list[dict]) -> list[dict]:
    """
    Para cada registro (estación/magnitud), busca la última hora del día
    marcada como válida (V='V') y se queda con su valor. Descarta registros
    con magnitud o valor en formato inesperado en vez de reventar el fetch
    entero por un dato puntual mal formado.
    """
    filas = []
    n_descartados = 0
    for r in registros:
        try:
            nombre_magnitud = MAGNITUDES.get(int(r["MAGNITUD"]))
        except (KeyError, ValueError, TypeError):
            n_descartados += 1
            continue
        if nombre_magnitud is None:
            continue
        for hora in range(24, 0, -1):
            if r.get(f"V{hora:02d}") == "V":
                try:
                    valor = float(r[f"H{hora:02d}"])
                except (KeyError, ValueError, TypeError):
                    n_descartados += 1
                    break
                filas.append({
                    "estacion": r["ESTACION"], "magnitud": nombre_magnitud,
                    "valor": valor, "hora": hora,
                })
                break

    if n_descartados:
        logger.warning("Meteorología en vivo: %d lecturas descartadas por formato inesperado.", n_descartados)
    return filas


def _pivotar_lecturas(filas: list[dict]) -> pd.DataFrame:
    """Pivota las lecturas (largo, una fila por estación/magnitud) a ancho (una fila por estación)."""
    df_largo = pd.DataFrame(filas)
    # pivot_table (no pivot): tolera un duplicado accidental (estación,
    # magnitud) del feed en vez de reventar toda la petición en vivo.
    df_ancho = df_largo.pivot_table(index="estacion", columns="magnitud", values="valor", aggfunc="first").reset_index()
    df_ancho.columns.name = None
    horas_por_estacion = df_largo.groupby("estacion")["hora"].max().reset_index()
    return df_ancho.merge(horas_por_estacion, on="estacion")


def _unir_con_catalogo_estaciones(df_ancho: pd.DataFrame) -> pd.DataFrame:
    """Añade nombre/lon/lat de cada estación desde el catálogo (download_estaciones)."""
    # En el catálogo de estaciones, "ESTACION" es el nombre y "CÓDIGO_CORTO"
    # el código numérico — al revés que en el feed en vivo, donde "ESTACION"
    # ES el código. Se renombra para no confundirlos.
    df_est = download_estaciones().rename(
        columns={"CÓDIGO_CORTO": "estacion", "ESTACION": "nombre", "LONGITUD": "lon", "LATITUD": "lat"}
    )
    df_est["estacion"] = df_est["estacion"].astype(str)
    df_ancho["estacion"] = df_ancho["estacion"].astype(str)
    return df_ancho.merge(df_est[["estacion", "nombre", "lon", "lat"]], on="estacion", how="left")


def fetch_meteorologia_actual() -> pd.DataFrame:
    """
    Descarga el estado meteorológico EN VIVO (observación real, no
    predicción) de las estaciones municipales que están reportando ahora
    mismo. Para cada estación y magnitud, toma el valor de la última hora
    del día con lectura marcada como válida (V='V'; las horas futuras del
    día vienen con V='N' porque aún no han ocurrido).

    Retorna un DataFrame con una fila por estación (código, nombre, lon,
    lat, hora del último dato válido) y una columna por magnitud
    (temperatura, precipitacion, velocidad_viento...).
    """
    registros = _fetch_registros_tiempo_real()
    filas = _extraer_ultima_lectura_valida(registros)

    if not filas:
        logger.warning("Meteorología en vivo: ninguna estación con lectura válida ahora mismo.")
        return pd.DataFrame(columns=["estacion", "nombre", "lon", "lat", "hora", *MAGNITUDES.values()])

    df_ancho = _pivotar_lecturas(filas)
    df_final = _unir_con_catalogo_estaciones(df_ancho)
    logger.info("Meteorología en vivo: %d estaciones con lectura reciente", len(df_final))
    return df_final


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_estaciones()
    download_meteorologia()
