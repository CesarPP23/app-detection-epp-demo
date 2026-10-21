# API

**Para quién es:** quien integre contra la API o depure el contrato entre `backend/` y
`frontend/`. **Última verificación:** 2026-10-11.

Fuente de verdad del contrato: `backend/epp_api/schemas.py` (modelos Pydantic) y los routers de
`backend/epp_api/routers/`. El OpenAPI se genera desde el código (`backend/scripts/dump_openapi.py`)
y el frontend consume tipos generados desde él — un cambio de contrato rompe el build en vez de
fallar en silencio en producción.

## 1. Autenticación

Flujo completo en `backend/epp_api/auth.py` y `backend/epp_api/routers/auth.py`:

1. La SPA carga `https://accounts.google.com/gsi/client` y muestra el botón de Google (Google
   Identity Services, sin client secret).
2. GIS entrega un **ID token** → `POST /api/auth/google {"credential": "..."}`.
3. El backend lo verifica con `google.oauth2.id_token.verify_oauth2_token` contra
   `GOOGLE_OAUTH_CLIENT_ID`, exige emisor `accounts.google.com` y `email_verified == true`.
4. Busca `(default)/dashboard_users/{email en minúsculas}` con `active == true`. Si no existe o
   está inactivo: `403 acceso_denegado` y se registra `login_denied` en `audit_log`.
5. Emite la cookie `epp_session`: payload `{email, role, name, picture}` firmado con
   `itsdangerous.URLSafeTimedSerializer(SESSION_SECRET, salt="epp-session")`,
   `HttpOnly; Secure; SameSite=Strict; Path=/`, vigencia `SESSION_MAX_HOURS` (12 h por defecto).
6. Cada request autenticada revalida el **rol** contra `dashboard_users` con una caché de 60 s
   (`repo.Repo.get_user`, `USER_TTL`): dar de baja a alguien tiene efecto en ≤ 60 s sin necesidad
   de que expire la cookie.
7. `POST /api/auth/logout` borra la cookie. `GET /api/auth/me` devuelve `{email, name, picture,
   role}` del usuario actual.

**`AUTH_MODE=dev`** (solo fuera de Cloud Run, es decir sin la variable `K_SERVICE`): omite Google
por completo y autentica siempre como `dev@localhost` con rol `admin`. Fuera de Cloud Run es
válido; **dentro** de Cloud Run, `Settings.from_env()` lanza `RuntimeError` si se intenta usar.

### CSRF

Sin CORS habilitado (API, relay y SPA viven en el mismo origen), así que la defensa es simple:
toda request con método `POST`, `PUT`, `PATCH` o `DELETE` debe incluir la cabecera
`X-Requested-With: epp-web` (`auth.check_csrf`); si falta, `403 csrf`. `SameSite=Strict` en la
cookie es la segunda capa: ni siquiera llega la cookie en una navegación cruzada.

### Roles

`viewer < operator < admin` (`auth.ROLE_ORDER`). Cada endpoint protegido usa
`Depends(auth.require_role("<mínimo>"))`:

| Rol | Puede |
|---|---|
| `viewer` | Ver dispositivos, resumen, eventos, capturas, abrir "En vivo" |
| `operator` | + pausar/reanudar la detección (`PUT /api/devices/{id}/desired-state`), iniciar/detener la captura para reentrenamiento |
| `admin` | + administrar usuarios, ver la auditoría, descargar el zip de una sesión de captura; es el único rol que recibe `lake_uri` (la ruta `gs://` cruda) en el detalle de un evento |

### WebSockets del navegador

`GET /api/devices/{device_id}/live` exige la cookie de sesión válida **y** que la cabecera
`Origin` de la conexión sea exactamente `PUBLIC_BASE_URL` (en `AUTH_MODE=dev`, también se acepta
`localhost`/`127.0.0.1` para Vite). Si falla cualquiera de las dos, el socket se cierra con el
código `1008` antes de aceptarse.

### Límite de intentos de login

`POST /api/auth/google` admite como máximo 10 intentos por IP por minuto
(`auth.check_login_rate`); al superarlo, `429 demasiados_intentos`.

## 2. Endpoints

Todos bajo `/api`, JSON, fechas en ISO-8601 UTC. Rutas tal como están declaradas en
`backend/epp_api/main.py` (prefijos) y cada router.

| Método y ruta | Rol mínimo | Descripción |
|---|---|---|
| `GET /api/auth/config` | — (público) | `{client_id}`: el client ID de Google, no es secreto |
| `POST /api/auth/google` | — | `{credential}` → `Me`; ver sección 1 |
| `POST /api/auth/logout` | sesión | Borra la cookie, `204` |
| `GET /api/auth/me` | sesión | `Me` del usuario actual |
| `GET /api/devices` | `viewer` | `DeviceStatus[]` de todos los dispositivos en `ALLOWED_DEVICES` |
| `GET /api/devices/{id}/summary?range=24h\|7d\|30d` | `viewer` | `Summary`: KPIs del periodo y del anterior (para el delta), serie temporal, desglose por clase |
| `GET /api/devices/{id}/events?cursor=&limit=&clase=&min_conf=&from=&to=&include_simulated=` | `viewer` | `EventPage`: página de `EventSummary` + `next_cursor` opaco (base64 de `end_ts\|event_id`) |
| `PUT /api/devices/{id}/desired-state` | `operator` | `{state: "running"\|"stopped"}` → `DeviceStatus`; registrado en `audit_log` |
| `POST /api/devices/{id}/training-captures` | `operator` | Pide al borde que empiece a subir fotos crudas para reentrenamiento → `TrainingCaptureStarted` (`session_id`, `started_at`); registrado en `audit_log` |
| `POST /api/devices/{id}/training-captures/{session_id}/stop` | `operator` | Pide detener esa sesión (sin efecto si ya no es la activa) → `204`; registrado en `audit_log` |
| `GET /api/devices/{id}/training-captures?cursor=&limit=` | `viewer` | `TrainingSessionPage`: página de `TrainingSessionSummary` + `next_cursor` |
| `GET /api/devices/{id}/training-captures/{session_id}/download` | `admin` | Zip (`application/zip`) con las fotos de la sesión, armado al vuelo y enviado en streaming (sin `Content-Length`; nunca entero en memoria, Cloud Run rechaza respuestas no streameadas de más de 32 MiB); `404` si no hay fotos, `400 sesion_demasiado_grande` por encima de `MAX_OBJECTS`; registrado en `audit_log` |
| `WS /api/devices/{id}/live` | `viewer` | Frames binarios (protocolo en sección 3) + mensajes de texto `{"type":"status","state":"waiting"\|"live"\|"ended"}` |
| `GET /api/events/{event_id}` | `viewer` | `EventDetail`: contrato v2 completo + `thumb_url`/`original_url` |
| `GET /api/events/{event_id}/capture?kind=thumb\|original` | `viewer` | `302` a una URL firmada de GCS (v4, 10 min, cacheable 9 min); `kind=original` se audita (`capture_original`) |
| `GET /api/admin/users` | `admin` | `UserOut[]` |
| `POST /api/admin/users` | `admin` | `{email, role}` → `UserOut` (`201`) |
| `PATCH /api/admin/users/{email}` | `admin` | `{role?, active?}` → `UserOut`; no se puede cambiar el propio rol ni quitar al último admin activo |
| `DELETE /api/admin/users/{email}` | `admin` | `204`; mismas protecciones que `PATCH` |
| `GET /api/admin/audit?cursor=&action=&email=&limit=` | `admin` | `AuditPage`: página de `AuditEntry` |
| `GET /healthz` | — | `{"ok": true, "version": "2.0.0"}` (fuera de `/api`; Cloud Run reserva `/healthz` como ruta especial — no usarla como healthcheck de aplicación personalizado) |
| `WS /ws/video/{device_id}` | `DEVICE_TOKEN` (no es un rol de usuario) | Uplink de la Pi; ver sección 3 |

El contrato implementado coincide con `docs/PLAN_REMEDIACION.md` sección 3.3 en rutas, roles y
formas de respuesta; la única nota es que el plan deja implícito el prefijo `/api` en el texto de
cada fila de esa tabla ("todos bajo `/api`" en el encabezado), mientras que esta página lo escribe
explícito en cada ruta para evitar ambigüedad al copiarla a un cliente HTTP.

## 3. Protocolo binario del video

Dos WebSockets usan el mismo formato de frame, definido en `backend/epp_api/relay.py` y
replicado en `raspberrypi/epp_edge/streamer.py` (`pack_frame`) y `frontend/src/api/live.ts`
(`parseFrame`):

```
offset 0..7   uint64 big-endian   captured_at en milisegundos Unix (reloj de la Pi, sincronizado por NTP)
offset 8..    JPEG                empieza con los bytes FF D8 (SOI)
```

- `POST`/uplink: `wss://<host>/ws/video/{device_id}`, cabecera `Authorization: Bearer
  <DEVICE_TOKEN>`. El backend valida el token con `hmac.compare_digest` (tiempo constante) y que
  `device_id` esté en `ALLOWED_DEVICES`; si no, cierra con `1008`.
- Reenvío al navegador: `wss://<host>/api/devices/{device_id}/live`, el backend retransmite el
  frame **sin modificarlo**, acotado a `LIVE_MAX_FPS` (si llega más rápido, se descartan frames
  intermedios — nunca se encola un frame viejo).
- El navegador calcula la latencia aproximada como `Date.now() - captured_at` y la muestra con
  "≈" (depende de la sincronización de relojes entre la Pi y el cliente). Si no llega ningún frame
  en `FROZEN_AFTER_MS` (2000 ms), la UI marca "señal congelada".
- Frames inválidos (cabecera corta, sin `FF D8` en el offset 8, o mayores que `MAX_FRAME_BYTES`)
  se descartan en silencio (`relay.valid_frame`), no cierran la conexión.

## 4. Formato de errores

Todo error de la API (propio o de FastAPI/Starlette) responde con el mismo cuerpo
(`backend/epp_api/errors.py`):

```json
{"error": {"code": "acceso_denegado", "message": "Tu cuenta (x@y.com) no tiene acceso. Pide acceso al administrador."}}
```

`code` es un identificador estable en español (p. ej. `no_autenticado`, `sin_permiso`, `csrf`,
`cursor_invalido`, `captura_expirada`, `ultimo_admin`, `demasiados_intentos`); `message` es el
texto en español mostrado al usuario. Los `422` de validación de Pydantic se homogeneízan al mismo
formato (`solicitud_invalida`) en vez de exponer el detalle crudo de FastAPI.

## 5. Regenerar `schema.d.ts` (tipos del frontend)

```bash
# 1. Vuelca el OpenAPI actual del backend a un archivo (no arranca el servidor, no toca GCP)
python backend/scripts/dump_openapi.py frontend/openapi.json

# 2. Regenera los tipos TypeScript desde ese archivo
cd frontend && pnpm gen:api    # openapi-typescript openapi.json -o src/api/schema.d.ts
```

`frontend/openapi.json` y `frontend/src/api/schema.d.ts` se versionan en git. La CI verifica que no
queden cambios pendientes de regenerar después de este paso — si el contrato cambió y alguien
olvidó regenerar los tipos, el build falla en CI en vez de romperse en producción silenciosamente.
