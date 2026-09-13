"""
Wrapper productivo de inferencia de tráfico real, sobre el modelo XGBoost
entrenado en el notebook 05_modelo_xgboost_densidad_trafico.ipynb (P3).

No sustituye a ml/predict.py (modelo sintético viejo, se mantiene aparte
hasta que el equipo decida retirarlo explícitamente; ningún endpoint lo
invoca — ver docs/p5/README_P5_API_Streamlit.md §4 y docs/adr/0008).
"""
import json
import logging
import os

import joblib
import pandas as pd

logger = logging.getLogger(__name__)

PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "../data/processed")
MODEL_PATH = os.path.join(PROCESSED_DIR, "modelo_trafico_xgboost.pkl")
METADATA_PATH = os.path.join(PROCESSED_DIR, "modelo_trafico_xgboost_metadata.json")

# Misma lista que add_calendar_features del notebook 05 y ml/generate_dataset.py.
FESTIVOS_MADRID = {
    "01-01", "06-01", "15-05", "02-05", "25-07", "15-08",
    "12-10", "01-11", "06-12", "08-12", "25-12",
}

_model = None
_metadata = None
_equip_pivot = None  # no cambia entre requests — se cachea en memoria tras la primera carga


def _load_model_and_metadata() -> None:
    """Carga el modelo y su metadata en caché de módulo. Falla explícito si algo
    falta, o si la metadata indica una reentrena que este wrapper no soporta."""
    global _model, _metadata
    if _model is not None:
        return

    if not os.path.exists(MODEL_PATH):
        raise FileNotFoundError(
            f"Modelo no encontrado en {MODEL_PATH}. "
            "Ejecuta notebooks/05_modelo_xgboost_densidad_trafico.ipynb para generarlo."
        )
    if not os.path.exists(METADATA_PATH):
        raise FileNotFoundError(
            f"Metadata no encontrada en {METADATA_PATH}. "
            "Ejecuta notebooks/05_modelo_xgboost_densidad_trafico.ipynb para generarla."
        )

    with open(METADATA_PATH, encoding="utf-8") as f:
        metadata = json.load(f)

    if metadata.get("use_weather_features"):
        raise ValueError(
            "metadata['use_weather_features'] es True: este wrapper no obtiene "
            "meteorología en vivo. Actualiza predict_trafico_real.py antes de servir "
            "este modelo en producción (ver docs/adr/0003-meteorologia-fuera-del-modelo.md)."
        )
    if metadata.get("use_lag_features"):
        raise ValueError(
            "metadata['use_lag_features'] es True: este wrapper no tiene acceso a "
            "tráfico observado reciente. Actualiza predict_trafico_real.py antes de "
            "servir este modelo en producción (ver docs/adr/0003-meteorologia-fuera-del-modelo.md)."
        )

    _model = joblib.load(MODEL_PATH)
    _metadata = metadata
    logger.info("Modelo de tráfico XGBoost cargado desde %s", MODEL_PATH)


def _load_equip_pivot() -> pd.DataFrame:
    """
    Pivota equipamientos_por_zona (n_{tipo} por zona) desde DuckDB, igual que
    el notebook 05. Esta dependencia no está en metadata.json — hay que leerla
    en tiempo de carga y cachearla, no cambia entre requests.
    """
    global _equip_pivot
    if _equip_pivot is not None:
        return _equip_pivot

    from pipeline.load.save_artifacts import get_connection

    # Solo lectura: este wrapper hace SELECT sobre equipamientos_por_zona /
    # pipeline_runs, nunca escribe. Una conexión read-only no necesita permiso de
    # escritura sobre el .duckdb, que importa cuando la base la sirve un volumen o
    # imagen de solo lectura (contenedor de la API como usuario no-root).
    con = get_connection(read_only=True)
    try:
        tablas = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
        if "equipamientos_por_zona" not in tablas:
            raise FileNotFoundError(
                "Tabla 'equipamientos_por_zona' no encontrada en DuckDB. "
                "Ejecuta: python pipeline/run_pipeline.py"
            )

        if "pipeline_runs" in tablas:
            query = """
                SELECT zona, tipo, n_equipamientos FROM equipamientos_por_zona
                WHERE run_id = (SELECT run_id FROM pipeline_runs ORDER BY fecha_ejecucion DESC LIMIT 1)
            """
        else:
            query = "SELECT zona, tipo, n_equipamientos FROM equipamientos_por_zona"

        df = con.execute(query).df()
    finally:
        con.close()

    if df.empty:
        _equip_pivot = pd.DataFrame(columns=["zona"])
    else:
        _equip_pivot = (
            df.pivot_table(index="zona", columns="tipo", values="n_equipamientos", aggfunc="sum", fill_value=0)
            .add_prefix("n_")
            .reset_index()
        )
    return _equip_pivot


def _add_calendar_features(df: pd.DataFrame) -> pd.DataFrame:
    """Idéntico a add_calendar_features del notebook 05."""
    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])
    df["hora"] = df["hora"].astype(int)
    df["dia_semana"] = df["fecha"].dt.dayofweek
    df["mes"] = df["fecha"].dt.month
    df["es_fin_de_semana"] = (df["dia_semana"] >= 5).astype(int)
    df["es_festivo"] = df["fecha"].dt.strftime("%m-%d").isin(FESTIVOS_MADRID).astype(int)
    return df


def _crear_features_produccion(fecha: str, hora: int, zonas: list[str]) -> pd.DataFrame:
    """Idéntico en espíritu a crear_features_produccion del notebook 05."""
    feature_cols = _metadata["feature_cols"]
    numeric_features = _metadata["numeric_features"]
    feature_medians = _metadata["feature_medians_train"]

    rows = pd.DataFrame({"zona": zonas})
    rows["fecha"] = fecha
    rows["hora"] = hora
    rows = _add_calendar_features(rows)

    equip_pivot = _load_equip_pivot()
    if len(equip_pivot):
        rows = rows.merge(equip_pivot, on="zona", how="left")

    for col in feature_cols:
        if col not in rows.columns:
            rows[col] = 0

    for col in numeric_features:
        rows[col] = pd.to_numeric(rows[col], errors="coerce").fillna(feature_medians.get(col, 0.0))

    return rows[feature_cols]


def predecir_trafico_real(fecha: str, hora: int, zonas: list[str] | None = None) -> dict[str, int]:
    """
    Devuelve {zona: nivel} (0=Bajo, 1=Medio, 2=Alto) usando el modelo XGBoost
    real de P3. Mismo formato que ya consume calcular_ruta(traffic_preds=...).
    """
    _load_model_and_metadata()

    if zonas is None:
        from ml.generate_dataset import DISTRITOS_MADRID
        zonas = DISTRITOS_MADRID

    X_pred = _crear_features_produccion(fecha, hora, zonas)
    pred = _model.predict(X_pred)
    return dict(zip(zonas, (int(p) for p in pred)))
