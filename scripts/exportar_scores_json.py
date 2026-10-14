"""Exporta a JSON los Scores de hoy de todos los instrumentos activos en las 9 combinaciones perfil × plazo, con su desglose (Score base, afinidad, descalce, Score final) y el veredicto
tal como lo ve el usuario (incluidas las restricciones operativas). SOLO LECTURA. Lo usa rentafy-servicioIA/scripts/armar_excel_scores.py para armar el documento de prueba.

    python -m scripts.exportar_scores_json salida.json
"""

import json
import sys
from datetime import timedelta

from sqlalchemy import func

from app import scoring
from app.database import SessionFinanciera
from app.lectura_perfil import restricciones_operativas, veredicto_de
from app.models_financiera import Cotizacion, Instrumento, Scoring
from app.scoring import aplicar_ajuste, ajuste_horizonte, clase_instrumento, score_base

PERFILES = ("conservador", "moderado", "agresivo")
PLAZOS = ("corto", "mediano", "largo")


def main(ruta: str) -> None:
    db = SessionFinanciera()
    try:
        fecha = db.query(func.max(Scoring.fecha_calculo)).filter(Scoring.modelo_id != scoring.PLACEHOLDER_MODELO_ID).scalar()
        filas = (
            db.query(Scoring, Instrumento)
            .join(Instrumento, Instrumento.ticker == Scoring.instrumento_ticker)
            .filter(Scoring.fecha_calculo == fecha, Instrumento.activo.is_(True), Scoring.modelo_id != scoring.PLACEHOLDER_MODELO_ID)
            .all()
        )
        ultima: dict[str, Cotizacion] = {}
        for c in db.query(Cotizacion).filter(Cotizacion.fecha >= fecha - timedelta(days=30)).order_by(Cotizacion.fecha):
            ultima[c.instrumento_ticker] = c
        salida = []
        for sc, inst in filas:
            cot = ultima.get(inst.ticker)
            dias = max(0, (fecha - cot.fecha).days) if cot else 99
            rest = restricciones_operativas(inst.liquidez, bool(cot.precio_stale) if cot else False, dias)
            clase = clase_instrumento(inst.tipo, inst.subtipo)
            anios = (inst.vencimiento - fecha).days / 365.0
            scores = {}
            for perfil in PERFILES:
                for plazo in PLAZOS:
                    base = score_base(sc.rendimiento, sc.riesgo, sc.liquidez, sc.estabilidad, perfil, plazo, None)
                    if base is None:
                        scores[f"{perfil}|{plazo}"] = None
                        continue
                    aj = ajuste_horizonte(clase, anios, perfil, plazo, sc.riesgo)
                    final = aplicar_ajuste(base, aj)
                    ver = veredicto_de(final, final - base)
                    scores[f"{perfil}|{plazo}"] = {
                        "base": base,
                        "afinidad": aj.afinidad,
                        "descalce": aj.descalce,
                        "atenuacion_calidad": aj.atenuacion_calidad,
                        "final": final,
                        "veredicto": ver,
                        "veredicto_usuario": "con_reservas" if (rest and ver == "recomendable") else ver,
                    }
            salida.append(
                {
                    "ticker": inst.ticker, "nombre": inst.nombre, "clase": clase, "tipo": inst.tipo, "moneda": inst.moneda, "emisor": inst.emisor,
                    "vencimiento": inst.vencimiento.isoformat(), "anios": anios,
                    "riesgo": sc.riesgo, "estabilidad": sc.estabilidad, "liquidez_factor": sc.liquidez, "rendimiento": sc.rendimiento,
                    "liquidez": inst.liquidez, "tir": cot.tir if cot else None, "precio_stale": bool(cot.precio_stale) if cot else None,
                    "ultima_cotizacion": cot.fecha.isoformat() if cot else None, "restricciones": rest, "scores": scores,
                }
            )
    finally:
        db.close()
    reglas = {"afinidad": scoring.AFINIDAD, "neutras": {f"{p}|{pl}": list(c) for (p, pl), c in scoring.SIN_INCLINACION_A_PROPOSITO.items()}, "descalce": {k: {l: list(v) for l, v in d.items()} for k, d in scoring.DESCALCE.items()},
              "banda_anios": {k: list(v) for k, v in scoring.BANDA_ANIOS.items()}, "descuento_largo": [scoring.DESCUENTO_LARGO_PUNTOS_POR_ANIO_QUE_FALTA, scoring.DESCUENTO_LARGO_TOPE],
              "mult_descalce_despues": scoring.MULT_DESCALCE_DESPUES, "min_anios_afinidad": scoring.MIN_ANIOS_AFINIDAD, "max_resta": scoring.MAX_RESTA_AJUSTE, "fraccion_piso": scoring.FRACCION_PISO,
              "atenuacion_calidad": {"desde": scoring.RIESGO_ATENUA_DESDE, "total": scoring.RIESGO_ATENUA_TOTAL, "minimo": scoring.FACTOR_MINIMO_CALIDAD, "clases": list(scoring.CLASES_CON_RIESGO_DE_EMISOR)}}
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump({"fecha": fecha.isoformat(), "instrumentos": salida, "reglas": reglas}, f, ensure_ascii=False)
    print(f"{len(salida)} instrumentos exportados a {ruta} (Scoring del {fecha})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "scores.json")
