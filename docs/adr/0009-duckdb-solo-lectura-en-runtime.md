# 9. Los consumidores de runtime abren la DuckDB en solo lectura

- Estado: aceptada
- Fecha: 2026-08-30
- Fase: P5 (bloque 10)

## Contexto

`pipeline/load/save_artifacts.get_connection()` se escribió para el
pipeline (P1), cuyo trabajo es **escribir** tablas (`INSERT` append-only).
Por eso abría siempre la conexión en lectura-escritura. En runtime, en
cambio, el único consumidor de la DuckDB es
`ml/predict_trafico_real._load_equip_pivot`, que solo hace `SELECT` sobre
`equipamientos_por_zona` y `pipeline_runs` para construir las *features*
del modelo de tráfico.

Una conexión de lectura-escritura de DuckDB toma el lock exclusivo del
fichero y puede crear el WAL: necesita permiso de **escritura** sobre el
`.duckdb` y sobre su carpeta. En los dos modos de despliegue de P5 el
proceso de la API no tiene ese permiso:

- `make up` (tribunal): `data-init` extrae el tarball como `root` con modo
  `0644`; la API corre como usuario no-root (`tfm`, uid 10001) y solo
  puede leer.
- `make dev`: el `data/processed` del *host* (dueño uid 1000) va por
  *bind-mount* a un contenedor que corre como uid 10001.

En ambos, `/prediccion_trafico` y `/ruta?…&date=…` respondían
`500 INTERNAL_ERROR` con `IOException … Permission denied` al abrir la
conexión. `/health` no lo detectaba porque ya abría con `read_only=True`.

## Decisión

`get_connection()` acepta `read_only: bool = False` (keyword-only) y lo
pasa a `duckdb.connect(..., read_only=...)`.
`_load_equip_pivot` llama `get_connection(read_only=True)`. El pipeline y
los tests que escriben siguen llamando sin argumentos (comportamiento
idéntico).

Es un arreglo de un módulo de P1/P3, hecho con autorización explícita
—como el `save_equipamientos_geojson` del bloque 4—, no por cuenta propia.

## Consecuencias

- La API sirve tráfico con la DuckDB del volumen/imagen siendo de `root` y
  de solo lectura para el proceso; no hace falta `chown` ni correr el
  contenedor como `root`. Verificado en el contenedor: fichero
  `-rw-r--r-- root`, proceso `uid=10001`, `/prediccion_trafico` y
  `/ruta?…&date=…` → 200.
- Mínimo privilegio: un servicio expuesto a red que solo lee ya no puede
  corromper el histórico ni bloquea a otros lectores con el lock de
  escritura.
- Con `read_only=True`, DuckDB **falla** sobre un fichero inexistente en
  vez de crear una base vacía. Es el comportamiento deseado (regla de P5:
  no rellenar artefactos ausentes en silencio); `get_connection` omite el
  `os.makedirs` en ese modo.
- Tres tests nuevos con prueba de mutación
  (`tests/test_pipeline.py::TestSaveArtifacts`,
  `tests/test_predict_trafico_real.py`), este último reproduce el bug
  poniendo el `.duckdb` y su carpeta en `0444`/`0555`.

## Alternativas descartadas

- **`chown`/`chmod` del volumen** para dar escritura al usuario de la API:
  *aumenta* los permisos del servicio en lugar de reducirlos, y se
  deshace en cada `down -v` + repoblado.
- **Correr el contenedor de `api` como `root`**: rompe el diseño no-root
  de la imagen para una necesidad que no existe (no se escribe nada).
- **Abrir `duckdb.connect(path, read_only=True)` directamente en
  `_load_equip_pivot`**, sin tocar `get_connection`: duplicaría la
  resolución de `DUCKDB_PATH` y el soporte de `md:` (MotherDuck).
