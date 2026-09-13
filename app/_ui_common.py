"""
Utilidades compartidas entre el dashboard (`app/streamlit_app.py`) y la
página de diagnóstico (`app/pages/1_Sistema.py`) — P5 bloque 7.

Un único punto de entrada HTTP (`_api_get`), un único traductor de errores
de la API a lenguaje humano (`humanizar_error`) y un único formateador del
badge de estado del sistema (`badge_estado`). Ninguna de las dos páginas
reimplementa esto por su cuenta ("prohibido duplicar constantes entre
Streamlit y la API", ver docs/p5/README_P5_API_Streamlit.md §1; aquí,
además, entre páginas de Streamlit).

Este módulo NO abre ficheros de data/ ni importa routing/ml: como el resto
del dashboard, todo dato viene de la API por HTTP.
"""
import math
import os
import re
from functools import lru_cache
from pathlib import Path

import folium
import requests

API_URL = os.environ.get("API_URL", "http://localhost:8080")

# Kit de iconos SVG (Iconoir, MIT) para bloques HTML propios; los widgets
# nativos usan Material Symbols vía icon= / :material/... (DESIGN_SYSTEM §6).
_ICONS_DIR = Path(__file__).resolve().parent / "static" / "icons"


@lru_cache(maxsize=64)
def icono_svg(nombre: str, size: int = 18, stroke: str = "currentColor") -> str:
    """Devuelve el SVG de `app/static/icons/<nombre>.svg` listo para incrustar,
    dimensionado a `size` px y heredando el color (`currentColor` por defecto).
    Cadena vacía si el icono no existe -- nunca revienta la página."""
    ruta = _ICONS_DIR / f"{nombre}.svg"
    try:
        svg = ruta.read_text(encoding="utf-8")
    except OSError:
        return ""
    svg = re.sub(r'\s(width|height)="[^"]*"', "", svg, count=2)
    svg = svg.replace(
        "<svg",
        f'<svg width="{size}" height="{size}" style="flex:none;vertical-align:-.15em" '
        f'stroke="{stroke}"',
        1,
    )
    if stroke != "currentColor":
        # Cada <path> de Iconoir trae su propio stroke="currentColor" -- es un
        # valor explícito de ESE elemento, no algo "sin especificar" que
        # herede del <svg> raíz de arriba. Sin este reemplazo, un `stroke`
        # pedido aquí no se ve: el icono sale del color de texto ambiente,
        # no del pedido (comprobado con getComputedStyle en un caso real).
        svg = svg.replace('stroke="currentColor"', f'stroke="{stroke}"')
    return svg


# Basemap claro y SIN API key. CartoDB Positron sería el más sobrio, pero
# CARTO marca de agua "API KEY REQUIRED" el tráfico no autenticado desde
# algunas redes/entornos; OpenStreetMap se sirve siempre limpio. Ambos son
# válidos según DESIGN_SYSTEM §3.4. Si Positron se ve limpio en tu red,
# vuelve a ponerlo aquí.
TILES_BASE = "OpenStreetMap"

# Paleta SECUENCIAL para las isócronas: un solo tono, intensidad creciente
# con el corte. Sustituye al semáforo verde/ámbar/rojo del bloque 4 (que no
# era secuencial). YlOrRd de ColorBrewer, 5 pasos -- cubre hasta 5 cortes;
# si /config publicara más o menos, se reparte esta misma paleta.
PALETA_ISOCRONAS = ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]

# Halo de la ruta: una línea blanca gruesa por debajo + la línea de color
# por encima, para que se lea sobre calles, zonas verdes y capa de isócronas.
# Colores del DESIGN_SYSTEM (§3.4): ruta completa en azul, parcial en el rojo
# funcional de error.
HALO_COLOR = "#ffffff"
RUTA_COLOR_COMPLETA = "#1565c0"
RUTA_COLOR_PARCIAL = "#b3261e"  # rojo funcional de error (DESIGN_SYSTEM §3.3)

# ── Tokens de color usados fuera del tema nativo (leyenda del mapa dentro
#    del iframe de folium, CSS puntual). Espejo de DESIGN_SYSTEM.md §10.
COLOR_PRIMARY = "#0055A0"
COLOR_TEXT = "#1A1A1A"
COLOR_TEXT_SECONDARY = "#4A5560"
COLOR_BORDER = "#C7CED6"
COLOR_SURFACE = "#F4F6F8"


def _api_get(path: str, params: dict | None = None, timeout: int = 15) -> tuple[dict, int]:
    """
    GET a la API. Devuelve (json_body, status_code); nunca deja pasar una
    excepción cruda hasta Streamlit (ni de red -- API apagada, timeout -- ni
    de un cuerpo no-JSON) -- en su lugar levanta ValueError con un mensaje
    legible para que el llamador lo muestre. Punto único de entrada HTTP del
    dashboard: config, parques, ruta, isócronas, predicción de tráfico,
    meteorología y /health pasan todos por aquí.
    """
    try:
        resp = requests.get(f"{API_URL}{path}", params=params, timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise ValueError(f"No se puede conectar a la API en {API_URL}: {e}") from e
    try:
        body = resp.json()
    except ValueError as e:
        raise ValueError(f"La API respondió con contenido no válido (HTTP {resp.status_code}).") from e
    return body, resp.status_code


def salud_api(timeout: int = 5) -> tuple[int | None, dict | None]:
    """
    (status_code, cuerpo) de GET /health, o (None, None) si la API no
    responde / responde algo que no es JSON. No lanza: es la sonda que usan
    tanto el arranque ("inicializando…") como el badge de estado.
    """
    try:
        body, status = _api_get("/health", timeout=timeout)
    except ValueError:
        return None, None
    return status, body


# ─────────────────────────────────────────
# Parque más cercano al destino — geometría pura sobre los parques que ya
# trae /equipamientos (bloque C). Línea recta con corrección de longitud
# por latitud, sin proyectar -- mismo criterio que _meteorologia_por_zona
# en server.py para "más cercano" entre un puñado de puntos. Vive aquí, en
# el cliente, y no en un endpoint nuevo: a diferencia de la meteorología
# (Streamlit solo recibe el valor ya agregado por distrito), aquí Streamlit
# YA tiene la lista completa de parques en memoria para pintar el selector,
# así que no hay ningún dato nuevo que traer por HTTP -- solo decidir cuál
# de los que ya tiene está más cerca. No ve sentidos únicos ni anchura del
# vehículo: es una aproximación geométrica, declarada como tal en la
# interfaz (caption junto al selector de parque en streamlit_app.py).
# ─────────────────────────────────────────
_LAT_REF_MADRID = 40.42  # misma latitud de referencia que server.py

PREFIJO_PARQUE = "PARQUE DE BOMBEROS "


def nombre_corto_parque(nombre_completo: str) -> str:
    """"PARQUE DE BOMBEROS 07. SAN BLAS" -> "07. SAN BLAS" para que se lea
    bien en el selector; a la API se le sigue enviando el nombre completo."""
    if nombre_completo.startswith(PREFIJO_PARQUE):
        return nombre_completo[len(PREFIJO_PARQUE):]
    return nombre_completo


def parque_mas_cercano(parques: list[dict], lat: float, lon: float) -> dict:
    """Parque de `parques` (formato de _cargar_parques: [{nombre, lat, lon}])
    más cercano en línea recta a (lat, lon). Devuelve una COPIA del dict del
    parque con la clave adicional 'distancia_km'; no muta `parques`.
    Lanza ValueError si `parques` está vacía.
    """
    if not parques:
        raise ValueError("parques no puede estar vacía")
    k = math.cos(math.radians(_LAT_REF_MADRID))
    mejor, mejor_d2 = None, None
    for p in parques:
        dx = (p["lon"] - lon) * k
        dy = p["lat"] - lat
        d2 = dx * dx + dy * dy
        if mejor_d2 is None or d2 < mejor_d2:
            mejor, mejor_d2 = p, d2
    distancia_km = math.sqrt(mejor_d2) * 111.32  # grados -> km, aprox. estándar
    return {**mejor, "distancia_km": distancia_km}


# ─────────────────────────────────────────
# Errores de la API -> español operativo. El texto técnico (traceback,
# ruta de disco, nombre de notebook) NO se pinta en la cara del operador:
# el llamador lo mete en un st.expander("Detalle técnico"). Cada rama dice
# QUÉ hacer a continuación, no solo qué ha fallado.
# ─────────────────────────────────────────
_ACCION_POR_CODIGO = {
    "MISSING_ARTIFACT": (
        "El servicio de rutas no encuentra un fichero de datos que necesita. "
        "Avisa al responsable técnico: hay que regenerar los artefactos del proyecto."
    ),
    "NO_ROUTE": (
        "No hay una ruta transitable entre el parque y el punto del incidente para "
        "este vehículo. Prueba con un vehículo más ligero o ajusta las coordenadas del destino."
    ),
    "NOT_FOUND": (
        "El parque o el punto indicado no existe en el callejero. Revisa la selección "
        "de parque y las coordenadas del destino."
    ),
    "VALIDATION_ERROR": (
        "Algún dato del formulario no es válido. Revisa parque, coordenadas del destino, "
        "vehículo y fecha/hora antes de volver a calcular."
    ),
    "DIRECCION_NO_ENCONTRADA": (
        "No se ha encontrado esa dirección en Madrid. Prueba a escribirla con más detalle "
        "(calle y número) o introduce las coordenadas del destino a mano."
    ),
    "GEOCODER_UNAVAILABLE": (
        "El buscador de direcciones no está disponible ahora mismo. Introduce las "
        "coordenadas del destino a mano."
    ),
}


def humanizar_error(status: int | None, body: dict | None, contexto: str = "") -> str:
    """
    Mensaje en español, sin jerga, que dice qué hacer. `status` None =
    la API no respondió. `contexto` es una coletilla opcional ("al calcular
    la ruta", "al cargar el catálogo de parques") para situar al operador;
    se añade SIEMPRE, también a los mensajes por código.
    """
    coletilla = f" ({contexto})" if contexto else ""
    codigo = (body or {}).get("code")
    if codigo in ("DIRECCION_NO_ENCONTRADA", "GEOCODER_UNAVAILABLE"):
        return _ACCION_POR_CODIGO[codigo]
    if status is None:
        return (
            f"No hay conexión con el servicio de rutas{coletilla}. Comprueba que la API "
            "está arrancada y vuelve a intentarlo en unos segundos."
        )
    if status == 503:
        return (
            f"El servicio de rutas está arrancando o no está listo{coletilla}. "
            "Espera unos segundos y vuelve a intentarlo; si persiste, consulta la página «Sistema»."
        )
    base = _ACCION_POR_CODIGO.get(codigo)
    if base is None and status == 404:
        base = _ACCION_POR_CODIGO["NOT_FOUND"]
    if base is None and status == 400:
        base = _ACCION_POR_CODIGO["VALIDATION_ERROR"]
    if base is not None:
        return f"{base}{coletilla}" if coletilla else base
    return (
        f"El servicio de rutas ha devuelto un error inesperado (HTTP {status}){coletilla}. "
        "Revisa la página «Sistema» para ver el diagnóstico."
    )


# ─────────────────────────────────────────
# Estado del sistema -> (texto, color de badge). Colores = los semánticos
# del tema (.streamlit/config.toml): green / orange / red.
# ─────────────────────────────────────────
def badge_estado(status: int | None, body: dict | None) -> tuple[str, str, str]:
    """
    Devuelve (etiqueta, color, icono_material) para st.badge, a partir de
    la respuesta de /health:
      - 200 + grafo cargado           -> "Operativo"  / green
      - 503 (grafo no cargado)        -> "Degradado"  / orange
      - sin respuesta                 -> "Sin conexión" / red
    """
    if status is None:
        return "Sin conexión", "red", "error"
    if status == 200 and (body or {}).get("grafo", {}).get("cargado"):
        return "Operativo", "green", "check_circle"
    return "Degradado", "orange", "warning"


# ─────────────────────────────────────────
# Mapa. Construcción aislada en funciones puras (reciben un folium.Map,
# le añaden capas) para poder probarlas sin navegador ni Streamlit:
# tests/test_streamlit_app.py cuenta las PolyLine del halo y verifica que
# las isócronas reciben la paleta secuencial en orden de corte.
# ─────────────────────────────────────────
def crear_mapa_base(center=(40.4168, -3.7038), zoom=12) -> folium.Map:
    return folium.Map(location=list(center), zoom_start=zoom, tiles=TILES_BASE, control_scale=True)


# Colores de los marcadores del mapa (DESIGN_SYSTEM §3.4: parque en azul,
# incidente en ámbar, hospital en azul claro). Los mismos tokens se usan en
# la leyenda (`leyenda_mapa_html`) para que la clave visual case con el
# marcador; el ámbar reutiliza --color-warning, no una paleta nueva.
COLOR_MARCADOR_PARQUE = COLOR_PRIMARY
COLOR_MARCADOR_INCIDENTE = "#A15C00"
COLOR_MARCADOR_HOSPITAL = "#4FA8DA"


def _icono_marcador_html(nombre_icono: str, color: str) -> str:
    """Insignia circular para los marcadores del mapa: círculo de color +
    borde blanco (contraste sobre cualquier tesela del basemap, AA §8.1) +
    icono Iconoir en blanco centrado. Sustituye al pin de Font Awesome
    (antes folium.Icon(prefix="fa")) -- un tercer set de iconos ajeno al
    resto de la app (DESIGN_SYSTEM §6.2 pide uno solo)."""
    svg = icono_svg(nombre_icono, 15, stroke="#FFFFFF")
    return (
        f'<div style="width:26px;height:26px;border-radius:50%;background:{color};'
        f'border:2px solid #FFFFFF;box-shadow:0 1px 3px rgba(0,0,0,.35);'
        f'display:flex;align-items:center;justify-content:center;">{svg}</div>'
    )


def _marcador_icono(m: folium.Map, lat: float, lon: float, tooltip: str,
                    nombre_icono: str, color: str) -> None:
    """folium.DivIcon con la insignia de `_icono_marcador_html`, anclada por
    su centro (es un círculo, no un pin con punta -- no hace falta anclar
    por la base)."""
    folium.Marker(
        [lat, lon], tooltip=tooltip,
        icon=folium.DivIcon(
            html=_icono_marcador_html(nombre_icono, color),
            icon_size=(26, 26), icon_anchor=(13, 13),
        ),
    ).add_to(m)


def marcador_parque(m: folium.Map, lat: float, lon: float, nombre: str) -> None:
    # DESIGN_SYSTEM §3.4: parque en azul, incidente en ámbar, hospital en
    # azul claro; icono distinto por tipo, no solo color.
    _marcador_icono(m, lat, lon, f"Parque: {nombre}", "truck", COLOR_MARCADOR_PARQUE)


def marcador_incidente(m: folium.Map, lat: float, lon: float) -> None:
    _marcador_icono(m, lat, lon, "Incidente", "fire-flame", COLOR_MARCADOR_INCIDENTE)


def marcador_hospital(m: folium.Map, lat: float, lon: float, nombre: str) -> None:
    _marcador_icono(m, lat, lon, f"Hospital: {nombre}", "health-shield", COLOR_MARCADOR_HOSPITAL)


def dibujar_ruta(m: folium.Map, coords: list, ruta_completa: bool, tooltip: str = "") -> None:
    """
    Traza la ruta con halo: PolyLine blanca ancha por debajo + PolyLine de
    color por encima (azul si la ruta es completa, rojo discontinuo si es
    parcial). Dos capas: quitar el halo se nota en el test.
    """
    locs = [(c[1], c[0]) for c in coords]
    if not locs:
        return
    folium.PolyLine(locs, color=HALO_COLOR, weight=11, opacity=0.95).add_to(m)
    folium.PolyLine(
        locs,
        color=RUTA_COLOR_COMPLETA if ruta_completa else RUTA_COLOR_PARCIAL,
        weight=6, opacity=1.0,
        dash_array=None if ruta_completa else "10,12",
        tooltip=tooltip or None,
    ).add_to(m)


def dibujar_isocronas(m: folium.Map, features: list, parque: str, opacidad: float) -> dict:
    """
    Pinta las isócronas con `PALETA_ISOCRONAS` (secuencial) asignada por
    corte ASCENDENTE, y las dibuja de mayor a menor corte para que el
    polígono más pequeño quede por encima. Devuelve {corte_min: color}
    para construir la leyenda.
    """
    cortes = sorted({f["properties"]["corte_min"] for f in features})
    color_por_corte = dict(zip(cortes, PALETA_ISOCRONAS))
    for feature in sorted(features, key=lambda f: -f["properties"]["corte_min"]):
        corte = feature["properties"]["corte_min"]
        color = color_por_corte[corte]
        folium.GeoJson(
            feature,
            style_function=lambda _f, color=color: {
                "color": color, "weight": 2, "fillColor": color, "fillOpacity": opacidad,
            },
            tooltip=f"{corte:.0f} min desde {parque}",
        ).add_to(m)
    return color_por_corte


def leyenda_mapa_html(parque: str | None, color_por_corte: dict,
                      con_ruta: bool, ruta_completa: bool = True,
                      con_parque: bool = False, con_incidente: bool = False,
                      con_hospitales: bool = False) -> str:
    """Caja de leyenda del mapa. Sobria, clara, borde de 1 px (DESIGN_SYSTEM
    §5). Va dentro del iframe de folium (HTML propio, no pasa por el
    saneador de Streamlit), así que fija su propia tipografía y trae su
    `<style>`. Solo se construye con lo que de verdad hay dibujado: sin
    parque/incidente/hospitales/isócronas/ruta no hay nada que leer, y
    devuelve "" -- el llamador entonces no añade la caja al mapa, en vez de
    una caja con el título "Leyenda" y nada debajo."""
    filas = []
    if con_parque:
        filas.append(_fila_leyenda_icono("truck", COLOR_MARCADOR_PARQUE, "Parque de bomberos"))
    if con_incidente:
        filas.append(_fila_leyenda_icono("fire-flame", COLOR_MARCADOR_INCIDENTE, "Incidente"))
    if con_hospitales:
        filas.append(_fila_leyenda_icono("health-shield", COLOR_MARCADOR_HOSPITAL, "Hospital"))
    for corte, c in color_por_corte.items():
        filas.append(_fila_leyenda_color(c, f"{corte:.0f} min"))
    if con_ruta:
        c = RUTA_COLOR_COMPLETA if ruta_completa else RUTA_COLOR_PARCIAL
        etiqueta = "Ruta óptima" if ruta_completa else "Ruta parcial"
        filas.append(_fila_leyenda_color(c, etiqueta, alto="5px", radio="2px"))

    if not filas:
        return ""

    encabezado = f"Isócronas — {parque}" if parque and color_por_corte else "Leyenda"
    # top-right: el zoom queda arriba-izquierda, y la escala + la atribución
    # de OpenStreetMap van abajo -- aquí no choca con ningún control nativo.
    return f"""
    <div style="position: fixed; top: 12px; right: 12px; z-index: 9999;
                background: #FFFFFF; color: {COLOR_TEXT}; padding: 12px 16px;
                border: 1px solid {COLOR_BORDER}; border-radius: 6px;
                font-family: 'DM Sans', Verdana, system-ui, sans-serif;
                font-size: 13px; line-height: 1.7; box-shadow: 0 1px 2px rgba(0,0,0,.06);">
      <b style="font-weight:700">{encabezado}</b>
      <div style="margin-top:6px;">{"".join(filas)}</div>
    </div>
    <style>
      .inst-leyenda-fila{{ display:flex; align-items:center; gap:8px; }}
      .inst-leyenda-fila + .inst-leyenda-fila{{ margin-top:4px; }}
      .inst-leyenda-fila svg{{ flex:none; }}
      .inst-leyenda-swatch{{
        display:inline-block; width:14px; height:14px; flex:none;
        border-radius:3px;
      }}
    </style>
    """


def _fila_leyenda_color(color: str, etiqueta: str, alto: str = "14px", radio: str = "3px") -> str:
    return (f'<div class="inst-leyenda-fila">'
            f'<span class="inst-leyenda-swatch" style="background:{color};height:{alto};border-radius:{radio}"></span>'
            f'<span>{etiqueta}</span></div>')


def _fila_leyenda_icono(nombre_icono: str, color: str, etiqueta: str) -> str:
    return f'<div class="inst-leyenda-fila">{icono_svg(nombre_icono, 16, stroke=color)}<span>{etiqueta}</span></div>'


# Nivel de tráfico -> color + etiqueta. Reutiliza los tokens funcionales ya
# definidos (success/warning/error) en vez de una paleta nueva: el color
# nunca sustituye al texto (Bajo/Medio/Alto), solo lo refuerza (§8.7).
NIVEL_TRAFICO_COLOR = {0: "var(--color-success)", 1: "var(--color-warning)", 2: "var(--color-error)"}
NIVEL_TRAFICO_ETIQUETA = {0: "Bajo", 1: "Medio", 2: "Alto"}


def parametros_efectivos_html(n_nodes, ancho_req_m, vehiculo: str, algoritmo: str,
                              trafico_aplicado: bool) -> str:
    """Lista de definición con los parámetros efectivos de la ruta (§2.2:
    etiqueta 12 px mayúscula secundaria / valor destacado). Sustituye al
    bloque de markdown en negrita de antes -- un par etiqueta-valor se
    escanea más rápido que una línea de texto corrido por parámetro."""
    filas = [
        ("Nodos recorridos", n_nodes),
        ("Ancho mínimo requerido", f"{ancho_req_m} m"),
        ("Vehículo efectivo", vehiculo),
        ("Algoritmo", algoritmo),
        ("Predicción de tráfico aplicada", "sí" if trafico_aplicado else "no"),
    ]
    filas_html = "".join(f"<div><dt>{etiqueta}</dt><dd>{valor}</dd></div>" for etiqueta, valor in filas)
    return f'<dl class="inst-dl">{filas_html}</dl>'


def tabla_trafico_html(preds: dict) -> str:
    """Tabla de predicción de tráfico por distrito, estilo AEMET (§5.7):
    cabecera con fondo, filas con borde inferior. El nivel lleva un punto de
    color junto al texto -- el color nunca es el único medio (§8.7). Sustituye
    a `st.table`, que no admite HTML en las celdas (no se podría pintar el
    punto). Va por `st.markdown(unsafe_allow_html=True)`, con `.inst-table`
    en `_ESTILO_INSTITUCIONAL` (no `st.html`: descartaría los estilos)."""
    filas = "".join(
        f'<tr><td>{distrito}</td><td>'
        f'<span class="inst-dot" style="background:{NIVEL_TRAFICO_COLOR.get(nivel, "var(--color-text-disabled)")}"></span>'
        f'{NIVEL_TRAFICO_ETIQUETA.get(nivel, str(nivel))}</td></tr>'
        for distrito, nivel in sorted(preds.items())
    )
    return f"""
    <div class="inst-table-wrap">
      <table class="inst-table">
        <caption class="sr-only">Predicción de tráfico por distrito</caption>
        <thead><tr><th scope="col">Distrito</th><th scope="col">Nivel de tráfico</th></tr></thead>
        <tbody>{filas}</tbody>
      </table>
    </div>
    """


# ─────────────────────────────────────────
# Andamiaje institucional compartido (DESIGN_SYSTEM §4–§5): hoja de estilos
# global, cabecera de dos niveles con marca + navegación + estado, migas de
# pan y pie. Las dos páginas (Panel y Sistema) lo usan igual -> consistencia.
# ─────────────────────────────────────────
_ESTILO_INSTITUCIONAL = """
<style>
:root{
  --color-primary:#0055A0; --color-primary-hover:#00468A; --color-primary-dark:#003A73;
  --color-primary-surface:#E8F0F7;
  --color-bg:#FFFFFF; --color-surface:#F4F6F8; --color-surface-2:#E9EDF1;
  --color-text:#1A1A1A; --color-text-secondary:#4A5560; --color-border:#C7CED6;
  --color-border-strong:#9AA4AF;
  /* Funcionales (DESIGN_SYSTEM §3.3, mismos valores que greenColor/orangeColor/
     redColor de .streamlit/config.toml) -- el tema nativo los usa en sus
     propios widgets pero no los expone como variables CSS; hacen falta aquí
     para HTML propio (p. ej. el punto de color del nivel de tráfico). */
  --color-success:#1E7E34; --color-success-surface:#E6F2E9;
  --color-warning:#A15C00; --color-warning-surface:#FBF0DF;
  --color-error:#B3261E; --color-error-surface:#FBE9E7;
  --space-sm:8px; --space-md:12px; --space-lg:16px; --space-xl:24px; --space-2xl:32px;
  --radius-sm:4px; --radius-md:6px;
}

/* — Quita el cromo "SaaS": barra decorativa superior, menú ⋮ y botón
     Deploy, pie "Made with Streamlit". Se CONSERVA la barra de herramientas
     porque aloja el botón para volver a mostrar la barra lateral (móvil). — */
[data-testid="stDecoration"], [data-testid="stStatusWidget"]{display:none !important;}
[data-testid="stToolbarActions"]{display:none !important;}
[data-testid="stAppDeployButton"], [data-testid="stDeployButton"], [data-testid="stMainMenu"]{display:none !important;}
footer{visibility:hidden; height:0;}

/* — La navegación va a la cabecera, no en el lateral — */
[data-testid="stSidebarNav"]{display:none !important;}

/* — Barra lateral plegable: se reaprovecha el plegado nativo de Streamlit.
     Los botones de ocultar/mostrar se estilan aparte (ver aplicar_estilo_barra_
     lateral): el sanitizador de st.html descarta la hoja entera si un selector
     apunta a stSidebarCollapseButton / stExpandSidebarButton. — */

/* — Contenedor principal: padding horizontal fijo para que las bandas de
     cabecera/pie puedan sangrar a ancho completo con margen negativo exacto — */
[data-testid="stMainBlockContainer"]{
  /* margen superior contenido: deja el hueco justo para el botón ☰ cuando la
     barra está oculta, sin exceso de blanco */
  padding:2rem 2rem 1.5rem; max-width:1680px;
  /* centrado: con la barra lateral colapsada (ver más abajo, aria-expanded)
     este bloque recupera el ancho de la barra y, centrado, se desplaza al
     medio de la pantalla en vez de quedarse pegado a la izquierda */
  margin-left:auto; margin-right:auto;
}
/* Menos aire entre los bloques superiores (cabecera, migas, título, lead) */
[data-testid="stMainBlockContainer"] > div > [data-testid="stVerticalBlock"]{ gap:.55rem; }
[data-testid="stMainBlockContainer"] h1{
  margin:.15rem 0 .25rem !important; line-height:1.12; letter-spacing:-.018em;
}
/* Cabecera nativa transparente y compacta; conserva el botón de mostrar/
   ocultar barra lateral (imprescindible en móvil). */
[data-testid="stHeader"]{background:transparent; height:2rem; min-height:0;}
@media (max-width:900px){
  [data-testid="stMainBlockContainer"]{padding:2.25rem 1rem 1.5rem;}
  [data-testid="stHeader"]{height:2.5rem;}
}
/* — El encabezado de la barra lateral ("Parámetros de simulación") arranca
     a la misma altura que el título del servicio ("Central de rutas") del
     cuerpo principal: se recorta el relleno superior del área de la barra
     y el del propio h2, y se ajusta con un margen negativo fino. — */
[data-testid="stSidebarUserContent"]{padding-top:.125rem;}
[data-testid="stSidebar"] [data-testid="stHeading"] h2{
  padding-top:0; margin-top:-.325rem;
}

/* — Foco visible en todo elemento interactivo (accesibilidad AA) — */
a:focus-visible, button:focus-visible, input:focus-visible, select:focus-visible,
[role="tab"]:focus-visible, [data-testid="stMetric"]:focus-within{
  outline:2px solid var(--color-primary) !important; outline-offset:2px !important; border-radius:2px;
}

/* — Cabecera institucional: marca (eyebrow + servicio) + estado, y debajo
     la navegación primaria. La nav sangra a ancho completo del área
     principal con -2rem (= padding del contenedor) y lo repone como padding. — */
@media (max-width:900px){
  .inst-nav, .inst-footer{margin-left:-1rem; margin-right:-1rem; padding-left:1rem; padding-right:1rem;}
}
.inst-brand{display:flex; flex-direction:column; gap:2px; min-width:0; padding:2px 0;}
.inst-brand .eyebrow{
  font-size:13px; font-weight:700; letter-spacing:.07em; text-transform:uppercase;
  color:var(--color-primary);
}
.inst-brand .service{
  font-size:24px; font-weight:700; color:var(--color-text); line-height:1.2;
  letter-spacing:-.01em;
}
/* badge de estado nativo, alineado a la derecha de la cabecera */
[data-testid="stMarkdownContainer"] span[class*="badge"]{border-radius:var(--radius-sm) !important;}

/* Navegación primaria: enlaces como pestañas subrayadas (estilo AEMET) */
.inst-nav{
  display:flex; gap:2px; margin:4px -2rem 0; padding:0 2rem;
  border-bottom:1px solid var(--color-border);
}
.inst-navitem, .inst-navitem:hover, .inst-navitem:visited{
  display:inline-flex; align-items:center; gap:7px;
  padding:9px 14px 11px; font-weight:600; font-size:15px;
  text-decoration:none !important;   /* pestaña, no enlace subrayado */
  color:var(--color-text-secondary); border-bottom:2px solid transparent;
}
.inst-navitem svg{opacity:.75;}
.inst-navitem:hover{color:var(--color-primary);}
.inst-navitem.current{color:var(--color-text); border-bottom-color:var(--color-primary);}
.inst-navitem.current svg{opacity:1;}

/* Navegación con st.page_link (navega sin recargar): mismo aspecto de pestaña.
   La página actual va como `disabled` -> se pinta subrayada y en color de texto. */
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"]{ margin:0; }
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"] a{
  padding:8px 12px 10px !important; border-radius:0 !important;
  border-bottom:2px solid transparent; text-decoration:none !important;
  font-weight:600; font-size:15px; color:var(--color-text-secondary) !important;
}
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"] a:hover{
  color:var(--color-primary) !important; background:transparent !important;
}
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"] a[aria-disabled="true"],
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"] a[disabled]{
  color:var(--color-text) !important; border-bottom-color:var(--color-primary); opacity:1;
}
[data-testid="stMainBlockContainer"] [data-testid="stPageLink"] [data-testid="stIconMaterial"]{
  opacity:.75; font-size:18px;
}
.inst-navrule{ border-bottom:1px solid var(--color-border); margin:-2px -2rem 0; }

/* — Migas de pan — */
.inst-breadcrumb{
  font-size:13px; color:var(--color-text-secondary); padding:8px 0 0; margin:0;
}
.inst-breadcrumb .sep{color:#9AA4AF; margin:0 8px;}
.inst-breadcrumb .current{color:var(--color-text);}

/* — Lead (párrafo de entrada) — */
.inst-lead{font-size:16px; color:var(--color-text-secondary); max-width:72ch; margin:2px 0 4px;}

/* — Cifras del panel de resultados (DESIGN_SYSTEM §2.2) — */
[data-testid="stMetricValue"]{
  font-size:2rem; font-weight:700; line-height:1.15; letter-spacing:-.01em;
  color:var(--color-text);
}
[data-testid="stMetricLabel"] p{
  font-size:12px !important; font-weight:600; text-transform:uppercase; letter-spacing:.06em;
  color:var(--color-text-secondary);
}
[data-testid="stMetric"]{
  background:var(--color-bg); border:1px solid var(--color-border);
  border-radius:var(--radius-md); padding:12px 16px;
}
[data-testid="stMetricValue"]{font-family:"DM Sans",Verdana,sans-serif;}

/* — Tarjetas / contenedores con borde: plano, 1 px, radio pequeño — */
[data-testid="stVerticalBlockBorderWrapper"]>div>[data-testid="stVerticalBlock"]{gap:.6rem;}

/* — Alertas: escuadradas, sin sombra; el color de fondo lo da el tema — */
[data-testid="stAlert"], [data-testid="stAlertContainer"]{
  border-radius:var(--radius-sm); box-shadow:none;
}

/* — Botones: radio pequeño, sin transformaciones — */
.stButton>button, .stDownloadButton>button{border-radius:var(--radius-sm); transition:background 120ms ease;}
.stButton>button:hover{transform:none;}

/* — Buscador de dirección: la lupa forma un solo control con el campo
     (input + botón pegados). Se busca con Enter o pulsando la lupa. — */
.st-key-dir-search [data-testid="stHorizontalBlock"]{gap:0 !important;}
/* la columna de la lupa ocupa su ancho y no encoge */
.st-key-dir-search [data-testid="stColumn"]:last-child{min-width:2.5rem; flex:0 0 2.5rem;}
.st-key-dir-search [data-testid="stColumn"]:last-child [data-testid="stElementContainer"],
.st-key-dir-search [data-testid="stColumn"]:last-child .stButton,
.st-key-dir-search [data-testid="stColumn"]:last-child .stTooltipHoverTarget{width:100% !important;}
/* campo: alto fijo y sin esquinas por el lado de la lupa */
.st-key-dir-search [data-testid="stTextInputRootElement"]{
  height:2.5rem;
  border-top-right-radius:0 !important; border-bottom-right-radius:0 !important;
}
.st-key-dir-search [data-testid="stTextInputRootElement"] input{
  height:2.5rem;
  border-top-right-radius:0 !important; border-bottom-right-radius:0 !important;
}
/* lupa: mismo alto que el campo, pegada, sin esquinas por el lado del campo */
.st-key-dir-search .stButton button{
  width:100%; min-width:2.5rem; height:2.5rem; min-height:2.5rem; padding:0;
  border:1px solid var(--color-border); border-left:0;
  border-top-left-radius:0 !important; border-bottom-left-radius:0 !important;
  box-shadow:none;
}
.st-key-dir-search .stButton button:hover{background:var(--color-surface);}
.st-key-dir-search .stButton button [data-testid="stIconMaterial"]{
  margin:0; color:var(--color-text-secondary); font-size:20px;
}

/* — Tablas estilo AEMET (st.table) — */
[data-testid="stTable"] table{border-collapse:collapse; font-size:14px; width:100%;}
[data-testid="stTable"] thead th{
  background:var(--color-surface-2); color:var(--color-text); font-weight:600;
  text-align:left; border-bottom:1px solid var(--color-border-strong);
}
[data-testid="stTable"] tbody td, [data-testid="stTable"] thead th{
  padding:8px 12px; border-bottom:1px solid var(--color-border); border-left:0; border-right:0;
}
[data-testid="stTable"] tbody tr:hover td{background:var(--color-primary-surface);}

/* — Pie académico (no institucional) — */
.inst-footer{
  margin:28px -2rem 0; padding:16px 2rem 12px; border-top:1px solid var(--color-border-strong);
  background:var(--color-surface); font-size:13px; color:var(--color-text-secondary);
}
.inst-footer .note{margin:0 0 6px; color:var(--color-text-secondary); max-width:88ch;}
.inst-footer .row{display:flex; flex-wrap:wrap; gap:4px 18px; align-items:center; margin:0;}
.inst-footer a{color:var(--color-primary); display:inline-flex; align-items:center; gap:4px;}
.inst-footer .brand{font-weight:700; color:var(--color-text);}

/* — Responsive: apila mapa y panel en pantallas estrechas — */
@media (max-width:900px){
  div[data-testid="stHorizontalBlock"]{flex-direction:column;}
  div[data-testid="stHorizontalBlock"]>div[data-testid="stColumn"]{width:100% !important; flex:1 1 100% !important;}
  .inst-brand .service{font-size:20px;}
  .inst-brand .eyebrow{font-size:12px;}
  h1, [data-testid="stHeading"] h1{font-size:26px !important;}
}
@media (prefers-reduced-motion:reduce){
  *{transition-duration:0ms !important; animation-duration:0ms !important;}
}

/* ───────────────────────────────────────────────────────────
   Bloque A — interacción y claridad de entrada
   ─────────────────────────────────────────────────────────── */

/* Enlace "Saltar al contenido principal": primer tabulable, oculto hasta
   recibir foco (DESIGN_SYSTEM §8.3). */
.skip-link{
  position:fixed; left:8px; top:8px; z-index:10000;   /* fixed: relativo al viewport */
  display:inline-block;                                 /* transform no aplica a inline */
  padding:8px 12px; background:var(--color-primary); color:#fff;
  border-radius:var(--radius-sm); font-weight:600; font-size:14px;
  text-decoration:none !important; transform:translateY(-260%);
  transition:transform 120ms ease;
}
.skip-link:focus, .skip-link:focus-visible{
  transform:translateY(0); outline:2px solid #fff; outline-offset:2px;
}

/* Barra lateral más ancha: que el contenido (etiquetas de sección, campo +
   lupa, coordenadas con sus botones +/-) quepa sin desplazamiento horizontal.
   Solo en escritorio. */
@media (min-width:901px){
  [data-testid="stSidebar"]{ width:360px !important; min-width:360px !important; }
  [data-testid="stSidebar"] [data-testid="stSidebarUserContent"]{ min-width:360px; }
}
/* Guarda defensiva: el texto de la barra lateral nunca fuerza scroll
   horizontal (envuelve en vez de desbordar), y el campo de dirección puede
   encogerse para dejar sitio a la lupa (los flex-item no encogen bajo su
   contenido mínimo por defecto). */
[data-testid="stSidebar"]{ overflow-x:hidden; }
[data-testid="stSidebarUserContent"] p,
[data-testid="stSidebarUserContent"] label,
[data-testid="stSidebarUserContent"] span{ overflow-wrap:anywhere; }
.st-key-dir-search [data-testid="stColumn"]:first-child{ min-width:0; }

/* Al ocultar la barra lateral, Streamlit solo la desliza fuera de la vista
   (transform), pero sigue reservando su ancho en el flex -> el contenido
   principal se queda fijo en el mismo sitio. Con el ancho a 0 en el estado
   colapsado (detectable por aria-expanded="false"), el contenido principal
   recupera ese espacio y, centrado, se desplaza al centro de la pantalla. */
[data-testid="stSidebar"][aria-expanded="false"]{
  width:0 !important; min-width:0 !important;
}

/* Alto del mapa por viewport (§4.7: 420 móvil / 560 tablet / 640-720
   escritorio). st_folium fija un alto en px; aquí se acota al viewport. */
iframe[title="streamlit_folium.st_folium"]{
  height:clamp(420px, 62vh, 720px) !important; min-height:420px;
}

/* Componente oculto de utilidad (fija lang="es" en el documento padre):
   es el único components.html de la app. */
.stApp iframe[title="st.iframe"]{ display:none !important; }

/* Estado vacío (§5.16): icono <=32 px, H3, una línea de guía, sin
   ilustración. */
.inst-empty{
  display:flex; flex-direction:column; align-items:center; text-align:center;
  gap:4px; padding:32px 20px; color:var(--color-text-secondary);
  /* recuadro de tarjeta (§5.6): sin él, el bloque flota sin relación visual
     con "Resultado" / "Predicción de tráfico por distrito" de al lado */
  border:1px solid var(--color-border); border-radius:var(--radius-md);
  background:var(--color-background);
}
.inst-empty svg{ color:var(--color-text-disabled, #8A929C); margin-bottom:4px; }
.inst-empty h3{
  margin:2px 0 0; font-size:1.1875rem; font-weight:600; color:var(--color-text);
}
.inst-empty p{ margin:0; max-width:44ch; font-size:14px; }

/* Sección "Destino" mientras no hay destino: acento a la izquierda para
   señalar el paso siguiente (indicación funcional de estado, no adorno).
   La regla se inyecta desde Python solo cuando procede. */
.st-key-sec-destino{ transition:border-color 120ms ease; }

/* Solo para lectores de pantalla (p. ej. la leyenda de una tabla cuyo
   título visible ya da el st.subheader de al lado -- ver tabla_trafico_html).
   NB: nunca escribir aquí un comentario con algo-entre-ángulos tipo
   "&lt;tag&gt;": el saneador de st.html lo confunde con una etiqueta
   incrustada y descarta la hoja de estilos ENTERA (visto en vivo, bloque de
   tráfico por distrito -- costó media hora de depuración). */
.sr-only{
  position:absolute; width:1px; height:1px; padding:0; margin:-1px;
  overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0;
}

/* Tabla de tráfico por distrito, estilo AEMET (§5.7) -- mismo aspecto que
   st.table (ver [data-testid="stTable"] más abajo), pero en HTML propio
   para poder pintar el punto de color del nivel. */
.inst-table-wrap{ overflow-x:auto; }
.inst-table{ border-collapse:collapse; font-size:14px; width:100%; }
.inst-table thead th{
  background:var(--color-surface-2); color:var(--color-text); font-weight:600;
  text-align:left; border-bottom:1px solid var(--color-border-strong);
  padding:8px 12px;
}
.inst-table tbody td{ padding:8px 12px; border-bottom:1px solid var(--color-border); }
.inst-table tbody tr:hover td{ background:var(--color-primary-surface); }
.inst-dot{
  display:inline-block; width:10px; height:10px; border-radius:50%;
  margin-right:6px; vertical-align:middle;
}

/* Lista de definición de los "parámetros efectivos" de la ruta (§2.2). */
.inst-dl{ margin:0; }
.inst-dl > div{
  display:flex; justify-content:space-between; align-items:baseline; gap:12px;
  padding:6px 0; border-bottom:1px solid var(--color-border);
}
.inst-dl > div:last-child{ border-bottom:none; }
.inst-dl dt{
  font-size:12px; font-weight:600; text-transform:uppercase; letter-spacing:.06em;
  color:var(--color-text-secondary); margin:0;
}
.inst-dl dd{ font-size:14px; font-weight:600; color:var(--color-text); margin:0; text-align:right; }
</style>
"""


def css_operativo() -> str:
    """Hoja de estilos global de la aplicación (DESIGN_SYSTEM.md). El
    enunciado pide aplicar el sistema de diseño; lo que el tema nativo de
    Streamlit no cubre (cabecera institucional, migas, pie, tamaño de las
    cifras, foco de doble anillo, tablas estilo AEMET, responsive) se aplica
    aquí con CSS puntual sobre selectores estables de Streamlit."""
    return _ESTILO_INSTITUCIONAL


# CSS para los botones de plegar/desplegar la barra lateral. Va por separado
# (st.markdown unsafe_allow_html) porque el sanitizador de st.html tira la
# hoja entera si un selector apunta a stSidebarCollapseButton /
# stExpandSidebarButton. Convierte el icono nativo («/») en la
# "hamburguesa" ☰ (tres líneas) y agranda/enmarca el botón para que se vea.
_CSS_BARRA_LATERAL = """
<style>
[data-testid="stSidebarCollapseButton"] button,
[data-testid="stExpandSidebarButton"]{
  width:2.25rem; height:2.25rem; min-width:2.25rem; padding:0;
  border:1px solid #C7CED6; border-radius:4px; background:#FFFFFF;
  display:inline-flex; align-items:center; justify-content:center;
}
[data-testid="stSidebarCollapseButton"] button:hover,
[data-testid="stExpandSidebarButton"]:hover{ background:#F4F6F8; }
/* botón flotante para volver a mostrar la barra: separado del borde y por
   encima de la cabecera */
[data-testid="stExpandSidebarButton"]{ margin:6px 0 0 6px; z-index:1000; box-shadow:0 1px 2px rgba(0,0,0,.06); }
[data-testid="stSidebarCollapseButton"] button [data-testid="stIconMaterial"],
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"]{
  font-size:0;                         /* oculta la ligadura del icono nativo */
}
[data-testid="stSidebarCollapseButton"] button [data-testid="stIconMaterial"]::after,
[data-testid="stExpandSidebarButton"] [data-testid="stIconMaterial"]::after{
  content:"\\2630";                    /* ☰ tres líneas horizontales */
  font-family:'Segoe UI Symbol','Noto Sans Symbols',system-ui,sans-serif;
  font-size:20px; line-height:1; color:#1A1A1A;
}
</style>
"""


def aplicar_estilo_barra_lateral() -> None:
    """Estiliza los botones nativos de plegar/desplegar la barra lateral
    (icono hamburguesa ☰, más grandes y visibles). Llamar una vez por página."""
    import streamlit as st

    st.markdown(_CSS_BARRA_LATERAL, unsafe_allow_html=True)


def fijar_idioma() -> None:
    """Marca ``<html lang="es">`` en el documento padre (Streamlit lo sirve con
    ``lang="en"``). Va por ``components.html`` porque ``st.markdown`` descarta el
    ``<script>`` (DESIGN_SYSTEM §8.7, §10.1). ``height=0`` -> el iframe no ocupa
    y se oculta por CSS. Llamar una vez por página."""
    import streamlit.components.v1 as components

    components.html(
        "<script>try{window.parent.document.documentElement.lang='es';}"
        "catch(e){}</script>",
        height=0,
    )


def estado_vacio(titulo: str, guia: str, icono: str = "navigator-alt") -> None:
    """Bloque de estado vacío (DESIGN_SYSTEM §5.16): icono <=32 px en color
    deshabilitado, H3, una sola línea de guía y —fuera— la acción primaria.
    Sin ilustración. Va por ``st.markdown(unsafe_allow_html=True)`` para
    conservar el ``<svg>`` de Iconoir (§10.1)."""
    import streamlit as st

    svg = icono_svg(icono, 32)
    st.markdown(
        f'<div class="inst-empty" role="status">{svg}'
        f'<h3>{titulo}</h3><p>{guia}</p></div>',
        unsafe_allow_html=True,
    )


def render_header(status: int | None, body: dict | None, activa: str = "panel") -> None:
    """Cabecera institucional: franja de utilidad + marca del Ayuntamiento +
    nombre del servicio + badge de estado del servicio, y debajo la
    navegación primaria (Panel / Sistema) como `st.page_link` estilizados.
    `activa` ∈ {panel, sistema}."""
    import streamlit as st

    # Primer elemento tabulable de la página (§8.3). El destino #contenido-
    # principal lo emite render_breadcrumb, justo antes del H1.
    st.markdown(
        '<a class="skip-link" href="#contenido-principal">'
        'Saltar al contenido principal</a>',
        unsafe_allow_html=True,
    )

    etiqueta, color, icono = badge_estado(status, body)
    marca, estado = st.columns([7, 2], gap="small", vertical_alignment="center")
    with marca:
        st.html(
            '<div class="inst-brand">'
            '<span class="eyebrow">Ayuntamiento de Madrid</span>'
            '<span class="service">Central de rutas · Cuerpo de Bomberos de Madrid</span>'
            '</div>'
        )
    with estado:
        st.badge(etiqueta, icon=f":material/{icono}:", color=color)

    # Navegación primaria. Preferimos `st.page_link`: navega en la MISMA página
    # del navegador (sin recarga completa). Si falla —p. ej. cuando un test
    # ejecuta `pages/1_Sistema.py` de forma aislada con AppTest, donde el
    # sistema de páginas no está montado— se cae a enlaces `<a>` normales.
    try:
        n1, n2, _ = st.columns([1, 1, 8], gap="small")
        n1.page_link("streamlit_app.py", label="Panel", icon=":material/map:",
                     disabled=(activa == "panel"))
        n2.page_link("pages/1_Sistema.py", label="Sistema", icon=":material/monitor_heart:",
                     disabled=(activa == "sistema"))
        st.markdown('<div class="inst-navrule"></div>', unsafe_allow_html=True)
    except Exception:
        def _item(href: str, texto: str, icono: str, actual: bool) -> str:
            cls = "inst-navitem current" if actual else "inst-navitem"
            aria = ' aria-current="page"' if actual else ""
            return f'<a class="{cls}" href="{href}"{aria}>{icono_svg(icono, 17)}<span>{texto}</span></a>'

        st.markdown(
            '<nav class="inst-nav" aria-label="Navegación principal">'
            + _item("/", "Panel", "map", activa == "panel")
            + _item("/Sistema", "Sistema", "activity", activa == "sistema")
            + '</nav>',
            unsafe_allow_html=True,
        )


def render_breadcrumb(*tramos: str) -> None:
    """Migas de pan: `render_breadcrumb("Inicio", "Panel")`. El último tramo
    es la página actual (sin enlace)."""
    import streamlit as st

    piezas = ['<span id="contenido-principal"></span>'
              '<nav class="inst-breadcrumb" aria-label="Ruta de navegación">']
    for i, tramo in enumerate(tramos):
        if i:
            piezas.append('<span class="sep" aria-hidden="true">&rsaquo;</span>')
        if i == len(tramos) - 1:
            piezas.append(f'<span class="current" aria-current="page">{tramo}</span>')
        else:
            piezas.append(f'<span>{tramo}</span>')
    piezas.append('</nav>')
    st.html("".join(piezas))


def render_footer() -> None:
    """Pie académico (DESIGN_SYSTEM §4.6, §1.6): nota de prototipo + versión +
    referencias. SIN los enlaces legales del Ayuntamiento: la app se inspira
    en su lenguaje visual, no lo suplanta."""
    import streamlit as st

    ext = icono_svg("open-new-window", 13)
    # st.markdown(unsafe_allow_html): conserva el <svg> del icono de enlace externo.
    st.markdown(
        '<div class="inst-footer">'
        '<p class="note">Trabajo Fin de Máster en Big Data, Ciencia de Datos e '
        'Inteligencia Artificial (Universidad Complutense de Madrid). Prototipo de '
        'apoyo al despacho de vehículos del Cuerpo de Bomberos de Madrid: calcula '
        'la ruta óptima entre un parque y el punto de un incidente teniendo en '
        'cuenta las restricciones físicas del vehículo y la predicción de tráfico '
        'por distrito. No es un servicio oficial del Ayuntamiento de Madrid.</p>'
        '<p class="row">'
        '<span class="brand">Central de rutas · TFM</span>'
        '<a href="/Sistema">Estado del sistema</a>'
        '<a href="https://www.madrid.es/portales/munimadrid/es/Inicio/Accesibilidad" '
        f'target="_blank" rel="noopener">Referencia de accesibilidad: Ayuntamiento de Madrid {ext}</a>'
        '</p>'
        '</div>',
        unsafe_allow_html=True,
    )


def ocultar_barra_lateral() -> None:
    """Oculta por completo la barra lateral y su control de plegado. Para
    páginas que no tienen parámetros (p. ej. «Sistema»), donde la barra
    quedaría vacía. `st.markdown(unsafe_allow_html)`: el sanitizador de
    st.html tira la hoja si un selector apunta a stSidebarCollapseButton."""
    import streamlit as st

    st.markdown(
        "<style>"
        '[data-testid="stSidebar"], [data-testid="stSidebarCollapseButton"],'
        '[data-testid="stExpandSidebarButton"]{display:none !important;}'
        '[data-testid="stMain"]{margin-left:0 !important;}'
        "</style>",
        unsafe_allow_html=True,
    )
