"""
Identidad de la build — Fase P5, bloque 8B (operabilidad).

Fuente ÚNICA de la versión de la API. La consume `GET /version` y el
`template` de flasgger en `app/server.py` (antes tenía `"1.0.0"` a pelo, en
dos sitios que podían desincronizarse).

Los otros tres datos de `/version` (SHA de git, fecha de build, hash del
manifiesto de datos) NO viven aquí: no se conocen hasta que se construye la
imagen. Entran como *build args* del `Dockerfile.api` y se leen de entorno
en tiempo de petición (ver `build_info()`), con `"desconocido"` como valor
por defecto para que `docker compose up` a secas —sin pasar los args— no
rompa el endpoint ni el arranque.
"""
import os

# Versión semántica del contrato de la API. Subir a mano cuando el contrato
# de `docs/p5/openapi_p5.yaml` cambie de forma incompatible.
API_VERSION = "1.0.0"


def build_info() -> dict:
    """
    Identidad de la build en curso, para `GET /version`.

    - `git_sha`: se emite tal cual llega en la variable de entorno. El
      `Makefile` lo inyecta ya recortado (`git rev-parse --short=12`): no
      se sirve el SHA completo de 40 caracteres para no entregar el commit
      exacto servido en bandeja a un repo público (ver "Escalado de
      seguridad" en docs/p5/README_P5_API_Streamlit.md).
    - `fecha_build`: ISO-8601 UTC, inyectada en el build.
    - `sha256_manifiesto_datos`: sha256 de `artifacts.manifest.json` con el
      que se empaquetó el set de datos. Ancla qué versión de los artefactos
      espera esta imagen; la comprobación fichero a fichero real la hace
      `docker/data_init.py` contra el volumen.

    Cualquiera de los tres es `"desconocido"` si la imagen se construyó sin
    pasar el build arg correspondiente (p. ej. un `docker build` a mano).
    """
    return {
        "version_api": API_VERSION,
        "git_sha": os.environ.get("GIT_SHA", "desconocido"),
        "fecha_build": os.environ.get("BUILD_DATE", "desconocido"),
        "sha256_manifiesto_datos": os.environ.get("MANIFEST_SHA256", "desconocido"),
    }
