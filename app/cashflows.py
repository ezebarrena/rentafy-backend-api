"""Puerto de rentafy-frontend/src/data/generators.ts (buildFlujos): construye un cronograma
de flujos de fondos plausible según el tipo de instrumento, usado únicamente para poblar el
seed de desarrollo (ver seed.py). En producción, FLUJO_FONDO se completa desde la fuente
externa de compararfondos.com.ar (ver chapter04.tex, "Modelo de datos y fuentes de información")."""

from datetime import date, timedelta

HOY = date(2026, 8, 25)


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    # Recorta el día si el mes destino tiene menos días (ej. 31 -> 28/29/30).
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def build_flujos(tipo: str, vencimiento: date, tna: float | None, capitalizacion: bool) -> list[dict]:
    if capitalizacion:
        tasa = (tna if tna is not None else 30) / 100
        months_to_maturity = max(1, (vencimiento.year - 2026) * 12 + (vencimiento.month - 8))
        valor_final = 100 * (1 + (tasa * months_to_maturity) / 12)
        return [{"fecha": vencimiento, "tipo": "Amortización", "importe": round(valor_final, 2)}]

    cupon_semestral = round((tna if tna is not None else 8) / 2, 2)
    pagos: list[date] = []
    cursor = vencimiento
    while cursor > HOY:
        pagos.insert(0, cursor)
        cursor = _add_months(cursor, -6)

    flujos = []
    for idx, fecha in enumerate(pagos):
        es_ultimo = idx == len(pagos) - 1
        flujos.append(
            {
                "fecha": fecha,
                "tipo": "Cupón y amortización" if es_ultimo else "Cupón",
                "importe": round(cupon_semestral + 100, 2) if es_ultimo else cupon_semestral,
            }
        )
    return flujos
