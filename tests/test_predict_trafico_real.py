"""Tests para el wrapper productivo de tráfico real (§3.3). Usa fixtures
pequeñas (pkl/json de juguete, DuckDB en memoria) — no el modelo real de P3."""
import json
import os

import joblib
import pandas as pd
import pytest
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

import ml.predict_trafico_real as predict_trafico_real
from pipeline.load.save_artifacts import get_connection

FEATURE_COLS = ["zona", "hora", "dia_semana", "mes", "es_fin_de_semana", "es_festivo", "n_hospital"]
CATEGORICAL_FEATURES = ["zona"]
NUMERIC_FEATURES = ["hora", "dia_semana", "mes", "es_fin_de_semana", "es_festivo", "n_hospital"]


def _build_fixture_model(path):
    """Pipeline de juguete (OneHotEncoder + DummyClassifier) con el mismo contrato
    que el modelo real: ColumnTransformer(cat=zona, num=passthrough) + .predict(df)."""
    X = pd.DataFrame({
        "zona": ["Centro", "Retiro", "Centro", "Retiro"],
        "hora": [8, 8, 20, 20],
        "dia_semana": [0, 0, 2, 2],
        "mes": [1, 1, 6, 6],
        "es_fin_de_semana": [0, 0, 0, 0],
        "es_festivo": [0, 0, 0, 0],
        "n_hospital": [1, 0, 1, 0],
    })
    y = [0, 1, 2, 0]
    preprocessor = ColumnTransformer(transformers=[
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ("num", "passthrough", NUMERIC_FEATURES),
    ])
    pipe = Pipeline(steps=[("preprocessor", preprocessor), ("model", DummyClassifier(strategy="constant", constant=1))])
    pipe.fit(X[FEATURE_COLS], y)
    joblib.dump(pipe, path)


def _build_fixture_metadata(path, use_weather_features=False, use_lag_features=False):
    metadata = {
        "feature_cols": FEATURE_COLS,
        "categorical_features": CATEGORICAL_FEATURES,
        "numeric_features": NUMERIC_FEATURES,
        "feature_medians_train": {
            "hora": 12.0, "dia_semana": 3.0, "mes": 6.0,
            "es_fin_de_semana": 0.0, "es_festivo": 0.0, "n_hospital": 0.0,
        },
        "use_weather_features": use_weather_features,
        "use_lag_features": use_lag_features,
        "labels": {"0": "Bajo", "1": "Medio", "2": "Alto"},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(metadata, f)


def _build_fixture_duckdb(db_path, con_incluir_pipeline_runs=True):
    con = get_connection(db_path)
    df_equip = pd.DataFrame({
        "zona": ["Centro", "Centro", "Retiro"],
        "tipo": ["hospital", "colegio", "hospital"],
        "n_equipamientos": [2, 5, 1],
        "run_id": ["run-1", "run-1", "run-1"],
    })
    con.register("df_equip", df_equip)
    con.execute("CREATE TABLE equipamientos_por_zona AS SELECT * FROM df_equip")
    con.unregister("df_equip")
    if con_incluir_pipeline_runs:
        con.execute(
            "CREATE TABLE pipeline_runs AS SELECT 'run-1' AS run_id, "
            "'2025-01-01T00:00:00+00:00' AS fecha_ejecucion"
        )
    con.close()


@pytest.fixture(autouse=True)
def _reset_module_cache():
    predict_trafico_real._model = None
    predict_trafico_real._metadata = None
    predict_trafico_real._equip_pivot = None
    yield
    predict_trafico_real._model = None
    predict_trafico_real._metadata = None
    predict_trafico_real._equip_pivot = None


@pytest.fixture
def fixture_env(tmp_path, monkeypatch):
    model_path = tmp_path / "modelo_trafico_xgboost.pkl"
    metadata_path = tmp_path / "modelo_trafico_xgboost_metadata.json"
    db_path = tmp_path / "test.duckdb"

    _build_fixture_model(model_path)
    _build_fixture_metadata(metadata_path)
    _build_fixture_duckdb(str(db_path))

    monkeypatch.setattr(predict_trafico_real, "MODEL_PATH", str(model_path))
    monkeypatch.setattr(predict_trafico_real, "METADATA_PATH", str(metadata_path))
    monkeypatch.setenv("DUCKDB_PATH", str(db_path))
    return {"model_path": model_path, "metadata_path": metadata_path, "db_path": db_path}


class TestPredecirTraficoReal:
    def test_devuelve_formato_zona_nivel(self, fixture_env):
        resultado = predict_trafico_real.predecir_trafico_real("2025-06-16", 8, zonas=["Centro", "Retiro"])
        assert resultado == {"Centro": 1, "Retiro": 1}

    def test_zonas_none_usa_distritos_madrid_por_defecto(self, fixture_env):
        from ml.generate_dataset import DISTRITOS_MADRID
        resultado = predict_trafico_real.predecir_trafico_real("2025-06-16", 8, zonas=None)
        assert set(resultado.keys()) == set(DISTRITOS_MADRID)

    def test_dataframe_entrada_tiene_columnas_exactas_en_orden(self, fixture_env):
        predict_trafico_real._load_model_and_metadata()
        X = predict_trafico_real._crear_features_produccion("2025-06-16", 8, ["Centro", "Retiro"])
        assert list(X.columns) == FEATURE_COLS

    def test_pivote_equipamientos_se_cachea_en_memoria(self, fixture_env):
        predict_trafico_real._load_model_and_metadata()
        pivot_1 = predict_trafico_real._load_equip_pivot()
        # Si borramos el fichero duckdb y sigue funcionando, es que usó la caché.
        os.remove(fixture_env["db_path"])
        pivot_2 = predict_trafico_real._load_equip_pivot()
        assert pivot_1 is pivot_2

    def test_predice_con_duckdb_de_solo_lectura_en_disco(self, fixture_env):
        """Regresión (bloque 10): con el .duckdb y su carpeta sin permiso de
        escritura para el proceso —contenedor de la API como usuario no-root
        sobre un volumen de root— la predicción sigue funcionando, porque el
        wrapper abre la conexión con read_only=True. Antes: IOException
        'Permission denied' al abrir la conexión en lectura-escritura."""
        db_path = str(fixture_env["db_path"])
        dir_db = os.path.dirname(db_path)
        os.chmod(db_path, 0o444)
        os.chmod(dir_db, 0o555)
        try:
            pred = predict_trafico_real.predecir_trafico_real(
                "2025-06-16", 8, zonas=["Centro", "Retiro"]
            )
        finally:
            os.chmod(dir_db, 0o755)
            os.chmod(db_path, 0o644)
        assert set(pred) == {"Centro", "Retiro"}
        assert all(nivel in (0, 1, 2) for nivel in pred.values())

    def test_falla_claro_si_falta_el_modelo(self, fixture_env):
        os.remove(fixture_env["model_path"])
        with pytest.raises(FileNotFoundError):
            predict_trafico_real.predecir_trafico_real("2025-06-16", 8, zonas=["Centro"])

    def test_falla_claro_si_falta_la_metadata(self, fixture_env):
        os.remove(fixture_env["metadata_path"])
        with pytest.raises(FileNotFoundError):
            predict_trafico_real.predecir_trafico_real("2025-06-16", 8, zonas=["Centro"])

    def test_falla_claro_si_falta_la_tabla_duckdb(self, tmp_path, monkeypatch):
        model_path = tmp_path / "modelo_trafico_xgboost.pkl"
        metadata_path = tmp_path / "modelo_trafico_xgboost_metadata.json"
        db_path = tmp_path / "vacio.duckdb"
        _build_fixture_model(model_path)
        _build_fixture_metadata(metadata_path)
        get_connection(str(db_path)).close()  # crea el fichero pero sin la tabla

        monkeypatch.setattr(predict_trafico_real, "MODEL_PATH", str(model_path))
        monkeypatch.setattr(predict_trafico_real, "METADATA_PATH", str(metadata_path))
        monkeypatch.setenv("DUCKDB_PATH", str(db_path))

        with pytest.raises(FileNotFoundError, match="equipamientos_por_zona"):
            predict_trafico_real.predecir_trafico_real("2025-06-16", 8, zonas=["Centro"])

    def test_falla_explicito_si_use_weather_features_true(self, tmp_path, monkeypatch):
        model_path = tmp_path / "modelo_trafico_xgboost.pkl"
        metadata_path = tmp_path / "modelo_trafico_xgboost_metadata.json"
        db_path = tmp_path / "test.duckdb"
        _build_fixture_model(model_path)
        _build_fixture_metadata(metadata_path, use_weather_features=True)
        _build_fixture_duckdb(str(db_path))

        monkeypatch.setattr(predict_trafico_real, "MODEL_PATH", str(model_path))
        monkeypatch.setattr(predict_trafico_real, "METADATA_PATH", str(metadata_path))
        monkeypatch.setenv("DUCKDB_PATH", str(db_path))

        with pytest.raises(ValueError, match="use_weather_features"):
            predict_trafico_real.predecir_trafico_real("2025-06-16", 8, zonas=["Centro"])

    def test_falla_explicito_si_use_lag_features_true(self, tmp_path, monkeypatch):
        model_path = tmp_path / "modelo_trafico_xgboost.pkl"
        metadata_path = tmp_path / "modelo_trafico_xgboost_metadata.json"
        db_path = tmp_path / "test.duckdb"
        _build_fixture_model(model_path)
        _build_fixture_metadata(metadata_path, use_lag_features=True)
        _build_fixture_duckdb(str(db_path))

        monkeypatch.setattr(predict_trafico_real, "MODEL_PATH", str(model_path))
        monkeypatch.setattr(predict_trafico_real, "METADATA_PATH", str(metadata_path))
        monkeypatch.setenv("DUCKDB_PATH", str(db_path))

        with pytest.raises(ValueError, match="use_lag_features"):
            predict_trafico_real.predecir_trafico_real("2025-06-16", 8, zonas=["Centro"])

    def test_funciona_sin_tabla_pipeline_runs(self, tmp_path, monkeypatch):
        """Si aún no existe pipeline_runs (ejecución muy antigua del pipeline),
        usa toda la tabla equipamientos_por_zona sin filtrar por run_id."""
        model_path = tmp_path / "modelo_trafico_xgboost.pkl"
        metadata_path = tmp_path / "modelo_trafico_xgboost_metadata.json"
        db_path = tmp_path / "test.duckdb"
        _build_fixture_model(model_path)
        _build_fixture_metadata(metadata_path)
        _build_fixture_duckdb(str(db_path), con_incluir_pipeline_runs=False)

        monkeypatch.setattr(predict_trafico_real, "MODEL_PATH", str(model_path))
        monkeypatch.setattr(predict_trafico_real, "METADATA_PATH", str(metadata_path))
        monkeypatch.setenv("DUCKDB_PATH", str(db_path))

        resultado = predict_trafico_real.predecir_trafico_real("2025-06-16", 8, zonas=["Centro"])
        assert resultado == {"Centro": 1}


class TestHumoNombresZona:
    """§3.3, riesgo de integración: los nombres de zona del modelo de P3 deben
    coincidir con los del grafo de P2. Solo corre si los artefactos reales
    existen — en este entorno de desarrollo (sin P1/P2/P3 generados) se salta."""

    def test_zonas_del_modelo_coinciden_con_distritos_y_callejero(self):
        from routing.graph_engine import CALLEJERO_PATH

        if not os.path.exists(predict_trafico_real.MODEL_PATH):
            pytest.skip("Requiere el modelo real de P3 (no presente en este entorno).")
        if not os.path.exists(CALLEJERO_PATH):
            pytest.skip("Requiere el callejero real de P2 (no presente en este entorno).")

        import geopandas as gpd
        from ml.generate_dataset import DISTRITOS_MADRID

        model = joblib.load(predict_trafico_real.MODEL_PATH)
        ohe = model.named_steps["preprocessor"].named_transformers_["cat"]
        zonas_modelo = set(ohe.categories_[0])

        zonas_callejero = set(gpd.read_file(CALLEJERO_PATH)["zona"].dropna().unique())
        zonas_distritos = set(DISTRITOS_MADRID)

        faltan_en_distritos = zonas_modelo - zonas_distritos
        faltan_en_callejero = zonas_modelo - zonas_callejero
        assert not faltan_en_distritos, (
            f"Zonas del modelo ausentes en DISTRITOS_MADRID: {faltan_en_distritos}"
        )
        assert not faltan_en_callejero, (
            f"Zonas del modelo ausentes en el callejero real: {faltan_en_callejero}"
        )
