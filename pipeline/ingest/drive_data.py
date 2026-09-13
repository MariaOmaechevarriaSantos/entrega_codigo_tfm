"""
Descarga los artefactos compartidos del TFM desde la carpeta publica de
Google Drive y los coloca en las rutas que consume el resto del proyecto.

La carpeta de Drive actua como bootstrap: permite crear `data/raw/` y
`data/processed/` sin tener que ejecutar la descarga completa de OSM/DuckDB.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from http.cookiejar import CookieJar
from urllib.parse import urlencode, urljoin
from urllib.request import HTTPCookieProcessor, build_opener

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_OSM_DIR = DATA_DIR / "raw" / "osm"
PROCESSED_DIR = DATA_DIR / "processed"

DRIVE_FOLDER_URL = "https://drive.google.com/drive/folders/1EEQOTgMklJWtYIg1126H5_mujV3i2Rf-?usp=sharing"


@dataclass(frozen=True)
class DriveArtifact:
    file_id: str
    output_path: Path


class _DownloadFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_download_form = False
        self.action = None
        self.params = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "form" and attrs.get("id") == "download-form":
            self.in_download_form = True
            self.action = attrs.get("action")
            return
        if self.in_download_form and tag == "input":
            name = attrs.get("name")
            if name:
                self.params[name] = attrs.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form" and self.in_download_form:
            self.in_download_form = False


DRIVE_ARTIFACTS = (
    DriveArtifact(
        file_id="1ys-jEcofPeIht5nDSWq4a5y7J4VjWvj5",
        output_path=RAW_OSM_DIR / "madrid_calles_raw.geojson",
    ),
    DriveArtifact(
        file_id="1RTqpiI2MhvxDGhCnsQQu90hnawjRtb2d",
        output_path=PROCESSED_DIR / "madrid_callejero_filtered.geojson",
    ),
    DriveArtifact(
        file_id="1SlRMGEFJ3-WZg40IpHDSYB_Mvv3bxjaW",
        output_path=PROCESSED_DIR / "tfm_madrid.duckdb",
    ),
)


def create_data_dirs() -> None:
    """Crea la estructura de carpetas de datos que usan pipeline, ML y routing."""
    RAW_OSM_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)


def _confirm_token(cookies: CookieJar) -> str | None:
    """Extrae el token de confirmacion que Google Drive usa en ficheros grandes."""
    for cookie in cookies:
        if cookie.name.startswith("download_warning"):
            return cookie.value
    return None


def _save_response_content(response, output_path: Path) -> None:
    tmp_path = output_path.with_suffix(output_path.suffix + ".part")
    with tmp_path.open("wb") as f:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
    tmp_path.replace(output_path)


def _looks_like_html(path: Path) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as f:
        head = f.read(256).lstrip().lower()
    return head.startswith(b"<!doctype html") or head.startswith(b"<html")


def _is_valid_existing_artifact(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0 and not _looks_like_html(path)


def _download_url(file_id: str, confirm: str | None = None) -> str:
    params = {"export": "download", "id": file_id}
    if confirm:
        params["confirm"] = confirm
    return f"https://drive.google.com/uc?{urlencode(params)}"


def _warning_form_download_url(html: str) -> str | None:
    parser = _DownloadFormParser()
    parser.feed(html)
    if not parser.action or not parser.params:
        return None
    return f"{urljoin('https://drive.google.com', parser.action)}?{urlencode(parser.params)}"


def download_drive_file(file_id: str, output_path: Path, force: bool = False) -> Path:
    """
    Descarga un fichero publico de Google Drive por id.

    Si `output_path` ya existe y no esta vacio, se conserva salvo `force=True`.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if _is_valid_existing_artifact(output_path) and not force:
        logger.info("Drive bootstrap: %s ya existe, se conserva.", output_path)
        return output_path
    if _looks_like_html(output_path):
        logger.warning("Drive bootstrap: %s contiene HTML, se volvera a descargar.", output_path)

    logger.info("Drive bootstrap: descargando %s -> %s", file_id, output_path)

    cookies = CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))

    response = opener.open(_download_url(file_id), timeout=60)
    token = _confirm_token(cookies)
    if token:
        response.close()
        response = opener.open(_download_url(file_id, confirm=token), timeout=60)
    elif "text/html" in response.headers.get("Content-Type", ""):
        html = response.read().decode("utf-8", errors="replace")
        response.close()
        warning_url = _warning_form_download_url(html)
        if warning_url is None:
            raise RuntimeError(f"Google Drive devolvio HTML y no se encontro formulario de descarga para {file_id}.")
        response = opener.open(warning_url, timeout=60)

    with response:
        _save_response_content(response, output_path)

    if _looks_like_html(output_path):
        raise RuntimeError(f"Google Drive guardo HTML en vez del artefacto esperado: {output_path}")

    logger.info(
        "Drive bootstrap: guardado %s (%.1f MB)",
        output_path,
        output_path.stat().st_size / (1024 * 1024),
    )
    return output_path


def bootstrap_drive_data(force: bool = False) -> list[Path]:
    """
    Crea `data/` y descarga los artefactos compartidos si faltan.

    Returns:
        Lista de rutas locales disponibles tras el bootstrap.
    """
    create_data_dirs()
    return [
        download_drive_file(artifact.file_id, artifact.output_path, force=force)
        for artifact in DRIVE_ARTIFACTS
    ]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    bootstrap_drive_data(force=os.environ.get("FORCE_DRIVE_DOWNLOAD") == "1")
