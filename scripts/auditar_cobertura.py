"""Compara el catálogo de la base con una lista de referencia de tickers (scripts/referencia_cobertura.json) y dice qué falta y qué sobra.

Uso, desde la raíz de rentafy-backend-api y con el .env de la base que quieras revisar (local o producción). Solo lee:

    python -m scripts.auditar_cobertura
    python -m scripts.auditar_cobertura --referencia otra_lista.json

Un instrumento cuenta como cubierto si está activo y no venció. Los tickers terminados en C o D (dólar MEP y cable) se comparan sin esa letra, y los Bopreales de otras
plataformas (BPOA7) se traducen a los nuestros (BPA7). Lo que "sobra" es lo que tenemos y la lista no menciona, dentro de los grupos que la lista cubre.
"""

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from app.database import SessionFinanciera
from app.models_financiera import Instrumento

REFERENCIA = Path(__file__).with_name("referencia_cobertura.json")
# Grupo de la lista -> cómo reconocer ese grupo en la base (prefijos de ticker o subtipo).
PREFIJOS = {"Bonares": ("AO", "AN", "AL", "AE"), "Globales": ("GD",), "Bopreales": ("BP",)}
SUBTIPO = {"TAMAR": "TAMAR", "Bono ARS": "Bono ARS"}
TIPO = {"LECAP": "LECAP", "BONCAP": "BONCAP"}


def normalizar(ticker: str) -> str:
    t = ticker.upper().strip()
    t = re.sub(r"^BPO([A-D]\d)$", r"BP\1", t)  # BPOA7 -> BPA7
    return re.sub(r"(?<=\d)[CD]$", "", t)  # AL30C / AL30D -> AL30


def pertenece(inst: Instrumento, grupo: str) -> bool:
    if grupo in PREFIJOS:
        return inst.subtipo == "Bono USD" and inst.ticker.upper().startswith(PREFIJOS[grupo])
    if grupo in SUBTIPO:
        return inst.subtipo == SUBTIPO[grupo]
    return inst.tipo == TIPO.get(grupo)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--referencia", default=str(REFERENCIA), help="JSON {grupo: [tickers]}")
    args = ap.parse_args()
    ref = {k: v for k, v in json.loads(Path(args.referencia).read_text(encoding="utf-8")).items() if not k.startswith("_")}

    with SessionFinanciera() as db:
        vivos = [i for i in db.query(Instrumento).filter(Instrumento.activo.is_(True)).all() if i.vencimiento is None or i.vencimiento >= date.today()]
    por_ticker = {normalizar(i.ticker) for i in vivos}

    faltan = 0
    for grupo, tickers in ref.items():
        pedidos = {normalizar(t): t for t in tickers}
        faltantes = sorted(t for n, t in pedidos.items() if n not in por_ticker)
        propios = {normalizar(i.ticker) for i in vivos if pertenece(i, grupo)}
        sobran = sorted(propios - set(pedidos))
        faltan += len(faltantes)
        print(f"{grupo}: {len(pedidos) - len(faltantes)}/{len(pedidos)} cubiertos")
        if faltantes:
            print(f"   faltan en la base: {', '.join(faltantes)}")
        if sobran:
            print(f"   tenemos y la lista no menciona: {', '.join(sobran)}")
    return 1 if faltan else 0


if __name__ == "__main__":
    sys.exit(main())
