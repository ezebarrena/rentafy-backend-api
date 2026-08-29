# Rentafy Backend API

Backend principal de Rentafy, desarrollado en FastAPI. Implementa el diseño descripto en el
capítulo 4 de la tesis (`chapters/chapter04.tex`, secciones "Arquitectura del producto" y
"Modelo de datos") y refleja el modelo de datos ya usado por el frontend
(`rentafy-frontend/src/data/types.ts`).

## Alcance de este backend

Esta es la primera versión del backend, pensada para desarrollo local. Se aparta del diseño
de producción de la tesis en los siguientes puntos, documentados también en el plan de la
sesión que la originó:

- **Un solo servicio**, no un backend + Servicio de IA separados. La tabla `scoring` se puebla
  con los mismos 4 factores (rendimiento, riesgo, liquidez, estabilidad) que hoy están
  hardcodeados en el frontend (`instruments.ts`), simulando el output que en producción
  generaría el Servicio de IA descripto en la tesis (regresión OLS, percentiles por grupo de
  pares, etc. — no implementado). Este backend solo aplica la ponderación por perfil sobre
  esos factores (`app/scoring.py`), tal como especifica la tesis que hace el backend.
- **SQLite en vez de PostgreSQL.** El acceso a datos es 100% vía SQLAlchemy, así que cambiar
  el motor es solo cuestión de `DATABASE_URL` (ver `.env.example`), sin tocar código.
- **JWT + password hashing propio**, no Google Identity Services / SSO real.
- No se implementa la entidad `EJECUCION_PIPELINE` (logging de corridas de ETL): no hay
  todavía un pipeline real que registrar.
- La API de compararfondos.com.ar (la fuente central de TIR/duration/flujos según la tesis) no
  se integró: no se encontró un endpoint público documentado durante esta sesión. Los datos de
  instrumentos se cargan desde un seed estático (`app/seed.py`) portado 1:1 del mock del
  frontend. `data912` y `ArgentinaDatos` sí están integrados en vivo (`app/routers/mercado.py`).

## Requisitos

- Python 3.11+
- pip

## Instalación

```bash
git clone <url-del-repositorio>
cd rentafy-backend-api
python -m venv rentaenv
source rentaenv/bin/activate  # Windows: rentaenv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

## Ejecutar

```bash
uvicorn app.main:app --reload --port 8000
```

Al arrancar por primera vez se crean las tablas y se carga el seed de 19 instrumentos
automáticamente (`app/seed.py`). Documentación interactiva en `http://localhost:8000/docs`.

## Endpoints principales

| Método | Ruta | Descripción |
|---|---|---|
| POST | `/auth/registro` | Alta de usuario (RF-01) |
| POST | `/auth/login` | Login, devuelve JWT (RF-02) |
| GET | `/auth/me` | Datos del usuario autenticado |
| PUT | `/auth/me/perfil-inversor` | Cambiar perfil conservador/moderado/agresivo (RF-05) |
| GET | `/instrumentos` | Listado con filtro, búsqueda, orden y paginación (RF-13..19) |
| GET | `/instrumentos/{ticker}` | Detalle de un instrumento (RF-19..29) |
| GET | `/rankings` | Ranking por score, perfil y moneda (RF-30..36) |
| GET | `/score-rentafy/pesos` | Ponderación vigente por perfil |
| GET | `/watchlist` / `POST` / `DELETE` `/watchlist/{ticker}` | Lista de seguimiento (RF-50..52, requiere JWT) |
| GET | `/calendario` | Flujos de fondos agregados (RF-47/48) |
| GET | `/mercado/indicadores` | Indicadores del Dashboard (dólar CCL/MEP en vivo) |
| GET | `/mercado/bonos/{symbol}` | Cotización en vivo vía data912 |

## Próximos pasos (no incluidos en esta versión)

- Reconectar el frontend (hoy usa datos mock en `src/data/`) a estos endpoints.
- Integrar compararfondos.com.ar en cuanto se confirme un endpoint público estable.
- Separar el Servicio de IA como su propio proceso/despliegue cuando se implemente el cálculo
  real de los 4 factores (regresión OLS + percentiles), en lugar del seed estático actual.
