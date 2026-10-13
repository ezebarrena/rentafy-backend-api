"""Lectura de un instrumento para el perfil y el horizonte del usuario: veredicto en tres niveles y una o dos razones.

El resumen de Instrumento.resumen (ver rentafy-servicioIA/app/perfiles/) se genera una vez por activo y no sabe nada de quién lo mira. Esta lectura se arma al
pedirla, con el MISMO cálculo que el Score (scoring.py), así que lo que dice el texto es exactamente lo que movió el número. Todo el texto es fijo
(plantillas), sin generación libre, igual criterio que el resumen: lo único que decide qué se cuenta son los números.
"""

from .scoring import AjusteHorizonte, HORIZONTE_ANIOS, PESOS_PERFIL, aplicar_ajuste
from .schemas import LecturaPerfil, PerfilInversor, PlazoInversion

UMBRAL_RECOMENDABLE = 70  # Score final desde el cual puede ser "recomendable" (si el ajuste no lo castigó mucho)
UMBRAL_NO_RECOMENDABLE = 40
AJUSTE_FUERTE = 8  # puntos de ajuste negativo a partir de los cuales no se lo puede llamar "recomendable"
AJUSTE_MUY_FUERTE = 15

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
    return f"Es menos adecuado para tu perfil {perfil} porque {motivo}."


def _razon_plazo(plazo: PlazoInversion, aj: AjusteHorizonte) -> str | None:
    horizonte = HORIZONTE_ANIOS[plazo]
    cuando = _duracion(aj.anios)
    if aj.descalce >= 3:
        if aj.anios > horizonte:
            if plazo == "corto":
                return (f"Vence en {cuando}, mucho después de tu horizonte de {_HORIZONTE_CORTO[plazo]}: para recuperar el capital tendrías que venderlo antes "
                        "a precio de mercado, y su precio puede moverse en contra.")
            return (f"Vence en {cuando}, bastante después de tu horizonte de {_HORIZONTE_CORTO[plazo]}: antes de cobrarlo tendrías que venderlo "
                    "a precio de mercado, y su precio puede moverse en contra.")
        return (f"Vence en {cuando}, antes de tu horizonte de {_HORIZONTE_CORTO[plazo]}: al vencer vas a tener que reinvertir sin saber a qué tasa.")
    if plazo != "largo" and aj.anios > 0 and 0.5 * horizonte <= aj.anios <= 1.5 * horizonte and aj.descalce < 1.5:
        return f"Su vencimiento ({cuando}) calza bien con tu horizonte de {_HORIZONTE_CORTO[plazo]}."
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
) -> LecturaPerfil:
    final = aplicar_ajuste(base, ajuste)
    total = final - base
    veredicto = veredicto_de(final, total)
    razones: list[str] = []
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
    return LecturaPerfil(veredicto=veredicto, titulo=_TITULO[veredicto], razones=razones[:3], scoreBase=round(base), ajuste=round(total))
