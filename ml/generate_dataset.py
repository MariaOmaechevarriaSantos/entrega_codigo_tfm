"""
Construye el dataset de entrenamiento para el modelo de predicción de tráfico.
Fuente principal: aforos históricos de Open Data Madrid.
Si los datos reales no están disponibles, genera un dataset sintético.
"""
import logging
import os

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "../data/processed")
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "aforos_dataset.csv")

# 21 distritos de Madrid
DISTRITOS_MADRID = [
    "Centro", "Arganzuela", "Retiro", "Salamanca", "Chamartin",
    "Tetuan", "Chamberi", "Fuencarral-El Pardo", "Moncloa-Aravaca",
    "Latina", "Carabanchel", "Usera", "Puente de Vallecas", "Moratalaz",
    "Ciudad Lineal", "Hortaleza", "Villaverde", "Villa de Vallecas",
    "Vicalvaro", "San Blas-Canillejas", "Barajas",
]

FESTIVOS_MADRID = [
    "01-01", "06-01", "15-05", "02-05", "25-07", "15-08",
    "12-10", "01-11", "06-12", "08-12", "25-12",
]


def _is_festivo(fecha_str: str) -> int:
    mm_dd = fecha_str[5:]
    return 1 if mm_dd in FESTIVOS_MADRID else 0


def _classify_traffic(intensidad: float, hora: int) -> int:
    """
    Clasifica nivel de tráfico: 0=Bajo, 1=Medio, 2=Alto.
    Basado en percentiles de intensidad por hora (heurística inicial).
    """
    if hora in range(1, 7):  # madrugada
        return 0
    if hora in range(7, 10) or hora in range(17, 21):  # horas punta
        if intensidad > 1500:
            return 2
        elif intensidad > 800:
            return 1
        return 0
    if intensidad > 1000:
        return 2
    elif intensidad > 500:
        return 1
    return 0


def build_from_aforos(aforos_path: str) -> pd.DataFrame:
    """
    Construye dataset desde CSV de aforos reales de Open Data Madrid.
    El CSV debe tener al menos: fecha, hora, intensidad, id_punto.
    """
    df = pd.read_csv(aforos_path, low_memory=False)
    logger.info("Aforos cargados: %d filas, columnas: %s", len(df), list(df.columns))
    # TODO: adaptar nombres de columnas al formato real del portal Madrid
    raise NotImplementedError("Implementar cuando los datos reales estén disponibles.")


def build_synthetic(n_samples: int = 50_000) -> pd.DataFrame:
    """
    Genera dataset sintético como proxy hasta tener aforos reales.
    Genera patrones realistas basados en calendario y franja horaria.
    """
    logger.warning("Generando dataset SINTÉTICO. Reemplazar por aforos reales.")
    rng = np.random.default_rng(42)

    fechas = pd.date_range("2022-01-01", "2024-12-31", freq="h")
    idx = rng.integers(0, len(fechas), n_samples)
    fechas_sample = fechas[idx]

    df = pd.DataFrame({
        "fecha": fechas_sample.strftime("%Y-%m-%d"),
        "hora": fechas_sample.hour,
        "dia_semana": fechas_sample.dayofweek,
        "mes": fechas_sample.month,
        "es_fin_de_semana": (fechas_sample.dayofweek >= 5).astype(int),
        "distrito": rng.choice(DISTRITOS_MADRID, n_samples),
    })
    df["es_festivo"] = df["fecha"].apply(_is_festivo)
    df["intensidad_simulada"] = (
        500
        + 800 * np.sin(2 * np.pi * df["hora"] / 24)
        + rng.normal(0, 150, n_samples)
        + df["es_fin_de_semana"] * (-300)
        + df["es_festivo"] * (-400)
    ).clip(0, 3000)
    df["nivel_trafico"] = df.apply(
        lambda r: _classify_traffic(r["intensidad_simulada"], r["hora"]), axis=1
    )
    df = df.drop(columns=["intensidad_simulada", "fecha"])
    return df


def generate_and_save(use_real: bool = False, aforos_path: str | None = None) -> pd.DataFrame:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if use_real and aforos_path:
        df = build_from_aforos(aforos_path)
    else:
        df = build_synthetic()

    df.to_csv(OUTPUT_FILE, index=False)
    logger.info("Dataset guardado: %s (%d filas)", OUTPUT_FILE, len(df))
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    generate_and_save()
