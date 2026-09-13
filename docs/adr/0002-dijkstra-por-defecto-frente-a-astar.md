# 2. Dijkstra por defecto; A\* como alternativa explícita

- Estado: aceptada
- Fecha: 2026-08-06
- Fase: P4

## Contexto

`routing/optimizer.py::calcular_ruta` puede resolver el camino mínimo con
Dijkstra o con A\* (`algoritmo="astar"`). A\* explora menos nodos si la
heurística es admisible, pero sobre este grafo el coste de arista no es la
distancia geométrica: es el tiempo de viaje ajustado por restricciones
físicas del vehículo y por un factor de tráfico (`TRAFFIC_FACTORS = {0:
1.0, 1: 1.5, 2: 3.0}`). Una heurística de distancia euclídea deja de ser
admisible en cuanto el tráfico multiplica tiempos, y una ruta subóptima en
un contexto de emergencia no es aceptable.

## Decisión

El algoritmo por defecto es **Dijkstra**. A\* queda disponible como opción
(`?algoritmo=astar` en `/ruta`, listado en `/config.algoritmos_disponibles`)
y como pieza de la comparativa de P4 (`routing/benchmark_astar.py`), pero
no es el camino por defecto.

## Consecuencias

- Garantía de optimalidad sin depender de que la heurística sea admisible
  bajo tráfico. El test lento de extremo a extremo verifica que A\* y
  Dijkstra devuelven la misma distancia (`rel=1e-6`) sobre el grafo real:
  si divergen, la heurística de A\* no es admisible.
- Sobre el grafo de Madrid la diferencia de tiempo de cómputo entre ambos
  es de milisegundos (medido en el bloque 8B: ~246 ms Dijkstra vs ~118 ms
  A\* para el par Chamberí → Puerta del Sol). El ahorro de A\* no justifica
  asumir su riesgo como opción por defecto.

## Alternativas descartadas

- **A\* por defecto con heurística de distancia / tiempo libre**: más
  rápido en papel, pero puede devolver rutas subóptimas cuando el factor
  de tráfico infla los tiempos.
