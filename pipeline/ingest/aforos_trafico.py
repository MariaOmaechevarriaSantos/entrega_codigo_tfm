"""
Descarga los aforos de tráfico históricos desde Open Data Madrid.
Los aforos son conteos de vehículos por punto de medición, hora y día.
Fuente: https://datos.madrid.es (sección Tráfico)

El dataset histórico (208627-0-transporte-ptomedida-historico) se descubre
vía el endpoint CKAN estándar `action/package_show`: cada mes es un recurso
ZIP independiente que contiene un único CSV a resolución de 15 minutos
(~700-800 MB sin comprimir por mes). Para mantener el pipeline manejable,
cada mes se agrega a resolución horaria por punto de medida durante la
propia descarga (streaming, sin cargar el CSV completo en memoria) — la
resolución horaria es también la que consume
`ml/generate_dataset.py`/`build_aforos_zonas` aguas abajo.

Las coordenadas de los puntos de medida vienen en un dataset aparte
(202468-0-intensidad-trafico, "Ubicación de los puntos de medida").
"""
import io
import logging
import re
import zipfile

import pandas as pd
import requests

logger = logging.getLogger(__name__)

CKAN_PACKAGE_SHOW = "https://datos.madrid.es/api/action/package_show"
AFOROS_PACKAGE_ID = "208627-0-transporte-ptomedida-historico"
PUNTOS_MEDIDA_PACKAGE_ID = "202468-0-intensidad-trafico"

_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_PAT_MES_ANO = re.compile(r"Histórico de datos del tráfico\.\s*(\w+)\s+(\d{4})\s*$")
_PAT_ANO_MES = re.compile(r"desde 2013\.\s*(\d{4})\.\s*(\w+)\s*$")


def _parse_mes_ano(descripcion: str) -> tuple[int, int] | None:
    """Extrae (año, mes) de la descripción de un recurso CKAN, o None si no aplica."""
    m = _PAT_MES_ANO.search(descripcion)
    if m:
        mes_nombre, ano = m.group(1).lower(), int(m.group(2))
        mes = _MESES_ES.get(mes_nombre)
        return (ano, mes) if mes else None
    m = _PAT_ANO_MES.search(descripcion)
    if m:
        ano, mes_nombre = int(m.group(1)), m.group(2).lower()
        mes = _MESES_ES.get(mes_nombre)
        return (ano, mes) if mes else None
    return None


def _fetch_ckan_resources(package_id: str) -> list[dict]:
    resp = requests.get(CKAN_PACKAGE_SHOW, params={"id": package_id}, timeout=30)
    resp.raise_for_status()
    return resp.json()["result"]["resources"]


def _monthly_resource_urls() -> dict[tuple[int, int], str]:
    """Mapea (año, mes) -> URL de descarga del ZIP mensual de aforos."""
    urls = {}
    for r in _fetch_ckan_resources(AFOROS_PACKAGE_ID):
        if r.get("format") != "ZIP":
            continue
        parsed = _parse_mes_ano(r.get("description", ""))
        if parsed:
            urls[parsed] = r["url"]
    return urls


def download_puntos_medida(anos: list[int] = [2024, 2025, 2026]) -> pd.DataFrame:
    """
    Descarga la ubicación de los puntos de medida (id, distrito, lat/lon).
    El portal publica una foto mensual de los puntos vigentes en cada
    momento; un sensor dado de baja entre una foto y otra desaparece de las
    fotos posteriores aunque siga teniendo aforos históricos en `anos`. Para
    no perder esos ids, se descargan y fusionan TODAS las fotos mensuales
    disponibles de `anos` (por defecto los mismos años que
    `download_all_aforos`), quedándose con las coordenadas más recientes
    cuando un mismo id aparece en varias.
    """
    resources = [
        r for r in _fetch_ckan_resources(PUNTOS_MEDIDA_PACKAGE_ID)
        if r.get("format") == "CSV"
    ]
    if not resources:
        raise RuntimeError("No se encontraron recursos CSV de puntos de medida.")
    resources.sort(key=lambda r: r.get("created", ""))

    candidatos = [r for r in resources if any(str(ano) in r.get("description", "") for ano in anos)]
    if not candidatos:
        raise ValueError(f"No hay puntos de medida publicados para {anos}.")

    frames = []
    for resource in candidatos:
        logger.info("Descargando puntos de medida: %s", resource["description"])
        resp = requests.get(resource["url"], timeout=60)
        resp.raise_for_status()
        frame = pd.read_csv(io.BytesIO(resp.content), sep=";", encoding="latin-1")
        frame.columns = [c.strip().lower() for c in frame.columns]
        frames.append(frame)

    df = pd.concat(frames, ignore_index=True)
    id_col = "id" if "id" in df.columns else "id_punto"
    df = df.drop_duplicates(subset=id_col, keep="last")
    logger.info("Puntos de medida: %d filas (fusión de %d fotos)", len(df), len(candidatos))
    return df


def download_aforos_mensual(ano: int, mes: int) -> pd.DataFrame:
    """
    Descarga el ZIP mensual de aforos y devuelve intensidad agregada a
    resolución horaria por punto de medida (id, fecha, hora).
    Procesa el CSV interno (>700 MB) en chunks para no cargarlo entero en memoria.
    """
    urls = _monthly_resource_urls()
    url = urls.get((ano, mes))
    if url is None:
        raise ValueError(f"No se encontró recurso de aforos para {ano}-{mes:02d}.")

    logger.info("Descargando aforos %d-%02d desde %s", ano, mes, url)
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()

    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    csv_name = zf.namelist()[0]

    sumas = None
    with zf.open(csv_name) as f:
        for chunk in pd.read_csv(
            f, sep=";", encoding="latin-1", chunksize=500_000,
            usecols=["id", "fecha", "intensidad", "ocupacion", "carga", "vmed"],
        ):
            chunk["fecha_dt"] = pd.to_datetime(chunk["fecha"])
            chunk["fecha_dia"] = chunk["fecha_dt"].dt.date
            chunk["hora"] = chunk["fecha_dt"].dt.hour
            agg = chunk.groupby(["id", "fecha_dia", "hora"]).agg(
                intensidad_sum=("intensidad", "sum"),
                ocupacion_sum=("ocupacion", "sum"),
                carga_sum=("carga", "sum"),
                vmed_sum=("vmed", "sum"),
                n=("intensidad", "count"),
            ).reset_index()
            sumas = agg if sumas is None else pd.concat([sumas, agg], ignore_index=True)

    if sumas is None or sumas.empty:
        return pd.DataFrame(columns=["id", "fecha", "hora", "intensidad", "ocupacion", "carga", "vmed"])

    sumas = sumas.groupby(["id", "fecha_dia", "hora"]).sum().reset_index()
    df = pd.DataFrame({
        "id": sumas["id"],
        "fecha": sumas["fecha_dia"].astype(str),
        "hora": sumas["hora"],
        "intensidad": sumas["intensidad_sum"] / sumas["n"],
        "ocupacion": sumas["ocupacion_sum"] / sumas["n"],
        "carga": sumas["carga_sum"] / sumas["n"],
        "vmed": sumas["vmed_sum"] / sumas["n"],
    })
    df["ano"] = ano
    df["mes"] = mes
    logger.info("Aforos %d-%02d agregados a horario: %d filas", ano, mes, len(df))
    return df


def download_aforos_anual(ano: int) -> pd.DataFrame:
    """Descarga los 12 meses de un año y concatena, saltando los meses que falten."""
    frames = []
    for mes in range(1, 13):
        try:
            frames.append(download_aforos_mensual(ano, mes))
        except Exception as e:
            logger.error("Error descargando aforos %d-%02d: %s", ano, mes, e)

    if not frames:
        raise RuntimeError(f"No se pudo descargar ningún mes de aforos para {ano}.")
    return pd.concat(frames, ignore_index=True)


def download_all_aforos(anos: list[int] = [2024, 2025, 2026]) -> pd.DataFrame:
    """Descarga aforos de `anos` y concatena. No persiste a disco (ver load/save_artifacts.py). No muta `anos`, así que el default compartido es seguro."""
    frames = []
    for ano in anos:
        try:
            frames.append(download_aforos_anual(ano))
        except Exception as e:
            logger.error("Error descargando aforos %d: %s", ano, e)

    if not frames:
        raise RuntimeError("No se pudieron descargar aforos de ningún año.")

    df_all = pd.concat(frames, ignore_index=True)
    logger.info("Aforos consolidados: %d filas", len(df_all))
    return df_all


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    download_all_aforos()
