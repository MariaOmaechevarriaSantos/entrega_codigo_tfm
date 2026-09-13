# 1. Grafo servido desde el GeoJSON del callejero, no desde un pickle

- Estado: aceptada
- Fecha: 2026-07 (Fase P2), consolidada en Fase P5
- Fases: P2, P5

## Contexto

El motor de rutas necesita un grafo dirigido de ~171 k nodos / ~236 k
aristas. Hubo un momento en P2 con dos representaciones vivas: el GeoJSON
`data/processed/madrid_callejero_filtered.geojson` (salida del pipeline) y
un `grafo_madrid.pkl` serializado con `pickle` para arrancar más rápido.
Mantener las dos obliga a regenerar el pickle cada vez que cambia el
callejero, y un pickle desincronizado es indetectable a simple vista.

## Decisión

La única fuente del grafo es el GeoJSON. `routing/graph_engine.load_graph()`
lo construye en memoria al arrancar el proceso (una vez, cacheado en un
singleton de módulo). No se versiona ni se sirve ningún `.pkl` del grafo.

## Consecuencias

- Un solo artefacto que mantener y verificar (su sha256 está en
  `artifacts.manifest.json`). El callejero es también lo que leen los
  notebooks y la validación de rutas: no hay copias que diverjan.
- Coste: la construcción del grafo tarda ~17 s en host y ~36-53 s en
  contenedor. Se paga una vez al arrancar; el `start_period` del
  healthcheck de `docker-compose.yml` lo cubre, y el dashboard muestra un
  panel de "inicializando" mientras tanto.
- `pickle` además es un formato inseguro para cargar (ejecuta código en la
  deserialización); un GeoJSON no.

## Alternativas descartadas

- **Pickle del grafo como caché de arranque**: ahorra ~15-40 s por
  arranque a cambio de una segunda representación que se puede quedar
  vieja sin avisar. No compensa en un sistema que se arranca pocas veces.
