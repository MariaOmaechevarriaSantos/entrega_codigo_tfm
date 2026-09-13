"""
Alerta meteorológica informativa — P5, bloque 6.

**INFORMATIVA.** No entra en `routing/optimizer.py::calcular_ruta`. El
modelo de tráfico de P3 excluye la meteorología a propósito
(`ml/predict_trafico_real.py` se niega a cargar si
`metadata["use_weather_features"]`), así que esta alerta solo se muestra
en la interfaz y nunca modifica un cálculo de ruta.

Módulo ÚNICO de umbrales del proyecto ("Prohibido duplicar constantes",
ver docs/p5/README_P5_API_Streamlit.md §1): la API lo llama en
`GET /meteorologia` y Streamlit consume el bloque `alerta` ya resuelto por
HTTP, no reimplementa la clasificación.

Umbrales anclados al **Plan Meteoalerta de AEMET** para las zonas de la
Comunidad de Madrid: `precaucion` ≈ aviso amarillo, `adversa` ≈ aviso
naranja.

  - Lluvia (mm acumulados en 1 h): amarillo 15, naranja 30. El feed
    municipal publica precipitación acumulada horaria en mm -> match
    directo.
  - Temperatura máxima (°C): amarillo 36, naranja 39 (Madrid capital).
  - Temperatura mínima (°C): amarillo -2, naranja -6.
  - Viento medio (km/h): 40 / 70. **Adaptación**, no una cifra AEMET
    literal: AEMET avisa por RACHA máxima (amarillo 70, naranja 90
    km/h) y el feed municipal reporta viento MEDIO (media ~= 0.6-0.7x
    racha), con lo que aplicar 70/90 sobre la media casi nunca
    dispararía. Se usan los equivalentes en viento sostenido: Beaufort 6
    ("viento fuerte", ~40 km/h) para precaución y Beaufort 8 ("temporal",
    ~70 km/h) para adversa. Decisión registrada en
    docs/p5/README_P5_API_Streamlit.md, bloque 6.
"""

NIVEL_NORMAL = "normal"
NIVEL_PRECAUCION = "precaucion"
NIVEL_ADVERSA = "adversa"

# Orden de severidad para poder tomar "el peor" nivel entre magnitudes/estaciones.
_SEVERIDAD = {NIVEL_NORMAL: 0, NIVEL_PRECAUCION: 1, NIVEL_ADVERSA: 2}

# Cada magnitud: (umbral_precaucion, umbral_adversa, sentido).
#   sentido "mayor"  -> el valor dispara al SUPERAR el umbral  (>=)
#   sentido "menor"  -> el valor dispara al BAJAR  del umbral  (<=)
# Los cortes son INCLUSIVOS: exactamente 15.0 mm/1h ya es precaución.
UMBRALES = {
    "lluvia": {
        "label": "lluvia",
        "unidad": "mm/1h",
        "precaucion": 15.0,
        "adversa": 30.0,
        "sentido": "mayor",
    },
    "viento": {
        "label": "viento medio",
        "unidad": "km/h",
        "precaucion": 40.0,
        "adversa": 70.0,
        "sentido": "mayor",
    },
    "temp_alta": {
        "label": "temperatura máxima",
        "unidad": "°C",
        "precaucion": 36.0,
        "adversa": 39.0,
        "sentido": "mayor",
    },
    "temp_baja": {
        "label": "temperatura mínima",
        "unidad": "°C",
        "precaucion": -2.0,
        "adversa": -6.0,
        "sentido": "menor",
    },
}

NOTA_INFORMATIVA = (
    "Alerta meramente informativa: no modifica el cálculo de la ruta. "
    "El modelo de tráfico de P3 excluye la meteorología a propósito."
)

# De qué clave de cada lectura sale el valor de cada magnitud.
_MAGNITUD_CAMPO = {
    "lluvia": "precipitacion_mm",
    "viento": "viento_kmh",
    "temp_alta": "temperatura",
    "temp_baja": "temperatura",
}


def _nivel_para_valor(valor: float, umbral: dict) -> str:
    if umbral["sentido"] == "mayor":
        if valor >= umbral["adversa"]:
            return NIVEL_ADVERSA
        if valor >= umbral["precaucion"]:
            return NIVEL_PRECAUCION
        return NIVEL_NORMAL
    # sentido "menor"
    if valor <= umbral["adversa"]:
        return NIVEL_ADVERSA
    if valor <= umbral["precaucion"]:
        return NIVEL_PRECAUCION
    return NIVEL_NORMAL


def _valor_extremo(lecturas: list[dict], campo: str, sentido: str):
    """(valor, nombre_estacion) del peor caso de `campo` entre las lecturas
    que lo reportan; (None, None) si ninguna lo reporta."""
    candidatos = [
        (lectura.get(campo), lectura.get("nombre") or "estación desconocida")
        for lectura in lecturas
        if isinstance(lectura.get(campo), (int, float))
    ]
    if not candidatos:
        return None, None
    return (max if sentido == "mayor" else min)(candidatos, key=lambda t: t[0])


def clasificar_alerta(lecturas: list[dict]) -> dict:
    """
    Clasifica el estado meteorológico de la ciudad a partir de las lecturas
    por estación (una alerta única de ciudad, el PEOR caso entre todas las
    estaciones que reportan). No agrega por distrito.

    `lecturas`: lista de dicts con, al menos, las claves `nombre`,
    `temperatura`, `precipitacion_mm`, `viento_kmh`. Los valores ausentes o
    no numéricos (None, NaN) se ignoran magnitud a magnitud.

    Devuelve `{"nivel", "criterio", "nota_informativa"}`:
      - `nivel`: "normal" | "precaucion" | "adversa".
      - `criterio`: texto legible de qué magnitud/estación/valor lo disparó
        (o por qué es normal / no evaluable).
    """
    disparos = []          # (nivel, texto) de cada magnitud que supera "normal"
    magnitudes_con_dato = 0

    for clave, umbral in UMBRALES.items():
        valor, estacion = _valor_extremo(lecturas, _MAGNITUD_CAMPO[clave], umbral["sentido"])
        if valor is None:
            continue
        magnitudes_con_dato += 1
        nivel = _nivel_para_valor(valor, umbral)
        if nivel == NIVEL_NORMAL:
            continue
        corte = umbral["adversa"] if nivel == NIVEL_ADVERSA else umbral["precaucion"]
        op = "≥" if umbral["sentido"] == "mayor" else "≤"
        disparos.append((
            nivel,
            f"{umbral['label']} {valor:.1f} {umbral['unidad']} en '{estacion}' ({op} {corte:g} {umbral['unidad']})",
        ))

    n_estaciones = sum(
        1 for lectura in lecturas
        if any(isinstance(lectura.get(c), (int, float)) for c in ("temperatura", "precipitacion_mm", "viento_kmh"))
    )

    if not disparos:
        if magnitudes_con_dato == 0:
            criterio = (
                "sin datos meteorológicos para evaluar "
                "(ninguna estación reporta lluvia, viento ni temperatura)"
            )
        elif n_estaciones == 1:
            criterio = "sin condiciones adversas en la única estación que reporta"
        else:
            criterio = f"sin condiciones adversas en las {n_estaciones} estaciones que reportan"
        return {"nivel": NIVEL_NORMAL, "criterio": criterio, "nota_informativa": NOTA_INFORMATIVA}

    nivel_global = max((n for n, _ in disparos), key=lambda n: _SEVERIDAD[n])
    criterio = "; ".join(texto for n, texto in disparos if n == nivel_global)
    return {"nivel": nivel_global, "criterio": criterio, "nota_informativa": NOTA_INFORMATIVA}
