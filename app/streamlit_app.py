"""
Dashboard Streamlit — TFM Rutas Bomberos Madrid.
Herramienta operativa de apoyo al despacho: mapa dominante, parámetros a la
izquierda y panel de resultados con las cifras grandes.

Todo dato viene de la API por HTTP (catálogo de vehículos, parques de
bomberos reales, isócronas, rutas, tráfico, meteorología, /health) -- este
módulo no abre ningún fichero de data/ ni importa routing/ml directamente
(invariantes de la fase, ver docs/p5/README_P5_API_Streamlit.md §1).

Presentación pulida en el bloque 7 de P5: tema claro sobrio
(.streamlit/config.toml), estado de arranque "inicializando", errores en
español que dicen qué hacer, ruta con halo y paleta secuencial de
isócronas, y una página "Sistema" aparte (app/pages/1_Sistema.py) con
/health legible. La lógica de negocio y el contrato de la API no cambian
en este bloque salvo el campo aditivo `zona_destino` de /ruta.

Uso:
    streamlit run app/streamlit_app.py
"""
import os
import sys
import time

import streamlit as st
from streamlit_folium import st_folium

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import folium  # noqa: E402

from app._ui_common import (  # noqa: E402  (sys.path debe ir antes)
    NIVEL_TRAFICO_ETIQUETA,
    _api_get,
    aplicar_estilo_barra_lateral,
    crear_mapa_base,
    css_operativo,
    dibujar_isocronas,
    dibujar_ruta,
    estado_vacio,
    fijar_idioma,
    humanizar_error,
    leyenda_mapa_html,
    marcador_hospital,
    marcador_incidente,
    marcador_parque,
    nombre_corto_parque,
    parametros_efectivos_html,
    parque_mas_cercano,
    render_breadcrumb,
    render_footer,
    render_header,
    salud_api,
    tabla_trafico_html,
)

API_URL = os.environ.get("API_URL", "http://localhost:8080")

# Reintentos del arranque: mientras /health no diga 200, se muestra
# "inicializando" y se reintenta cada ESPERA_ARRANQUE_S segundos, hasta
# REINTENTOS_ARRANQUE veces (la carga del grafo real tarda ~15-20 s y el
# servidor no acepta conexiones hasta terminarla). Configurables por
# entorno para poder fijarlos a 0 en los tests.
REINTENTOS_ARRANQUE = int(os.environ.get("DASHBOARD_REINTENTOS_ARRANQUE", "15"))
ESPERA_ARRANQUE_S = float(os.environ.get("DASHBOARD_ESPERA_ARRANQUE_S", "2"))

# ─────────────────────────────────────────
# Configuración de página
# ─────────────────────────────────────────
st.set_page_config(
    page_title="Central de rutas — Bomberos de Madrid",
    page_icon=":material/local_fire_department:",
    layout="wide",
)
st.html(css_operativo())
aplicar_estilo_barra_lateral()
fijar_idioma()


# ─────────────────────────────────────────
# Arranque: nunca pantalla en blanco. Mientras la API no responda 200 a
# /health, se muestra el panel de "inicializando" y se reintenta; agotados
# los reintentos, un error en español con la acción a tomar.
# ─────────────────────────────────────────
_status_salud, _health = salud_api()
if _status_salud != 200:
    render_header(_status_salud, _health, activa="panel")
    render_breadcrumb("Inicio", "Panel")
    st.title("Simulación de rutas de emergencia", anchor=False)
    st.info(
        "Inicializando el servicio de rutas… La primera vez, cargar el mapa de "
        "calles de Madrid lleva entre 15 y 20 segundos.",
        icon=":material/hourglass_top:",
    )
    _intentos = st.session_state.get("_arranque_intentos", 0)
    if _intentos < REINTENTOS_ARRANQUE:
        st.session_state["_arranque_intentos"] = _intentos + 1
        with st.spinner("Conectando con la API…"):
            time.sleep(ESPERA_ARRANQUE_S)
        st.rerun()
    st.session_state["_arranque_intentos"] = 0
    st.error(humanizar_error(_status_salud, _health, "al arrancar el servicio"),
             icon=":material/error:")
    if st.button("Reintentar", icon=":material/refresh:"):
        st.rerun()
    st.stop()
st.session_state["_arranque_intentos"] = 0


# ─────────────────────────────────────────
# Cargas cacheadas desde la API
# ─────────────────────────────────────────
@st.cache_data(ttl=60)
def _cargar_config():
    """Catálogo de vehículos/algoritmos desde GET /config -- única fuente
    ("Prohibido duplicar constantes entre Streamlit y la API", ver
    docs/adr/0006-catalogo-unico-de-vehiculos.md)."""
    body, status = _api_get("/config")
    if status != 200:
        raise ValueError(humanizar_error(status, body, "al cargar el catálogo de vehículos"))
    return body


@st.cache_data(ttl=60)
def _cargar_parques():
    """Catálogo real de parques de bomberos desde GET /equipamientos?tipo=bomberos.
    Devuelve [{nombre, lat, lon}] ordenado por nombre; lat/lon son solo para
    el marcador, el origen que se envía a /ruta es la identidad
    (orig_tipo_nodo=bomberos, orig_nombre), no la coordenada."""
    body, status = _api_get("/equipamientos", params={"tipo": "bomberos"})
    if status != 200:
        raise ValueError(humanizar_error(status, body, "al cargar el catálogo de parques"))
    parques = [
        {
            "nombre": f["properties"]["nombre"],
            "lon": f["geometry"]["coordinates"][0],
            "lat": f["geometry"]["coordinates"][1],
        }
        for f in body["features"]
    ]
    return sorted(parques, key=lambda p: p["nombre"])


@st.cache_data(ttl=300)
def _cargar_hospitales():
    """Hospitales para dibujar como referencia en el mapa (capa opcional).
    Devuelve [] si la API no responde 200 -- es un adorno, su ausencia no
    debe romper nada."""
    try:
        body, status = _api_get("/equipamientos", params={"tipo": "hospitales"}, timeout=10)
    except ValueError:
        return []
    if status != 200:
        return []
    return [
        {
            "nombre": f["properties"].get("nombre", "Hospital"),
            "lon": f["geometry"]["coordinates"][0],
            "lat": f["geometry"]["coordinates"][1],
        }
        for f in body["features"]
    ]


@st.cache_data(ttl=300)
def _cargar_meteo():
    """Alerta meteorológica INFORMATIVA desde GET /meteorologia. Devuelve el
    cuerpo tal cual, o None si la API no responde 200 -- la banda es un
    adorno de cabecera, su ausencia no debe romper el dashboard."""
    body, status = _api_get("/meteorologia", timeout=10)
    return body if status == 200 else None


def _render_banda_meteo(meteo: dict | None) -> None:
    """Banda discreta con el nivel de alerta (normal / precaución / adversa),
    el criterio y la nota de que es informativa y NO afecta al cálculo de la
    ruta. Sobria: precaución y adversa comparten st.warning (ámbar), sin
    st.error ni iconografía alarmista."""
    nota = "dato informativo — no afecta al cálculo de la ruta"
    if not meteo:
        st.caption(f"Meteorología no disponible ahora mismo · {nota}.")
        return

    alerta = meteo.get("alerta") or {}
    nivel = alerta.get("nivel", "normal")
    criterio = alerta.get("criterio", "")

    sufijo = ""
    if meteo.get("origen") == "cache":
        edad = meteo.get("edad_min")
        sufijo = (
            f" · lectura de hace {edad} min (feed en vivo no disponible)"
            if edad is not None else " · sin lectura reciente disponible"
        )

    if nivel == "adversa":
        st.warning(f"**Meteorología adversa** — {criterio}.{sufijo}  \nEs {nota}.",
                   icon=":material/rainy:")
    elif nivel == "precaucion":
        st.warning(f"**Precaución meteorológica** — {criterio}.{sufijo}  \nEs {nota}.",
                   icon=":material/warning:")
    else:
        st.caption(f"Meteorología: sin incidencias{sufijo} · {nota}.")


try:
    _config = _cargar_config()
    _config_error = None
except ValueError as e:
    _config = None
    _config_error = str(e)

try:
    _parques = _cargar_parques()
    _parques_error = None
except ValueError as e:
    _parques = None
    _parques_error = str(e)

try:
    _meteo = _cargar_meteo()
except ValueError:
    _meteo = None


# ─────────────────────────────────────────
# Cabecera institucional + migas + título de página + banda meteo
# ─────────────────────────────────────────
render_header(_status_salud, _health, activa="panel")
render_breadcrumb("Inicio", "Panel")
st.title("Simulación de rutas de emergencia", anchor=False)
st.html(
    '<p class="inst-lead">Traza la ruta óptima de un vehículo de bomberos entre un '
    'parque y el punto de un incidente en Madrid, con las restricciones físicas del '
    'vehículo y, opcionalmente, la predicción de tráfico por distrito.</p>'
)
_render_banda_meteo(_meteo)


# ─────────────────────────────────────────
# Barra lateral de parámetros: destino, origen, vehículo, fecha/hora,
# algoritmo, isócronas.
# ─────────────────────────────────────────
with st.sidebar:
    st.header("Parámetros de simulación", anchor=False)

    with st.container(border=True, key="sec-destino"):
        st.markdown(":material/location_on: **1 · Destino — incidente**")
        st.caption("Búscalo por dirección (Intro o la lupa), o actívalo con un "
                   "clic en el mapa. Se pide antes que el parque para poder "
                   "sugerir el más cercano.")
        # Búsqueda por dirección: geocodifica y rellena las coordenadas (que
        # siguen siendo lo que se envía a /ruta y se puede ajustar a mano).
        # Se busca al pulsar Enter en el campo (commit del text_input) o al
        # pulsar la lupa que va pegada a su derecha. Sin st.form -> sin el
        # texto "Press Enter to submit form".
        st.session_state.setdefault("dest_lat", 40.4168)
        st.session_state.setdefault("dest_lon", -3.7038)

        # Coordenada fijada con un clic en el mapa. Se aplica AQUÍ, antes de
        # instanciar los number_input de abajo: una vez creado el widget no se
        # puede escribir en su session_state. El manejador del clic (sección
        # del mapa) solo deja la intención en `_dest_click` y hace rerun.
        if "_dest_click" in st.session_state:
            _lat_clic, _lon_clic = st.session_state.pop("_dest_click")
            st.session_state["dest_lat"] = _lat_clic
            st.session_state["dest_lon"] = _lon_clic
            st.session_state["dest_dir"] = ""
            st.session_state["_click_fijado"] = True

        _destino_pendiente = not (
            st.session_state.get("dest_dir") or st.session_state.get("_click_fijado")
        )
        # Acento a la izquierda mientras no hay destino: "este es tu siguiente
        # paso" (§5, estado funcional, no adorno). Se emite SIEMPRE —con color o
        # transparente— para no dejar la regla anterior pegada en el DOM.
        _acento = "var(--color-primary)" if _destino_pendiente else "transparent"
        st.html(f'<style>.st-key-sec-destino{{'
                f'border-left:3px solid {_acento} !important}}</style>')

        # Consumo del apagado diferido del modo "fijar destino": el manejador
        # del clic no puede tocar la key del toggle una vez instanciado, así
        # que deja `_modo_clic_off` y aquí se apaga antes de crear el widget.
        if st.session_state.pop("_modo_clic_off", False):
            st.session_state["modo_fijar_destino"] = False

        with st.container(key="dir-search"):
            _c_in, _c_go = st.columns([6, 1], gap="small", vertical_alignment="bottom")
            _dir_q = _c_in.text_input(
                "Dirección o calle en Madrid", key="dir_q",
                placeholder="Ej. Calle de Alcalá 100",
            )
            _lupa = _c_go.button("", icon=":material/search:", key="dir_go",
                                 help="Buscar la dirección")

        _texto_dir = (_dir_q or "").strip()
        _buscar = bool(_texto_dir) and (_lupa or _texto_dir != st.session_state.get("_dir_buscada"))
        if _buscar:
            st.session_state["_dir_buscada"] = _texto_dir
            with st.spinner("Buscando la dirección…"):
                try:
                    _gc_body, _gc_status = _api_get("/geocodificar", params={"q": _texto_dir}, timeout=15)
                except ValueError as e:
                    st.error(str(e), icon=":material/error:")
                    _gc_status = None
            if _gc_status == 200:
                st.session_state["dest_lat"] = round(_gc_body["lat"], 6)
                st.session_state["dest_lon"] = round(_gc_body["lon"], 6)
                st.session_state["dest_dir"] = _gc_body["direccion"]
                st.toast(f"Destino fijado en: {_gc_body['direccion']}", icon=":material/check_circle:")
                st.rerun()
            elif _gc_status is not None:
                st.error(humanizar_error(_gc_status, _gc_body, "al buscar la dirección"),
                         icon=":material/error:")

        st.toggle(
            "Fijar el destino con un clic en el mapa", key="modo_fijar_destino",
            help="Al activarlo, el siguiente clic en el mapa fija el punto del "
            "incidente y el modo se desactiva solo. Arrastrar y hacer zoom siguen "
            "funcionando igual.",
        )

        if st.session_state.get("dest_dir"):
            st.success(f"Destino: {st.session_state['dest_dir']}", icon=":material/location_on:")
        elif st.session_state.get("_click_fijado"):
            st.success(
                "Destino: punto fijado en el mapa · "
                f"{st.session_state['dest_lat']:.5f}, {st.session_state['dest_lon']:.5f}",
                icon=":material/place:",
            )
        # Las coordenadas van plegadas: son la vía de ajuste fino, no la
        # entrada habitual (esa es la búsqueda por dirección de arriba).
        with st.expander("Coordenadas del punto", icon=":material/my_location:"):
            st.caption("Se rellenan al buscar la dirección. Ajústalas a mano si hace falta.")
            dest_lat = st.number_input("Latitud destino", key="dest_lat", format="%.6f")
            dest_lon = st.number_input("Longitud destino", key="dest_lon", format="%.6f")

    # Selección automática del parque más cercano al destino (bloque C):
    # se recalcula SOLO cuando cambian de verdad las coordenadas del
    # destino -- no con cualquier rerun de otro control de la barra lateral
    # (isócronas, vehículo, fecha…). El sentinel arranca igual al valor por
    # defecto de dest_lat/dest_lon para no sugerir nada antes de que el
    # usuario toque el destino. Se preasigna `parque_sel_key` AQUÍ, antes de
    # instanciar el selectbox de más abajo -- una vez creado el widget ya no
    # se puede escribir en su session_state (mismo motivo que `_dest_click`).
    st.session_state.setdefault("_dest_para_autoparque", (dest_lat, dest_lon))
    if _parques and (dest_lat, dest_lon) != st.session_state["_dest_para_autoparque"]:
        _sugerencia = parque_mas_cercano(_parques, dest_lat, dest_lon)
        st.session_state["parque_sel_key"] = nombre_corto_parque(_sugerencia["nombre"])
        st.session_state["_sugerencia_parque"] = _sugerencia
        st.session_state["_dest_para_autoparque"] = (dest_lat, dest_lon)
    _sugerencia_parque = st.session_state.get("_sugerencia_parque")

    with st.container(border=True):
        st.markdown(":material/pin_drop: **2 · Origen — parque de bomberos**")
        if _parques is None:
            st.error(_parques_error, icon=":material/error:")
            parque_sel = None
            orig_lat = orig_lon = None
        else:
            # La API nombra los parques "PARQUE DE BOMBEROS 07. SAN BLAS";
            # en el selector se muestra solo "07. SAN BLAS" (ya se sobreentiende
            # que es un parque) para que se lea bien el elegido. A la API se le
            # sigue enviando el nombre completo.
            _corto_a_completo = {nombre_corto_parque(n): n for n in (p["nombre"] for p in _parques)}

            # `parque_sel_key` (la key del selectbox) puede desaparecer de
            # session_state si el st.rerun() de la sección Destino corta el
            # script ANTES de llegar aquí sin haber tocado esta key él mismo
            # (ahora Destino va primero -- Streamlit descarta el estado de un
            # widget que no ve instanciado en el run que acaba de terminar,
            # salvo que el propio script lo haya reescrito en ese run). Sin
            # este espejo en `_parque_elegido` (variable normal, no atada a
            # ningún widget, inmune a ese descarte), ese corte resetearía la
            # elección al primer parque alfabético en vez de conservar la
            # última real -- reproducido en
            # TestSeleccionAutomaticaParqueBloqueC (bloque C).
            if "parque_sel_key" not in st.session_state:
                st.session_state["parque_sel_key"] = st.session_state.get(
                    "_parque_elegido", next(iter(_corto_a_completo))
                )

            # Consumo diferido del botón "Usar el más cercano" -- mismo
            # motivo que `_modo_clic_off` en la sección Destino: no se puede
            # tocar la key del selectbox tras pulsar el botón en el mismo run
            # en el que ya se instanció, así que aquí, antes de crearlo.
            if st.session_state.pop("_forzar_parque_cercano", False) and _sugerencia_parque is not None:
                st.session_state["parque_sel_key"] = nombre_corto_parque(_sugerencia_parque["nombre"])

            parque_label = st.selectbox("Parque de origen", list(_corto_a_completo), key="parque_sel_key")
            st.session_state["_parque_elegido"] = parque_label
            parque_sel = _corto_a_completo[parque_label]
            parque_info = next(p for p in _parques if p["nombre"] == parque_sel)
            orig_lat, orig_lon = parque_info["lat"], parque_info["lon"]
            st.caption(f"{len(_parques)} parques reales — coordenadas {orig_lat:.4f}, {orig_lon:.4f}")

            if _sugerencia_parque is not None:
                _label_sugerido = nombre_corto_parque(_sugerencia_parque["nombre"])
                if parque_label == _label_sugerido:
                    st.caption(
                        "Sugerido automáticamente por cercanía en línea recta al "
                        f"destino (≈{_sugerencia_parque['distancia_km']:.2f} km)."
                    )
                else:
                    _c_txt, _c_btn = st.columns([3, 1], vertical_alignment="center")
                    _c_txt.caption(
                        "Elegido manualmente. El más cercano en línea recta sería "
                        f"«{_label_sugerido}» (≈{_sugerencia_parque['distancia_km']:.2f} km)."
                    )
                    if _c_btn.button("Usar el más cercano", key="usar_parque_cercano"):
                        st.session_state["_forzar_parque_cercano"] = True
                        st.rerun()

    with st.container(border=True):
        st.markdown(":material/fire_truck: **3 · Vehículo y algoritmo**")
        if _config is None:
            st.error(_config_error, icon=":material/error:")
            vehiculo_id = vehiculo_info = algoritmo = None
        else:
            vehiculos_por_nombre = {v["nombre"]: v["id"] for v in _config["vehiculos"]}
            nombres = list(vehiculos_por_nombre.keys())
            default_id = _config["vehiculo_default"]
            default_nombre = next((n for n, i in vehiculos_por_nombre.items() if i == default_id), nombres[0])
            nombre_sel = st.selectbox("Tipo de vehículo", nombres, index=nombres.index(default_nombre))
            vehiculo_id = vehiculos_por_nombre[nombre_sel]
            vehiculo_info = next(v for v in _config["vehiculos"] if v["id"] == vehiculo_id)
            st.caption(f"{vehiculo_info['descripcion']} (ancho req. {vehiculo_info['ancho_req_m']} m)")

            algoritmos = _config["algoritmos_disponibles"]
            algoritmo = st.selectbox("Algoritmo", algoritmos, index=algoritmos.index(_config["algoritmo_default"]))

    with st.container(border=True):
        st.markdown(":material/schedule: **4 · Fecha y hora del incidente**")
        fecha = st.date_input("Fecha del incidente")
        hora = st.slider("Hora del incidente", 0, 23, 8)
        aplicar_trafico = st.checkbox(
            "Aplicar predicción de tráfico a la ruta/isócronas", value=False,
            help="Si se desactiva, /ruta e /isocronas no reciben date/hora y calculan "
            "sin ajuste de tráfico -- útil si el modelo de tráfico (P3) no está desplegado.",
        )
    fecha_params = {"date": str(fecha), "hora": hora} if aplicar_trafico else {}

    with st.container(border=True):
        st.markdown(":material/layers: **5 · Capas del mapa**")
        mostrar_isocronas = st.checkbox("Mostrar isócronas del parque seleccionado", value=False)
        opacidad_isocronas = 0.30
        if mostrar_isocronas:
            opacidad_isocronas = st.slider("Transparencia de la capa", 0.05, 0.6, 0.30, step=0.05)
            st.caption(
                "Cobertura calculada en vivo con el vehículo y la hora seleccionados "
                "(la primera vez para una combinación nueva tarda unos segundos; "
                "las siguientes salen de caché en el servidor)."
            )
        mostrar_hospitales = st.checkbox("Mostrar hospitales de referencia", value=False)

    calcular = st.button(
        "Calcular ruta óptima", type="primary", width="stretch",
        icon=":material/route:",
        disabled=(_config is None or _parques is None),
    )


# ─────────────────────────────────────────
# Mapa dominante + panel de resultados
# ─────────────────────────────────────────
col_mapa, col_info = st.columns([3.4, 1.6], gap="medium")

with col_mapa:
    m = crear_mapa_base()

    if orig_lat is not None:
        marcador_parque(m, orig_lat, orig_lon, parque_sel)
    marcador_incidente(m, dest_lat, dest_lon)

    if mostrar_hospitales:
        for h in _cargar_hospitales():
            marcador_hospital(m, h["lat"], h["lon"], h["nombre"])

    color_por_corte: dict = {}
    if mostrar_isocronas and parque_sel is not None:
        with st.spinner(f"Calculando isócronas de {parque_sel}…"):
            try:
                body, status = _api_get(
                    "/isocronas",
                    params={"nombre": parque_sel, "vehiculo": vehiculo_id, **fecha_params},
                    timeout=30,
                )
            except ValueError as e:
                st.error(str(e), icon=":material/error:")
                body, status = None, None

        if status == 200:
            color_por_corte = dibujar_isocronas(m, body["features"], parque_sel, opacidad_isocronas)
        elif status is not None:
            st.error(humanizar_error(status, body, "al calcular las isócronas"), icon=":material/error:")

    # ─── Cálculo de ruta ───
    if calcular:
        with st.spinner("Calculando ruta óptima…"):
            try:
                body, status = _api_get(
                    "/ruta",
                    params={
                        "orig_tipo_nodo": "bomberos", "orig_nombre": parque_sel,
                        "dest_lat": dest_lat, "dest_lon": dest_lon,
                        "vehiculo": vehiculo_id,
                        "ancho_req": vehiculo_info["ancho_req_m"],
                        "galibo_req": vehiculo_info["galibo_req_m"],
                        "algoritmo": algoritmo,
                        **fecha_params,
                    },
                    timeout=30,
                )
            except ValueError as e:
                st.error(str(e), icon=":material/error:")
                body, status = None, None

            if status == 200:
                feature = body["features"][0]
                st.session_state["ruta_coords"] = feature["geometry"]["coordinates"]
                st.session_state["ruta_props"] = feature["properties"]
                st.session_state["ruta_parametros_efectivos"] = body["parametros_efectivos"]
                st.session_state["ruta_trafico"] = body["trafico_por_zona"]
                st.session_state["ruta_zona_destino"] = body.get("zona_destino") or {}
            elif status is not None:
                st.error(humanizar_error(status, body, "al calcular la ruta"), icon=":material/error:")

    # Dibuja la última ruta calculada (persistida en session_state) en TODOS
    # los reruns, no solo justo tras pulsar el botón -- así activar una capa
    # no la borra del mapa.
    props = st.session_state.get("ruta_props")
    coords = st.session_state.get("ruta_coords")
    if coords:
        ruta_completa = props["ruta_completa"]
        tooltip = (
            f"{props['distancia_m']:.0f} m — {props['tiempo_min']:.1f} min"
            + ("" if ruta_completa else " (ruta PARCIAL)")
        )
        dibujar_ruta(m, coords, ruta_completa, tooltip)

    leyenda = leyenda_mapa_html(
        parque_sel, color_por_corte,
        con_ruta=bool(coords),
        ruta_completa=bool(coords) and props["ruta_completa"],
        con_parque=orig_lat is not None,
        con_incidente=True,
        con_hospitales=mostrar_hospitales,
    )
    if leyenda:  # "" si no hay nada que enseñar -> no se añade la caja vacía
        m.get_root().html.add_child(folium.Element(leyenda))

    # Modo "fijar destino": un clic en el mapa fija el punto del incidente.
    # Arrastrar (paneo) y la rueda/botones (zoom) no cuentan como clic en
    # Leaflet, así que moverse por el mapa sigue igual. El modo se apaga solo
    # tras el primer punto. Con el modo apagado no se pide `last_clicked` -> el
    # mapa no provoca reruns al panear/hacer zoom.
    _modo_clic = st.session_state.get("modo_fijar_destino", False)
    if _modo_clic:
        m.get_root().header.add_child(folium.Element(
            "<style>.leaflet-container{cursor:crosshair !important}</style>"))
        st.caption(
            ":material/ads_click: Haz clic en el punto del incidente "
            "(el modo se desactiva solo al fijarlo)."
        )
    elif not (st.session_state.get("dest_dir") or st.session_state.get("_click_fijado")):
        st.caption(
            "Fija el lugar del incidente: búscalo por dirección o activa "
            "«Fijar el destino con un clic en el mapa» en el panel de la izquierda."
        )

    # `returned_objects=["last_clicked"]` SIEMPRE (no condicionado al modo): el
    # hash con que st_folium cachea el iframe no incluye `returned_objects`, así
    # que alternarlo no re-monta el componente y se queda con el valor viejo.
    # Con esta lista fija, panear/hacer zoom no provoca reruns (last_clicked no
    # cambia); solo un clic en el mapa lo hace, y fuera del modo se ignora.
    _mapa = st_folium(m, key="mapa", width=None, height=700,
                      returned_objects=["last_clicked"])

    if _modo_clic and isinstance(_mapa, dict) and _mapa.get("last_clicked"):
        _clic = _mapa["last_clicked"]
        _punto = (round(_clic["lat"], 6), round(_clic["lng"], 6))
        if _punto != st.session_state.get("_click_procesado"):
            st.session_state["_click_procesado"] = _punto
            st.session_state["_dest_click"] = _punto      # lo aplica la sección Destino
            st.session_state["_modo_clic_off"] = True      # apaga el toggle en el rerun
            st.toast(
                f"Destino fijado en el mapa · {_punto[0]:.5f}, {_punto[1]:.5f}",
                icon=":material/place:",
            )
            st.rerun()

with col_info:
    st.subheader("Resultado", anchor=False)
    parametros_efectivos = st.session_state.get("ruta_parametros_efectivos")
    if props and parametros_efectivos:
        if props["ruta_completa"]:
            st.success("Ruta calculada correctamente.", icon=":material/check_circle:")
        else:
            st.warning(
                "No existe ruta completa para este vehículo; se muestra el trayecto "
                f"hasta el punto accesible más cercano, quedan "
                f"{props['distancia_sin_cubrir_m']:.0f} m sin cubrir.",
                icon=":material/warning:",
            )

        zt = st.session_state.get("ruta_zona_destino") or {}
        nivel_destino = zt.get("nivel_trafico")
        traf_valor = NIVEL_TRAFICO_ETIQUETA.get(nivel_destino, "—")
        if nivel_destino is not None:
            traf_ayuda = f"Distrito del incidente: {zt.get('zona') or 'no localizado'}"
        else:
            traf_ayuda = (
                f"Distrito del incidente: {zt.get('zona') or 'no localizado'}. "
                "Sin predicción: activa «Aplicar predicción de tráfico»."
            )

        st.metric("Tiempo estimado", f"{props['tiempo_min']:.1f} min", border=True, icon=":material/schedule:")
        st.metric("Distancia", f"{props['distancia_m'] / 1000:.2f} km", border=True, icon=":material/straighten:")
        st.metric("Tráfico en zona destino", traf_valor, border=True, help=traf_ayuda, icon=":material/traffic:")

        with st.container(border=True):
            st.markdown(
                parametros_efectivos_html(
                    props.get("n_nodes", "—"),
                    parametros_efectivos["ancho_req"],
                    parametros_efectivos["vehiculo"],
                    parametros_efectivos["algoritmo"],
                    bool(st.session_state.get("ruta_trafico")),
                ),
                unsafe_allow_html=True,
            )
    else:
        estado_vacio(
            "Aún no has calculado ninguna ruta",
            "Elige el destino y el parque de origen, y pulsa «Calcular ruta óptima».",
            "navigator-alt",
        )

    st.subheader("Predicción de tráfico por distrito", anchor=False)
    if st.button("Ver tráfico por distrito", icon=":material/traffic:"):
        try:
            body, status = _api_get("/prediccion_trafico", params={"date": str(fecha), "hora": hora}, timeout=15)
        except ValueError as e:
            st.error(str(e), icon=":material/error:")
        else:
            if status == 200:
                st.markdown(tabla_trafico_html(body["trafico_por_zona"]), unsafe_allow_html=True)
            else:
                st.error(humanizar_error(status, body, "al predecir el tráfico"), icon=":material/error:")
    else:
        estado_vacio(
            "Sin predicción por distrito",
            "Elige la fecha y la hora en el panel y pulsa «Ver tráfico por distrito».",
            "map",
        )

render_footer()
