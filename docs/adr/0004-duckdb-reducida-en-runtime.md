# 4. En runtime se sirve una DuckDB reducida a dos tablas

- Estado: aceptada
- Fecha: 2026-08-30
- Fase: P5 (bloque 8)

## Contexto

`data/processed/tfm_madrid.duckdb` completa pesa ~827 MB: el 99,9 % es
`aforos_por_sensor` (~99,9 M filas). En runtime, la API solo lee dos
tablas: `equipamientos_por_zona` (75 filas, la usa
`ml/predict_trafico_real._load_equip_pivot`) y `pipeline_runs` (1 fila, la
usa `/health`). Meter los 827 MB en el tarball de artefactos que descarga
el tribunal sería cargar 826 MB que nadie consulta.

## Decisión

`scripts/reducir_duckdb.py` genera una DuckDB de runtime que contiene
**solo** `equipamientos_por_zona` y `pipeline_runs`, con verificación de
que las filas coinciden con el origen. Viaja en el tarball como
`tfm_madrid.duckdb` (~780 KiB). El entrenamiento del modelo y el análisis
siguen usando la DuckDB completa, que no se versiona.

## Consecuencias

- El tarball de artefactos baja de >800 MB a ~5 MB. Medido: 826.814.464 B
  → 798.720 B (factor ~1.035×).
- `reducir_duckdb.py` es determinista y de un comando; hay que acordarse
  de ejecutarlo antes de `empaquetar_artefactos.py` (el `Makefile`
  encadena ambos en `make data`).
- Si algún endpoint futuro necesitara otra tabla, hay que añadirla a
  `TABLAS_RUNTIME` en el script y regenerar. El test
  `tests/test_empaquetado.py::TestReducirDuckdb` falla nombrando la tabla
  si falta.

## Alternativas descartadas

- **Shippear la DuckDB completa**: +826 MB al tarball para datos que
  runtime no toca.
- **MotherDuck** (`md:tfm_madrid`): elimina el fichero local pero mete una
  dependencia de red y una cuenta en el arranque del tribunal. Queda como
  opción detrás de `DUCKDB_PATH` / `MOTHERDUCK_TOKEN`, nunca por defecto.
