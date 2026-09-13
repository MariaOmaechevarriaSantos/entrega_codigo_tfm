# 6. Un único catálogo de vehículos, expuesto por la API

- Estado: aceptada
- Fecha: 2026-08-27
- Fase: P5 (bloque 3)

## Contexto

Antes de P5, Streamlit tenía su propio `ancho_map` con anchuras y gálibos
de vehículo, y la API tenía los suyos. Dos copias de las mismas constantes
que se desincronizan en cuanto alguien toca una: el bug del bloque 3 de P5
venía justo de ahí (el vehículo elegido en el dashboard no llegaba a
afectar al cálculo).

## Decisión

Una sola definición: `app/config.py::VEHICULOS` (que además importa
`ANCHO_CAMION_REQ` / `GALIBO_REQ` de `routing/optimizer.py`, ya
parametrizados por entorno en P4, en vez de recopiarlos). La API la expone
en `GET /config`; Streamlit la **consume por HTTP**, no mantiene ninguna
constante de vehículo propia. Regla transversal de la fase P5: "Prohibido
duplicar constantes entre Streamlit y la API" (ver
`docs/p5/README_P5_API_Streamlit.md`, sección 1).

## Consecuencias

- Añadir o cambiar un vehículo se hace en un sitio y se propaga a la API,
  al dashboard y a la resolución de `ancho_req`/`galibo_req` de `/ruta` e
  `/isocronas`.
- Toda respuesta de la API devuelve el vehículo efectivo usado
  (`parametros_efectivos.vehiculo`), incluido `"personalizado"` cuando se
  pasan `ancho_req`/`galibo_req` que no son los de ningún vehículo del
  catálogo.
- El dashboard no funciona sin la API arriba: aceptable, ya que tampoco
  puede calcular rutas sin ella.

## Alternativas descartadas

- **Módulo compartido importado por ambos**: obligaría a Streamlit a
  importar código del backend, rompiendo el invariante "Streamlit solo
  habla HTTP con la API".
