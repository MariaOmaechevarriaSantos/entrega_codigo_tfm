"""
Función de inferencia reutilizable para el modelo de tráfico.
Importada tanto por server.py como por streamlit_app.py.
"""
import logging
import os

import joblib
import pandas as pd

logger = logging.getLogger(__name__)

PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "../data/processed")
MODEL_PATH = os.path.join(PROCESSED_DIR, "modelo_trafico_madrid.pkl")
ENCODER_PATH = os.path.join(PROCESSED_DIR, "encoder_madrid.pkl")

FESTIVOS_MADRID = {
    "01-01", "06-01", "15-05", "02-05", "25-07",
    "15-08", "12-10", "01-11", "06-12", "08-12", "25-12",
}

_model = None
_encoder = None


def _load_artifacts():
    global _model, _encoder
    if _model is None:
        if not os.path.exists(MODEL_PATH):
            raise FileNotFoundError(
                f"Modelo no encontrado en {MODEL_PATH}. Ejecuta: python ml/train_model.py"
            )
        _model = joblib.load(MODEL_PATH)
        _encoder = joblib.load(ENCODER_PATH)
        logger.info("Modelo de tráfico cargado desde %s", MODEL_PATH)


def predecir_trafico(fecha_str: str, hora: int, distritos: list[str]) -> dict[str, int]:
    """
    Predice el nivel de tráfico (0=Bajo, 1=Medio, 2=Alto) para cada distrito
    en una fecha y hora dadas.

    Args:
        fecha_str: Fecha en formato "YYYY-MM-DD"
        hora: Hora del día (0-23)
        distritos: Lista de nombres de distritos de Madrid

    Returns:
        Dict {distrito: nivel_trafico}
    """
    _load_artifacts()
    dt = pd.to_datetime(fecha_str)
    dia_semana = dt.dayofweek
    mes = dt.month
    es_fin_de_semana = 1 if dia_semana >= 5 else 0
    es_festivo = 1 if dt.strftime("%m-%d") in FESTIVOS_MADRID else 0

    result = {}
    for distrito in distritos:
        try:
            distrito_encoded = _encoder.transform([distrito])[0]
        except ValueError:
            distrito_encoded = 0

        row = pd.DataFrame([[hora, dia_semana, mes, es_fin_de_semana, es_festivo, distrito_encoded]],
                           columns=["hora", "dia_semana", "mes", "es_fin_de_semana", "es_festivo", "distrito_encoded"])
        nivel = int(_model.predict(row)[0])
        result[distrito] = nivel

    return result
