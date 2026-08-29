"""Prueba de integración con la API pública de compararfondos.com.ar (RF-07/RF-08).

GET https://compararfondos.com.ar/api/bonos devuelve ~275 bonos con ticker, tipo, moneda,
precio, TIR, duration, paridad, ley, vencimiento y el cronograma completo de flujos futuros —
exactamente la fuente que chapter04.tex describe como "fuente central" para estas variables.

Este módulo mapea esa respuesta a nuestro modelo (INSTRUMENTO/COTIZACION/FLUJO_FONDO) y deja
documentados, en el resultado de `importar()`, los campos que la fuente NO expone de forma
directa y que este mapeo resuelve mediante una heurística (ver `_derivar_emisor`,
`_derivar_riesgo`, `_derivar_liquidez`): no reemplazan al Servicio de IA ni a información real
de riesgo crediticio, son solo un valor de exhibición hasta contar con esas fuentes.

No se persiste SCORING para los instrumentos importados por esta vía: los 4 factores del Score
(rendimiento, riesgo, liquidez, estabilidad) son responsabilidad del Servicio de IA, que no se
implementa en este backend (ver README, "Alcance de este backend"). El resto de la plataforma
ya tolera esta ausencia (ver serializers.py, RNF-29).
"""

from datetime import date, datetime

import requests
from sqlalchemy.orm import Session

from .models import Cotizacion, FlujoFondo, FuenteDatos, Instrumento

BONOS_URL = "https://compararfondos.com.ar/api/bonos"

# tipo (compararfondos) -> (tipo interno, subtipo)
_TIPO_MAP: dict[str, tuple[str, str | None]] = {
    "LECAP": ("LECAP", None),
    "BONCAP": ("BONCAP", None),
    "ON": ("ON", None),
    "CER": ("BONO", "BONCER"),
    "DUAL": ("BONO", "DUAL"),
    "TAMAR": ("BONO", "TAMAR"),
    "DL": ("BONO", "Dólar Linked"),
    "FIJA": ("BONO", None),  # subtipo se resuelve por moneda, ver _mapear_tipo
    "Soberano": ("BONO", None),
    "International": ("BONO", None),
    "Treasury": ("BONO", None),
    "Provincial": ("BONO", None),
}

_LEY_MAP = {"Argentina": "Ley Argentina", "NY": "Ley Nueva York", "Nueva York": "Ley Nueva York"}


def _mapear_tipo(bond: dict) -> tuple[str, str | None]:
    tipo_raw = bond["tipo"]
    tipo, subtipo = _TIPO_MAP.get(tipo_raw, ("BONO", tipo_raw))
    if tipo == "BONO" and subtipo is None and tipo_raw in ("FIJA", "Soberano", "International", "Treasury"):
        subtipo = "Bono USD" if bond["moneda"] == "USD" else "Bono ARS"
    return tipo, subtipo


def _derivar_emisor(bond: dict) -> str:
    """La fuente no expone un campo de emisor estructurado; se aproxima por tipo/país."""
    tipo, moneda = bond["tipo"], bond["moneda"]
    if tipo in ("Soberano", "Treasury", "FIJA", "CER", "DUAL", "TAMAR", "DL", "LECAP", "BONCAP"):
        return "República Argentina" if moneda == "USD" else "Tesoro Nacional"
    if tipo == "International":
        pais = bond.get("pais")
        return f"Gobierno de {pais}" if pais else "Emisor soberano extranjero"
    if tipo == "Provincial":
        return "Gobierno provincial"
    # ON: no hay campo emisor; se aproxima con el primer token del nombre (ej. "CGC 2026 Zero" -> "CGC").
    return bond["nombre"].split()[0]


def _derivar_riesgo(duration: float | None, plazo_residual: float | None) -> str:
    """Heurística de exhibición por plazo. No reemplaza el modelo de riesgo de la tesis
    (percentil de duration dentro del grupo de pares, ver chapter04.tex)."""
    referencia = duration if duration is not None else plazo_residual
    if referencia is None:
        return "Medio"
    if referencia < 1:
        return "Bajo"
    if referencia < 3:
        return "Medio"
    return "Alto"


def _derivar_liquidez(operaciones: int) -> str:
    """Heurística de exhibición por cantidad de operaciones. El factor Liquidez real de la
    tesis usa un percentil dentro del grupo de pares, no un umbral fijo."""
    if operaciones >= 300:
        return "Alta"
    if operaciones >= 50:
        return "Media"
    return "Baja"


def _plazo_residual(vencimiento: date, hoy: date) -> float:
    return round((vencimiento - hoy).days / 365, 2)


def _parse_fecha(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def importar(db: Session) -> dict:
    response = requests.get(BONOS_URL, timeout=10)
    response.raise_for_status()
    payload = response.json()
    bonds = payload["bonds"]
    hoy = date.today()

    procesados = 0
    con_tir = 0
    con_par_legislacion = 0
    tipos_no_mapeados: set[str] = set()

    for bond in bonds:
        ticker = bond["ticker"]
        tipo, subtipo = _mapear_tipo(bond)
        if bond["tipo"] not in _TIPO_MAP:
            tipos_no_mapeados.add(bond["tipo"])

        vencimiento = _parse_fecha(bond["vencimiento"])
        duration = bond.get("duration")
        plazo_residual = _plazo_residual(vencimiento, hoy) if duration is None else None

        instrumento = db.get(Instrumento, ticker)
        if instrumento is None:
            instrumento = Instrumento(ticker=ticker)
            db.add(instrumento)

        instrumento.nombre = bond["nombre"]
        instrumento.tipo = tipo
        instrumento.subtipo = subtipo
        instrumento.moneda = bond["moneda"]
        instrumento.emisor = _derivar_emisor(bond)
        instrumento.legislacion = _LEY_MAP.get(bond.get("ley"))
        instrumento.par_legislacion = bond.get("parTicker") or bond.get("par")
        instrumento.vencimiento = vencimiento
        instrumento.riesgo = _derivar_riesgo(duration, plazo_residual)
        instrumento.liquidez = _derivar_liquidez(bond.get("operaciones") or 0)
        instrumento.resumen = instrumento.resumen or ""

        # Reemplaza la cotización del día (idempotente si se corre más de una vez en la jornada).
        db.query(Cotizacion).filter(
            Cotizacion.instrumento_ticker == ticker, Cotizacion.fecha == hoy
        ).delete()
        db.add(
            Cotizacion(
                instrumento_ticker=ticker,
                fecha=hoy,
                precio=bond["precio"],
                variacion=bond.get("pctChange", 0.0),
                volumen=bond.get("volume") or 0,
                operaciones=bond.get("operaciones") or 0,
                tir=bond.get("tir"),
                tir_sufijo=None,
                tna=bond.get("tna"),
                duration=duration,
                plazo_residual=plazo_residual,
                paridad=bond.get("paridad"),
                precio_stale=bool(bond.get("precioStale", False)),
            )
        )

        # Reemplaza el cronograma de flujos completo (la fuente ya lo entrega proyectado a futuro).
        db.query(FlujoFondo).filter(FlujoFondo.instrumento_ticker == ticker).delete()
        flujos = bond.get("flujos") or []
        for flujo in flujos:
            es_ultimo = flujo is flujos[-1]
            db.add(
                FlujoFondo(
                    instrumento_ticker=ticker,
                    fecha=_parse_fecha(flujo["fecha"]),
                    tipo="Cupón y amortización" if es_ultimo else "Cupón",
                    importe=flujo["monto"],
                )
            )

        procesados += 1
        if bond.get("tir") is not None:
            con_tir += 1
        if instrumento.par_legislacion:
            con_par_legislacion += 1

    fuente = db.query(FuenteDatos).filter(FuenteDatos.nombre == "compararfondos.com.ar").first()
    if fuente is None:
        fuente = FuenteDatos(nombre="compararfondos.com.ar")
        db.add(fuente)
    fuente.ultima_actualizacion = datetime.utcnow()

    db.commit()

    return {
        "procesados": procesados,
        "totalEnFuente": payload.get("count", len(bonds)),
        "conTir": con_tir,
        "conParLegislacion": con_par_legislacion,
        "tiposNoMapeadosExplicitamente": sorted(tipos_no_mapeados),
    }
