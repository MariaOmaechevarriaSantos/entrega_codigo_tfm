# SPEC — TFM: Pipeline de Datos e Optimización de Rutas de Emergencia (Madrid)

> **Versión:** 1.0 — 2026-06-22

---

## 1. Objetivo

Reducir los tiempos de respuesta del Cuerpo de Bomberos de Madrid calculando la ruta óptima para un camión de gran tonelaje desde el parque más cercano hasta cualquier punto de la ciudad, integrando:

- Restricciones físicas reales de la red viaria (anchura, gálibos, sentidos de circulación).
- Predicción de tráfico por segmento basada en hora y tipo de día.
- Datos meteorológicos (viento, precipitación) como factor de riesgo secundario.

**Usuario final:** El TFM está orientado a demostración académica y entrega como proyecto de Data Engineering + Data Science. No se despliega en producción operativa.

---

## 2. Arquitectura General

```
┌─────────────────────────────────────────────────────┐
│               FASE 1 — DATA PIPELINE                │
│  Ingesta (OSM / Open Data Madrid / AEMET)           │
│       ↓ transform ↓                                 │
│  Grafo viario enriquecido (GeoJSON + .pkl)          │
└─────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────┐
│               FASE 2 — MODELO ML                    │
│  Dataset aforos históricos → RF/XGBoost             │
│  Salida: predicción nivel_tráfico por segmento      │
│          en función de hora + día laborable/festivo  │
└─────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────┐
│          FASE 3 — MOTOR DE RUTAS                    │
│  Dijkstra con pesos dinámicos                       │
│  Filtro estricto: width_m >= ANCHO_CAMION_REQ       │
└─────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────┐
│             FASE 4 — VISUALIZACIÓN                  │
│  Flask API  +  Streamlit dashboard                  │
└─────────────────────────────────────────────────────┘
```

---

## 3. Estructura de Carpetas

```
TFM/
├── SPEC.md                         # Este documento
├── README.md                       # (generado al finalizar)
├── requirements.txt
├── .gitignore
│
├── pipeline/                       # FASE 1 — Data Engineering
│   ├── ingest/
│   │   ├── osm_callejero.py        # Descarga red viaria Madrid via osmnx
│   │   ├── open_data_madrid.py     # Parques bomberos, hospitales, colegios, mayores
│   │   ├── aforos_trafico.py       # Aforos históricos Open Data Madrid (CSV/API)
│   │   └── aemet_weather.py        # Previsión AEMET (viento, precipitación)
│   ├── transform/
│   │   ├── build_graph.py          # Construye nx.DiGraph con oneway + width
│   │   ├── enrich_edges.py         # Une aforos, zonas administrativas y AEMET
│   │   └── merge_equipamientos.py  # Une instalaciones críticas al grafo
│   ├── load/
│   │   └── save_artifacts.py       # Serializa grafo y datasets procesados
│   └── run_pipeline.py             # Orquestador batch (cron-friendly)
│
├── ml/                             # FASE 2 — Data Science
│   ├── generate_dataset.py         # Construye dataset tabular desde aforos
│   ├── train_model.py              # Entrena RF/XGBoost, guarda .pkl
│   ├── evaluate_model.py           # Métricas, curvas, feature importance
│   └── predict.py                  # Función reutilizable de inferencia
│
├── routing/                        # FASE 3 — Motor algorítmico
│   ├── graph_engine.py             # Carga grafo + KDTree + nearest-node
│   └── optimizer.py                # Dijkstra con pesos dinámicos
│
├── app/                            # FASE 4 — Interfaz
│   ├── server.py                   # Flask API (endpoints /ruta, /prediccion, /health)
│   └── streamlit_app.py            # Dashboard interactivo Streamlit
│
├── data/
│   ├── raw/                        # Datos descargados sin modificar
│   │   ├── osm/
│   │   ├── open_data_madrid/
│   │   └── aemet/
│   └── processed/                  # Artefactos listos para usar
│       ├── madrid_callejero_filtered.geojson
│       ├── madrid_equipamientos.geojson
│       ├── aforos_dataset.csv
│       ├── modelo_trafico_madrid.pkl
│       └── encoder_madrid.pkl
│
├── notebooks/                      # Exploración y análisis (entrega TFM)
│   ├── 01_exploracion_red_viaria.ipynb
│   ├── 02_analisis_aforos_trafico.ipynb
│   ├── 03_entrenamiento_modelo_trafico.ipynb
│   └── 04_validacion_rutas_emergencia.ipynb
│
└── tests/
    ├── test_graph_engine.py
    ├── test_optimizer.py
    └── test_pipeline.py
```

---

## 4. Fuentes de Datos

| Fuente | Tipo | Datos | URL |
|--------|------|-------|-----|
| OpenStreetMap (osmnx) | Estático | Red viaria, oneway, width, highway type | `osmnx.graph_from_place("Madrid, Spain")` |
| Open Data Madrid — Callejero | Estático | Callejero oficial con geometrías | datos.madrid.es |
| Open Data Madrid — Equipamientos | Estático | Parques bomberos, hospitales, colegios, centros mayores | datos.madrid.es |
| Open Data Madrid — Aforos | Batch (~diario) | Conteo histórico de tráfico por punto/hora | datos.madrid.es |
| AEMET Open Data | Batch (~horario) | Predicción meteorológica Madrid | `opendata.aemet.es/opendata/api` |

**Variables de entorno requeridas:**
- `AEMET_API_KEY` — clave gratuita de AEMET Open Data.
- `CESIUM_TOKEN` (opcional) — solo si se añade visualización 3D en el futuro.

---

## 5. Modelo de Datos del Grafo

Cada arista del grafo `nx.DiGraph` tiene los siguientes atributos mínimos:

| Atributo | Tipo | Descripción |
|----------|------|-------------|
| `length_m` | float | Longitud del segmento en metros |
| `travel_time_s` | float | Tiempo base (length_m / speed_kph) |
| `width_m` | float | Anchura estimada o real |
| `highway` | str | Tipo de vía OSM (`residential`, `primary`, …) |
| `oneway` | str | `yes`, `-1`, `no` |
| `zona` | str | Distrito/barrio de Madrid |
| `maxspeed` | float\|None | Límite de velocidad si existe en OSM |

---

## 6. Módulo de Rutas — Lógica de Negocio

### 6.1 Filtrado de aristas

Antes de ejecutar el pathfinding, **todas las aristas con `width_m < ANCHO_CAMION_REQ`** se eliminan del grafo o se les asigna peso infinito. El valor por defecto:

```python
ANCHO_CAMION_REQ = 3.5  # metros (camión pesado bomberos Madrid ~2.9m + 0.6m margen)
GALIBO_REQ = 4.0        # metros de altura libre (definir según unidad)
```

Estos valores se leen de variables de entorno para poder ajustarlos por tipo de unidad (`ANCHO_CAMION_REQ`, `GALIBO_REQ`).

### 6.2 Pesos dinámicos

```python
def dynamic_weight(u, v, d, traffic_predictions):
    if d['width_m'] < ANCHO_CAMION_REQ:
        return float('inf')
    base = d['travel_time_s']
    nivel = traffic_predictions.get(d['zona'], 0)
    factor = {0: 1.0, 1: 1.5, 2: 3.0}.get(nivel, 1.0)
    return base * factor
```

### 6.3 Algoritmo

`networkx.dijkstra_path` con la función de peso anterior. Si no existe ruta, retornar `None` con mensaje claro.

---

## 7. Modelo ML — Predicción de Tráfico

### Entrada (features)
- `hora` (0–23)
- `dia_semana` (0=lunes … 6=domingo)
- `es_fin_de_semana` (bool)
- `es_festivo` (bool, calendario festivos Madrid)
- `mes` (1–12)
- `zona_encoded` (label-encoded)
- `tipo_via` (codificado desde `highway`)

### Salida
- `nivel_trafico` ∈ {0=Bajo, 1=Medio, 2=Alto}

### Algoritmo
1. Baseline: Random Forest (`sklearn`).
2. Comparativa opcional: XGBoost.
3. Validación cruzada temporal (no aleatoria, para respetar orden temporal).
4. Métricas: Accuracy, F1-macro, matriz de confusión.

### Artefactos generados
- `data/processed/modelo_trafico_madrid.pkl`
- `data/processed/encoder_madrid.pkl`

---

## 8. API Flask

```
GET  /health                     → {"status": "ok", "nodes": N, "edges": M}
GET  /ruta?orig_lat=&orig_lon=&dest_lat=&dest_lon=&date=&hora=
     → GeoJSON FeatureCollection con la ruta óptima
GET  /prediccion_trafico?date=&hora=
     → {"zona": nivel, ...}
GET  /equipamientos?tipo=bomberos|hospital|colegio|mayores
     → GeoJSON con instalaciones críticas
GET  /callejero_full             → GeoJSON red viaria completa (para debug)
```

Respuestas de error: `{"error": "mensaje", "code": "CODIGO"}` con HTTP status apropiado.

---

## 9. Dashboard Streamlit

El dashboard (`app/streamlit_app.py`) tendrá estos componentes:

1. **Mapa interactivo** (`folium` embebido o `pydeck`) con la red viaria de Madrid coloreada por nivel de tráfico predicho.
2. **Sidebar de simulación**:
   - Selector de fecha/hora para la predicción.
   - Picker de parque de bomberos de origen (desplegable con los reales de Madrid).
   - Input de coordenadas de destino (incidente).
   - Selector de tipo de camión (configura `ANCHO_CAMION_REQ`).
3. **Botón "Calcular Ruta"** → llama a Flask `/ruta`, dibuja la ruta en el mapa.
4. **Panel de resultados**: distancia, tiempo estimado, número de calles filtradas por anchura.
5. **Visualización de factores meteorológicos** (AEMET): si hay lluvia/viento fuerte, alerta visual.

---

## 10. Stack Técnico

| Capa | Tecnología |
|------|-----------|
| Lenguaje | Python 3.11+ |
| Red viaria | `osmnx`, `geopandas`, `shapely` |
| Grafo | `networkx` |
| Búsqueda espacial | `scipy.spatial.cKDTree` |
| ML | `scikit-learn`, `xgboost`, `joblib` |
| API | `Flask` + `flasgger` (Swagger) |
| Dashboard | `streamlit` + `folium` o `pydeck` |
| Datos | `pandas`, `numpy` |
| HTTP | `requests` |
| Scheduler | `schedule` (librería Python) |
| Tests | `pytest` |

---

## 11. Comandos Principales

```bash
# 1. Instalar dependencias
pip install -r requirements.txt

# 2. Ejecutar pipeline completo (descarga + transforma + guarda)
python pipeline/run_pipeline.py

# 3. Entrenar el modelo ML
python ml/train_model.py

# 4. Lanzar API Flask
python app/server.py

# 5. Lanzar dashboard Streamlit (en otra terminal)
streamlit run app/streamlit_app.py

# 6. Ejecutar tests
pytest tests/ -v
```

---

## 12. Estrategia de Testing

- **Unit tests** (`pytest`):
  - `test_graph_engine.py`: construcción del grafo, KDTree nearest-node, filtrado por anchura.
  - `test_optimizer.py`: ruta correcta en grafo sencillo sintético, manejo de no-ruta, peso dinámico.
  - `test_pipeline.py`: parseo de archivos GeoJSON de muestra, lógica de enriquecimiento.
- **No se testean**: descarga de APIs externas (mockeadas con `unittest.mock`), visualizaciones Streamlit.
- **Cobertura mínima**: 80% en `routing/` y `ml/`.

---

## 13. Restricciones y Límites

### Siempre hacer
- Leer secrets (AEMET API key) exclusivamente de variables de entorno.
- Guardar datos descargados en `data/raw/` antes de transformar.
- Validar con `assert` o `raise ValueError` los CRS de los GeoDataFrames antes de operar.
- Respetar el flag `oneway` de OSM.
- Filtrar aristas por anchura **antes** de invocar Dijkstra, no dentro del callback de peso (más eficiente).

### Pedir confirmación antes de
- Modificar el pipeline de descarga para sobreescribir `data/raw/` (los datos OSM pueden tardar minutos).
- Cambiar `ANCHO_CAMION_REQ` por defecto (afecta a toda la lógica de rutas).

### Nunca hacer
- Hardcodear la API key de AEMET en el código.
- Usar `console.log` / `print` en producción — usar `logging` con nivel configurable.
- Commit de archivos `.pkl` o GeoJSON pesados (añadirlos a `.gitignore`).
- Modificar la carpeta `data/raw/` desde los módulos `transform/` o `routing/`.

---

## 14. Fases de Desarrollo Sugeridas

| Fase | Descripción | Entregable |
|------|-------------|-----------|
| **F1** | Pipeline de ingesta: OSM + equipamientos estáticos | `data/processed/madrid_callejero_filtered.geojson` |
| **F2** | Motor de rutas básico sin ML | Flask `/ruta` funcional |
| **F3** | Pipeline aforos + entrenamiento ML | `modelo_trafico_madrid.pkl` |
| **F4** | Integración ML → pesos dinámicos | Flask `/ruta?date=` con tráfico predicho |
| **F5** | Integración AEMET | Factor meteorológico en la respuesta |
| **F6** | Dashboard Streamlit | `streamlit run app/streamlit_app.py` |
| **F7** | Notebooks de análisis | 4 notebooks para entrega TFM |
| **F8** | Tests + documentación | `pytest` verde, README completo |
