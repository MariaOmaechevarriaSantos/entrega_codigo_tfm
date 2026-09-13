"""
Comprobación en tiempo de build (Dockerfile.api): tras quitar
nvidia-nccl-cu13, xgboost sigue entrenando y prediciendo en CPU, y un
modelo XGBoost serializado con joblib se recarga y predice igual — que es
justo lo que hace ml/predict_trafico_real.py con
data/processed/modelo_trafico_xgboost.pkl en runtime.

Falla el build si algo de esto no se cumple.
"""
import sys
import tempfile

import joblib
import numpy as np
import xgboost as xgb

# nccl NO debe estar instalado.
try:
    import nvidia.nccl  # noqa: F401
    print("ERROR: nvidia-nccl sigue instalado", file=sys.stderr)
    sys.exit(1)
except ModuleNotFoundError:
    pass

rng = np.random.default_rng(0)
X = rng.random((60, 5))
y = rng.integers(0, 3, 60)

clf = xgb.XGBClassifier(n_estimators=10, max_depth=3, tree_method="hist")
clf.fit(X, y)
pred1 = clf.predict(X[:5])
assert pred1.shape == (5,), pred1.shape

with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as fh:
    joblib.dump(clf, fh.name)
    reload = joblib.load(fh.name)

pred2 = reload.predict(X[:5])
assert (pred1 == pred2).all(), "predicción distinta tras joblib.load"
print("probe_build OK: xgboost CPU + joblib round-trip sin nvidia-nccl")
