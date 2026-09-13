"""
Página "Sistema" — diagnóstico del servicio de rutas.

Muestra el contenido de GET /health en formato legible para un operador:
estado del servicio, grafo cargado, modelo de tráfico, DuckDB, artefactos
que faltan y marca de tiempo. No añade lógica: solo formatea el JSON que
la API ya devuelve. Mismo andamiaje institucional que el Panel
(DESIGN_SYSTEM.md). Todo por HTTP.
"""
import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from app._ui_common import (  # noqa: E402
    css_operativo,
    fijar_idioma,
    humanizar_error,
    ocultar_barra_lateral,
    render_breadcrumb,
    render_footer,
    render_header,
    salud_api,
)

st.set_page_config(
    page_title="Sistema — Central de rutas",
    page_icon=":material/monitor_heart:",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.html(css_operativo())
fijar_idioma()
ocultar_barra_lateral()  # esta página no tiene parámetros: la barra sobra

status, health = salud_api()

render_header(status, health, activa="sistema")
render_breadcrumb("Inicio", "Sistema")
st.title("Estado del sistema", anchor=False)
st.html(
    '<p class="inst-lead">Diagnóstico en vivo del servicio de rutas: grafo de '
    'calles, modelo de tráfico, base de datos y artefactos. Se actualiza al '
    'pulsar «Actualizar».</p>'
)

if st.button("Actualizar", icon=":material/refresh:"):
    st.rerun()

if health is None:
    st.error(humanizar_error(status, health, "al consultar el estado"), icon=":material/error:")
    render_footer()
    st.stop()

grafo = health.get("grafo", {})
modelo = health.get("modelo_trafico", {})
duckdb_info = health.get("duckdb", {})
faltantes = health.get("artefactos_faltantes", [])

col1, col2 = st.columns(2, gap="medium")

with col1:
    st.markdown(":material/hub: **Grafo de calles**")
    if grafo.get("cargado"):
        mc1, mc2 = st.columns(2, gap="small")
        mc1.metric("Nodos", f"{grafo.get('nodes'):,}".replace(",", "."), icon=":material/scatter_plot:")
        mc2.metric("Aristas", f"{grafo.get('edges'):,}".replace(",", "."), icon=":material/timeline:")
        st.metric("Segundos de carga", grafo.get("segundos_carga"), icon=":material/timer:")
        especiales = grafo.get("nodos_especiales") or {}
        st.markdown(
            "**Nodos especiales:** "
            + (", ".join(f"{k}: {v}" for k, v in especiales.items()) or "—")
        )
    else:
        st.error(
            "El grafo no está cargado: el servicio no puede calcular rutas. "
            + (grafo.get("error") or "Revisa el log de arranque del servidor."),
            icon=":material/error:",
        )

with col2:
    with st.container(border=True):
        st.markdown(":material/model_training: **Modelo de tráfico (P3)**")
        if modelo.get("cargado"):
            st.success("Cargado y disponible.", icon=":material/check_circle:")
        else:
            st.warning(
                "No cargado. Se pueden calcular rutas sin ajuste de tráfico "
                "(desactiva «Aplicar predicción de tráfico» en el Panel). "
                "Para activarlo hay que generar el modelo de P3.",
                icon=":material/warning:",
            )
        st.caption(f"Fichero: {modelo.get('ruta_pkl', '—')}")

    with st.container(border=True):
        st.markdown(":material/database: **Base de datos histórica (DuckDB)**")
        if duckdb_info.get("accesible"):
            st.success("Accesible.", icon=":material/check_circle:")
        else:
            st.warning("No accesible. Afecta a la predicción de tráfico, no al cálculo de rutas.",
                       icon=":material/warning:")
        st.caption(f"Fichero: {duckdb_info.get('ruta', '—')}")

with st.container(border=True):
    st.markdown(":material/folder_open: **Artefactos que faltan**")
    if faltantes:
        for f in faltantes:
            st.markdown(f"- `{f}`")
        st.caption("Regenéralos con `python pipeline/run_pipeline.py` (o el notebook de P3 para el modelo).")
    else:
        st.success("Ninguno: todos los artefactos esperados están presentes.", icon=":material/check_circle:")

st.caption(f"Marca de tiempo de la respuesta: {health.get('timestamp', '—')}")

render_footer()
