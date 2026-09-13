# 8. Operabilidad = reproducibilidad y mantenimiento, no escalado

- Estado: aceptada
- Fecha: 2026-08-30
- Fase: P5 (bloque 8B)

## Contexto

El bloque 8 empaquetó el sistema con `docker compose`; el 8B añade
diagnóstico de build (`GET /version`), logs estructurados JSON con
`request_id`, métricas Prometheus (`GET /metrics`), CI en GitHub Actions,
estas ADR y un `Makefile`. Todo esto puede confundirse con "preparar el
sistema para escalar". No es el caso, y el capítulo de P1 ya justificó
**explícitamente** haber rechazado la sobre-ingeniería (sin almacenamiento
de objetos, sin *data warehouse*) por desproporcionada para un sistema de
**un único usuario** — un puesto de central de bomberos, no un servicio
multiusuario.

## Decisión

Lo del bloque 8B se presenta —en el `README_P5` y en estas ADR— como
**reproducibilidad y mantenibilidad después de la defensa**: saber qué
versión corre, poder seguir una petición en los logs, tener un objetivo de
`make` para cada tarea y una CI que avise si algo se rompe. **No** como
capacidad de escalado.

Se descartan a propósito, y se dice cuál y por qué:

| Descartado | Por qué |
|---|---|
| **Kubernetes** (u orquestador) | Un host, un proceso de API con un worker, un dashboard. `docker compose` levanta y para todo. Un orquestador añade plano de control, manifiestos y operación sin resolver ningún problema real aquí. |
| **Base de datos gestionada** | El estado que se consulta en runtime son ~76 filas (ADR 0004). Un fichero DuckDB de ~780 KiB basta y no necesita servidor, cuenta ni copias de seguridad. MotherDuck queda como opción tras variable de entorno, nunca por defecto. |
| **Colas / mensajería** | No hay trabajo asíncrono. `/ruta` e `/isocronas` son síncronos y responden en cientos de ms (Dijkstra) o pocos segundos (isócronas en vivo). Una cola solo añadiría un broker que mantener. |
| **Registro de contenedores** (GHCR, Docker Hub) | Las imágenes se construyen en local (`docker compose build`) y, sin red, se pasan con `docker save` (~423 MB). Un registro añade cuenta, credenciales en CI y limpieza de tags (ADR 0005). |
| **Stack Prometheus + Grafana** | `GET /metrics` expone las series en texto; para un usuario único que arranca el sistema bajo demanda, levantar Prometheus + Grafana + dashboards sería el mismo tipo de desproporción que P1 ya rechazó. El endpoint queda por si algún día se le apunta un scraper externo. |
| **Extraer el entrenamiento del modelo a un script CLI** | El modelo de runtime se regenera hoy desde `notebooks/05_...ipynb` (ver `README_P5`, análisis del punto 7). Portarlo a `ml/train_trafico_xgboost.py` verificable cuesta ~1-1,5 días y depende de un dataset que no está confirmado como reproducible. Se documenta como deuda conocida, no se hace en este TFM. |

## Consecuencias

- Coherencia con el capítulo P1: el discurso "no sobre-ingeniería para un
  usuario único" se mantiene de P1 a P5 sin contradicción.
- Si el proyecto pasara a multiusuario o a producción real, esta ADR (y la
  sección "Escalado de seguridad" del `README_P5`) marcan qué habría que
  reconsiderar: no se ha cerrado ninguna puerta, solo no se ha construido
  lo que hoy no hace falta.
- El coste de operabilidad de 8B es bajo y reversible: endpoints de
  diagnóstico, un formato de log, ficheros de CI y un `Makefile`. Nada de
  esto es infraestructura que haya que operar.
