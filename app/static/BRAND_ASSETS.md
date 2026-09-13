# Assets estáticos — marca e iconos

`app/static/` la sirve Streamlit como estática (`[server] enableStaticServing =
true` en `.streamlit/config.toml`). Un fichero `app/static/x.svg` se referencia
como `app/static/x.svg` desde `st.image(...)` o `<img src="app/static/x.svg">`.

## Marca — deliberadamente sin logo oficial

La app **toma como referencia** el lenguaje visual del Ayuntamiento de Madrid
(azul `#0055A0`, DM Sans, sobriedad), pero **no** es ni parece un servicio
oficial (`DESIGN_SYSTEM.md` §1.6). Por eso:

- La cabecera (`app/_ui_common.py::render_header`) usa **solo texto** en DM Sans
  ("Ayuntamiento de Madrid" como *eyebrow* de referencia + nombre de la app).
- **No se incluye el escudo (oso y madroño) ni el logotipo oficial**, ni se
  recrean con SVG/CSS a mano.
- El escudo/logotipo del Ayuntamiento requieren autorización de uso institucional
  y **no** se pueden incrustar en un prototipo de terceros solo por estar
  descargables. No se añaden.
- Favicon: se usa un icono Material (`:material/local_fire_department:` en
  `st.set_page_config`), no un favicon municipal.

Si el TFM necesitara en algún momento una marca gráfica propia, sería una
**identidad de la aplicación** (no del Ayuntamiento), y su SVG iría aquí como
`logo-central-rutas.svg`.

## Iconos — `app/static/icons/`

Kit **Iconoir** (MIT, 21 SVG) para iconografía dentro de bloques HTML propios
(`st.html` / `st.markdown`): leyenda del mapa, cabecera, estados vacíos.
Ver `app/static/icons/README.md` y `DESIGN_SYSTEM.md` §6. Los widgets nativos de
Streamlit siguen usando **Material Symbols** vía `icon=` / `:material/…:`.

## No hacer

- No descargar assets de terceros sin licencia clara (los de `icons/` son MIT y
  llevan `LICENSE.iconoir.txt`).
- No usar el escudo/logotipo del Ayuntamiento sin autorización de uso
  institucional.
- No reconstruir el oso y el madroño con paths SVG "a mano".
