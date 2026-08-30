# Rentafy — Backend

Backend principal de Rentafy, en FastAPI. Implementa el diseño del capítulo 4 de la tesis
(`chapters/chapter04.tex`, secciones "Arquitectura del producto" y "Modelo de datos") y expone
la API que consume el frontend (`rentafy-frontend`). No calcula el Score — eso lo hace el
servicio separado `rentafy-servicioIA`, que escribe directo en la base financiera; este
backend solo lee el resultado y aplica la ponderación por perfil de inversor.

Es una de tres piezas independientes que comparten únicamente la base de datos:

| Proyecto | Rol |
|---|---|
| `rentafy-backend-api` (acá) | API para el frontend: auth, catálogo, watchlist, calendario, importación de mercado |
| `rentafy-servicioIA` | Calcula los 4 factores del Score (Rendimiento, Riesgo, Liquidez, Estabilidad) |
| `rentafy-frontend` | La aplicación web (React) |

## Dos bases de datos

Tal como especifica la tesis, la persistencia se divide en dos bases PostgreSQL separadas,
cada una con su propio motor/sesión de SQLAlchemy (`app/database.py`):

- **`rentafy_financiera`** (`app/models_financiera.py`): `instrumentos`, `cotizaciones`,
  `flujos_fondos`, `scoring`, `modelos`, `pesos_perfil`, `fuentes_datos`. También la escribe
  `rentafy-servicioIA` (Scoring/Modelo/PesoPerfil) y la lee `rentafy-servicioIA` (Instrumento/Cotizacion).
- **`rentafy_no_financiera`** (`app/models_no_financiera.py`): `usuarios`,
  `perfiles_inversor`, `favoritos`. Solo la usa este backend.

No hay ninguna foreign key ni `relationship()` de SQLAlchemy entre las dos bases — no es
posible entre dos conexiones/motores distintos. La única referencia cruzada
(`Favorito.instrumento_ticker` → `Instrumento.ticker`) se resuelve por valor en la capa de
aplicación (`app/routers/watchlist.py`: primero se consulta la lista de tickers favoritos en
la base no financiera, después el detalle de esos tickers en la financiera), igual que la
tesis resuelve la relación entre `PERFIL_INVERSOR` y `PESO_PERFIL`.

## Alcance de este backend

Se aparta del diseño de producción de la tesis en estos puntos:

- **JWT + password hashing propio** (`app/security.py`), no Google Identity Services / SSO real.
- No se implementa la entidad `EJECUCION_PIPELINE` (logging de corridas de ETL): no hay
  todavía un pipeline real que registrar.
- El catálogo se actualiza vía un job diario a las 18:00 ART (`app/scheduler.py`, después del
  cierre de la rueda), más un disparo manual (`POST /mercado/importar/compararfondos`) para
  correr una actualización ad-hoc sin esperar al horario.
- Instrumentos importados que todavía no tienen Scoring real (porque `rentafy-servicioIA`
  no corrió todavía, o porque el instrumento es nuevo) reciben un Scoring
  aleatorio-pero-estático de exhibición (sembrado por ticker, `app/ingest.py`), bajo un modelo
  separado (`placeholder-aleatorio`, `activo=False`) que nunca se confunde con un modelo real.

## Requisitos

- Python 3.11+
- PostgreSQL 14+ corriendo localmente, con las dos bases creadas (ver abajo)

## Instalación

**Importante:** creá el entorno virtual **fuera** de cualquier carpeta sincronizada a la nube
(iCloud Drive, Dropbox, etc.). Si el proyecto vive en una de esas carpetas (como en este repo),
un venv ahí adentro puede ser evictado por el sistema operativo y hacer que `uvicorn` tarde
minutos en arrancar la primera vez que se toca — no es un cuelgue real, es el SO
redescargando archivos del venv bajo demanda.

```bash
cd rentafy-backend-api
python3 -m venv ~/.rentafy-venvs/rentafy-backend-api
~/.rentafy-venvs/rentafy-backend-api/bin/pip install -r requirements.txt
cp .env.example .env
```

### Crear las dos bases de datos

Con el servidor de PostgreSQL corriendo (Postgres.app, Homebrew o Docker):

```bash
createdb rentafy_financiera
createdb rentafy_no_financiera
```

Editá `.env` con el usuario/contraseña/puerto de tu instalación
(`DATABASE_URL_FINANCIERA` / `DATABASE_URL_NO_FINANCIERA`).

## Ejecutar

```bash
~/.rentafy-venvs/rentafy-backend-api/bin/uvicorn app.main:app --reload --port 8000
```

Al arrancar por primera vez se crean las tablas en ambas bases y se carga un seed de
desarrollo (`app/seed.py`) en la financiera si está vacía. Documentación interactiva en
`http://localhost:8000/docs`.

Para tener datos reales (no solo el seed) hay que disparar al menos una vez la importación del
catálogo real:

```bash
curl -X POST http://localhost:8000/mercado/importar/compararfondos
```

Y para que esos instrumentos tengan un Score real (no el placeholder aleatorio), correr
`rentafy-servicioIA` (ver su propio README) — `POST /entrenamiento/ejecutar` seguido de
`POST /scoring/ejecutar`.

## Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/auth/registro` | Alta de usuario (RF-01) |
| POST | `/auth/login` | Login, devuelve JWT (RF-02) |
| GET | `/auth/me` | Datos del usuario autenticado |
| PUT | `/auth/me/perfil-inversor` | Cambiar perfil conservador/moderado/agresivo (RF-05) |
| GET | `/instrumentos` | Listado con filtro, búsqueda, orden y paginación (RF-13..19) |
| GET | `/instrumentos/{ticker}` | Detalle de un instrumento (RF-19..29) |
| GET | `/instrumentos/{ticker}/historico` | Serie de precios de cierre diarios (para el gráfico) |
| GET | `/instrumentos/emisores` | Emisores distintos del catálogo (filtro avanzado) |
| GET | `/instrumentos/opciones` | Catálogo completo sin paginar (selectores de Comparador/Calculadora) |
| GET | `/rankings` | Ranking por score, perfil y moneda (RF-30..36) |
| GET | `/score-rentafy/pesos` | Ponderación vigente por perfil |
| GET / POST / DELETE | `/watchlist`, `/watchlist/{ticker}` | Lista de seguimiento (RF-50..52, requiere JWT) |
| GET | `/calendario` | Flujos de fondos agregados (RF-47/48) |
| GET | `/mercado/indicadores` | Indicadores del Dashboard (dólar CCL/MEP, inflación, riesgo país) |
| GET | `/mercado/bonos/{symbol}` | Cotización en vivo vía data912 |
| POST | `/mercado/importar/compararfondos` | Importa/actualiza el catálogo desde compararfondos.com.ar (RF-07/08) |

## Próximos pasos (no incluidos en esta versión)

- Google Identity Services / SSO real en lugar de JWT propio.
- Entidad `EJECUCION_PIPELINE` (logging de corridas de ETL).
- Que este backend lea `PESO_PERFIL` de la base (ya la escribe `rentafy-servicioIA`) en lugar
  de la ponderación hardcodeada en `app/scoring.py` — hoy son los mismos valores, pero
  duplicados en dos lugares.
