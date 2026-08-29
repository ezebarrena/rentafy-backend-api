# Rentafy Backend API

Backend principal de Rentafy, desarrollado en FastAPI. Implementa el diseño descripto en el
capítulo 4 de la tesis (`chapters/chapter04.tex`, secciones "Arquitectura del producto" y
"Modelo de datos") y refleja el modelo de datos ya usado por el frontend
(`rentafy-frontend/src/data/types.ts`), que ya está conectado a esta API (dejó de usar mocks).

## Dos bases de datos

Tal como especifica la tesis, la persistencia se divide en dos bases PostgreSQL separadas,
cada una con su propio motor/sesión de SQLAlchemy (`app/database.py`):

- **`rentafy_financiera`** (`app/models_financiera.py`): `instrumentos`, `cotizaciones`,
  `flujos_fondos`, `scoring`, `modelos`, `pesos_perfil`, `fuentes_datos`.
- **`rentafy_no_financiera`** (`app/models_no_financiera.py`): `usuarios`,
  `perfiles_inversor`, `favoritos`.

No hay ninguna foreign key ni `relationship()` de SQLAlchemy entre ambas bases — no es
posible entre dos conexiones/motores distintos. La única referencia cruzada
(`Favorito.instrumento_ticker` → `Instrumento.ticker`) se resuelve por valor en la capa de
aplicación (`app/routers/watchlist.py`: primero se consulta la lista de tickers favoritos en
la base no financiera, después el detalle de esos tickers en la financiera), exactamente
como la tesis resuelve la relación entre `PERFIL_INVERSOR` y `PESO_PERFIL`.

## Alcance de este backend

Esta es la primera versión del backend, pensada para desarrollo local. Se aparta del diseño
de producción de la tesis en los siguientes puntos:

- **Un solo servicio**, no un backend + Servicio de IA separados. La tabla `scoring` se puebla
  con los mismos 4 factores (rendimiento, riesgo, liquidez, estabilidad) que hoy están
  hardcodeados en el seed original (19 instrumentos), simulando el output que en producción
  generaría el Servicio de IA descripto en la tesis (regresión OLS, percentiles por grupo de
  pares, etc. — no implementado). Para los instrumentos importados en vivo desde
  compararfondos.com.ar que todavía no tienen Scoring, se genera uno aleatorio-pero-estático
  (sembrado por ticker, no cambia entre corridas) bajo un modelo separado
  (`placeholder-aleatorio`, marcado `activo=False`) solo para poder probar Rankings/Dashboard
  con el catálogo completo — nunca se confunde con el modelo real (`v1.4.0`).
- **JWT + password hashing propio**, no Google Identity Services / SSO real.
- No se implementa la entidad `EJECUCION_PIPELINE` (logging de corridas de ETL): no hay
  todavía un pipeline real que registrar.
- La importación de compararfondos.com.ar (`POST /mercado/importar/compararfondos`) se dispara
  manualmente; en producción sería un job periódico (RNF-05).

## Requisitos

- Python 3.11+
- PostgreSQL 14+ corriendo localmente, con las dos bases creadas (ver abajo)

## Instalación

```bash
git clone <url-del-repositorio>
cd rentafy-backend-api
python -m venv rentaenv
source rentaenv/bin/activate  # Windows: rentaenv\Scripts\activate
pip install -r requirements.txt
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
uvicorn app.main:app --reload --port 8000
```

Al arrancar por primera vez se crean las tablas en ambas bases y se carga el seed de 19
instrumentos automáticamente (`app/seed.py`) en la financiera. Documentación interactiva en
`http://localhost:8000/docs`.

## Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/auth/registro` | Alta de usuario (RF-01) |
| POST | `/auth/login` | Login, devuelve JWT (RF-02) |
| GET | `/auth/me` | Datos del usuario autenticado |
| PUT | `/auth/me/perfil-inversor` | Cambiar perfil conservador/moderado/agresivo (RF-05) |
| GET | `/instrumentos` | Listado con filtro, búsqueda, orden y paginación (RF-13..19) |
| GET | `/instrumentos/{ticker}` | Detalle de un instrumento (RF-19..29) |
| GET | `/instrumentos/emisores` | Emisores distintos del catálogo (para el filtro avanzado) |
| GET | `/rankings` | Ranking por score, perfil y moneda (RF-30..36) |
| GET | `/score-rentafy/pesos` | Ponderación vigente por perfil |
| GET | `/watchlist` / `POST` / `DELETE` `/watchlist/{ticker}` | Lista de seguimiento (RF-50..52, requiere JWT) |
| GET | `/calendario` | Flujos de fondos agregados (RF-47/48) |
| GET | `/mercado/indicadores` | Indicadores del Dashboard (dólar CCL/MEP en vivo) |
| GET | `/mercado/bonos/{symbol}` | Cotización en vivo vía data912 |
| POST | `/mercado/importar/compararfondos` | Importa/actualiza el catálogo desde compararfondos.com.ar (RF-07/08) |

## Próximos pasos (no incluidos en esta versión)

- Separar el Servicio de IA como su propio proceso/despliegue cuando se implemente el cálculo
  real de los 4 factores (regresión OLS + percentiles), en lugar del seed/placeholder actual.
- Automatizar `POST /mercado/importar/compararfondos` como job periódico en vez de disparo manual.
- Migrar la watchlist del frontend (hoy sigue en `localStorage`, no llama a estos endpoints).
