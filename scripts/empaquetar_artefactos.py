"""
Empaqueta el *set mínimo* de artefactos de runtime en artifacts.tar.gz y
emite su manifiesto — Fase P5, bloque 8 (empaquetado).

QUÉ ENTRA (y por qué)
--------------------
  madrid_callejero_filtered.geojson  IMPRESCINDIBLE  grafo viario (routing/graph_engine)
  parques_bomberos.geojson           IMPRESCINDIBLE  nodos especiales 'bomberos', /isocronas
  hospitales.geojson                 IMPRESCINDIBLE  nodos especiales 'hospitales', /equipamientos?tipo=hospitales
  modelo_trafico_xgboost.pkl         IMPRESCINDIBLE  ml/predict_trafico_real (/ruta, /prediccion_trafico)
  modelo_trafico_xgboost_metadata.json  IMPRESCINDIBLE  idem (valida use_weather/lag_features)
  tfm_madrid.duckdb                  IMPRESCINDIBLE  equipamientos_por_zona + pipeline_runs
                                                     (viene de scripts/reducir_duckdb.py: ~780 KiB, no 788 MiB)
  isocronas_bomberos.geojson         IMPRESCINDIBLE  /isocronas por defecto sin recorrer el grafo (~6 s -> ms)
  centros_educativos.geojson         OPCIONAL        /equipamientos?tipo=centros_educativos
  centros_mayores.geojson            OPCIONAL        /equipamientos?tipo=centros_mayores

QUÉ NO ENTRA: aforos_dataset_xgboost.csv (solo entrenamiento),
madrid_calles_raw.geojson (solo pipeline --data-source raw), la DuckDB
completa (99,9 M filas que runtime no lee).

EL MANIFIESTO (artifacts.manifest.json, SE COMMITEA)
--------------------------------------------------
Lista, por fichero: nombre, bytes y sha256. `data-init` verifica cada
fichero del volumen contra ESTA lista. Manifiesto y tarball son ficheros
distintos: lo que se comprueba es cada fichero YA EXTRAÍDO en el volumen
contra la lista, no el tarball contra sí mismo. Eso es lo que detecta una
extracción incompleta, un volumen corrupto o un paquete que no corresponde
a esta versión del código.

Ambos viajan en el repositorio (dist/artifacts.tar.gz son 5,3 MB): el
tribunal clona y levanta, sin red ni credenciales. `ARTIFACTS_URL` sigue
disponible para servir el tarball desde un GitHub Release, pero ya no es
necesaria para arrancar.

El manifiesto guarda además el sha256 del propio tarball, como control de
integridad de la copia o la descarga (no como única verificación).

USO
---
    python scripts/reducir_duckdb.py           # antes: genera tfm_madrid.runtime.duckdb
    python scripts/empaquetar_artefactos.py    # genera dist/artifacts.tar.gz + artifacts.manifest.json
    python scripts/empaquetar_artefactos.py --verify /ruta/al/volumen   # comprueba un directorio contra el manifiesto
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile

_AQUI = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_AQUI, ".."))
_PROCESSED = os.path.join(_REPO, "data", "processed")

MANIFEST_PATH = os.path.join(_REPO, "artifacts.manifest.json")
DIST_DIR = os.path.join(_REPO, "dist")
TARBALL_NAME = "artifacts.tar.gz"

MANIFEST_SCHEMA = "tfm-bomberos/artefactos-runtime/1"

# (ruta_origen_relativa_a_data/processed, nombre_en_el_tarball, imprescindible)
# El orden fija el orden de escritura en el tar -> salida estable.
SET_MINIMO: tuple[tuple[str, str, bool], ...] = (
    ("madrid_callejero_filtered.geojson", "madrid_callejero_filtered.geojson", True),
    ("parques_bomberos.geojson", "parques_bomberos.geojson", True),
    ("hospitales.geojson", "hospitales.geojson", True),
    ("modelo_trafico_xgboost.pkl", "modelo_trafico_xgboost.pkl", True),
    ("modelo_trafico_xgboost_metadata.json", "modelo_trafico_xgboost_metadata.json", True),
    # La reducida (scripts/reducir_duckdb.py) viaja CON EL NOMBRE que espera
    # el runtime (app/server.py: ARTEFACTOS_ESPERADOS["duckdb"]).
    ("tfm_madrid.runtime.duckdb", "tfm_madrid.duckdb", True),
    ("isocronas_bomberos.geojson", "isocronas_bomberos.geojson", True),
    ("centros_educativos.geojson", "centros_educativos.geojson", False),
    ("centros_mayores.geojson", "centros_mayores.geojson", False),
)

_BLOQUE = 1024 * 1024


def _sha256_fichero(path: str) -> tuple[str, int]:
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


def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def construir(processed_dir: str = _PROCESSED) -> dict:
    """Genera dist/artifacts.tar.gz y devuelve el dict del manifiesto."""
    faltan = [
        src for src, _, _ in SET_MINIMO
        if not os.path.exists(os.path.join(processed_dir, src))
    ]
    if faltan:
        raise FileNotFoundError(
            "Faltan artefactos para empaquetar: " + ", ".join(faltan)
            + ".\n¿Has ejecutado el pipeline y scripts/reducir_duckdb.py?"
        )

    os.makedirs(DIST_DIR, exist_ok=True)
    tar_path = os.path.join(DIST_DIR, TARBALL_NAME)

    ficheros_manifest: list[dict] = []
    # Tar determinista: mtime fijo, uid/gid 0, sin nombres de dueño, orden fijo.
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w", format=tarfile.PAX_FORMAT) as tar:
        for src, arcname, imprescindible in SET_MINIMO:
            ruta = os.path.join(processed_dir, src)
            sha, size = _sha256_fichero(ruta)
            ficheros_manifest.append({
                "ruta": arcname,
                "bytes": size,
                "sha256": sha,
                "imprescindible": imprescindible,
            })
            info = tar.gettarinfo(ruta, arcname=arcname)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o644
            with open(ruta, "rb") as fh:
                tar.addfile(info, fh)

    # gzip con mtime=0 -> salida byte a byte estable entre ejecuciones.
    gz_buf = io.BytesIO()
    with gzip.GzipFile(fileobj=gz_buf, mode="wb", compresslevel=9, mtime=0) as gz:
        gz.write(tar_buf.getvalue())
    tar_bytes = gz_buf.getvalue()
    with open(tar_path, "wb") as f:
        f.write(tar_bytes)
    tar_sha = _sha256_bytes(tar_bytes)
    with open(tar_path + ".sha256", "w", encoding="utf-8") as f:
        f.write(f"{tar_sha}  {TARBALL_NAME}\n")

    # Sin timestamp a propósito: el manifiesto debe salir byte a byte igual
    # al regenerarlo (git status limpio si los artefactos no cambiaron).
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "tarball": {"nombre": TARBALL_NAME, "bytes": len(tar_bytes), "sha256": tar_sha},
        "ficheros": ficheros_manifest,
    }
    return manifest


def _leer_manifest(path: str = MANIFEST_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def verificar(directorio: str, manifest: dict | None = None, solo_imprescindibles: bool = False) -> list[str]:
    """Comprueba `directorio` contra el manifiesto. Devuelve lista de
    problemas (vacía = todo correcto)."""
    manifest = manifest or _leer_manifest()
    problemas: list[str] = []
    for entrada in manifest["ficheros"]:
        if solo_imprescindibles and not entrada.get("imprescindible", True):
            continue
        ruta = os.path.join(directorio, entrada["ruta"])
        if not os.path.exists(ruta):
            problemas.append(f"falta: {entrada['ruta']}")
            continue
        sha, size = _sha256_fichero(ruta)
        if size != entrada["bytes"]:
            problemas.append(f"tamaño: {entrada['ruta']} ({size} != {entrada['bytes']})")
        if sha != entrada["sha256"]:
            problemas.append(f"sha256: {entrada['ruta']}")
    return problemas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", metavar="DIR", help="verifica DIR contra artifacts.manifest.json y sale")
    parser.add_argument("--processed-dir", default=_PROCESSED, help="dónde están los artefactos de origen")
    args = parser.parse_args(argv)

    if args.verify:
        problemas = verificar(args.verify)
        if problemas:
            print("MANIFIESTO NO CUADRA:")
            for p in problemas:
                print("  -", p)
            return 1
        print(f"OK: {args.verify} cuadra con el manifiesto.")
        return 0

    manifest = construir(args.processed_dir)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    total = sum(x["bytes"] for x in manifest["ficheros"])
    print(f"manifiesto : {MANIFEST_PATH}")
    print(f"tarball    : {os.path.join(DIST_DIR, TARBALL_NAME)}  ({manifest['tarball']['bytes']:,} B)")
    print(f"  sha256   : {manifest['tarball']['sha256']}")
    print(f"ficheros   : {len(manifest['ficheros'])}  ({total:,} B sin comprimir)")
    for x in manifest["ficheros"]:
        flag = "" if x["imprescindible"] else "  (opcional)"
        print(f"  {x['ruta']:38s} {x['bytes']:>12,} B  {x['sha256'][:12]}…{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
