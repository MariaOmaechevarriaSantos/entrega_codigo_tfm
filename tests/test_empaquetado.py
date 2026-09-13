"""
Tests del empaquetado docker-compose — Fase P5, bloque 8.

Cubre SOLO lo que aporta este bloque:
  * scripts/reducir_duckdb.py      -> DuckDB de runtime = 2 tablas, nada más.
  * scripts/empaquetar_artefactos.py -> tarball determinista + manifiesto que
                                        detecta ficheros ausentes o alterados.
  * docker/data_init.py            -> puebla el volumen si falta / no cuadra;
                                       no descarga nada si ya está poblado.
  * app/wsgi.py                    -> carga el grafo al importarse (gunicorn).

Nada aquí toca data/processed real ni construye imágenes: se trabaja con
DuckDBs y ficheros de juguete en tmp_path.
"""
import hashlib
import importlib.util
import io
import json
import sys
import tarfile
from pathlib import Path

import duckdb
import pytest

REPO = Path(__file__).resolve().parent.parent


def _cargar_modulo(nombre: str, ruta: Path):
    """Importa un fichero suelto (scripts/, docker/) como módulo."""
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# scripts/reducir_duckdb.py
# ─────────────────────────────────────────────────────────────────────────
class TestReducirDuckdb:
    @pytest.fixture
    def mod(self):
        return _cargar_modulo("reducir_duckdb", REPO / "scripts" / "reducir_duckdb.py")

    def _duckdb_origen(self, path: Path, con_pipeline_runs=True, con_extra=True) -> None:
        con = duckdb.connect(str(path))
        # Mismo esquema que el real: equipamientos_por_zona lleva run_id, y
        # _load_equip_pivot filtra por el run_id más reciente de pipeline_runs.
        con.execute(
            "CREATE TABLE equipamientos_por_zona AS SELECT * FROM (VALUES "
            "('Centro','hospitales',3,'run-1'), ('Retiro','bomberos',1,'run-1')"
            ") t(zona,tipo,n_equipamientos,run_id)"
        )
        if con_pipeline_runs:
            con.execute("CREATE TABLE pipeline_runs AS SELECT 'run-1' AS run_id, now() AS fecha_ejecucion")
        if con_extra:
            # la tabla gorda que runtime NO lee: debe quedar fuera.
            con.execute("CREATE TABLE aforos_por_sensor AS SELECT range AS id FROM range(5000)")
        con.execute("CHECKPOINT")
        con.close()

    def test_incluye_exactamente_las_dos_tablas_de_runtime(self, mod, tmp_path):
        src, dst = tmp_path / "full.duckdb", tmp_path / "runtime.duckdb"
        self._duckdb_origen(src)

        filas = mod.reducir(str(src), str(dst))

        con = duckdb.connect(str(dst), read_only=True)
        tablas = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
        con.close()
        assert tablas == {"equipamientos_por_zona", "pipeline_runs"}
        assert "aforos_por_sensor" not in tablas
        assert filas == {"equipamientos_por_zona": 2, "pipeline_runs": 1}
        assert dst.stat().st_size < src.stat().st_size

    def test_filas_identicas_al_origen(self, mod, tmp_path):
        src, dst = tmp_path / "full.duckdb", tmp_path / "runtime.duckdb"
        self._duckdb_origen(src)
        mod.reducir(str(src), str(dst))

        s = duckdb.connect(str(src), read_only=True)
        d = duckdb.connect(str(dst), read_only=True)
        for t in ("equipamientos_por_zona", "pipeline_runs"):
            assert (s.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                    == d.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall())
        s.close()
        d.close()

    def test_origen_sin_tabla_de_runtime_falla_nombrandola(self, mod, tmp_path):
        src, dst = tmp_path / "full.duckdb", tmp_path / "runtime.duckdb"
        self._duckdb_origen(src, con_pipeline_runs=False)
        with pytest.raises(RuntimeError, match="pipeline_runs"):
            mod.reducir(str(src), str(dst))

    def test_origen_inexistente_falla(self, mod, tmp_path):
        with pytest.raises(FileNotFoundError):
            mod.reducir(str(tmp_path / "no_existe.duckdb"), str(tmp_path / "x.duckdb"))

    def test_predict_trafico_real_funciona_contra_la_reducida(self, mod, tmp_path, monkeypatch):
        """El consumidor real (_load_equip_pivot) pivota equipamientos_por_zona
        filtrando por el run_id de pipeline_runs -- justo las 2 tablas."""
        src, dst = tmp_path / "full.duckdb", tmp_path / "runtime.duckdb"
        self._duckdb_origen(src)
        mod.reducir(str(src), str(dst))

        import ml.predict_trafico_real as p
        monkeypatch.setattr(p, "_equip_pivot", None)
        monkeypatch.setenv("DUCKDB_PATH", str(dst))
        piv = p._load_equip_pivot()
        assert "zona" in piv.columns and len(piv) == 2


# ─────────────────────────────────────────────────────────────────────────
# scripts/empaquetar_artefactos.py
# ─────────────────────────────────────────────────────────────────────────
class TestEmpaquetarArtefactos:
    @pytest.fixture
    def mod(self, tmp_path, monkeypatch):
        m = _cargar_modulo("empaquetar_artefactos", REPO / "scripts" / "empaquetar_artefactos.py")
        # Aísla las salidas del repo real.
        monkeypatch.setattr(m, "DIST_DIR", str(tmp_path / "dist"))
        return m

    @pytest.fixture
    def processed(self, tmp_path):
        """tmp con un fichero por cada entrada de SET_MINIMO (contenido
        arbitrario pero estable)."""
        d = tmp_path / "processed"
        d.mkdir()
        m = _cargar_modulo("_ea_peek", REPO / "scripts" / "empaquetar_artefactos.py")
        for i, (src, _arc, _imp) in enumerate(m.SET_MINIMO):
            (d / src).write_bytes(b"contenido-de-prueba-%d\n" % i * 10)
        return d

    def test_manifiesto_lista_los_nueve_ficheros_con_hash_y_bytes(self, mod, processed):
        manifest = mod.construir(str(processed))
        assert len(manifest["ficheros"]) == len(mod.SET_MINIMO)
        for entrada in manifest["ficheros"]:
            # el nombre del manifiesto es el ARCNAME, no el de origen
            origen = next(s for s, arc, _ in mod.SET_MINIMO if arc == entrada["ruta"])
            real = processed / origen
            assert entrada["bytes"] == real.stat().st_size
            assert entrada["sha256"] == _sha256(real)

    def test_duckdb_viaja_con_el_nombre_de_runtime(self, mod, processed):
        manifest = mod.construir(str(processed))
        rutas = {e["ruta"] for e in manifest["ficheros"]}
        assert "tfm_madrid.duckdb" in rutas
        assert "tfm_madrid.runtime.duckdb" not in rutas

    def test_tarball_extrae_y_cuadra_con_el_manifiesto(self, mod, processed, tmp_path):
        manifest = mod.construir(str(processed))
        tarball = Path(mod.DIST_DIR) / mod.TARBALL_NAME
        assert _sha256(tarball) == manifest["tarball"]["sha256"]

        destino = tmp_path / "vol"
        destino.mkdir()
        with tarfile.open(tarball) as t:
            t.extractall(destino, filter="data")
        assert mod.verificar(str(destino), manifest) == []

    def test_verificar_detecta_fichero_alterado(self, mod, processed, tmp_path):
        manifest = mod.construir(str(processed))
        destino = tmp_path / "vol"
        destino.mkdir()
        with tarfile.open(Path(mod.DIST_DIR) / mod.TARBALL_NAME) as t:
            t.extractall(destino, filter="data")

        # Alteración SIN cambiar el tamaño: solo el sha256 puede detectarla.
        victima = destino / manifest["ficheros"][0]["ruta"]
        b = bytearray(victima.read_bytes())
        b[0] ^= 0xFF
        victima.write_bytes(bytes(b))
        problemas = mod.verificar(str(destino), manifest)
        assert any(p.startswith("sha256") for p in problemas)
        assert not any(p.startswith("tamaño") for p in problemas)

    def test_verificar_detecta_fichero_ausente(self, mod, processed, tmp_path):
        manifest = mod.construir(str(processed))
        destino = tmp_path / "vol"
        destino.mkdir()
        with tarfile.open(Path(mod.DIST_DIR) / mod.TARBALL_NAME) as t:
            t.extractall(destino, filter="data")
        (destino / manifest["ficheros"][2]["ruta"]).unlink()
        assert any(p.startswith("falta") for p in mod.verificar(str(destino), manifest))

    def test_tarball_es_determinista(self, mod, processed):
        h1 = mod.construir(str(processed))["tarball"]["sha256"]
        h2 = mod.construir(str(processed))["tarball"]["sha256"]
        assert h1 == h2
        # La única fuente plausible de no-determinismo es el mtime del gzip:
        # cabecera RFC 1952, bytes 4..8 (little-endian) = 0.
        cabecera = (Path(mod.DIST_DIR) / mod.TARBALL_NAME).read_bytes()[:10]
        assert cabecera[:2] == b"\x1f\x8b"
        assert int.from_bytes(cabecera[4:8], "little") == 0, "gzip mtime debe ser 0"

    def test_falta_un_artefacto_de_origen_falla(self, mod, processed):
        (processed / mod.SET_MINIMO[0][0]).unlink()
        with pytest.raises(FileNotFoundError):
            mod.construir(str(processed))


# ─────────────────────────────────────────────────────────────────────────
# docker/data_init.py
# ─────────────────────────────────────────────────────────────────────────
class TestDataInit:
    def _entorno(self, tmp_path, ficheros: dict[str, bytes]):
        """Crea manifiesto + tarball coherentes en tmp_path. Devuelve
        (manifest_path, bundle_path, dest_dir)."""
        ficheros_manifest = []
        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w") as tar:
            for nombre, contenido in sorted(ficheros.items()):
                ficheros_manifest.append({
                    "ruta": nombre,
                    "bytes": len(contenido),
                    "sha256": hashlib.sha256(contenido).hexdigest(),
                    "imprescindible": True,
                })
                info = tarfile.TarInfo(nombre)
                info.size = len(contenido)
                tar.addfile(info, io.BytesIO(contenido))
        import gzip
        gz = io.BytesIO()
        with gzip.GzipFile(fileobj=gz, mode="wb", mtime=0) as g:
            g.write(tar_buf.getvalue())
        bundle = tmp_path / "artifacts.tar.gz"
        bundle.write_bytes(gz.getvalue())

        manifest = {
            "schema": "test/1",
            "tarball": {"nombre": "artifacts.tar.gz", "bytes": len(gz.getvalue()),
                        "sha256": hashlib.sha256(gz.getvalue()).hexdigest()},
            "ficheros": ficheros_manifest,
        }
        mpath = tmp_path / "artifacts.manifest.json"
        mpath.write_text(json.dumps(manifest))
        dest = tmp_path / "vol"
        dest.mkdir()
        return mpath, bundle, dest

    def _cargar(self, monkeypatch, mpath, bundle, dest, url=""):
        monkeypatch.setenv("ARTIFACTS_MANIFEST", str(mpath))
        monkeypatch.setenv("ARTIFACTS_DIR", str(dest))
        monkeypatch.setenv("ARTIFACTS_BUNDLE", str(bundle))
        monkeypatch.setenv("ARTIFACTS_URL", url)
        return _cargar_modulo("data_init", REPO / "docker" / "data_init.py")

    def test_puebla_desde_tarball_local_y_verifica(self, tmp_path, monkeypatch, capsys):
        mpath, bundle, dest = self._entorno(tmp_path, {"a.txt": b"AAA", "b.bin": b"\x00\x01\x02"})
        di = self._cargar(monkeypatch, mpath, bundle, dest)

        assert di.main() == 0
        assert (dest / "a.txt").read_bytes() == b"AAA"
        assert (dest / "b.bin").read_bytes() == b"\x00\x01\x02"
        out = capsys.readouterr().out
        assert "tarball local" in out and "verificado" in out

    def test_segundo_arranque_no_descarga_ni_extrae(self, tmp_path, monkeypatch, capsys):
        mpath, bundle, dest = self._entorno(tmp_path, {"a.txt": b"AAA"})
        di = self._cargar(monkeypatch, mpath, bundle, dest)
        assert di.main() == 0
        capsys.readouterr()

        # Rompe el bundle y borra la URL: si intentara re-obtenerlo, fallaría.
        bundle.write_bytes(b"basura")
        di2 = self._cargar(monkeypatch, mpath, bundle, dest)
        marca = (dest / "a.txt").stat().st_mtime_ns
        assert di2.main() == 0
        assert (dest / "a.txt").stat().st_mtime_ns == marca  # no se reescribió
        assert "ya poblado" in capsys.readouterr().out

    def test_fichero_alterado_dispara_re_extraccion(self, tmp_path, monkeypatch):
        mpath, bundle, dest = self._entorno(tmp_path, {"a.txt": b"AAA", "b.txt": b"BBB"})
        di = self._cargar(monkeypatch, mpath, bundle, dest)
        assert di.main() == 0

        (dest / "a.txt").write_bytes(b"corrupto")
        di2 = self._cargar(monkeypatch, mpath, bundle, dest)
        assert di2.main() == 0
        assert (dest / "a.txt").read_bytes() == b"AAA"  # restaurado del bundle

    def test_sin_bundle_ni_url_sale_1(self, tmp_path, monkeypatch, capsys):
        mpath, bundle, dest = self._entorno(tmp_path, {"a.txt": b"AAA"})
        bundle.unlink()
        di = self._cargar(monkeypatch, mpath, bundle, dest, url="")
        assert di.main() == 1
        assert "no sé de dónde sacarlos" in capsys.readouterr().out

    def test_bundle_con_sha_incorrecto_aborta(self, tmp_path, monkeypatch):
        mpath, bundle, dest = self._entorno(tmp_path, {"a.txt": b"AAA"})
        di = self._cargar(monkeypatch, mpath, bundle, dest)
        # manipula el manifiesto para que el sha del tarball no cuadre
        m = json.loads(mpath.read_text())
        m["tarball"]["sha256"] = "0" * 64
        mpath.write_text(json.dumps(m))
        di2 = self._cargar(monkeypatch, mpath, bundle, dest)
        with pytest.raises(SystemExit):
            di2.main()

    def test_verificar_distingue_ok_ausente_y_alterado(self, tmp_path, monkeypatch):
        mpath, bundle, dest = self._entorno(tmp_path, {"a.txt": b"AAA", "b.txt": b"BBB"})
        di = self._cargar(monkeypatch, mpath, bundle, dest)
        manifest = json.loads(mpath.read_text())

        assert di._verificar(manifest)  # dest vacío -> problemas
        (dest / "a.txt").write_bytes(b"AAA")
        (dest / "b.txt").write_bytes(b"XXX")
        problemas = di._verificar(manifest)
        assert not any("a.txt" in p for p in problemas)
        assert any("b.txt" in p for p in problemas)


# ─────────────────────────────────────────────────────────────────────────
# app/wsgi.py
# ─────────────────────────────────────────────────────────────────────────
class TestWsgi:
    def test_importar_wsgi_carga_el_grafo(self, monkeypatch):
        """gunicorn app.wsgi:app tiene que dejar el grafo cargado sin pasar
        por el bloque __main__ de server.py."""
        import networkx as nx
        import routing.graph_engine as ge

        G = nx.DiGraph()
        G.add_node(1, x=0.0, y=0.0)
        G.add_node(2, x=1.0, y=1.0)
        G.add_edge(1, 2, length_m=1.0, travel_time_s=1.0)
        monkeypatch.setattr(ge, "load_graph", lambda *a, **k: G)

        for m in ("app.wsgi", "app.server"):
            sys.modules.pop(m, None)
        import app.wsgi as wsgi
        import app.server as srv

        assert wsgi.app is srv.app
        assert srv._graph_state["G"] is G
        assert srv._graph_state["load_seconds"] is not None

    def test_sin_wsgi_el_grafo_no_se_carga_al_importar_server(self, monkeypatch):
        """Contraste: importar app.server a secas NO carga el grafo (sigue
        siendo responsabilidad del __main__ o de wsgi.py)."""
        import networkx as nx
        import routing.graph_engine as ge
        monkeypatch.setattr(ge, "load_graph", lambda *a, **k: nx.DiGraph())

        for m in ("app.wsgi", "app.server"):
            sys.modules.pop(m, None)
        import app.server as srv
        assert srv._graph_state["G"] is None
