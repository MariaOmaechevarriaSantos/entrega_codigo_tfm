"""
Entrena el modelo de predicción de tráfico (Random Forest / XGBoost).
Guarda los artefactos .pkl en data/processed/.

Uso:
    python ml/train_model.py
    python ml/train_model.py --model xgboost
"""
import argparse
import logging
import os

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import LabelEncoder

logger = logging.getLogger(__name__)

PROCESSED_DIR = os.path.join(os.path.dirname(__file__), "../data/processed")
DATASET_PATH = os.path.join(PROCESSED_DIR, "aforos_dataset.csv")
MODEL_OUT = os.path.join(PROCESSED_DIR, "modelo_trafico_madrid.pkl")
ENCODER_OUT = os.path.join(PROCESSED_DIR, "encoder_madrid.pkl")

FEATURES = ["hora", "dia_semana", "mes", "es_fin_de_semana", "es_festivo", "distrito_encoded"]
TARGET = "nivel_trafico"


def load_and_prepare(path: str) -> tuple[pd.DataFrame, LabelEncoder]:
    df = pd.read_csv(path)
    le = LabelEncoder()
    df["distrito_encoded"] = le.fit_transform(df["distrito"].astype(str))
    return df, le


def train_random_forest(X_train, y_train) -> RandomForestClassifier:
    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=5,
        class_weight="balanced",
        n_jobs=-1,
        random_state=42,
    )
    clf.fit(X_train, y_train)
    return clf


def train_xgboost(X_train, y_train):
    try:
        from xgboost import XGBClassifier
    except ImportError:
        raise ImportError("Instala xgboost: pip install xgboost")
    clf = XGBClassifier(
        n_estimators=300,
        max_depth=8,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        eval_metric="mlogloss",
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_train, y_train)
    return clf


def evaluate(clf, X_val, y_val) -> float:
    y_pred = clf.predict(X_val)
    report = classification_report(y_val, y_pred, target_names=["Bajo", "Medio", "Alto"])
    logger.info("Informe de clasificación:\n%s", report)
    return f1_score(y_val, y_pred, average="macro")


def train(model_type: str = "rf") -> None:
    os.makedirs(PROCESSED_DIR, exist_ok=True)

    if not os.path.exists(DATASET_PATH):
        logger.info("Dataset no encontrado, generando sintético...")
        from ml.generate_dataset import generate_and_save
        generate_and_save()

    df, le = load_and_prepare(DATASET_PATH)
    logger.info("Dataset cargado: %d filas, distribución target:\n%s",
                len(df), df[TARGET].value_counts())

    X = df[FEATURES].values
    y = df[TARGET].values

    # Validación cruzada temporal (no mezclar futuro con pasado)
    tscv = TimeSeriesSplit(n_splits=5)
    f1_scores = []
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]
        clf_fold = train_random_forest(X_tr, y_tr) if model_type == "rf" else train_xgboost(X_tr, y_tr)
        f1 = evaluate(clf_fold, X_val, y_val)
        f1_scores.append(f1)
        logger.info("Fold %d — F1-macro: %.4f", fold + 1, f1)

    logger.info("F1-macro medio CV: %.4f ± %.4f", np.mean(f1_scores), np.std(f1_scores))

    # Entrenar modelo final con todos los datos
    logger.info("Entrenando modelo final con todos los datos...")
    final_clf = train_random_forest(X, y) if model_type == "rf" else train_xgboost(X, y)

    joblib.dump(final_clf, MODEL_OUT)
    joblib.dump(le, ENCODER_OUT)
    logger.info("Modelo guardado en %s", MODEL_OUT)
    logger.info("Encoder guardado en %s", ENCODER_OUT)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["rf", "xgboost"], default="rf")
    args = parser.parse_args()
    train(model_type=args.model)
