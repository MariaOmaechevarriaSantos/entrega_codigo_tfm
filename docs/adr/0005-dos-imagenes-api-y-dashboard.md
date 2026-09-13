# 5. Dos imágenes de contenedor (API y dashboard), sin registro

- Estado: aceptada
- Fecha: 2026-08-30
- Fase: P5 (bloque 8)

## Contexto

El sistema tiene dos procesos servidores: la API Flask (`app/server.py`) y
el dashboard Streamlit (`app/streamlit_app.py`). El dashboard habla con la
API solo por HTTP (invariante de P5): no carga grafos, modelos ni GeoJSON.
Sus dependencias apenas se solapan — el dashboard no lleva `geopandas`,
`xgboost`, `duckdb` ni `flask`; la API no lleva `streamlit`, `folium` ni
`pydeck`.

## Decisión

Dos `Dockerfile` y dos imágenes: `docker/Dockerfile.api` y
`docker/Dockerfile.dashboard`, cada una con su `requirements-*.txt`
recortado. Un tercer contenedor efímero, `data-init`, puebla el volumen de
datos (ver ADR 0007). Las imágenes **no se publican en ningún registro**:
se construyen en local con `docker compose build`, y como plan B sin red
se distribuyen con `docker save` (~423 MB las tres).

## Consecuencias

- Cada imagen instala solo lo suyo. Juntarlas sumaría ~250 MB a un
  contenedor (el dashboard) que solo sirve HTML estático + `requests`.
- El fallo de una no arrastra a la otra; el dashboard arranca cuando la
  API está `healthy` (`depends_on: service_healthy`).
- Sin registro no hay `docker pull` para el tribunal: clona el repo y
  `docker compose up --build`. Tampoco hay que gestionar credenciales de
  registro ni retención de tags.

## Alternativas descartadas

- **Una sola imagen con los dos procesos** (supervisord o similar): acopla
  ciclos de vida y dependencias, y engorda el contenedor del dashboard.
- **Publicar en GHCR / Docker Hub**: añade cuenta, credenciales en CI y
  política de limpieza de tags, para un proyecto que se despliega en una
  máquina. Descartado explícitamente (ver ADR 0008).
