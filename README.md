# TFM - Pipeline de datos inteligente y optimizacion de rutas de emergencia en Madrid

Sistema de apoyo al despacho de emergencias para el Cuerpo de Bomberos de Madrid. El proyecto combina un pipeline ETL con datos reales, un grafo navegable de la red viaria, un modelo de Machine Learning para estimar nivel de trafico por distrito y un motor de rutas que ajusta los costes de circulacion segun restricciones fisicas y condiciones operativas.

## Estado actual

El repositorio contiene cinco bloques principales:

| Bloque | Estado | Descripcion |
|---|---:|---|
| P1 - Pipeline de datos | Implementado | Descarga, limpia y persiste callejero, aforos, meteorologia y equipamientos. |
| P2 - Grafo y rutas | Implementado | Construye el grafo dirigido de Madrid y calcula rutas sobre la red viaria. |
| P3 - Modelo ML de trafico | Implementado en notebook | Entrena y evalua modelos para predecir trafico bajo/medio/alto por zona, fecha y hora. |
| P4 - Motor de rutas A* | Implementado | Parametrizacion de vehiculo por llamada, filtro de galibo, A* configurable frente a Dijkstra, nodos especiales reales (parques/hospitales), wrapper de trafico real y entregables de cobertura/comparativa algoritmica. |
| P5 - Integracion API + dashboard y empaquetado | Implementado | Contrato de API versionado (`docs/p5/openapi_p5.yaml`), catalogo unico de vehiculos, tráfico real de P3 en `/ruta`/`/isocronas`/`/prediccion_trafico`, panel meteorologico informativo, dashboard operativo, empaquetado con `docker compose` (volumen con nombre, datos fuera de la imagen), operabilidad (`/version`, logs JSON, `/metrics`, CI, ADR) y DuckDB de solo lectura en runtime. |

La parte P3 esta documentada con detalle en [README_P3_MODELO_TRAFICO.md](README_P3_MODELO_TRAFICO.md); la especificacion funcional del conjunto esta en [SPEC.md](SPEC.md) y el motor de rutas P4 se resume mas abajo en este mismo README. La fase P5 (API + dashboard + empaquetado) esta documentada en [docs/p5/README_P5_API_Streamlit.md](docs/p5/README_P5_API_Streamlit.md), con las decisiones de arquitectura de P1-P5 recogidas en [docs/adr/](docs/adr/README.md) y el registro de trabajo por bloque en [docs/p5/registro_por_bloque.md](docs/p5/registro_por_bloque.md).

## Mapa de documentacion

| Documento | Contenido |
|---|---|
| [SPEC.md](SPEC.md) | Especificacion funcional del proyecto (objetivo, fases, alcance). |
| [ETL.md](ETL.md) | Pipeline de datos P1: fuentes, transformaciones y persistencia. |
| [README_P3_MODELO_TRAFICO.md](README_P3_MODELO_TRAFICO.md) | Modelo ML de trafico: dataset, entrenamiento, metricas y SHAP. |
| [docs/p5/README_P5_API_Streamlit.md](docs/p5/README_P5_API_Streamlit.md) | Fase P5 completa: contrato de API, dashboard, empaquetado y arranque. |
| [docs/p5/guia_defensa.md](docs/p5/guia_defensa.md) | Guion de defensa del TFM. |
| [docs/p5/registro_por_bloque.md](docs/p5/registro_por_bloque.md) | Diario tecnico: que se hizo en cada bloque de P5 y por que. |
| [docs/p5/verificacion_docker.md](docs/p5/verificacion_docker.md) | Comprobaciones del empaquetado en una maquina limpia. |
| [docs/adr/](docs/adr/README.md) | Decisiones de arquitectura (9 ADR, P1-P5). |
| [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md) | Sistema de diseño del dashboard: tipografia, color, componentes, accesibilidad. |
| [docs/p5/openapi_p5.yaml](docs/p5/openapi_p5.yaml) | Contrato OpenAPI de la API (Swagger UI en `/apidocs`). |

## Arranque rapido con docker-compose (P5)

Para levantar el sistema completo (API + dashboard) en una maquina limpia, sin instalar Python. Necesita Docker con Compose v2 y el paquete de datos `dist/artifacts.tar.gz` (no viaja en el `git clone`; se copia a `./dist/`, se genera con `make data`, o se apunta a un GitHub Release con `ARTIFACTS_URL` en `.env`).

```bash
git clone <URL-del-repositorio> tfm_codigo && cd tfm_codigo
mkdir -p dist && cp /ruta/a/artifacts.tar.gz dist/     # o: make data
docker compose -f docker-compose.yml build              # ~5 min la primera vez (descarga de PyPI)
docker compose -f docker-compose.yml up -d --wait --wait-timeout 300
curl -s http://localhost:8080/health                    # -> 200, "status": "ok"; dashboard en http://localhost:8501
```

Procedimiento completo (dos caminos, comprobaciones, que hacer si falla, plan B sin red): [docs/p5/README_P5_API_Streamlit.md](docs/p5/README_P5_API_Streamlit.md), seccion 13.

## Caracteristicas principales

- **Pipeline ETL reproducible**: descarga fuentes oficiales o recupera artefactos preparados desde Google Drive.
- **Persistencia analitica en DuckDB**: historico de aforos, meteorologia, equipamientos y ejecuciones del pipeline.
- **Callejero enriquecido**: red viaria filtrada y asignada a los 21 distritos de Madrid.
- **Grafo dirigido navegable**: aristas con longitud, tiempo estimado, anchura, galibo, zona y tipo de via; parques de bomberos y hospitales conectados como nodos reales.
- **Motor de rutas parametrizable**: ancho y galibo del vehiculo se pasan por llamada (no por variable de entorno fija), con Dijkstra por defecto y A* como alternativa configurable.
- **Modelo ML production-safe**: XGBoost entrenado sin variables de fuga de informacion, usando solo datos disponibles antes de predecir.
- **Wrapper de trafico real**: `ml/predict_trafico_real.py` conecta el motor de rutas con el modelo XGBoost de P3 (formato `{zona: nivel}`).
- **Isocronas de cobertura**: poligonos de alcance a 5/10/15 min por parque de bomberos, reutilizando el mismo filtrado fisico y de trafico que las rutas.
- **Explicabilidad**: importancia de variables y SHAP values para interpretar el modelo ganador.
- **API y dashboard**: Flask y Streamlit para exponer rutas, equipamientos y prediccion de trafico. El dashboard **solo habla con la API por HTTP**: no abre ficheros de `data/` ni importa `routing`/`ml`.
- **Empaquetado reproducible**: dos imagenes (API y dashboard) mas un `data-init` que puebla un volumen con nombre; los datos viajan fuera de la imagen.
- **Operabilidad**: `/health` con autodiagnostico, `/version` con identidad de build, `/metrics` en formato Prometheus, logs JSON con `request_id` y CI en GitHub Actions.

## Arquitectura

```text
OSM / Open Data Madrid / Google Drive
          |
          v
pipeline/run_pipeline.py
          |
          v
data/processed/
  - madrid_callejero_filtered.geojson
  - tfm_madrid.duckdb
          |
          +--> notebooks/05_modelo_xgboost_densidad_trafico.ipynb
          |      - dataset ML
          |      - entrenamiento
          |      - evaluacion
          |      - SHAP
          |      - modelo .pkl
          |
          +--> routing/
                 - grafo
                 - optimizador de rutas
                 - pesos dinamicos por trafico
                 - isocronas de cobertura
                        |
                        v
                 app/server.py (API Flask)
                        |  HTTP
                        v
                 app/streamlit_app.py (dashboard)
```

En despliegue, esas dos ultimas cajas son dos contenedores distintos (`api` y
`dashboard`) que comparten un volumen con nombre poblado por `data-init`; la
API abre la DuckDB en **solo lectura** ([ADR 0009](docs/adr/0009-duckdb-solo-lectura-en-runtime.md)).

## Instalacion

Solo hace falta si se quiere ejecutar el pipeline, los notebooks o los tests
fuera de Docker. Se recomienda usar un entorno virtual dentro del repositorio.

```bash
python -m venv .venv
source .venv/bin/activate                             # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
```

Hay cuatro ficheros de dependencias, deliberadamente separados:

| Fichero | Para que |
|---|---|
| `requirements.txt` | Entorno completo de desarrollo: pipeline, notebooks, ML, API y dashboard. |
| `requirements-runtime.txt` | Solo lo que importa el proceso de la API. Es lo que entra en la imagen `api` (versiones ancladas). |
| `requirements-dashboard.txt` | Solo lo que importa Streamlit. Es lo que entra en la imagen `dashboard`; sin geopandas, DuckDB ni Flask. |
| `requirements-dev.txt` | Herramientas de desarrollo y CI (`ruff`, `pytest`, `pytest-cov`, `PyYAML`). No va a ninguna imagen. |

### Configuracion

Todas las variables tienen valor por defecto: `.env` es **opcional**. El
catalogo comentado esta en [.env.example](.env.example); copialo si quieres
cambiar algo.

```bash
cp .env.example .env
```

Las mas usadas:

| Variable | Por defecto | Efecto |
|---|---|---|
| `ANCHO_CAMION_REQ` / `GALIBO_REQ` | `3.5` / `4.0` | Dimensiones del camion de referencia (afectan a todo el calculo de rutas). |
| `API_PORT` / `DASHBOARD_PORT` | `8080` / `8501` | Puertos publicados en el host. |
| `DUCKDB_PATH` | `data/processed/tfm_madrid.duckdb` | Ruta de la DuckDB; admite `md:tfm_madrid` para MotherDuck. |
| `API_URL` | `http://localhost:8080` | A que API apunta el dashboard. |
| `LOG_LEVEL` | `INFO` | Nivel de los logs JSON de la API. |
| `ARTIFACTS_URL` | vacio | URL del `artifacts.tar.gz` si no se usa `./dist/`. |

## Atajos con `make`

`make` a secas lista los objetivos disponibles.

| Objetivo | Que hace |
|---|---|
| `make dev` | Levanta API + dashboard con el override de desarrollo (bind-mounts: editar sin rebuild). |
| `make up` | Arranque exacto de entrega (`docker-compose.yml`, sin override) con `/version` poblado. |
| `make down` | Para los contenedores conservando el volumen de datos. |
| `make test` / `make test-all` | Suite rapida / suite completa con `--runslow`. |
| `make data` | Regenera la DuckDB reducida, el tarball de artefactos y `artifacts.manifest.json`. |
| `make lint` | `ruff` (E9 + pyflakes) sobre `app/ scripts/ docker/ tests/`. |
| `make docs-pdf` | Regenera el PDF de la fase P5 (necesita `pandoc` + `xelatex`). |
| `make clean` | Borra cachés de Python/pytest/ruff. |

## Datos y pipeline

El pipeline se puede ejecutar de dos formas.

### Opcion rapida: artefactos desde Google Drive

Descarga los artefactos ya preparados. Es la opcion recomendada para trabajar en el modelo sin repetir todo el ETL pesado.

```bash
python pipeline/run_pipeline.py --data-source drive
```

Si se quiere forzar la redescarga aunque los ficheros ya existan:

```bash
python pipeline/run_pipeline.py --data-source drive --force-drive-download
```

### Opcion reproducible: fuentes oficiales desde cero

Descarga y procesa los datos desde OpenStreetMap y Open Data Madrid.

```bash
python pipeline/run_pipeline.py --data-source raw
```

Si ya existe la red OSM descargada y solo se quiere reconstruir el resto:

```bash
python pipeline/run_pipeline.py --data-source raw --skip-osm
```

La documentacion completa del pipeline esta en [ETL.md](ETL.md).

## Artefactos generados

| Ruta | Descripcion |
|---|---|
| `data/raw/osm/madrid_calles_raw.geojson` | Callejero bruto descargado de OSM. |
| `data/processed/madrid_callejero_filtered.geojson` | Callejero filtrado y enriquecido con zona por arista. |
| `data/processed/tfm_madrid.duckdb` | Base DuckDB con historico de aforos, meteorologia y equipamientos. |
| `data/processed/aforos_dataset_xgboost.csv` | Dataset tabular usado por el notebook P3. |
| `data/processed/modelo_trafico_xgboost.pkl` | Modelo ganador serializado. |
| `data/processed/modelo_trafico_xgboost_metadata.json` | Metadata: features, metricas, umbrales y configuracion. |
| `data/processed/isocronas_bomberos.geojson` | Poligonos de cobertura a 5/10/15 min por parque de bomberos (P4). Opcional: si falta, `/isocronas` las calcula en vivo. |
| `benchmark_astar_resultados.csv` | Comparativa A* vs Dijkstra: nodos explorados, tiempo de computo y distancia por par origen-destino (P4). |
| `validacion_rutas_emergencia.csv` | Validacion con rutas reales parque-destino; `tiempo_google_min` se completa a mano (P4). |
| `dist/artifacts.tar.gz` + `artifacts.manifest.json` | Paquete de datos para el despliegue y su inventario con sha256 (P5, `make data`). |

Los artefactos de `data/` no se versionan en git por tamano y reproducibilidad. Los CSV de P4 se generan en la raiz del repo y tampoco se versionan.

## Tablas principales de DuckDB

| Tabla | Uso |
|---|---|
| `aforos_historicos` | Trafico agregado por `zona`, `fecha`, `hora`, con `intensidad_media` y `ocupacion_media`. |
| `aforos_por_sensor` | Mediciones por sensor y hora, con coordenadas y cercania a equipamientos. |
| `meteorologia_historica` | Temperatura y humedad relativa por zona, fecha y hora. |
| `equipamientos` | Equipamientos puntuales: bomberos, hospitales, centros educativos y centros de mayores. |
| `equipamientos_por_zona` | Conteo estatico de equipamientos por distrito y tipo. |
| `pipeline_runs` | Registro de ejecuciones del pipeline. |

## Modelo de trafico P3

El modelo actual se desarrolla en:

```text
notebooks/05_modelo_xgboost_densidad_trafico.ipynb
```

Resumen del modelo ganador:

| Campo | Valor |
|---|---|
| Problema | Clasificacion multiclase: `Bajo`, `Medio`, `Alto`. |
| Target | `nivel_trafico`, derivado de `ocupacion_media`. |
| Modelo ganador | XGBoost. |
| Validacion | Split temporal + validacion interna temporal para early stopping. |
| Test final | 2025-12-30 a 2026-06-30. |
| Metricas test | `accuracy=0.809`, `balanced_accuracy=0.795`, `precision_macro=0.780`, `recall_macro=0.795`, `F1_macro=0.785`, `F1_weighted=0.815`, `ROC-AUC_OvR_macro=0.931`, `average_precision_macro=0.822`. |
| Variables | Zona, hora, calendario y equipamientos por zona. |
| Leakage | Sin lags, sin trafico observado como feature, sin meteorologia futura. |
| Interpretabilidad | Feature importance y SHAP exportados en `docs/p3_modelo_trafico/`. |

Detalles completos: [README_P3_MODELO_TRAFICO.md](README_P3_MODELO_TRAFICO.md).

## Motor de rutas P4

Resumen de lo implementado (las decisiones estan razonadas en [ADR 0002](docs/adr/0002-dijkstra-por-defecto-frente-a-astar.md)):

- `calcular_ruta(..., ancho_req, galibo_req, algoritmo="dijkstra"|"astar")`: parametros de vehiculo por llamada (ya no fijos al importar el modulo) y filtro de galibo ademas de anchura.
- Parques de bomberos y hospitales conectados como nodos reales del grafo (`graph_engine.load_graph`/`get_special_node`), en vez de coordenadas aproximadas.
- `ml/predict_trafico_real.py::predecir_trafico_real`: wrapper productivo sobre el modelo XGBoost real de P3 (no sustituye a `ml/predict.py`, que sigue sirviendo el modelo sintetico viejo).
- `routing/isochrones.py::calcular_isocronas`: isocronas de cobertura (poligono concave hull) a 5/10/15 min por parque.
- `routing/benchmark_astar.py`: comparativa A* vs Dijkstra (nodos explorados, tiempo de computo, distancia) sobre pares parque-destino reales.
- `routing/validate_routes.py`: validacion con rutas reales parque-destino (Dijkstra + trafico real de una fecha/hora representativa) frente a tiempos de Google Maps. La columna `tiempo_google_min` **se rellena a mano**: no se fabrica ningun dato de referencia.

```bash
python routing/benchmark_astar.py     # comparativa A* vs Dijkstra -> benchmark_astar_resultados.csv
python routing/validate_routes.py     # validacion con rutas reales -> validacion_rutas_emergencia.csv
```

## Notebooks

| Notebook | Contenido |
|---|---|
| `01_exploracion_red_viaria.ipynb` | Exploracion de la red viaria, tipos de via, anchuras y construccion inicial. |
| `02_analisis_aforos_trafico.ipynb` | Analisis de patrones de trafico por hora, dia y distrito. |
| `p2_enriquecimiento.ipynb` | Enriquecimiento del callejero: joins espaciales, zona por arista y equipamientos. |
| `03_entrenamiento_modelo_trafico.ipynb` | Version inicial de entrenamiento ML. |
| `04_validacion_rutas_emergencia.ipynb` | Validacion de rutas sobre el grafo. |
| `05_modelo_xgboost_densidad_trafico.ipynb` | Version final P3: dataset, split temporal, modelos, grid search, early stopping, metricas train/test y SHAP. |

## API

Contrato completo con esquemas y ejemplos: [docs/p5/openapi_p5.yaml](docs/p5/openapi_p5.yaml) (Swagger UI en `/apidocs` con la API levantada) y [docs/p5/README_P5_API_Streamlit.md](docs/p5/README_P5_API_Streamlit.md), seccion 4.

| Endpoint | Metodo | Parametros | Respuesta |
|---|---|---|---|
| `/health` | GET | - | Diagnostico: `status`, `grafo` (cargado, nodos, aristas, nodos especiales, segundos de carga), `modelo_trafico`, `duckdb`, `artefactos_faltantes`, `timestamp`. `503` con el mismo cuerpo si el grafo no esta cargado. |
| `/version` | GET | - | `version_api`, `git_sha`, `fecha_build`, `sha256_manifiesto_datos` (los tres ultimos = `desconocido` si la imagen se construyo sin build args). |
| `/metrics` | GET | - | Exposicion Prometheus en texto (`tfm_http_requests_total`, `tfm_http_request_duration_seconds`, `tfm_cache_events_total`, `tfm_isocronas_origen_total`). |
| `/config` | GET | - | Catalogo compartido: `vehiculos` (id, nombre, `ancho_req_m`, `galibo_req_m`), `vehiculo_default`, `algoritmos_disponibles`, `algoritmo_default`, `cortes_isocronas_min_default`, `trafico_niveles`. Fuente unica; el dashboard lo consume por HTTP. |
| `/ruta` | GET | Origen y destino, cada uno por coordenadas (`orig_lat`+`orig_lon`) o por identidad (`orig_tipo_nodo`+`orig_nombre`), mutuamente excluyentes. `date` (`YYYY-MM-DD`, opcional; sin ella no se aplica trafico), `hora` (0-23), `vehiculo` (id de `/config`), `ancho_req`/`galibo_req` (float, ganan sobre `vehiculo`), `algoritmo` (`dijkstra`｜`astar`). | GeoJSON `LineString` + `properties` (`distancia_m`, `tiempo_min`, `ruta_completa`, y `distancia_sin_cubrir_m`/`motivo` si es parcial), `parametros_efectivos`, `trafico_por_zona`, `origen`, `destino`, `zona_destino`. |
| `/isocronas` | GET | `nombre` (parque de bomberos, obligatorio), `date`, `hora`, `vehiculo`, `ancho_req`/`galibo_req`, `corte_min` (filtra a un corte). | GeoJSON con un `Polygon` por corte (5/10/15 min), `parque`, `parametros_efectivos`, `trafico_por_zona`. Cabecera `X-Isocronas-Origen: precomputado｜vivo`. |
| `/prediccion_trafico` | GET | `date` (obligatorio), `hora`. | `{parametros_efectivos, trafico_por_zona}` con el nivel (`0=Bajo`, `1=Medio`, `2=Alto`) por distrito. Usa el modelo real de P3; `503` si el `.pkl` no esta. |
| `/equipamientos` | GET | `tipo` ∈ `bomberos｜hospitales｜centros_educativos｜centros_mayores` (por defecto `bomberos`). | GeoJSON `FeatureCollection` + `tipo`. `404` si el GeoJSON de ese tipo no existe. |
| `/geocodificar` | GET | `q` (texto de la direccion, obligatorio). | `{lat, lon, direccion}` (Nominatim, filtrado al bounding box de Madrid). `404 DIRECCION_NO_ENCONTRADA`, `503 GEOCODER_UNAVAILABLE`. |
| `/meteorologia` | GET | `zona` (filtra a un distrito), `fuente` ∈ `municipal｜aemet` (por defecto `municipal`). | Lectura actual por distrito (temperatura, humedad, precipitacion, viento) desde el feed municipal en vivo + `alerta` informativa de tres niveles (`normal`/`precaucion`/`adversa`). **No modifica el calculo de la ruta** y lo declara. Nunca 5xx. |

Todos los errores son JSON `{error, code}` con `code` de un enum cerrado; un `MISSING_ARTIFACT` nombra el fichero ausente. Toda respuesta 200 que dependa de un parametro con valor por defecto devuelve `parametros_efectivos` con lo realmente usado.

## Dashboard

Herramienta operativa de apoyo al despacho, no una demo de graficos: mapa
dominante, parametros a la izquierda y panel de resultados con las cifras
grandes. El sistema de diseño (tipografia, color, componentes, accesibilidad
AA) esta documentado en [DESIGN_SYSTEM.md](DESIGN_SYSTEM.md).

Barra lateral, en el orden en que se rellena:

| Seccion | Contenido |
|---|---|
| 1 · Destino — incidente | Buscador de direcciones (via `/geocodificar`) **o** fijar el punto con un clic en el mapa; coordenadas siempre ajustables a mano. |
| 2 · Origen — parque de bomberos | Selector de parque real. Al fijar el destino se **preselecciona el parque mas cercano** en linea recta; se puede cambiar a cualquier otro. |
| 3 · Vehiculo y algoritmo | Catalogo de vehiculos y `dijkstra｜astar`, ambos servidos por `/config` (sin constantes duplicadas en el cliente). |
| 4 · Fecha y hora del incidente | Hora del incidente y casilla para aplicar o no la prediccion de trafico de P3. |
| 5 · Capas del mapa | Isocronas del parque seleccionado (con transparencia regulable) y hospitales de referencia. |

Ademas: banda meteorologica informativa en cabecera (alerta de tres niveles,
que **no** altera el calculo de la ruta y lo declara), estados vacios con guia
en vez de paneles en blanco, y una pagina **Sistema** (`app/pages/1_Sistema.py`)
que muestra `/health` en formato legible para un operador.

## Ejecutar servicios

API Flask:

```bash
python app/server.py --dev
```

Dashboard Streamlit (necesita la API levantada; `API_URL` si no esta en `localhost:8080`):

```bash
streamlit run app/streamlit_app.py
```

Swagger UI, si la API esta levantada:

```text
http://localhost:8080/apidocs
```

## Tests

Suite rapida (por defecto): no toca `data/processed/`, no construye el
grafo real de Madrid. Es la que hay que correr siempre.

```bash
pytest tests/                 # suite rapida
pytest tests/ -v
```

Suite completa: anade el unico test marcado `@pytest.mark.slow`, que
carga el callejero real desde `data/processed/madrid_callejero_filtered.geojson`
(~17 s, ~1 GB de RAM) y comprueba una ruta conocida de punta a punta.
Requiere haber ejecutado antes `python pipeline/run_pipeline.py`.

```bash
pytest tests/ --runslow
```

Tambien disponibles como `make test` y `make test-all`.

Ultima ejecucion medida en este entorno (tras los bloques A/B/C del dashboard):

```text
pytest tests/            -> 456 passed, 1 skipped   (~17 s)
pytest tests/ --runslow  -> anade el end-to-end del grafo real (~25 s mas); tambien pasa
```

El `skipped` de la suite rapida es el test `@pytest.mark.slow` (opt-in con
`--runslow`). En un entorno sin el modelo XGBoost real de P3 se salta
ademas `test_predict_trafico_real.py`.

## Estructura del proyecto

```text
tfm_codigo/
  app/                       API Flask y dashboard Streamlit
    server.py                Endpoints de la API
    wsgi.py                  Entrada de gunicorn (carga el grafo con --preload)
    config.py                Catalogo unico de vehiculos y defaults
    streamlit_app.py         Panel operativo (habla con la API solo por HTTP)
    _ui_common.py            Componentes, estilos y mapas compartidos del dashboard
    pages/1_Sistema.py       Pagina de diagnostico: /health legible
    meteo_alerta.py          Alerta meteorologica informativa de tres niveles
    logging_setup.py         Logs JSON con request_id
    metrics.py               Contadores e histogramas Prometheus
    version.py               Identidad de build para /version
    static/icons/            Iconos Iconoir usados por el dashboard
  data/
    raw/                     Datos brutos no versionados
    processed/               Artefactos procesados no versionados
  docker/                    Dockerfiles (api, dashboard, data-init) y data_init.py
  docs/
    openapi_p5.yaml          Contrato OpenAPI de la API
    adr/                     Decisiones de arquitectura (P1-P5)
    p3_modelo_trafico/       Figuras del modelo (importancias, SHAP, metricas)
    p5/                      Documentacion de la fase P5
  ml/                        Modulos de entrenamiento/prediccion reutilizables
                               (predict.py: modelo sintetico viejo;
                                predict_trafico_real.py: wrapper XGBoost real de P3)
  notebooks/                 Analisis reproducibles del TFM
  pipeline/
    ingest/                  Descarga de fuentes raw y artefactos de Drive
    transform/               Limpieza, joins espaciales y agregaciones
    load/                    Persistencia GeoJSON/DuckDB
    run_pipeline.py          Orquestador principal
  routing/                   Grafo, optimizador de rutas, isocronas,
                               validacion y benchmark A*/Dijkstra
  scripts/                   Reduccion de la DuckDB y empaquetado de artefactos
  tests/                     Tests automatizados
  Makefile                   Atajos de operacion (dev, up, test, data, lint, docs-pdf)
  docker-compose.yml         Despliegue de entrega (API + dashboard + data-init)
  docker-compose.override.yml Override de desarrollo (bind-mounts)
  .env.example               Catalogo comentado de variables de entorno
  artifacts.manifest.json    Inventario + sha256 del paquete de datos
  DESIGN_SYSTEM.md           Sistema de diseño del dashboard
  ETL.md                     Documentacion del pipeline
  README_P3_MODELO_TRAFICO.md Documentacion detallada del modelo ML
  SPEC.md                    Especificacion funcional del proyecto
```

## Fuentes de datos

| Fuente | Tipo | Contenido |
|---|---|---|
| OpenStreetMap | Estatica | Red viaria, geometria, sentidos, tipos de via, anchuras cuando existen. |
| Open Data Madrid - Aforos | Historica | Intensidad y ocupacion de trafico por sensor y hora. |
| Open Data Madrid - Meteorologia | Historica + vivo | Temperatura y humedad por estacion. |
| Open Data Madrid - Equipamientos | Estatica | Bomberos, hospitales, centros educativos y centros de mayores. |
| Open Data Madrid - Distritos | Estatica | Limites administrativos de los 21 distritos. |
| Google Drive compartido | Artefactos | Copia ya procesada de GeoJSON y DuckDB para reproducir resultados rapidamente. |

## Notas metodologicas importantes

- El trafico real no se persiste por arista en el GeoJSON final; vive en DuckDB por zona/fecha/hora.
- El modelo P3 predice nivel de trafico por distrito, no velocidad ni ocupacion exacta por calle.
- `Bajo`, `Medio` y `Alto` son categorias relativas al historico de cada zona.
- El modelo principal evita leakage: no usa lags ni valores reales de trafico como variables explicativas.
- La meteorologia historica existe, pero no se usa por defecto en el modelo final porque para prediccion futura haria falta meteo en vivo o forecast.
