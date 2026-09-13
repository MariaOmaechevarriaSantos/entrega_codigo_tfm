# 3. La meteorología no entra en el modelo de tráfico

- Estado: aceptada
- Fecha: 2026-08-03 (Fase P3), reafirmada en Fase P5 bloque 6
- Fases: P3, P5

## Contexto

El modelo XGBoost de P3 predice el nivel de tráfico por distrito a partir
de features de calendario y de equipamientos por zona. Se evaluó añadir
meteorología (temperatura, humedad, precipitación, viento). La
meteorología histórica de P1 solo tiene cobertura suficiente por distrito
en temperatura y humedad; el resto de magnitudes tienen demasiados huecos.
Además, servir el modelo con features meteorológicas obligaría a obtener
meteorología **en vivo** en cada petición de ruta, con su modo de fallo
propio (feed caído) en el camino crítico.

## Decisión

El modelo **no usa features meteorológicas** (`metadata["use_weather_features"]
= false`; `ml/predict_trafico_real` se **niega a cargar** un modelo que las
declare). La meteorología queda como panel **informativo** en el dashboard
(`GET /meteorologia`, feed municipal en vivo), y su alerta no modifica el
cálculo de la ruta — la interfaz lo dice explícitamente.

## Consecuencias

- El cálculo de ruta no depende de un feed externo de meteorología. Si el
  feed municipal está caído, `/ruta` sigue funcionando igual; solo el
  panel informativo se degrada (sirve el último snapshot con su edad).
- Si en el futuro se entrena un modelo con meteorología, hay que **extender**
  `ml/predict_trafico_real` para obtenerla en vivo, no saltarse la
  comprobación. El propio wrapper lo dice en el mensaje de error con el que
  se niega a cargar ese modelo.

## Alternativas descartadas

- **Modelo con features meteorológicas en vivo**: mete una dependencia de
  red en el camino crítico de una función de emergencia, a cambio de una
  mejora de precisión no demostrada con los datos disponibles.
