"""Resumen corto y resumen completo de un instrumento.

El servicio de IA guarda en Instrumento.resumen la oración de factores y, a continuación, el contexto del instrumento (qué es, cuándo vence, qué rinde), separados por SEPARADOR_RESUMEN
(ver rentafy-servicioIA/app/perfiles/contexto.py). No hay migraciones para agregar una columna, así que se guardan juntos y acá se parten:

  - resumen_corto: solo la oración de factores. Va a los listados, a la tarjeta "La oportunidad de hoy", al comparador y a la vista previa: espacios chicos.
  - resumen_completo: la oración de factores y el contexto, en un solo párrafo. Va al Análisis Rentafy de la ficha del instrumento.

Los textos que ya estaban guardados antes de que existiera la marca traen el contexto pegado con un espacio; se reconocen porque el contexto SIEMPRE empieza con "Es un ..." o "Es una ..."
(las oraciones de factores nunca lo hacen), y se parten en ese punto. Un texto sin contexto (solo factores, o el de un instrumento sin datos) es igual de corto que de completo.
"""

import re

SEPARADOR_RESUMEN = "\n\n"
_CONTEXTO_SIN_MARCA = re.compile(r"(?<=[.!?])\s+(?=Es (?:un|una) )")


def partir_resumen(texto: str | None) -> tuple[str, str]:
    """(corto, contexto) de un Instrumento.resumen; contexto es "" si el texto no lo trae."""
    if not texto:
        return "", ""
    if SEPARADOR_RESUMEN in texto:
        corto, contexto = texto.split(SEPARADOR_RESUMEN, 1)
        return corto.strip(), contexto.strip()
    partes = _CONTEXTO_SIN_MARCA.split(texto, maxsplit=1)
    if len(partes) == 2:
        return partes[0].strip(), partes[1].strip()
    return texto.strip(), ""


def resumen_corto(texto: str | None) -> str:
    return partir_resumen(texto)[0]


def resumen_completo(texto: str | None) -> str:
    corto, contexto = partir_resumen(texto)
    return f"{corto} {contexto}".strip() if contexto else corto
