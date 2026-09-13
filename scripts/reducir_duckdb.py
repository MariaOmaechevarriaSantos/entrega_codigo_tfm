"""
Genera la DuckDB *reducida* de runtime a partir de la DuckDB completa del
pipeline — Fase P5, bloque 8 (empaquetado).

POR QUÉ
-------
`data/processed/tfm_madrid.duckdb` pesa ~788 MiB, dominada por
`aforos_por_sensor` (~99,9 M filas) y `aforos_historicos` / `meteorologia_historica`.
El proceso que sirve la API (`app/server.py`) NO lee ninguna de esas tablas.
Lo único que toca DuckDB en runtime es:

  - `ml/predict_trafico_real.py::_load_equip_pivot()` -> `SELECT ... FROM
    equipamientos_por_zona` (75 filas) y, si existe, `pipeline_runs` para
    quedarse con el `run_id` más reciente.
  - `app/server.py::/health` -> `SELECT 1` (solo comprueba que el fichero abre).

Así que la imagen solo necesita esas dos tablas. Medido (bloque 8):
788 MiB -> ~780 KiB.

QUÉ INCLUYE Y POR QUÉ
--------------------
  equipamientos_por_zona   imprescindible: features n_{tipo} por distrito que
                           el wrapper de tráfico pivota en cada arranque.
  pipeline_runs            imprescindible: sin ella el wrapper no puede filtrar
                           por el run_id más reciente (cae a "todas las filas",
                           que con un único run da igual, pero el contrato de
                           _load_equip_pivot la espera y test la cubre).

Todo lo demás (aforos_historicos, aforos_por_sensor, meteorologia_historica)
se deja fuera a propósito: no hay ruta de código en runtime que las lea.

DETERMINISMO
-----------
El contenido lógico es determinista: mismas tablas, mismas filas, mismo orden
de almacenamiento que el origen. El fichero .duckdb resultante puede NO ser
idéntico byte a byte entre ejecuciones (metadatos internos del formato), por
lo que el sha256 que se publica en artifacts.manifest.json ANCLA UNA BUILD
concreta: si regeneras la DuckDB reducida, vuelve a empaquetar y re-commitea
el manifiesto (ver scripts/empaquetar_artefactos.py).

USO
---
    python scripts/reducir_duckdb.py
    python scripts/reducir_duckdb.py --src ruta/otra.duckdb --dst /tmp/mini.duckdb
"""
from __future__ import annotations

import argparse
import os
import sys

import duckdb

_AQUI = os.path.dirname(os.path.abspath(__file__))
_PROCESSED = os.path.normpath(os.path.join(_AQUI, "..", "data", "processed"))

SRC_DEFAULT = os.path.join(_PROCESSED, "tfm_madrid.duckdb")
DST_DEFAULT = os.path.join(_PROCESSED, "tfm_madrid.runtime.duckdb")

# Orden fijo -> salida estable. Cambiar esta tupla es el único punto donde se
# decide qué viaja a la imagen.
TABLAS_RUNTIME: tuple[str, ...] = ("equipamientos_por_zona", "pipeline_runs")


def reducir(src: str, dst: str) -> dict[str, int]:
    """Escribe `dst` con solo TABLAS_RUNTIME copiadas de `src`. Devuelve
    {tabla: n_filas}. Lanza si `src` no existe o le falta alguna tabla."""
    if not os.path.exists(src):
        raise FileNotFoundError(
            f"DuckDB de origen no encontrada: {src}. "
            "Ejecuta antes el pipeline (python pipeline/run_pipeline.py)."
        )

    src_con = duckdb.connect(src, read_only=True)
    try:
        presentes = {t[0] for t in src_con.execute("SHOW TABLES").fetchall()}
    finally:
        src_con.close()

    faltan = [t for t in TABLAS_RUNTIME if t not in presentes]
    if faltan:
        raise RuntimeError(
            f"A la DuckDB de origen le faltan tablas de runtime: {faltan}. "
            "¿Pipeline incompleto? Tablas presentes: " + ", ".join(sorted(presentes))
        )

    if os.path.exists(dst):
        os.remove(dst)
    dst_dir = os.path.dirname(dst) or "."
    os.makedirs(dst_dir, exist_ok=True)

    con = duckdb.connect(dst)
    filas: dict[str, int] = {}
    try:
        # ATTACH no admite parámetros ligados para la ruta; `src` viene de
        # argparse (entrada local de confianza). Se escapan comillas simples.
        src_sql = src.replace("'", "''")
        con.execute(f"ATTACH '{src_sql}' AS src (READ_ONLY)")
        for tabla in TABLAS_RUNTIME:
            con.execute(f"CREATE TABLE {tabla} AS SELECT * FROM src.{tabla}")
            filas[tabla] = con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        con.execute("DETACH src")
        con.execute("CHECKPOINT")
    finally:
        con.close()

    # Verificación: la reducida abre y las filas cuadran con el origen.
    src_con = duckdb.connect(src, read_only=True)
    chk = duckdb.connect(dst, read_only=True)
    try:
        for tabla in TABLAS_RUNTIME:
            n_src = src_con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
            n_dst = chk.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
            if n_src != n_dst:
                raise RuntimeError(
                    f"Verificación fallida en {tabla}: origen {n_src} != reducida {n_dst}."
                )
    finally:
        src_con.close()
        chk.close()

    return filas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", default=SRC_DEFAULT, help=f"DuckDB completa de origen (def: {SRC_DEFAULT})")
    parser.add_argument("--dst", default=DST_DEFAULT, help=f"DuckDB reducida de salida (def: {DST_DEFAULT})")
    args = parser.parse_args(argv)

    filas = reducir(args.src, args.dst)

    tam_src = os.path.getsize(args.src)
    tam_dst = os.path.getsize(args.dst)
    print(f"origen : {args.src}  ({tam_src:,} B)")
    print(f"salida : {args.dst}  ({tam_dst:,} B)")
    for tabla, n in filas.items():
        print(f"  + {tabla}: {n:,} filas")
    print(f"reducción: {tam_src / tam_dst:,.0f}x  ({tam_src:,} -> {tam_dst:,} B)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
