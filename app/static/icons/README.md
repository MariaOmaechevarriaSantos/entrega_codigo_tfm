# Iconos SVG — kit de arranque (Iconoir)

Set **secundario** de iconos, para usar en bloques HTML propios (`st.html` /
`st.markdown`) donde `:material/nombre:` de Streamlit no está disponible:
leyenda del mapa, cabecera, estados vacíos, diagramas. Ver `DESIGN_SYSTEM.md` §6.

- **Fuente:** [Iconoir](https://iconoir.com/) — `icons/regular/*.svg` del repo
  <https://github.com/iconoir-icons/iconoir>.
- **Licencia:** MIT (`LICENSE.iconoir.txt`). Atribución no obligatoria; se cita
  en `DESIGN_SYSTEM.md` §6 y aquí.
- **Formato:** 24×24, `stroke-width="1.5"`, `stroke="currentColor"`,
  `fill="none"` — listos para incrustar y heredar el color del texto.

## El icono nativo sigue siendo Material Symbols

Para botones, badges, métricas y alertas se usa **Material Symbols (Outlined)**
vía los parámetros `icon=` de Streamlit y `:material/nombre:`. Apache 2.0, ya
integrado, sin empaquetar nada. Estos SVG de Iconoir son solo para lo que Material
no cubre en HTML embebido. **No mezclar un tercer set.**

## Cómo usar uno

Incrustado (hereda color y se puede dimensionar):

```python
from pathlib import Path
svg = Path("app/static/icons/truck.svg").read_text()
st.html(f'<span style="color:#0055A0">{svg}</span>')
```

o como imagen (fija el tamaño en el propio `<img>`):

```html
<img src="app/static/icons/warning-triangle.svg" width="20" height="20" alt="">
```

Tamaños: 16 / 20 / 24 / 32 px (los de §6.2). No escalar por CSS un `<img>` SVG
sin `width`/`height`.

## Kit actual (21) y a qué se destinan

| Fichero | Uso previsto |
|---|---|
| `menu` | plegar/desplegar la barra lateral (☰) |
| `map` | navegación "Panel" |
| `map-pin` | punto del incidente en la leyenda |
| `navigator-alt` | acción "Calcular ruta óptima" |
| `fire-flame` | incidente / marca de la app |
| `truck` | parque de bomberos (marcador / leyenda) |
| `health-shield`, `hospital` | hospital de referencia |
| `activity` | página "Sistema" / diagnóstico |
| `database` | estado de DuckDB en "Sistema" |
| `refresh-double` | "Actualizar" / "Reintentar" |
| `clock` | KPI "Tiempo estimado" |
| `ruler`, `ruler-combine` | KPI "Distancia" |
| `check-circle` | estado "Operativo" / éxito |
| `warning-triangle` | avisos (ruta parcial, degradado) |
| `warning-circle`, `xmark-circle` | error / "Sin conexión" |
| `nav-arrow-right` | separador de migas de pan |
| `long-arrow-right-up`, `open-new-window` | enlace externo |

Faltan sin equivalente directo en Iconoir: **tráfico/semáforo** (usar
`:material/traffic:` de Material, que sí existe) y **ambulancia**.

## Añadir más

Descarga solo el SVG concreto que necesites del repo de Iconoir a esta carpeta
(nunca por CDN: la CSP de Streamlit y el uso offline lo impiden). Mantén el
`stroke="currentColor"` y quita cualquier `width`/`height`/`color` fijo que
estorbe.
