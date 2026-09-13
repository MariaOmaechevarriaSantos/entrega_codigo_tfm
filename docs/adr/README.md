# Decisiones de arquitectura (ADR)

Una página por decisión estructural, en formato corto: **contexto**,
**decisión**, **consecuencias** (y, cuando aclara algo, las alternativas
descartadas). Registran decisiones **ya tomadas** a lo largo de P1-P5; se
escriben ahora, en el bloque 8B, para que queden explicadas en un sitio y
no repartidas por `ETL.md`, `README_P3_MODELO_TRAFICO.md` y el registro de
P5.

No se modifican una vez aceptadas: si una decisión se revierte, se añade
una ADR nueva que la supersede.

| # | Decisión | Fase |
|---|---|---|
| [0001](0001-grafo-desde-geojson-no-pickle.md) | Grafo servido desde el GeoJSON del callejero, no desde un pickle | P2 / P5 |
| [0002](0002-dijkstra-por-defecto-frente-a-astar.md) | Dijkstra por defecto; A\* como alternativa explícita | P4 |
| [0003](0003-meteorologia-fuera-del-modelo.md) | La meteorología no entra en el modelo de tráfico | P3 / P5 |
| [0004](0004-duckdb-reducida-en-runtime.md) | En runtime se sirve una DuckDB reducida a dos tablas | P5 |
| [0005](0005-dos-imagenes-api-y-dashboard.md) | Dos imágenes de contenedor (API y dashboard), sin registro | P5 |
| [0006](0006-catalogo-unico-de-vehiculos.md) | Un único catálogo de vehículos, expuesto por la API | P5 |
| [0007](0007-volumen-con-nombre-frente-a-datos-horneados.md) | Datos en un volumen con nombre, no horneados en la imagen | P5 |
| [0008](0008-alcance-de-operabilidad-mantenibilidad-no-escalado.md) | Operabilidad = reproducibilidad y mantenimiento, no escalado | P5 (8B) |
| [0009](0009-duckdb-solo-lectura-en-runtime.md) | Los consumidores de runtime abren la DuckDB en solo lectura | P5 (bloque 10) |
