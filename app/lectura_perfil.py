"""Lectura de un instrumento para el perfil y el horizonte del usuario: veredicto en tres niveles y una o dos razones.

El resumen de Instrumento.resumen (ver rentafy-servicioIA/app/perfiles/) se genera una vez por activo y no sabe nada de quién lo mira. Esta lectura se arma al
pedirla, con el MISMO cálculo que el Score (scoring.py), así que lo que dice el texto es exactamente lo que movió el número. Todo el texto es fijo
(plantillas), sin generación libre, igual criterio que el resumen: lo único que decide qué se cuenta son los números.
"""

from .scoring import AjusteHorizonte, BANDA_ANIOS, PESOS_PERFIL, aplicar_ajuste
from .schemas import LecturaPerfil, Moneda, PerfilInversor, PlazoInversion, RetornoEsperado

UMBRAL_RECOMENDABLE = 70  # Score final desde el cual puede ser "recomendable" (si el ajuste no lo castigó mucho)
UMBRAL_NO_RECOMENDABLE = 40
AJUSTE_FUERTE = 8  # puntos de ajuste negativo a partir de los cuales no se lo puede llamar "recomendable"
AJUSTE_MUY_FUERTE = 15
DIAS_SIN_COTIZAR_AVISO = 3  # días corridos sin cotización nueva a partir de los cuales se avisa

# Horizonte con el que se hace la cuenta del retorno esperado (el Score usa bandas, ver scoring.BANDA_ANIOS; acá hace falta un número).
HORIZONTE_REPRESENTATIVO_ANIOS: dict[str, float] = {"corto": 0.5, "mediano": 2.0, "largo": 5.0}
_HORIZONTE_TEXTO = {"corto": "corto plazo (hasta 1 año)", "mediano": "mediano plazo (1 a 3 años)", "largo": "largo plazo (más de 3 años)"}
_HORIZONTE_CORTO = {"corto": "corto plazo", "mediano": "mediano plazo", "largo": "largo plazo"}

# Por qué un tipo de activo encaja (o no) con un perfil. Solo las combinaciones donde la tabla de afinidad de scoring.py inclina el Score.
_MOTIVO_AFINIDAD: dict[tuple[str, str], str] = {
    ("conservador", "LECAP"): "es una letra de tasa fija y corto plazo: busca ganarle al plazo fijo con poco riesgo, que es lo que apunta un perfil conservador",
    ("conservador", "BONCAP"): "es un bono capitalizable en pesos de corto plazo: busca ganarle al plazo fijo con poco riesgo, que es lo que apunta un perfil conservador",
    ("conservador", "BONCER"): "ajusta por inflación, que es justamente contra lo que quiere protegerse un perfil conservador",
    ("conservador", "TAMAR"): "paga una tasa variable ligada al mercado, razonable para acompañar a la inflación",
    ("conservador", "DUAL"): "combina tasa fija y variable, razonable para acompañar a la inflación",
    ("conservador", "Dólar Linked"): "cubre contra el dólar pero no contra la inflación, que es lo que prioriza un perfil conservador",
    ("conservador", "Bono USD"): "es un bono en dólares: suma riesgo de precio y de emisor por encima de lo que busca un perfil conservador, que apunta a ganarle a la inflación y al plazo fijo",
    ("conservador", "ON"): "es una obligación negociable en dólares: suma riesgo de emisor y de precio por encima de lo que busca un perfil conservador, que apunta a ganarle a la inflación y al plazo fijo",
    ("conservador", "Bono ARS"): "es un bono de tasa fija en pesos de plazo medio o largo: no protege de la inflación y se la juega con la evolución de la tasa, algo que un perfil conservador evita",
    ("moderado", "TAMAR"): "paga una tasa variable ligada al mercado, una cobertura razonable frente a la inflación para un perfil moderado",
    ("moderado", "DUAL"): "combina tasa fija y variable, una cobertura razonable frente a la inflación para un perfil moderado",
    ("agresivo", "Dólar Linked"): "te deja apostar al tipo de cambio, algo con lo que un perfil agresivo puede querer experimentar",
    ("agresivo", "Bono ARS"): "es un bono de tasa fija en pesos de plazo largo: gana mucho si las tasas bajan, una apuesta con duration que un perfil agresivo puede asumir",
    ("moderado", "LECAP"): "es una letra de corto plazo con poco riesgo, buena para un horizonte corto",
    ("moderado", "BONCAP"): "es un bono capitalizable de corto plazo con poco riesgo, bueno para un horizonte corto",
    ("moderado", "BONCER"): "ajusta por inflación, una cobertura sensata para un perfil moderado",
    ("moderado", "Bono USD"): "es un bono en dólares: para un horizonte corto suma más riesgo de precio del que conviene, con un plazo mayor es una opción razonable",
    ("moderado", "ON"): "es una obligación negociable en dólares: para un horizonte corto suma riesgo de emisor y de precio, con un plazo mayor es una opción razonable",
    ("agresivo", "Bono USD"): "es un bono en dólares de plazo largo: ofrece rendimiento alto a cambio de más riesgo de precio, que un perfil agresivo está dispuesto a asumir",
    ("agresivo", "ON"): "es una obligación negociable en dólares: ofrece más rendimiento a cambio de riesgo de emisor, que un perfil agresivo está dispuesto a asumir",
}

_NOMBRE_FACTOR = {"rendimiento": "rendimiento", "riesgo": "riesgo (más alto es menos riesgo)", "liquidez": "liquidez", "estabilidad": "estabilidad"}


def _duracion(anios: float) -> str:
    if anios < 0.08:
        return "muy pocos días"
    if anios < 1:
        meses = max(1, round(anios * 12))
        return f"{meses} {'mes' if meses == 1 else 'meses'}"
    a = round(anios, 1)
    return f"{str(a).rstrip('0').rstrip('.').replace('.', ',')} años"


def _razon_afinidad(perfil: PerfilInversor, plazo: PlazoInversion, aj: AjusteHorizonte) -> str | None:
    if abs(aj.afinidad) < 2:
        return None
    motivo = _MOTIVO_AFINIDAD.get((perfil, aj.clase))
    if motivo is None:
        return None
    if aj.afinidad > 0:
        return f"Encaja con tu perfil {perfil} porque {motivo}."
    texto = f"Es menos adecuado para tu perfil {perfil} porque {motivo}."
    if aj.atenuacion_calidad < 0.9:
        texto += " Su bajo riesgo (calificación y duration) atenúa ese descuento."
    return texto


def _razon_plazo(plazo: PlazoInversion, aj: AjusteHorizonte) -> str | None:
    desde, hasta = BANDA_ANIOS[plazo]
    cuando = _duracion(aj.anios)
    if aj.descalce >= 3:
        if aj.anios > hasta:
            if plazo == "corto":
                return (f"Vence en {cuando}, mucho después de tu horizonte de {_HORIZONTE_CORTO[plazo]}: para recuperar el capital tendrías que venderlo antes "
                        "a precio de mercado, y su precio puede moverse en contra.")
            return (f"Vence en {cuando}, bastante después de tu horizonte de {_HORIZONTE_CORTO[plazo]}: antes de cobrarlo tendrías que venderlo "
                    "a precio de mercado, y su precio puede moverse en contra.")
        return f"Vence en {cuando}, antes de tu horizonte de {_HORIZONTE_CORTO[plazo]}: al vencer vas a tener que reinvertir sin saber a qué tasa."
    if plazo != "largo" and desde < aj.anios <= hasta and aj.anios > 0.1:
        return f"Su vencimiento ({cuando}) calza con tu horizonte de {_HORIZONTE_CORTO[plazo]}."
    if plazo == "largo" and aj.anios >= 3:
        return f"Su plazo largo ({cuando}) encaja con tu horizonte de {_HORIZONTE_CORTO[plazo]}: tiene tiempo para rendir."
    return None


def _razon_factor(perfil: PerfilInversor, factores: dict[str, float | None]) -> str | None:
    """El factor al que más peso le da el perfil (entre los calculados), si es claramente alto o bajo."""
    pesos = PESOS_PERFIL[perfil]
    candidatos = [(getattr(pesos, k), k, v) for k, v in factores.items() if v is not None]
    if not candidatos:
        return None
    _, nombre, valor = max(candidatos)
    if valor >= 75:
        return f"Su {_NOMBRE_FACTOR[nombre]} es alto ({round(valor)}/100), justo lo que más pesa en tu perfil."
    if valor < 50:
        return f"Su {_NOMBRE_FACTOR[nombre]} es bajo ({round(valor)}/100), justo lo que más pesa en tu perfil."
    return None


def veredicto_de(final: float, ajuste_total: float) -> str:
    if final < UMBRAL_NO_RECOMENDABLE or (ajuste_total <= -AJUSTE_MUY_FUERTE and final < 55):
        return "no_recomendable"
    if final >= UMBRAL_RECOMENDABLE and ajuste_total > -AJUSTE_FUERTE:
        return "recomendable"
    return "con_reservas"


_TITULO = {
    "recomendable": "Recomendable para tu perfil y tu horizonte",
    "con_reservas": "Con reservas para tu perfil y tu horizonte",
    "no_recomendable": "No recomendable para tu perfil y tu horizonte",
}


def armar_lectura(
    base: float,
    ajuste: AjusteHorizonte | None,
    factores: dict[str, float | None],
    perfil: PerfilInversor,
    plazo: PlazoInversion,
    retorno: RetornoEsperado | None = None,
    restricciones: list[str] | None = None,
) -> LecturaPerfil:
    final = aplicar_ajuste(base, ajuste)
    total = final - base
    veredicto = veredicto_de(final, total)
    # Restricciones de operabilidad (liquidez baja, precio sin actualizar): un activo que no se puede vender o no tiene precio reciente no puede ser
    # "recomendable" por buen Score que tenga; el Score no cambia, pero el veredicto baja a "con reservas" y la razón va primero.
    restricciones = restricciones or []
    if restricciones and veredicto == "recomendable":
        veredicto = "con_reservas"
    razones: list[str] = list(restricciones)
    if ajuste is not None:
        for r in (_razon_afinidad(perfil, plazo, ajuste), _razon_plazo(plazo, ajuste)):
            if r:
                razones.append(r)
    if len(razones) < 2:
        r = _razon_factor(perfil, factores)
        if r:
            razones.append(r)
    if not razones:
        razones.append(f"Su Score de {round(final)} sale de cómo se comportan sus cuatro factores para un perfil {perfil} de {_HORIZONTE_CORTO[plazo]}.")
    return LecturaPerfil(veredicto=veredicto, titulo=_TITULO[veredicto], razones=razones[:4], scoreBase=round(base), ajuste=round(total), retornoEsperado=retorno)


def armar_retorno_esperado(
    tir: float | None,
    tir_nominal_estimada: float | None,
    subtipo: str | None,
    duration: float | None,
    anios: float,
    moneda: Moneda,
    plazo: PlazoInversion,
) -> RetornoEsperado | None:
    """Retorno a tu horizonte = (1 + TIR)^años − 1, con los años hasta el horizonte o hasta el vencimiento si es antes. Es aritmética con la TIR de HOY
    (supone que el emisor cumple y que la tasa no cambia), no una predicción: el modelo de oportunidades mostró que predecir el retorno es otra cosa.
    BONCER cotiza en tasa real: se usa la TIR real más la inflación esperada del REM cuando está disponible. None sin TIR, o si vence en días."""
    tasa = tir_nominal_estimada if (subtipo == "BONCER" and tir_nominal_estimada is not None) else tir
    if tasa is None or anios < 0.03 or tasa <= -99:
        return None
    horizonte = HORIZONTE_REPRESENTATIVO_ANIOS[plazo]
    hasta_vencimiento = anios <= horizonte
    años_de_tenencia = anios if hasta_vencimiento else horizonte
    retorno = ((1 + tasa / 100) ** años_de_tenencia - 1) * 100
    caida = round(duration, 1) if (not hasta_vencimiento and duration is not None and duration > 0) else None
    en_moneda = "en dólares" if moneda == "USD" else "en pesos"
    base = "(TIR real más inflación esperada)" if (subtipo == "BONCER" and tir_nominal_estimada is not None) else "(TIR de hoy)"
    if hasta_vencimiento:
        texto = f"Si lo mantenés hasta el vencimiento (en {_duracion(anios)}) y el emisor cumple, rinde ≈ {retorno:+.1f}% {en_moneda}, a {tasa:.1f}% anual {base}."
    else:
        texto = f"A tu horizonte de {_duracion(horizonte)}, con una tasa de {tasa:.1f}% anual {base} y si el emisor cumple, rinde ≈ {retorno:+.1f}% {en_moneda}."
        if caida is not None:
            texto += f" Si las tasas suben 1 punto, su precio cae ≈ {caida:.1f}%."
    texto += " Es una cuenta con la TIR de hoy, no una predicción."
    return RetornoEsperado(
        horizonteMeses=max(1, round(años_de_tenencia * 12)),
        hastaVencimiento=hasta_vencimiento,
        tirAnual=round(tasa, 2),
        retornoPct=round(retorno, 2),
        moneda=moneda,
        caidaPorPuntoDeTasa=caida,
        texto=texto,
    )


def restricciones_operativas(liquidez: str | None, precio_stale: bool, dias_sin_cotizar: int) -> list[str]:
    """Razones por las que el instrumento, aunque tenga buen Score, no se puede operar con confianza hoy."""
    r: list[str] = []
    if dias_sin_cotizar >= DIAS_SIN_COTIZAR_AVISO:
        r.append(f"No tiene cotización reciente (hace {dias_sin_cotizar} días): el precio que ves puede no ser el de hoy.")
    elif precio_stale:
        r.append("Su precio no se actualizó hoy (se repite el último operado): puede no reflejar el mercado.")
    if liquidez == "Baja":
        r.append("Tiene liquidez baja (pocas operaciones): puede costar venderlo cuando quieras o a un precio razonable.")
    return r
