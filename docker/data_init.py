"""
Servicio `data-init` de docker-compose — Fase P5, bloque 8.

Puebla el volumen de `data/processed` ANTES de que arranque la API:

  1. Verifica el volumen contra artifacts.manifest.json (fichero a fichero:
     existe + bytes + sha256). Si todo cuadra -> no hace nada y sale 0.
     (2.º arranque: no descarga nada.)
  2. Si falta algo o un hash no cuadra, consigue artifacts.tar.gz:
       a. si hay un tarball local montado ($ARTIFACTS_BUNDLE) -> lo usa
          (RUTA OFFLINE, sin red);
       b. si no, y hay $ARTIFACTS_URL -> lo descarga;
       c. si no hay ninguno -> sale 1 explicando cómo conseguirlo.
     En a y b se verifica el sha256 del tarball contra el manifiesto.
  3. Extrae en el volumen y vuelve a verificar. Sale 0 si queda correcto.

Solo stdlib: la imagen de este servicio es python:3.14-slim pelado.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
import tempfile
import urllib.request

MANIFEST = os.environ.get("ARTIFACTS_MANIFEST", "/opt/tfm/artifacts.manifest.json")
DEST_DIR = os.environ.get("ARTIFACTS_DIR", "/artefactos")
BUNDLE_LOCAL = os.environ.get("ARTIFACTS_BUNDLE", "/bundle/artifacts.tar.gz")
BUNDLE_URL = os.environ.get("ARTIFACTS_URL", "").strip()

_BLOQUE = 1024 * 1024


def _log(msg: str) -> None:
    print(f"[data-init] {msg}", flush=True)


def _sha256(path: str) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_BLOQUE)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def _cargar_manifest() -> dict:
    if not os.path.exists(MANIFEST):
        _log(f"ERROR: no encuentro el manifiesto en {MANIFEST}.")
        sys.exit(1)
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def _verificar(manifest: dict) -> list[str]:
    problemas: list[str] = []
    for entrada in manifest["ficheros"]:
        ruta = os.path.join(DEST_DIR, entrada["ruta"])
        if not os.path.exists(ruta):
            problemas.append(f"falta {entrada['ruta']}")
            continue
        sha, size = _sha256(ruta)
        if size != entrada["bytes"]:
            problemas.append(f"bytes {entrada['ruta']} ({size} != {entrada['bytes']})")
        elif sha != entrada["sha256"]:
            problemas.append(f"sha256 {entrada['ruta']}")
    return problemas


def _verificar_tarball(path: str, manifest: dict) -> None:
    esperado = manifest["tarball"]["sha256"]
    sha, size = _sha256(path)
    if sha != esperado:
        _log(f"ERROR: sha256 del tarball no cuadra con el manifiesto ({sha} != {esperado}).")
        sys.exit(1)
    _log(f"tarball verificado: {size:,} B, sha256 OK.")


def _descargar(url: str, destino: str) -> None:
    _log(f"descargando {url} …")
    req = urllib.request.Request(url, headers={"User-Agent": "tfm-data-init/1.0"})
    with urllib.request.urlopen(req, timeout=120) as resp, open(destino, "wb") as out:
        while True:
            chunk = resp.read(_BLOQUE)
            if not chunk:
                break
            out.write(chunk)


def _extraer(tarball: str) -> None:
    _log(f"extrayendo en {DEST_DIR} …")
    os.makedirs(DEST_DIR, exist_ok=True)
    with tarfile.open(tarball, "r:gz") as tar:
        # filter="data" (default en 3.14): rechaza rutas absolutas, '..',
        # enlaces y dispositivos. Los miembros del bundle son ficheros planos.
        tar.extractall(DEST_DIR, filter="data")


def main() -> int:
    manifest = _cargar_manifest()
    _log(f"manifiesto: {len(manifest['ficheros'])} ficheros; destino: {DEST_DIR}")

    problemas = _verificar(manifest)
    if not problemas:
        _log("volumen ya poblado y verificado — no se descarga nada.")
        return 0
    _log(f"faltan/no cuadran {len(problemas)} ficheros: " + "; ".join(problemas[:6])
         + (" …" if len(problemas) > 6 else ""))

    with tempfile.TemporaryDirectory() as tmp:
        if os.path.exists(BUNDLE_LOCAL):
            _log(f"usando tarball local {BUNDLE_LOCAL} (sin red).")
            tarball = BUNDLE_LOCAL
        elif BUNDLE_URL:
            tarball = os.path.join(tmp, "artifacts.tar.gz")
            _descargar(BUNDLE_URL, tarball)
        else:
            _log(
                "ERROR: no hay artefactos y no sé de dónde sacarlos.\n"
                f"  - monta el tarball en {BUNDLE_LOCAL}, o\n"
                "  - define ARTIFACTS_URL con la URL del Release.\n"
                "  Genéralo con: python scripts/reducir_duckdb.py && python scripts/empaquetar_artefactos.py"
            )
            return 1

        _verificar_tarball(tarball, manifest)
        _extraer(tarball)

    problemas = _verificar(manifest)
    if problemas:
        _log("ERROR: tras extraer, el volumen sigue sin cuadrar: " + "; ".join(problemas))
        return 1
    _log("volumen poblado y verificado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
