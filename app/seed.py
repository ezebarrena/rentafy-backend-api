"""Carga inicial de datos de desarrollo.

Puerto 1:1 de rentafy-frontend/src/data/instruments.ts: mismos 19 tickers, mismas variables
de mercado y mismos factores de score, para que cuando el frontend migre de datos mock a este
backend el resultado visual no cambie. En producción estos datos provendrían de las APIs
externas descriptas en chapter04.tex (compararfondos.com.ar, data912, ArgentinaDatos), no de
un seed estático.
"""

from datetime import date

from sqlalchemy.orm import Session

from .cashflows import build_flujos
from .models import Cotizacion, FlujoFondo, FuenteDatos, Instrumento, Modelo, PesoPerfil, Scoring
from .scoring import PESOS_PERFIL

MODELO_ID = "v1.4.0"
FECHA_CALCULO = date(2026, 8, 25)

# Cada tupla: (datos del instrumento, factores [rendimiento, riesgo, liquidez, estabilidad], capitalizacion)
SEEDS: list[tuple[dict, list[float | None], bool]] = [
    (
        dict(ticker="S16S6", nombre="Letra Capitalizable Set. 2026", tipo="LECAP", subtipo=None, moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2026, 9, 16),
             precio=105.2, variacion=0.31, volumen=18_400_000, operaciones=612, tir=38.5, tir_sufijo=None,
             tna=34.8, duration=0.06, plazo_residual=None, paridad=None, riesgo="Bajo", liquidez="Alta",
             resumen="Vencimiento muy próximo y liquidez alta. Ideal para parking de corto plazo con tasa fija conocida de antemano."),
        [88, 96, 94, 92], True,
    ),
    (
        dict(ticker="S31O6", nombre="Letra Capitalizable Oct. 2026", tipo="LECAP", subtipo=None, moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2026, 10, 31),
             precio=108.4, variacion=0.18, volumen=21_900_000, operaciones=704, tir=36.9, tir_sufijo=None,
             tna=33.6, duration=0.18, plazo_residual=None, paridad=None, riesgo="Bajo", liquidez="Alta",
             resumen="Buen equilibrio entre rendimiento y plazo corto. Una de las opciones más operadas del segmento LECAP."),
        [90, 94, 96, 90], True,
    ),
    (
        dict(ticker="S30D6", nombre="Letra Capitalizable Dic. 2026", tipo="LECAP", subtipo=None, moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2026, 12, 30),
             precio=112.1, variacion=-0.12, volumen=9_800_000, operaciones=341, tir=35.2, tir_sufijo=None,
             tna=32.1, duration=0.34, plazo_residual=None, paridad=None, riesgo="Bajo", liquidez="Media",
             resumen="Rendimiento algo menor que sus pares de plazo más corto, con liquidez algo más acotada."),
        [80, 90, 78, 88], True,
    ),
    (
        dict(ticker="T15E7", nombre="BONCAP Ene. 2027", tipo="BONCAP", subtipo=None, moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2027, 1, 15),
             precio=118.6, variacion=1.2, volumen=24_500_000, operaciones=588, tir=39.9, tir_sufijo=None,
             tna=35.0, duration=0.42, plazo_residual=None, paridad=None, riesgo="Bajo", liquidez="Alta",
             resumen="Muy buena relación riesgo/rendimiento. Su corta duration lo hace poco sensible a cambios de tasas."),
        [92, 95, 95, 90], True,
    ),
    (
        dict(ticker="T30J8", nombre="BONCAP Jun. 2028", tipo="BONCAP", subtipo=None, moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2028, 6, 30),
             precio=145.3, variacion=0.42, volumen=6_100_000, operaciones=187, tir=33.4, tir_sufijo=None,
             tna=28.7, duration=1.75, plazo_residual=None, paridad=None, riesgo="Medio", liquidez="Media",
             resumen="Mayor plazo dentro del segmento BONCAP, con más sensibilidad a tasas que sus pares más cortos."),
        [70, 68, 70, 74], True,
    ),
    (
        dict(ticker="TZXD6", nombre="Boncer Dic. 2026", tipo="BONO", subtipo="BONCER", moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2026, 12, 15),
             precio=104.8, variacion=0.24, volumen=5_300_000, operaciones=156, tir=8.4, tir_sufijo="+ CER",
             tna=None, duration=0.3, plazo_residual=None, paridad=None, riesgo="Bajo", liquidez="Media",
             resumen="Cobertura frente a inflación con vencimiento próximo. Riesgo de tasa acotado por su corta duration."),
        [78, 88, 74, 90], False,
    ),
    (
        dict(ticker="TX28", nombre="Boncer 2028", tipo="BONO", subtipo="BONCER", moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2028, 3, 30),
             precio=109.5, variacion=-0.08, volumen=4_200_000, operaciones=121, tir=9.1, tir_sufijo="+ CER",
             tna=None, duration=1.4, plazo_residual=None, paridad=None, riesgo="Medio", liquidez="Media",
             resumen="Cobertura inflacionaria de mediano plazo, con mayor sensibilidad a tasas que Boncer más cortos."),
        [74, 70, 68, 78], False,
    ),
    (
        dict(ticker="TY30", nombre="Bonte 2030", tipo="BONO", subtipo="Bono ARS", moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2030, 6, 17),
             precio=96.8, variacion=0.55, volumen=11_200_000, operaciones=298, tir=32.4, tir_sufijo=None,
             tna=None, duration=2.9, plazo_residual=None, paridad=None, riesgo="Medio", liquidez="Alta",
             resumen="Tasa fija a mediano plazo en pesos. Mayor sensibilidad a tasas por su duration más extendida."),
        [86, 62, 84, 70], False,
    ),
    (
        dict(ticker="AL30", nombre="Bonar 2030", tipo="BONO", subtipo="Bono USD", moneda="USD",
             emisor="República Argentina", legislacion="Ley Argentina", par_legislacion="GD30",
             vencimiento=date(2030, 7, 9), precio=63.8, variacion=-0.3, volumen=48_200_000, operaciones=1_450,
             tir=12.6, tir_sufijo=None, tna=None, duration=3.1, plazo_residual=None, paridad=None, riesgo="Medio",
             liquidez="Alta",
             resumen="Buena liquidez dentro de los soberanos en dólares. Rinde por encima de su par bajo ley extranjera."),
        [78, 66, 92, 74], False,
    ),
    (
        dict(ticker="GD30", nombre="Global 2030", tipo="BONO", subtipo="Bono USD", moneda="USD",
             emisor="República Argentina", legislacion="Ley Nueva York", par_legislacion="AL30",
             vencimiento=date(2030, 7, 9), precio=66.1, variacion=-0.18, volumen=36_700_000, operaciones=1_120,
             tir=11.3, tir_sufijo=None, tna=None, duration=3.1, plazo_residual=None, paridad=None, riesgo="Bajo",
             liquidez="Alta",
             resumen="Mismo flujo de fondos que AL30 bajo ley extranjera, lo que reduce el riesgo legal percibido por el mercado."),
        [70, 82, 90, 74], False,
    ),
    (
        dict(ticker="AL35", nombre="Bonar 2035", tipo="BONO", subtipo="Bono USD", moneda="USD",
             emisor="República Argentina", legislacion="Ley Argentina", par_legislacion="GD35",
             vencimiento=date(2035, 1, 9), precio=58.4, variacion=0.62, volumen=19_800_000, operaciones=640,
             tir=12.9, tir_sufijo=None, tna=None, duration=5.4, plazo_residual=None, paridad=None, riesgo="Alto",
             liquidez="Media",
             resumen="Mayor plazo y duration dentro de los soberanos en dólares, con mayor sensibilidad a tasas."),
        [80, 42, 66, 58], False,
    ),
    (
        dict(ticker="GD35", nombre="Global 2035", tipo="BONO", subtipo="Bono USD", moneda="USD",
             emisor="República Argentina", legislacion="Ley Nueva York", par_legislacion="AL35",
             vencimiento=date(2035, 1, 9), precio=61.0, variacion=0.4, volumen=14_500_000, operaciones=512,
             tir=11.8, tir_sufijo=None, tna=None, duration=5.4, plazo_residual=None, paridad=None, riesgo="Medio",
             liquidez="Media",
             resumen="Par bajo ley extranjera de AL35, con menor spread de riesgo legal exigido por el mercado."),
        [72, 58, 64, 58], False,
    ),
    (
        dict(ticker="YPFDAR27", nombre="YPF S.A. 2027", tipo="ON", subtipo=None, moneda="USD",
             emisor="YPF S.A.", legislacion=None, par_legislacion=None, vencimiento=date(2027, 9, 23),
             precio=101.2, variacion=0.15, volumen=7_400_000, operaciones=210, tir=9.3, tir_sufijo=None,
             tna=None, duration=1.0, plazo_residual=None, paridad=None, riesgo="Medio", liquidez="Media",
             resumen="Obligación negociable corporativa con buena liquidez relativa dentro de su segmento."),
        [80, 68, 72, 84], False,
    ),
    (
        dict(ticker="PAMPYO", nombre="Pampa Energía 2029", tipo="ON", subtipo=None, moneda="USD",
             emisor="Pampa Energía S.A.", legislacion=None, par_legislacion=None, vencimiento=date(2029, 4, 10),
             precio=98.7, variacion=-0.22, volumen=3_600_000, operaciones=98, tir=8.6, tir_sufijo=None,
             tna=None, duration=2.4, plazo_residual=None, paridad=None, riesgo="Medio", liquidez="Baja",
             resumen="Emisor con buena calidad crediticia relativa, aunque con menor liquidez que otras ON del segmento."),
        [68, 66, 42, 76], False,
    ),
    (
        dict(ticker="TLC1O", nombre="Telecom Argentina 2028", tipo="ON", subtipo=None, moneda="USD",
             emisor="Telecom Argentina S.A.", legislacion=None, par_legislacion=None, vencimiento=date(2028, 7, 18),
             precio=103.4, variacion=0.08, volumen=5_900_000, operaciones=174, tir=8.1, tir_sufijo=None,
             tna=None, duration=1.8, plazo_residual=None, paridad=None, riesgo="Bajo", liquidez="Media",
             resumen="Emisor de bajo riesgo relativo dentro del universo de obligaciones negociables cubierto."),
        [66, 84, 70, 82], False,
    ),
    (
        dict(ticker="CRESY31", nombre="Cresud 2031", tipo="ON", subtipo=None, moneda="USD",
             emisor="Cresud S.A.C.I.F. y A.", legislacion=None, par_legislacion=None, vencimiento=date(2031, 2, 5),
             precio=92.1, variacion=-0.6, volumen=1_900_000, operaciones=54, tir=10.4, tir_sufijo=None,
             tna=None, duration=3.6, plazo_residual=None, paridad=None, riesgo="Alto", liquidez="Baja",
             resumen="Mayor rendimiento relativo dentro de las ON cubiertas, con menor liquidez y mayor riesgo crediticio."),
        [84, 40, 34, 56], False,
    ),
    (
        dict(ticker="BDC24", nombre="Bono TAMAR 2027", tipo="BONO", subtipo="TAMAR", moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2027, 11, 20),
             precio=99.5, variacion=0.05, volumen=8_700_000, operaciones=233, tir=None, tir_sufijo=None,
             tna=33.0, duration=None, plazo_residual=1.24, paridad=None, riesgo="Medio", liquidez="Media",
             resumen="Tasa variable atada a referencias mayoristas del mercado local. Sin Rendimiento calculable por TIR nula."),
        [None, 74, 70, 68], False,
    ),
    (
        dict(ticker="TDA26", nombre="Bono Dual 2028", tipo="BONO", subtipo="DUAL", moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2028, 5, 15),
             precio=101.8, variacion=-0.1, volumen=6_400_000, operaciones=176, tir=None, tir_sufijo=None,
             tna=None, duration=None, plazo_residual=1.72, paridad=None, riesgo="Medio", liquidez="Media",
             resumen="Cobertura dual entre inflación y tipo de cambio, aplicando al vencimiento el ajuste más beneficioso."),
        [None, 70, 68, 72], False,
    ),
    (
        dict(ticker="TV27D", nombre="Bono Dólar Linked 2027", tipo="BONO", subtipo="Dólar Linked", moneda="ARS",
             emisor="Tesoro Nacional", legislacion=None, par_legislacion=None, vencimiento=date(2027, 6, 30),
             precio=97.3, variacion=0.28, volumen=4_100_000, operaciones=112, tir=None, tir_sufijo=None,
             tna=None, duration=None, plazo_residual=0.85, paridad=None, riesgo="Medio", liquidez="Media",
             resumen="Cobertura cambiaria emitida en pesos y ajustada por tipo de cambio oficial al vencimiento."),
        [None, 76, 66, 70], False,
    ),
]

FUENTES = ["compararfondos.com.ar", "Data912", "ArgentinaDatos"]


def seed_if_empty(db: Session) -> None:
    if db.query(Instrumento).first() is not None:
        return  # ya sembrado

    modelo = Modelo(id=MODELO_ID, publicado_en=FECHA_CALCULO, activo=True)
    db.add(modelo)

    for perfil, w in PESOS_PERFIL.items():
        db.add(
            PesoPerfil(
                modelo_id=MODELO_ID,
                perfil=perfil,
                w_rendimiento=w.rendimiento,
                w_riesgo=w.riesgo,
                w_liquidez=w.liquidez,
                w_estabilidad=w.estabilidad,
            )
        )

    for nombre in FUENTES:
        db.add(FuenteDatos(nombre=nombre))

    for datos, factores_raw, capitalizacion in SEEDS:
        instrumento = Instrumento(
            ticker=datos["ticker"],
            nombre=datos["nombre"],
            tipo=datos["tipo"],
            subtipo=datos["subtipo"],
            moneda=datos["moneda"],
            emisor=datos["emisor"],
            legislacion=datos["legislacion"],
            par_legislacion=datos["par_legislacion"],
            vencimiento=datos["vencimiento"],
            riesgo=datos["riesgo"],
            liquidez=datos["liquidez"],
            resumen=datos["resumen"],
        )
        db.add(instrumento)

        db.add(
            Cotizacion(
                instrumento_ticker=datos["ticker"],
                fecha=FECHA_CALCULO,
                precio=datos["precio"],
                variacion=datos["variacion"],
                volumen=datos["volumen"],
                operaciones=datos["operaciones"],
                tir=datos["tir"],
                tir_sufijo=datos["tir_sufijo"],
                tna=datos["tna"],
                duration=datos["duration"],
                plazo_residual=datos["plazo_residual"],
                paridad=datos["paridad"],
                precio_stale=False,
            )
        )

        rendimiento, riesgo, liquidez, estabilidad = factores_raw
        db.add(
            Scoring(
                instrumento_ticker=datos["ticker"],
                modelo_id=MODELO_ID,
                fecha_calculo=FECHA_CALCULO,
                rendimiento=rendimiento,
                riesgo=riesgo,
                liquidez=liquidez,
                estabilidad=estabilidad,
            )
        )

        for flujo in build_flujos(datos["tipo"], datos["vencimiento"], datos["tna"], capitalizacion):
            db.add(FlujoFondo(instrumento_ticker=datos["ticker"], **flujo))

    db.commit()
