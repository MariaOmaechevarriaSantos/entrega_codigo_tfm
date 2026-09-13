# DESIGN_SYSTEM.md — Central de rutas de emergencia (TFM)

Sistema de diseño de la aplicación. Objetivo: que la interfaz **beba del lenguaje
visual** de los portales del Ayuntamiento de Madrid —su paleta, su tipografía, su
sobriedad y su forma de organizar la información— para transmitir claridad,
seriedad, confianza y densidad de información razonable, **sin hacerse pasar por**
un servicio oficial del Ayuntamiento y **sin** parecer una *landing page*
comercial. Es un **prototipo de TFM con identidad propia** que se inspira en ese
ecosistema visual, no un producto del Ayuntamiento.

- **Referencia visual principal:** los portales del Ayuntamiento de Madrid
  (`madrid.es`) — de ahí salen el azul, la tipografía y el tono. **No** su marca:
  no se reproduce el escudo, ni el logotipo, ni el pie legal oficial, ni se
  reclama conformidad oficial de accesibilidad. Ver §1.6.
- **Referencias de estructura e IA:** AEMET (predicción por municipios) y
  Comunidad de Madrid (Bomberos) — tablas de datos, pestañas, acordeones,
  migas de pan. Se toman patrones, no marca.
- Este documento define el objetivo y los *tokens*. La aplicación del sistema al
  código se hace por iteraciones (ver "Estado del código").

> **Estado del código.** Aplicado a la interfaz (agosto 2026):
> `.streamlit/config.toml` usa `primaryColor = "#0055A0"` (azul Ayuntamiento) y
> DM Sans; el rojo queda solo como `--color-error`. `app/_ui_common.py`
> concentra la hoja de estilos institucional (`css_operativo`) y el andamiaje
> (`render_header` / `render_breadcrumb` / `render_footer`). Basemap: OpenStreetMap
> (CartoDB Positron sale con marca de agua en algunos entornos; ambos son válidos
> según §3.4 — cambia `TILES_BASE` en `_ui_common.py` si Positron se ve limpio en
> tu red). Assets de marca: ver `app/static/BRAND_ASSETS.md`. Iconos: Material
> Symbols en los widgets + kit **Iconoir** (MIT, 21 SVG) en `app/static/icons/`
> para HTML propio — ver §6. Posicionamiento (referencia, no suplantación): §1.6.

---

## 0. Investigación previa

### 0.1 Fuentes oficiales del Ayuntamiento de Madrid

| Dato | Valor oficial | Fuente |
|---|---|---|
| Color corporativo | **Azul, blanco y negro.** El azul es el color maestro: **Pantone 286 C**, del que derivan CMYK / RGB / HEX / RAL para cada entorno. | [identidad.madrid.es/colores](https://identidad.madrid.es/colores/) · [COLOR.pdf](https://identidad.madrid.es/wp-content/uploads/2026/01/COLOR.pdf) · [Manual 2026 V.1.1](https://identidad.madrid.es/wp-content/uploads/2026/03/V.1.1-Ayuntamiento-de-Madrid-Manual-2026-1.pdf) |
| Valor digital del azul | Recogido de la ficha de identidad como **RGB 0/85/160 · HEX `#0055A0`** (CMYK 100/75/0/0). *No se ha podido abrir el PDF oficial directamente (403); confírmese `#0055A0` contra `COLOR.pdf`.* | resúmenes de [identidad.madrid.es/colores](https://identidad.madrid.es/colores/) |
| Regla de color | "No se permiten variantes, ajustes manuales ni reinterpretaciones del color corporativo." Un solo azul, sin degradados de marca. | [identidad.madrid.es/colores](https://identidad.madrid.es/colores/) |
| Tipografía corporativa | **DM Sans** (todas las variantes, online y offline; se prefieren **Regular** y **Semibold**) + **Verdana** como *fallback* cuando no se puede usar DM Sans (elegida por legibilidad/accesibilidad). | [identidad.madrid.es/tipografias](https://identidad.madrid.es/tipografias/) · [Manual 2026 V.1.1](https://identidad.madrid.es/wp-content/uploads/2026/03/V.1.1-Ayuntamiento-de-Madrid-Manual-2026-1.pdf) |
| Logotipo | Escudo (oso y madroño) + "AYUNTAMIENTO DE MADRID". Uso en azul, blanco o negro. *No se reproduce el logo en esta app académica.* | [identidad.madrid.es/manual](https://identidad.madrid.es/manual/) |
| Accesibilidad | Portales municipales conformes con **RD 1112/2018** (transposición de la Directiva UE 2016/2102) y **WCAG 2.1 / EN 301 549**; declaración de accesibilidad pública. | [madrid.es/Accesibilidad](https://www.madrid.es/portales/munimadrid/es/Inicio/Accesibilidad) |

DM Sans y Verdana son de uso libre (Google Fonts / sistema): se pueden servir sin
licencia adicional.

### 0.2 Patrones comunes a las tres referencias (madrid.es, AEMET, Comunidad de Madrid)

1. **Logo institucional arriba a la izquierda**, siempre enlaza a inicio.
2. **Navegación primaria horizontal corta** (5–7 secciones de primer nivel) +
   **buscador destacado** ("¿Qué estás buscando?").
3. **Migas de pan** en toda página interior: `Inicio › Sección › Página`.
4. **Título de página (H1) grande y plano**, sin *hero* decorativo en páginas de
   contenido. La información va primero.
5. **Contenido modular**: acordeones para divulgación progresiva, tarjetas
   sobrias (miniatura + título + enlace), listas de viñetas y **tablas de datos**
   (la tabla de predicción a 7 días de AEMET es el mejor modelo para nuestros
   resultados de ruta e isócronas).
6. **Paleta contenida**: azul/marino + grises + blanco. El color se usa de forma
   **funcional** (iconos de estado del tiempo, azul de enlace), nunca decorativa.
7. **Sans-serif con varios pesos** para la jerarquía; **densidad media** (ni
   apelmazado ni con enormes vacíos).
8. **Pie institucional** con enlaces legales, protección de datos, accesibilidad,
   RSS y redes oficiales: señales de confianza. *(Nosotros lo adaptamos a un pie
   académico de una línea — §4.6, §1.6 — no lo copiamos.)*
9. **Accesibilidad declarada** (RD 1112/2018, WAI/W3C) como elemento de primer
   nivel.
10. **Vistas conmutables** de un mismo dato mediante **pestañas** (AEMET:
    "7 días / por horas / mapa").

### 0.3 Elementos específicos de cada institución

| Institución | Rasgos propios | Qué tomamos |
|---|---|---|
| **Ayuntamiento de Madrid** (`madrid.es`) | Azul Madrid `#0055A0`, escudo oso y madroño, "Sede Electrónica", portada con tarjetas y "Lo más visto", tipografía **DM Sans**. | **Solo el lenguaje visual**: color, tipografía, tono sobrio, migas de pan, H1 plano. **No** el escudo, ni "Sede Electrónica", ni un pie legal como el suyo. |
| **AEMET** | Co-marca ministerial; **tabla de predicción 7 días** + pestañas 7 días / por horas / mapa; set de iconos meteorológicos estandarizado; alta densidad de datos legible; selector de idioma. | **Modelo de presentación de datos**: tablas densas y legibles, pestañas para vistas del mismo dato, iconografía de estado consistente. |
| **Comunidad de Madrid** (Bomberos) | Marino regional; **acordeón** "Conoce el Cuerpo de Bomberos"; *hero* fotográfico en la portada; tarjetas de unidades (GERA, ERICAM, GED); "Sugerencias, quejas y agradecimientos". | **IA**: acordeón para detalle secundario, tarjetas para objetos discretos, lenguaje formal ("Cuerpo de Bomberos"). **No** copiamos el *hero* fotográfico: nuestra app es operativa, no divulgativa. |

### 0.4 Traducción a nuestra aplicación

La app es una **herramienta operativa** (apoyo al despacho de rutas), no un portal
divulgativo. Por tanto:

- **Menos editorial** que `madrid.es` (sin *hero*, sin "lo más visto", sin
  fotografía).
- **Más "AEMET"**: tablas de datos, vistas por pestañas, páginas de diagnóstico,
  migas de pan, H1 plano.
- **Con el lenguaje visual del Ayuntamiento**, no con su marca: azul `#0055A0`,
  DM Sans, paleta austera (azul + grises + blanco). El pie es **académico**
  (prototipo de TFM), no un pie institucional con las políticas legales del
  Ayuntamiento. Ver §1.6 y §4.6.

---

## 1. Brand identity

### 1.1 Personalidad visual
Sobria, técnica y funcional, con el aire de una herramienta de administración
pública: seria sin ser fría, densa sin ser abrumadora. Cercana al panel de un
centro de control, no a un producto SaaS. Toma prestado el registro visual de los
portales municipales, pero es una aplicación autónoma (prototipo de TFM).

### 1.2 Sensación que debe transmitir
- **Confianza:** los datos y las cifras son fiables y trazables; el aspecto sobrio
  y familiar —próximo al de los portales municipales— refuerza esa percepción.
  La app **no** se presenta como un servicio oficial.
- **Claridad:** se entiende de un vistazo qué hace cada zona y qué significa cada
  cifra, también en un proyector.
- **Competencia:** el diseño no llama la atención sobre sí mismo; deja ver la
  información.
- **Accesibilidad:** utilizable por cualquiera, con teclado, con baja visión, en
  pantallas grandes de sala.

### 1.3 Principios de diseño
1. **Información antes que decoración.** Cada píxel no textual debe significar
   algo (un marcador, una ruta, un estado).
2. **Un solo azul.** El azul corporativo es la única marca de color; lo demás son
   grises y colores funcionales usados con moderación.
3. **Jerarquía por tipografía y espacio**, no por cajas ni sombras.
4. **Consistencia total.** Un encabezado, una navegación, una escala tipográfica,
   un estilo de tarjeta y de tabla en todas las páginas.
5. **Densidad institucional.** "Lleno pero ordenado": secciones separadas por
   32–48 px, no por 120.
6. **Degradado plano.** Bordes de 1 px para separar; sombras casi inexistentes.
7. **Lenguaje humano.** Textos y errores en español claro que dicen qué hacer a
   continuación.
8. **Accesibilidad AA como mínimo**, verificada, no asumida.

### 1.4 Qué hereda del lenguaje visual municipal
- Azul Madrid `#0055A0` como color primario y tipografía **DM Sans**.
- **Migas de pan** en toda página interior y **H1 plano** con párrafo de entrada.
- Navegación primaria **corta y estable** (Panel, Sistema).
- **Tablas de datos** legibles al estilo AEMET; **estados y badges con etiqueta
  de texto**, no solo color.
- Tono formal: "Central de rutas — Cuerpo de Bomberos de Madrid".
- Tarjetas y componentes **sin adornos**: borde de 1 px, radio pequeño.
- Pie discreto y de una línea (ver §4.6) — **no** un pie institucional con las
  políticas legales del Ayuntamiento.

### 1.5 Qué evitar para que no parezca una landing comercial
- *Hero* a pantalla completa con titular gigante y CTA.
- Degradados decorativos, *glassmorphism*, sombras grandes, tarjetas flotantes.
- Morados/índigos/azules eléctricos genéricos de SaaS.
- Bordes muy redondeados, "pills" como contenedores, esquinas de 16–24 px.
- Animaciones de aparición al hacer *scroll*, *parallax*, movimiento en bucle.
- Iconos grandes decorativos junto a cada título.
- Emojis como iconografía.
- Vacíos enormes entre secciones; texto centrado flotando en la página.
- Ilustraciones de *marketing*, fotos de archivo "de negocios".

### 1.6 Posicionamiento: referencia, no suplantación

La app **se inspira** en los portales del Ayuntamiento; **no es** —ni debe
parecer— uno de ellos. Frontera explícita:

**Sí se toma de la referencia:**
- La paleta (azul `#0055A0` + grises + blanco) y su regla de "un solo azul".
- La tipografía (DM Sans + Verdana) y la escala moderada.
- La sobriedad: bordes de 1 px, sin sombras, sin degradados, densidad media.
- Los patrones de arquitectura de la información: migas de pan, H1 plano + lead,
  navegación corta, tablas de datos, pestañas, acordeones.
- El tono formal en los textos.

**No se hace (para no suplantar a una web oficial):**
- Reproducir el **escudo** (oso y madroño) ni el **logotipo** "AYUNTAMIENTO DE
  MADRID" —ni recrearlos con CSS/SVG—. La marca en cabecera es texto en DM Sans,
  claramente el de una aplicación, no el bloque de marca oficial.
- Copiar el **pie institucional** (Aviso legal / Protección de datos / RSS /
  redes oficiales) como si fueran las políticas de esta app.
- **Declarar conformidad oficial** de accesibilidad (RD 1112/2018) como hacen los
  portales municipales. La app *aspira* a WCAG 2.1 AA (§8) y puede **enlazar** a
  la declaración de `madrid.es` como referencia, pero no publica una propia.
- Usar el dominio, cabeceras "Sede Electrónica", o cualquier elemento que lleve a
  un usuario a creer que está en `madrid.es` o que el Ayuntamiento respalda la
  herramienta.
- Insinuar respaldo, colaboración o encargo institucional.

**Marca propia siempre visible:** cada página lleva en el pie la identificación
del máster (Big Data, Ciencia de Datos e IA — UCM), el propósito del prototipo y
el descargo *"No es un servicio oficial del Ayuntamiento de Madrid"* (§4.6).

---

## 2. Typography

### 2.1 Familias

| Rol | Familia | Notas |
|---|---|---|
| Principal | **DM Sans** | Tipografía corporativa del Ayuntamiento. Variable; se usan pesos 400 / 500 / 700 (el manual nombra Regular y Semibold como preferentes). Vía Google Fonts. |
| *Fallback* / secundaria | **Verdana** | *Fallback* oficial cuando DM Sans no está disponible. Websafe, alta legibilidad. |
| Monoespaciada | **DM Mono** | Solo para coordenadas, IDs de nodo, rutas de fichero y JSON (página Sistema). Vía Google Fonts. *Fallback:* `ui-monospace, Consolas, monospace`. |

**Stack CSS:**

```css
--font-family:       "DM Sans", Verdana, "Segoe UI", system-ui, -apple-system, Arial, sans-serif;
--font-family-mono:  "DM Mono", ui-monospace, "Cascadia Mono", Consolas, "Liberation Mono", monospace;
```

Carga (Google Fonts, con `display=swap`):
`DM Sans` pesos 400,500,700 (+ italic 400) · `DM Mono` 400,500.

### 2.2 Escala tipográfica

Base **16 px**. Escala moderada (institucional, no expresiva).

| Estilo | Tamaño | Peso | Line-height | Letter-spacing | Uso |
|---|---|---|---|---|---|
| **H1** | 30 px (1.875rem) | 700 | 1.25 | −0.01em | Un único título por página. |
| **H2** | 24 px (1.5rem) | 700 | 1.30 | −0.005em | Secciones principales. |
| **H3** | 19 px (1.1875rem) | 600 | 1.35 | 0 | Sub-secciones, títulos de tarjeta. |
| **H4** | 16 px (1rem) | 600 | 1.4 | 0.01em | Etiquetas de bloque dentro de tarjetas. |
| **Body** | 16 px (1rem) | 400 | 1.6 | 0 | Texto general. |
| **Body small** | 14 px (0.875rem) | 400 | 1.5 | 0 | Texto de apoyo, celdas de tabla densas. |
| **Caption / legal** | 13 px (0.8125rem) | 400 | 1.5 | 0 | Pie, notas al pie, marcas de tiempo. |
| **Label** | 13 px (0.8125rem) | 600 | 1.4 | 0.01em | Etiquetas de formulario (sentence case). |
| **Eyebrow / métrica** | 12 px (0.75rem) | 600 | 1.3 | 0.06em | Etiqueta sobre una cifra grande. `TEXT-TRANSFORM: uppercase`. |
| **Métrica (valor)** | 32 px (2rem) | 700 | 1.15 | −0.01em | Cifras del panel de resultados (tiempo, distancia). |
| **Botón** | 15 px (0.9375rem) | 600 | 1 | 0.01em | Sentence case, nunca MAYÚSCULAS completas salvo el *eyebrow*. |
| **Code / mono** | 13.5 px (0.84rem) | 400 | 1.55 | 0 | `--font-family-mono`. |

**Reglas:**
- **Sentence case** en títulos, etiquetas y botones. `Title Case` y `MAYÚSCULAS`
  solo en el *eyebrow* de una métrica.
- Longitud de línea del cuerpo ≤ **72 caracteres** (`max-width: 72ch` en bloques
  de texto largo).
- No usar pesos por debajo de 400 ni "hairline". Negrita = 700.
- Cursiva solo para citas o términos; nunca para párrafos.
- No justificar texto.

---

## 3. Colors

Paleta **pequeña y deliberada**: un azul de marca + neutros + colores funcionales.

### 3.1 Marca

| Token | HEX | Uso | Contraste |
|---|---|---|---|
| `--color-primary` | `#0055A0` | Azul Madrid (Pantone 286 C). Botones primarios, enlaces, foco, cabecera, pestaña activa. | Blanco encima ≈ 7.5:1 (AA normal, AAA grande). |
| `--color-primary-hover` | `#00468A` | *Hover* de elementos primarios. | Blanco ≈ 10.4:1. |
| `--color-primary-dark` | `#003A73` | Estado activo/pulsado, franja superior de cabecera, cabeceras de tabla enfáticas. | Blanco ≈ 12.6:1. |
| `--color-primary-contrast` | `#FFFFFF` | Texto/iconos sobre superficies primarias. | — |
| `--color-primary-surface` | `#E8F0F7` | Fondo tenue: fila seleccionada, fondo de alerta informativa, resaltado sutil. | Texto `#1A1A1A` encima ≈ 15:1. |

> El azul **no se reinterpreta**: no generar tintes/sombras "a ojo" fuera de estos
> cinco valores. `hover` y `dark` son oscurecidos controlados del maestro.

### 3.2 Neutros

| Token | HEX | Uso |
|---|---|---|
| `--color-background` | `#FFFFFF` | Fondo principal de la página y del mapa-contenedor. |
| `--color-surface` | `#F4F6F8` | Fondo secundario: barra lateral, tarjetas, zonas agrupadas. |
| `--color-surface-2` | `#E9EDF1` | Anidado: cabecera de tabla, celda de cabecera, *chips*. |
| `--color-text` | `#1A1A1A` | Texto principal (el "negro" de la identidad). ≈ 17:1 sobre blanco. |
| `--color-text-secondary` | `#4A5560` | Texto de apoyo, *captions*, ayuda de campo. ≈ 7.6:1 sobre blanco. |
| `--color-text-disabled` | `#8A929C` | Texto deshabilitado. ≈ 3.5:1 (solo para estados no informativos). |
| `--color-border` | `#C7CED6` | Borde por defecto de tarjetas, inputs, divisores. |
| `--color-border-strong` | `#9AA4AF` | Divisores enfáticos, borde de tabla exterior. |

### 3.3 Funcionales (interfaz, **no** marca)

Uso **solo** cuando comunican estado. Siempre acompañados de icono + texto.

| Token | HEX | Superficie | Uso |
|---|---|---|---|
| `--color-success` | `#1E7E34` | `--color-success-surface: #E6F2E9` | Confirmación ("Ruta calculada correctamente", "Operativo"). |
| `--color-warning` | `#A15C00` | `--color-warning-surface: #FBF0DF` | Aviso / degradación ("ruta parcial", "modelo no cargado", "Degradado"). |
| `--color-error` | `#B3261E` | `--color-error-surface: #FBE9E7` | Error que impide continuar ("Sin conexión", "no existe ruta"). |
| `--color-info` | `#0055A0` | `--color-info-surface: #E8F0F7` | Informativo neutro ("Inicializando el servicio…", meteorología). **= azul de marca.** |

Contrastes sobre blanco: success ≈ 5.1:1 · warning ≈ 5.2:1 · error ≈ 6.5:1 —
todos ≥ 4.5:1 (AA texto normal). Sus superficies con texto `#1A1A1A` encima ≥ 13:1.

> El **rojo bomberos** (`#c62828`) que hoy usa `config.toml` deja de ser color de
> marca. Solo `--color-error` es rojo, y más apagado (`#B3261E`). En el mapa, la
> línea de ruta usa `--color-primary` (completa) y `--color-error` (parcial).

### 3.4 Mapa (colores de dato, dentro del componente de mapa)

| Elemento | Color | Nota |
|---|---|---|
| Basemap | CartoDB Positron / OpenStreetMap | Neutro, claro, sin marca de agua. |
| Ruta completa | `#1565C0` (≈ `--color-primary`) + halo `#FFFFFF` | Halo para leerse sobre cualquier fondo. |
| Ruta parcial | `--color-error` discontinua + halo | |
| Isócronas | Rampa **secuencial** YlOrRd `#FFFFB2 → #FECC5C → #FD8D3C → #F03B20 → #BD0026` | Único degradado permitido; es dato, no decoración. Probada para daltonismo (secuencial de un tono). |
| Marcadores | Parque (azul), incidente (ámbar), hospital (azul claro) | Icono distinto por tipo, no solo color. |

---

## 4. Layout

### 4.1 Medidas

| Token | Valor | Nota |
|---|---|---|
| `--layout-max-width` | **1280 px** | Ancho máximo del contenido centrado (páginas de contenido y Sistema). |
| `--layout-max-width-wide` | **1600 px** | Solo para la vista de Panel (mapa dominante). |
| `--layout-text-measure` | **72ch** | Ancho máximo de bloques de texto largo. |
| `--layout-gutter` | 24 px | Separación entre columnas del grid. |
| `--layout-margin` | 24 px (≥1024 px) · 16 px (<1024 px) | Margen exterior. |
| `--header-height` | 64 px | Cabecera principal. |
| `--utilitybar-height` | 36 px | Franja superior opcional (accesibilidad/ayuda). |

### 4.2 Grid
- **12 columnas**, *gutter* 24 px, márgenes exteriores según `--layout-margin`.
- Panel: 2 zonas — **barra lateral de parámetros** (≈ 320 px fija) + **área
  principal** (mapa + resultados). Fuera de Panel: contenido a una o dos columnas
  (8/4 para contenido + aparte).
- Alinear todo a la rejilla de 4 px (espaciado) — sin valores sueltos.

### 4.3 Escala de espaciado (base 4 px)

| Token | px | Uso típico |
|---|---|---|
| `--spacing-2xs` | 2 | Ajustes finos. |
| `--spacing-xs` | 4 | Separación icono–texto. |
| `--spacing-sm` | 8 | Padding interno pequeño, gap en filas. |
| `--spacing-md` | 12 | Padding de celdas de tabla, gap de formulario. |
| `--spacing-lg` | 16 | Padding de tarjeta / input. |
| `--spacing-xl` | 24 | Padding de contenedor, separación entre tarjetas. |
| `--spacing-2xl` | 32 | Separación entre sub-secciones. |
| `--spacing-3xl` | 48 | Separación entre secciones principales. |
| `--spacing-4xl` | 64 | Separación de bloque de página / antes del pie. |

**Ritmo vertical:** secciones a `--spacing-3xl` (48). Nunca > 64 entre bloques de
contenido (evitar el exceso de *whitespace* de las landings).

### 4.4 Estructura del header
1. **Cabecera principal** (`--header-height`, fondo `--color-background`, borde
   inferior 1 px `--color-border`):
   - Izquierda: **eyebrow** "Ayuntamiento de Madrid" en 11 px / 700 / mayúsculas /
     `--color-primary` (referencia visual, **texto plano**, nunca el escudo ni el
     bloque de marca oficial) + nombre de la app: **"Central de rutas · Cuerpo de
     Bomberos de Madrid"**.
   - Centro/derecha: **navegación primaria** (Panel · Sistema) — enlaces de texto,
     activo con subrayado de 2 px `--color-primary`.
   - Extremo derecho: **badge de estado del servicio** (Operativo / Degradado /
     Sin conexión).
2. **Barra de migas de pan** bajo la cabecera en páginas interiores
   (`Inicio › Panel`), 13 px, `--color-text-secondary`, separador `›`.

Sin franja de utilidad azul ni "Sede Electrónica": esos elementos identifican a
una web oficial y aquí se omiten a propósito (§1.6).

### 4.5 Estructura del contenido
`Migas → H1 → párrafo de entrada (lead, 16–18 px, `--color-text-secondary`) →
contenido modular`. Sin *hero*, sin imagen de cabecera. En Panel, tras el H1
mínimo: barra lateral (parámetros) + mapa + panel de resultados.

### 4.6 Footer
Fondo `--color-surface`, borde superior 1 px `--color-border-strong`, texto 13 px.
**Discreto y académico**, no un pie institucional. Dos líneas:

1. **Contexto (obligatorio):** identifica el máster y el propósito del trabajo,
   más el descargo de no oficialidad. Texto actual: *"Trabajo Fin de Máster en
   Big Data, Ciencia de Datos e Inteligencia Artificial (Universidad Complutense
   de Madrid). Prototipo de apoyo al despacho de vehículos del Cuerpo de Bomberos
   de Madrid: calcula la ruta óptima entre un parque y el punto de un incidente
   teniendo en cuenta las restricciones físicas del vehículo y la predicción de
   tráfico por distrito. No es un servicio oficial del Ayuntamiento de Madrid."*
   — "Ayuntamiento de Madrid" en texto normal, sin escudo.
2. **Servicio y referencias:** versión de la app · enlace a la página **Sistema**
   (`/health`) · enlace **externo** a la
   [declaración de accesibilidad de `madrid.es`](https://www.madrid.es/portales/munimadrid/es/Inicio/Accesibilidad)
   rotulado como referencia ("Referencia de accesibilidad: Ayuntamiento de
   Madrid"), **no** como política propia.

**No** incluir "Aviso legal", "Protección de datos", RSS ni redes oficiales como
si fueran de esta app: son señales de portal oficial (§1.6). Si el TFM necesita
un aviso legal propio, redáctese uno específico del prototipo, con lenguaje que
deje claro que no es del Ayuntamiento.

### 4.7 Responsive
| Breakpoint | Comportamiento |
|---|---|
| `≥ 1280 px` | Layout completo. Panel en modo `wide`. |
| `1024–1279 px` | Contenido a `--layout-max-width`. Barra lateral visible. |
| `768–1023 px` | Barra lateral de parámetros colapsa a un panel superior plegable ("Parámetros"). Nav primaria se mantiene. Tablas con *scroll* horizontal en contenedor propio. |
| `< 768 px` | Nav primaria a menú. Mapa `min-height: 420px`. Cifras del panel de resultados en una columna. Padding a `--spacing-lg`. |
| Mapa | `min-height`: 420 px (móvil) / 560 px (tablet) / 640–720 px (escritorio). Nunca *scroll* horizontal de página. |

---

## 5. Components

Reglas transversales: **borde 1 px `--color-border`**, **radio `--radius-sm` (4 px)**
por defecto y **`--radius-md` (6 px)** solo en tarjetas/diálogos, **sombra máxima
`--shadow-sm`**. Sin sombras de color, sin *glow*, sin blur.

### 5.1 Header
Ver §4.4. Fijo en *scroll* opcional (`position: sticky; top: 0`) con borde inferior
que aparece al desplazar. Sin sombra proyectada grande.

### 5.2 Navigation
- Enlaces de texto, 15 px / 600, `--color-text`. *Hover:* `--color-primary`.
  **Activo:** `--color-primary` + subrayado inferior 2 px `--color-primary`.
- Máx. 5–7 ítems de primer nivel. Sin megamenús.
- En móvil: botón "Menú" (icono + texto) que abre lista vertical a pantalla
  completa; cierra con Esc.
- Elemento activo con `aria-current="page"`.

### 5.3 Breadcrumbs
- 13 px, `--color-text-secondary`. Último elemento (`aria-current="page"`) en
  `--color-text`, sin enlace.
- Separador `›` (`--color-text-disabled`), con `aria-hidden`.
- Contenedor `<nav aria-label="Ruta de navegación">` con `<ol>`.
- Siempre presente en páginas que no son la principal.

### 5.4 Buttons

| Variante | Fondo | Texto | Borde | Uso |
|---|---|---|---|---|
| **Primario** | `--color-primary` | `#FFFFFF` | — | Acción principal de la vista ("Calcular ruta óptima"). **Uno por vista.** |
| **Secundario** | `--color-background` | `--color-primary` | 1 px `--color-primary` | Acciones alternativas ("Ver tráfico por distrito"). |
| **Terciario / texto** | transparente | `--color-primary` | — | Acciones de baja jerarquía; subrayado en *hover*. |
| **Peligro** | `--color-error` | `#FFFFFF` | — | Solo acciones destructivas. Raro en esta app. |

- Alto mínimo **40 px** (44 px en *touch*). Padding `10px 16px`. Radio `--radius-sm`.
- Peso 600, sentence case, 15 px. Icono opcional a la izquierda (20 px), **uno**.
- Estados: `hover` → `--color-primary-hover`; `active` → `--color-primary-dark`;
  `disabled` → `--color-surface-2` + `--color-text-disabled`, `cursor: not-allowed`;
  `focus-visible` → anillo `--focus-ring`.
- Sin sombras. Sin transición de más de 150 ms.

### 5.5 Links
- `--color-primary`, **subrayado siempre** en cuerpo de texto (no depender solo
  del color). *Hover:* `--color-primary-hover` + subrayado más grueso.
- `visited` no se distingue (contexto de aplicación).
- Enlaces externos: icono de 16 px "abrir en ventana nueva" + `rel="noopener"`.
- `focus-visible`: anillo `--focus-ring`.

### 5.6 Cards
- Fondo `--color-surface` **o** `--color-background` con borde 1 px
  `--color-border`. Radio `--radius-md` (6 px). Padding `--spacing-lg`
  (16) a `--spacing-xl` (24).
- Cabecera opcional: H3 o `**etiqueta**` (H4). Sin imagen salvo miniatura
  funcional.
- Sombra: **ninguna** por defecto; `--shadow-sm` solo si la tarjeta es
  interactiva (enlace de tarjeta completa).
- **No** usar tarjetas para todo. Agrupar con títulos + espacio + regla de 1 px.
  Tarjeta = objeto discreto real (una métrica, un bloque de diagnóstico, una
  unidad).

### 5.7 Tables (modelo AEMET)
- Ancho 100 % del contenedor; contenedor con `overflow-x: auto` en pantallas
  estrechas.
- **Cabecera:** fondo `--color-surface-2`, texto `--color-text` 13 px / 600,
  alineación según contenido. `position: sticky; top: 0` en tablas largas.
- **Filas:** borde inferior 1 px `--color-border`; sin bordes verticales. Zebra
  opcional (`--color-surface` en impares). *Hover* de fila: `--color-primary-surface`.
- **Celdas:** padding `--spacing-md` (12) vertical, `--spacing-lg` (16) horizontal.
  Números **alineados a la derecha**, tabulares (`font-variant-numeric: tabular-nums`).
- `<caption>` visible encima con el título de la tabla; `scope` en `<th>`.
- Sin tablas para *layout*.

### 5.8 Forms
- Una columna. Etiqueta **encima** del campo (Label: 13 px / 600 / `--color-text`).
- Ayuda del campo: 13 px `--color-text-secondary`, **encima** del campo o justo
  debajo de la etiqueta, antes de interactuar.
- Campos obligatorios: sufijo textual "(obligatorio)"; no depender de `*`.
- Agrupar campos relacionados con `<fieldset>` + `<legend>` (p. ej. "Origen —
  parque de bomberos").
- Errores: resumen al principio del formulario con enlaces a cada campo +
  mensaje **inline** bajo el campo, en `--color-error`, con icono, asociado por
  `aria-describedby`. El campo con error: borde `--color-error` 1 px.
- Botón de envío: primario, al final, alineado a la izquierda.

### 5.9 Inputs
- Alto 40 px. Fondo `--color-background`. Borde 1 px `--color-border`. Radio
  `--radius-sm`. Padding `8px 12px`. Texto 15–16 px.
- `:focus-visible`: borde `--color-primary` + anillo `--focus-ring` (2 px, offset 0
  en inputs).
- `:disabled`: fondo `--color-surface-2`, texto `--color-text-disabled`.
- Placeholder: `--color-text-secondary`, nunca como sustituto de la etiqueta.
- Numéricos (coordenadas): `inputmode="decimal"`, mono opcional para el valor.

### 5.10 Selects
- Igual que inputs. Indicador de despliegue: icono `expand_more` 20 px
  `--color-text-secondary` a la derecha.
- Lista desplegable: fondo `--color-background`, borde 1 px `--color-border`,
  sombra `--shadow-md`, radio `--radius-sm`. Opción activa:
  `--color-primary-surface`; opción con foco de teclado: borde izquierdo 2 px
  `--color-primary`.
- Para 2–4 opciones excluyentes y cortas, preferir **segmented control**
  (equivalente a las pestañas de AEMET) en vez de select.

### 5.11 Alerts
- Estructura: **borde izquierdo 4 px** del color semántico + fondo de superficie
  semántica + icono (20 px) + título opcional en 600 + texto.
- Cuatro tipos: `info` (azul de marca), `success`, `warning`, `error`.
- Radio `--radius-sm`. Sin sombra. Texto en `--color-text` (no en el color
  semántico, salvo el título).
- No usar `error` (rojo) para avisos no bloqueantes — eso es `warning`.
- Cerrable solo si el aviso es efímero; los de estado del sistema, no.
- `role="status"` (info/success) o `role="alert"` (warning/error).

### 5.12 Badges
- Altura ~20 px, 12 px / 600, padding `2px 8px`, radio `--radius-sm` (**no pill**).
- Color: superficie semántica + texto semántico oscurо + icono 14 px opcional.
- Estado del servicio: `Operativo` (success), `Degradado` (warning),
  `Sin conexión` (error). **Siempre con etiqueta de texto**, nunca solo un punto.
- No usar badges como decoración ni para conteos grandes (usar texto).

### 5.13 Tabs
- Estilo **subrayado** (no cajas): fila de enlaces 15 px / 600
  `--color-text-secondary`; activo `--color-text` + borde inferior 2 px
  `--color-primary`; borde inferior general 1 px `--color-border`.
- Uso: conmutar **vistas del mismo dato** (p. ej. resultado "Resumen / Detalle",
  o mapa "Ruta / Isócronas"). No para navegación entre páginas.
- `role="tablist"` / `role="tab"` / `role="tabpanel"`, operables con flechas.
- No anidar niveles de pestañas.

### 5.14 Modals
- Ancho máx. **560 px**, centrado. Fondo `--color-background`, borde 1 px
  `--color-border`, radio `--radius-md`, sombra `--shadow-md`.
- Cabecera: H3 + botón cerrar (icono `close` 24 px, `aria-label="Cerrar"`) arriba
  a la derecha. Pie con acciones alineadas a la derecha (primaria + secundaria).
- Backdrop `rgba(26,26,26,0.5)`. Cierra con Esc y clic fuera (si no es
  destructivo). **Atrapa el foco**; al cerrar, devuelve el foco al disparador.
- Usar **poco**: preferir divulgación *inline* / acordeón. Nunca para contenido
  esencial de lectura.

### 5.15 Loading states
- **Nunca** un spinner desnudo ni pantalla en blanco.
- Spinner **con texto** que dice qué ocurre: "Calculando ruta óptima…",
  "Calculando isócronas de {parque}…".
- Arranque del servicio: panel "Inicializando el servicio de rutas… La primera
  vez tarda 15–20 s" con reintento (patrón ya presente en la app).
- Contenido diferido: **skeletons** con la forma del contenido (rectángulos
  `--color-surface-2`), no un spinner central.
- Barra de progreso determinada cuando se conozca el avance.
- Botón que dispara trabajo: pasa a estado *loading* (spinner 16 px + texto),
  `aria-busy="true"`, deshabilitado mientras dura.

### 5.16 Empty states
- Bloque centrado y **breve**: título en H3 + una línea de guía en
  `--color-text-secondary` + la acción primaria.
- **Sin ilustración.** Icono de 32 px como mucho, en `--color-text-disabled`.
- Ejemplos:
  - Resultados: *"Aún no has calculado ninguna ruta. Selecciona el parque de
    origen y el destino, y pulsa «Calcular ruta óptima»."*
  - Tráfico por distrito: *"Sin predicción disponible. Activa «Aplicar predicción
    de tráfico» y elige fecha y hora."*

---

## 6. Icons

### 6.1 Estilo
- **Iconos de línea (outline)**, trazo **1.5 px a 24 px**, rejilla de 24 px,
  geometría simple y neutra.
- **Un solo estilo visual** en toda la app (todos *outline*, mismo grosor). No
  mezclar "filled" y "outline"; no mezclar dos sets con proporciones distintas.

### 6.2 Dos sets, cada uno en su sitio

| Contexto | Set | Por qué |
|---|---|---|
| **Widgets nativos de Streamlit** (`st.button(icon=…)`, `st.badge`, `st.metric`, `st.tabs`, `:material/nombre:` en Markdown) | **Material Symbols — Outlined** | Es el set que Streamlit **ya trae integrado**; usarlo da consistencia gratis en todos los `icon=` y no hay que empaquetar ni cargar nada. Licencia **Apache 2.0** (software libre). Offline: la fuente la sirve el propio Streamlit. |
| **Iconos dentro de HTML propio** (`st.html` / `st.markdown`): leyenda del mapa, cabecera, estados vacíos, diagramas, marcadores del mapa | **Iconoir** (SVG incrustado) | `:material/…:` **no** está disponible fuera de los widgets; y Material Symbols como SVG inline obliga a exportar del *variable font*. Iconoir da SVG listos para incrustar, con `stroke="currentColor"` y trazo **1.5 px** — visualmente el mismo lenguaje que Material Outlined. Licencia **MIT**. |

**Alternativa equivalente a Iconoir:** **Lucide** (ISC/MIT, trazo 2 px, muy
consistente) o **Tabler Icons** (MIT). Elegir **uno** y no cambiarlo. Aquí se
adopta **Iconoir** por variedad (~1600 iconos) y por el trazo de 1.5 px, que
encaja con Material Outlined.

**Descartado:** Font Awesome Pro (de pago); cualquier set sin licencia clara;
sets "duotone"/"3D"/coloreados; emojis. Nota: los marcadores del mapa usan hoy
`folium.Icon(prefix="fa")` = Font Awesome 4 (un **tercer** set, colado por
Leaflet). Para coherencia total, sustituirlos por `folium.DivIcon` con el SVG de
Iconoir correspondiente (`fire-flame`, `truck`, `health-shield`) coloreado con
los tokens de §3.4. Opcional, no bloqueante.

### 6.3 El set SVG (Iconoir): reglas

- **Bundle local, nunca CDN.** Se descargan **solo los SVG que se usan** a
  `app/static/icons/<nombre>.svg` (la CSP de Streamlit y el uso offline lo
  exigen; misma regla que los assets de marca). Kit inicial de 21 iconos ya
  descargado + `LICENSE.iconoir.txt` + `README.md` con el mapeo icono→uso.
- **Incrustar, no enlazar como color fijo.** Preferir el `<svg>` inline para que
  herede el color:
  ```python
  from pathlib import Path
  svg = Path("app/static/icons/warning-triangle.svg").read_text()
  st.html(f'<span style="color:var(--color-warning)">{svg}</span>')
  ```
  Como `<img src="app/static/icons/…svg" width="20" height="20" alt="">` solo si
  no hace falta que herede color.
- **`stroke="currentColor"`** (ya viene así en Iconoir). Quitar cualquier
  `width`/`height`/`color` fijo del SVG que estorbe al dimensionarlo.
- **Grosor:** el nativo de Iconoir (1.5 px). No usar la variante *bold*.

### 6.4 Tamaños (ambos sets)
| Tamaño | Uso |
|---|---|
| 16 px | *Inline* con texto pequeño, sufijos de campo, ítems de leyenda. |
| 20 px | Botones, inputs, alertas. |
| 24 px | Navegación, cabeceras de sección, botón de cerrar. |
| 32 px | Estado vacío, badge de estado grande en la página Sistema. |

Dimensionar en el propio `<svg>`/`<img>` (`width`/`height`), nunca escalar por CSS
un `<img>` de SVG sin dimensiones.

### 6.5 Color
- Heredan `currentColor` (color del texto contiguo).
- Interactivos: `--color-primary`. Dentro de alertas: color semántico.
- Nunca un icono con color de marca "porque sí".

### 6.6 Cuándo usar iconos
- Ítems de navegación primaria.
- Botones cuya acción se reconoce mejor con símbolo (actualizar, calcular ruta,
  ver tráfico, reintentar).
- Estado: `check-circle`, `warning-triangle`, `warning-circle`, `activity`.
- Afordancias de campo (desplegable, calendario).
- Claves de la **leyenda del mapa** (marcadores: `fire-flame` / `truck` /
  `health-shield`).

### 6.7 Cuándo NO usar iconos
- Como adorno junto a cada título o cada línea de texto.
- Sustituyendo a las viñetas de una lista.
- Más de un icono por botón.
- Iconos "solo símbolo" sin `aria-label` / `alt`.
- **Emojis** como iconografía de interfaz.

### 6.8 Licencias (todas libres, se pueden incluir en el repo)

| Set | Licencia | Atribución |
|---|---|---|
| Material Symbols | Apache License 2.0 | No obligatoria. |
| **Iconoir** (en uso) | MIT | No obligatoria; se cita en `app/static/icons/README.md` y aquí. |
| Lucide | ISC | No obligatoria. |
| Tabler Icons / Phosphor | MIT | No obligatoria. |
| Remix Icon | Apache 2.0 | No obligatoria. |

Añadir un `NOTICE` / `CREDITS.md` en la raíz cuando se cierre el TFM, listando
Material Symbols (Apache-2.0), Iconoir (MIT), DM Sans (OFL) y OpenStreetMap
(ODbL, atribución **obligatoria** — ya en el pie del mapa).

---

## 7. Images

Prioridad absoluta: **información y función sobre decoración.**

| Tipo | Regla |
|---|---|
| **Fotografías** | Evitar en la herramienta operativa. Si alguna vez se usan (página "Acerca de"), fotografía documental real del servicio de bomberos / municipal, nunca *stock* corporativo. Ratio 3:2 o 16:9, con pie y crédito. Nunca a pantalla completa como *hero*. |
| **Ilustraciones** | Ninguna. Es una herramienta de datos. |
| **Mapas** | Son el **contenido principal**, no una imagen: componente vivo. Basemap neutro y legible (Positron / OSM), superposiciones de alto contraste (ruta con halo), paleta **secuencial** para isócronas, marcadores distintos por tipo, **leyenda siempre visible**, control de escala, zoom operable por teclado. El mapa llena su contenedor con los `min-height` de §4.7. |
| **Iconos** | Ver §6. |
| **Imágenes decorativas** | Ninguna. Sin patrones de fondo, sin texturas, sin *blobs*, sin degradados de ambiente. |

Regla: si una imagen no transmite información (un marcador, una ruta, un estado,
un dato), no va.

---

## 8. Accessibility

Objetivo: **WCAG 2.1 nivel AA** / EN 301 549, el mismo listón que exige el
RD 1112/2018 a los portales públicos. La app **no publica una declaración de
accesibilidad propia** (eso identifica a una web oficial, §1.6); en el pie
**enlaza**, como referencia, a la del Ayuntamiento.

### 8.1 Contraste
- Texto normal: **≥ 4.5:1**. Texto grande (≥ 24 px, o ≥ 19 px y 700): ≥ 3:1.
- Componentes de interfaz y objetos gráficos (bordes de input, iconos con
  significado, líneas del mapa frente al fondo): **≥ 3:1**.
- Valores de esta paleta ya verificados en §3 (todos AA; la mayoría AAA).
- La línea de ruta y los marcadores llevan **halo** para garantizar ≥ 3:1 contra
  cualquier tesela del basemap.

### 8.2 Tamaños mínimos
- Cuerpo de texto **nunca < 14 px**; objetivo 16 px.
- Objetivos táctiles **≥ 44 × 44 px**; enlaces *inline* ≥ 24 px de alto efectivo
  con separación suficiente.
- Zoom del navegador al 200 % sin pérdida de contenido ni *scroll* horizontal.

### 8.3 Estados de foco
- **Todo** elemento interactivo tiene foco visible: `outline: 2px solid
  var(--color-focus-ring); outline-offset: 2px`.
- Prohibido `outline: none` sin sustituto equivalente.
- Orden de foco = orden visual = orden del DOM.
- Enlace "Saltar al contenido principal" como primer elemento tabulable.

### 8.4 Navegación por teclado
- Toda la funcionalidad operable sin ratón, incluidos el mapa (zoom/paneo con
  teclado, marcadores enfocables con tooltip accesible) y los controles
  personalizados.
- Modales: foco atrapado, Esc cierra, foco devuelto al disparador.
- Pestañas y *segmented controls*: flechas para moverse, `Home`/`End` a los
  extremos.

### 8.5 Formularios
- `<label for>` programático en cada campo; nunca solo *placeholder*.
- Instrucciones **antes** del campo. Estado obligatorio en texto.
- Agrupación semántica con `<fieldset>`/`<legend>`.
- Autocompletado (`autocomplete`) donde aplique.

### 8.6 Mensajes de error
- En español claro: **qué ha pasado + qué hacer a continuación** (patrón
  `humanizar_error` ya presente).
- Nunca *tracebacks*, rutas de disco ni códigos crudos en la cara del usuario;
  el detalle técnico va en un desplegable "Detalle técnico".
- Asociados al campo (`aria-describedby`) y anunciados (`role="alert"` /
  `aria-live="assertive"` en el resumen).
- Identificados por texto e icono, **no solo por color**.

### 8.7 Uso correcto del color
- El color **nunca** es el único medio para transmitir información: estado =
  color + icono + etiqueta; enlace = color + subrayado; series del mapa = color +
  forma/etiqueta en la leyenda.
- Paletas del mapa comprobadas para deficiencias de visión del color (la
  secuencial de un tono lo cumple; evitar rojo/verde como única distinción).
- `prefers-reduced-motion`: desactiva transiciones no esenciales.
- `lang="es"` en la raíz.

---

## 9. Visual anti-patterns

Elementos que **no** se usan salvo justificación concreta y escrita.

| Anti-patrón | Por qué | En su lugar |
|---|---|---|
| **Glassmorphism** (blur, translúcidos) | Ruido visual, baja legibilidad, estética de producto. | Superficies planas con borde de 1 px. |
| **Degradados decorativos** | "Marca" que el Ayuntamiento prohíbe reinterpretar; estética SaaS. | Rellenos sólidos de `--color-*`. Único degradado: la rampa **secuencial** de isócronas (es dato). |
| **Sombras exageradas / de color / glow** | Falsa profundidad, distrae. | Máx. `--shadow-sm` (0 1px 2px rgba(0,0,0,.06)); preferir bordes. |
| **Exceso de tarjetas** | Todo "flotando", sin jerarquía real. | Agrupar con títulos, espacio y reglas de 1 px. Tarjeta solo para objeto discreto. |
| **Border-radius grande** (≥ 12–16 px), "pills" como contenedor | Estética de *app* de consumo. | Máx. `--radius-md` (6 px) en tarjetas/diálogos; 4 px en controles. |
| **Morados / índigos / azules eléctricos genéricos** (`#6366F1`, `#8B5CF6`, `#0EA5E9`…) | Es "el look" de plantilla SaaS; choca con la identidad municipal. | **Un solo azul: `#0055A0`.** |
| **Animaciones innecesarias** (aparición al *scroll*, *parallax*, bucles) | Distraen, penalizan rendimiento y accesibilidad. | Solo transiciones de estado (hover/focus/expand) ≤ 150 ms; respetar `prefers-reduced-motion`. |
| **Neumorphism** | Contraste insuficiente, moda pasajera. | Plano + borde. |
| **Exceso de whitespace** (secciones a 120–200 px, un párrafo centrado en una página vacía) | Parece *landing*, baja la densidad informativa esperable de un portal. | Secciones a 32–48 px; contenido "lleno pero ordenado". |
| **Hero sections** de landing (banner a sangre, titular gigante + CTA) | La app es operativa, no promocional. | Migas → H1 plano → párrafo de entrada → contenido. |
| **Iconos como pura decoración** | Ruido; si se quitan no se pierde nada. | Cada icono mapea a una acción o un estado; si no, se elimina. |
| **Diseños excesivamente redondeados** | Ver radius. | Coherencia con `--radius-sm` / `--radius-md`. |
| **Un lenguaje visual distinto por sección** | Rompe la coherencia y la sensación de herramienta seria. | Un header, una nav, una escala tipográfica, un estilo de tarjeta y de tabla en Panel, Sistema y futuras páginas. Los *tokens* son la única fuente de verdad. |
| **Emojis como iconografía** | Inconsistentes entre plataformas, informales. | Material Symbols (widgets) o Iconoir SVG (HTML propio) + etiqueta. Ver §6. |
| **Reproducir el escudo/logo del Ayuntamiento o su pie legal** | La app se haría pasar por una web oficial. | Marca en texto DM Sans; pie académico con la nota de "prototipo de TFM". Ver §1.6 y §4.6. |

---

## 10. Design tokens

Bloque `:root` listo para CSS. Los valores derivan del análisis anterior; el azul
y las tipografías son los **oficiales del Ayuntamiento de Madrid**.

```css
:root {
  /* ---------- Color · marca ---------- */
  --color-primary:            #0055A0;  /* Azul Madrid — Pantone 286 C */
  --color-primary-hover:      #00468A;
  --color-primary-dark:       #003A73;
  --color-primary-contrast:   #FFFFFF;
  --color-primary-surface:    #E8F0F7;

  /* ---------- Color · neutros ---------- */
  --color-background:         #FFFFFF;
  --color-surface:            #F4F6F8;
  --color-surface-2:          #E9EDF1;
  --color-text:               #1A1A1A;
  --color-text-secondary:     #4A5560;
  --color-text-disabled:      #8A929C;
  --color-border:             #C7CED6;
  --color-border-strong:      #9AA4AF;

  /* ---------- Color · funcional (no marca) ---------- */
  --color-success:            #1E7E34;
  --color-success-surface:    #E6F2E9;
  --color-warning:            #A15C00;
  --color-warning-surface:    #FBF0DF;
  --color-error:              #B3261E;
  --color-error-surface:      #FBE9E7;
  --color-info:               #0055A0;
  --color-info-surface:       #E8F0F7;

  /* ---------- Color · mapa (dato) ---------- */
  --map-route:                #1565C0;
  --map-route-halo:           #FFFFFF;
  --map-route-partial:        #B3261E;
  --map-iso-1:                #FFFFB2;
  --map-iso-2:                #FECC5C;
  --map-iso-3:                #FD8D3C;
  --map-iso-4:                #F03B20;
  --map-iso-5:                #BD0026;

  /* ---------- Tipografía ---------- */
  --font-family:              "DM Sans", Verdana, "Segoe UI", system-ui, -apple-system, Arial, sans-serif;
  --font-family-mono:         "DM Mono", ui-monospace, "Cascadia Mono", Consolas, "Liberation Mono", monospace;

  --font-size-h1:             1.875rem;  /* 30 */
  --font-size-h2:             1.5rem;    /* 24 */
  --font-size-h3:             1.1875rem; /* 19 */
  --font-size-h4:             1rem;      /* 16 */
  --font-size-body:           1rem;      /* 16 */
  --font-size-body-sm:        0.875rem;  /* 14 */
  --font-size-caption:        0.8125rem; /* 13 */
  --font-size-label:          0.8125rem; /* 13 */
  --font-size-eyebrow:        0.75rem;   /* 12 */
  --font-size-metric:         2rem;      /* 32 */
  --font-size-button:         0.9375rem; /* 15 */

  --font-weight-regular:      400;
  --font-weight-medium:       500;
  --font-weight-semibold:     600;
  --font-weight-bold:         700;

  --line-height-tight:        1.25;
  --line-height-heading:      1.35;
  --line-height-body:         1.6;
  --line-height-ui:           1.4;

  --letter-spacing-heading:   -0.01em;
  --letter-spacing-eyebrow:   0.06em;
  --letter-spacing-label:     0.01em;

  /* ---------- Espaciado (base 4px) ---------- */
  --spacing-2xs:              0.125rem;  /* 2  */
  --spacing-xs:               0.25rem;   /* 4  */
  --spacing-sm:               0.5rem;    /* 8  */
  --spacing-md:               0.75rem;   /* 12 */
  --spacing-lg:               1rem;      /* 16 */
  --spacing-xl:               1.5rem;    /* 24 */
  --spacing-2xl:              2rem;      /* 32 */
  --spacing-3xl:              3rem;      /* 48 */
  --spacing-4xl:              4rem;      /* 64 */

  /* ---------- Radios ---------- */
  --radius-sm:                4px;       /* controles: botón, input, badge, alerta */
  --radius-md:                6px;       /* tarjetas, diálogos */
  --radius-pill:              999px;     /* SOLO puntos de estado / spinners; nunca contenedores */

  /* ---------- Bordes ---------- */
  --border-width:             1px;
  --border-color:             var(--color-border);
  --border:                   var(--border-width) solid var(--border-color);

  /* ---------- Sombras (mínimas) ---------- */
  --shadow-sm:                0 1px 2px rgba(0, 0, 0, 0.06);
  --shadow-md:                0 4px 12px rgba(0, 0, 0, 0.10);  /* solo modales / desplegables */

  /* ---------- Layout ---------- */
  --layout-max-width:         1280px;
  --layout-max-width-wide:    1600px;
  --layout-text-measure:      72ch;
  --layout-gutter:            1.5rem;    /* 24 */
  --layout-margin:            1.5rem;    /* 24 (>=1024px) */
  --header-height:            64px;
  --utilitybar-height:        36px;
  --sidebar-width:            320px;
  --map-min-height:           640px;

  /* ---------- Foco ---------- */
  --color-focus-ring:         #0055A0;
  --focus-ring:               0 0 0 2px #FFFFFF, 0 0 0 4px var(--color-focus-ring); /* doble anillo para fondos de color */
  --focus-outline:            2px solid var(--color-focus-ring);
  --focus-outline-offset:     2px;

  /* ---------- Movimiento ---------- */
  --transition-fast:          120ms ease;
  --transition-base:          150ms ease;

  /* ---------- Z-index ---------- */
  --z-header:                 100;
  --z-dropdown:               200;
  --z-map-overlay:            400;   /* leyenda, controles del mapa */
  --z-modal-backdrop:         900;
  --z-modal:                  1000;
  --z-toast:                  1100;
}

@media (max-width: 1023px) {
  :root { --layout-margin: 1rem; --map-min-height: 560px; }
}
@media (max-width: 767px) {
  :root { --map-min-height: 420px; }
}
@media (prefers-reduced-motion: reduce) {
  :root { --transition-fast: 0ms; --transition-base: 0ms; }
}
```

### 10.1 Mapeo a `.streamlit/config.toml`

Lo que el tema nativo de Streamlit puede asumir directamente:

| `config.toml` `[theme]` | Valor | Token de origen |
|---|---|---|
| `base` | `"light"` | — |
| `primaryColor` | `#0055A0` | `--color-primary` |
| `backgroundColor` | `#FFFFFF` | `--color-background` |
| `secondaryBackgroundColor` | `#F4F6F8` | `--color-surface` |
| `textColor` | `#1A1A1A` | `--color-text` |
| `linkColor` | `#0055A0` | `--color-primary` |
| `borderColor` | `#C7CED6` | `--color-border` |
| `showWidgetBorder` | `true` | §5 |
| `showSidebarBorder` | `true` | §4.4 |
| `baseRadius` | `"4px"` | `--radius-sm` |
| `font` | `DM Sans` (Google Fonts) + fallback Verdana | `--font-family` |
| `headingFont` | `DM Sans` | `--font-family` |
| `codeFont` | `DM Mono` | `--font-family-mono` |
| `baseFontSize` | `16` | `--font-size-body` |
| `headingFontSizes` | `["30px","24px","19px","16px","14px","13px"]` | §2.2 |
| `headingFontWeights` | `[700,700,600,600,600,600]` | §2.2 |
| `linkUnderline` | `true` | §5.5 |
| `greenColor` / `orangeColor` / `redColor` | `#1E7E34` / `#A15C00` / `#B3261E` | `--color-success` / `--color-warning` / `--color-error` |
| `[theme.sidebar] backgroundColor` | `#F4F6F8` | `--color-surface` |

Lo que **no** cabe en el tema nativo y requiere `st.html` / `st.markdown` puntual
o componentes: cabecera (marca + nav + migas), pie de página académico, tamaño de
la cifra de métrica (32 px), tablas estilo AEMET, *segmented control* con
subrayado, anillo de foco de doble color, y los iconos SVG de Iconoir (§6).

> **Aviso de implementación (visto en la app):** el sanitizador de `st.html` es
> más estricto que el de `st.markdown(unsafe_allow_html=True)`. En concreto,
> `st.html` **(a)** descarta la hoja de estilos entera si un selector apunta a
> `stSidebarCollapseButton` / `stExpandSidebarButton`, **(b)** elimina el
> `<svg>` incrustado, y **(c)** descarta la hoja de estilos entera si el TEXTO
> de un comentario CSS contiene algo con pinta de etiqueta (`<caption>`,
> `<nombre-de-tag>`…) — no hace falta que sea HTML real, basta con que el
> saneador lo confunda con una etiqueta incrustada al escanear el string. Un
> comentario como `/* el <caption> de la tabla */` tira TODA la hoja
> (`css_operativo()`), no solo esa regla: sin aviso en consola, sin excepción,
> simplemente ninguna variable/clase de `_ESTILO_INSTITUCIONAL` se aplica. Por
> eso el CSS de esos botones (icono ☰) y los bloques con iconos Iconoir (nav,
> pie) van por `st.markdown(unsafe_allow_html=True)`, y por eso en los
> comentarios CSS de `_ESTILO_INSTITUCIONAL` no se escribe un nombre de
> elemento entre `< >` (se dice "la leyenda de la tabla", no "el `<caption>`
> de la tabla"). Los iconos de widgets (`st.button/badge/metric(icon=…)`) usan
> Material y no tienen este problema.

---

## Fuentes

- Ayuntamiento de Madrid — Identidad institucional: <https://identidad.madrid.es/>
  · Colores: <https://identidad.madrid.es/colores/>
  · Tipografías: <https://identidad.madrid.es/tipografias/>
  · Manual 2026 V.1.1 (PDF): <https://identidad.madrid.es/wp-content/uploads/2026/03/V.1.1-Ayuntamiento-de-Madrid-Manual-2026-1.pdf>
  · Color (PDF): <https://identidad.madrid.es/wp-content/uploads/2026/01/COLOR.pdf>
- Ayuntamiento de Madrid — Accesibilidad: <https://www.madrid.es/portales/munimadrid/es/Inicio/Accesibilidad>
- Portal municipal: <https://www.madrid.es/portal/site/munimadrid>
- AEMET — Predicción Madrid: <https://www.aemet.es/es/eltiempo/prediccion/municipios/madrid-id28079>
- Comunidad de Madrid — Bomberos: <https://www.comunidad.madrid/seguridad-emergencias-asem-112/bomberos-comunidad-madrid>
- Gràffica — "El Ayuntamiento de Madrid actualiza su logo" (contexto de la marca anterior de 2016): <https://graffica.info/nuevo-logo-del-ayuntamiento-de-madrid/>
- Iconoir (set de iconos en uso, MIT): <https://iconoir.com/> · repo: <https://github.com/iconoir-icons/iconoir>
- Alternativas de iconos: Lucide <https://lucide.dev/> (ISC) · Tabler <https://tabler.io/icons> (MIT) · Material Symbols <https://fonts.google.com/icons> (Apache-2.0, el que trae Streamlit)

**Notas de verificación pendientes** (identidad.madrid.es bloquea el acceso
automatizado; confirmar con el PDF abierto en un navegador):

1. Valor **digital exacto** del azul: se ha usado `#0055A0` (RGB 0/85/160), que
   las fichas de `identidad.madrid.es/colores` atribuyen al maestro Pantone 286 C.
   Una fuente secundaria menciona un valor digital alternativo (RGB 0/61/246);
   parece corresponder a material anterior. **Confírmese `#0055A0` en `COLOR.pdf`**
   antes de fijar el token.
2. Pesos exactos autorizados de DM Sans (el manual nombra "Regular" y "Semibold"
   como preferentes; aquí se añaden 500 y 700 por necesidad de interfaz).
3. Existencia de una gama de grises oficial: el manual reduce la marca a azul +
   negro + blanco; los grises de §3.2 son **decisión de interfaz**, no de marca.
