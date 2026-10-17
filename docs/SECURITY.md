# Seguridad

**Para quién es:** quien evalúe el riesgo del sistema, responda a un incidente o decida si algo es
seguro de publicar. **Última verificación:** 2026-10-11.

Este documento no contiene secretos: solo **nombres** de credenciales, IDs de recursos (no
sensibles) e identificadores de commits. Los valores viven exclusivamente en Secret Manager, en
`/etc/epp-edge/edge.env` (en la Pi) o en credenciales ADC locales — nunca en git.

## 1. Modelo de amenazas (STRIDE, resumido)

| Categoría | Amenaza concreta en este sistema | Mitigación |
|---|---|---|
| **S**poofing | Alguien se conecta a `/ws/video/{device_id}` haciéndose pasar por la Pi | `DEVICE_TOKEN` (≥ 32 caracteres, en Secret Manager) comparado con `hmac.compare_digest` (tiempo constante) en `relay.py`; corrige el bypass de la versión anterior (`if auth_token != UPLINK_TOKEN`, donde `None != None` era falso y una conexión sin cabecera pasaba) |
| **S**poofing | Alguien se hace pasar por un usuario del dashboard | Login real contra Google Identity Services (verificación de ID token con `google.oauth2.id_token.verify_oauth2_token`); cookie de sesión firmada con `itsdangerous`, `HttpOnly; Secure; SameSite=Strict` |
| **T**ampering | Un cliente altera una request para actuar con otro rol o sin CSRF | `require_role()` en cada endpoint que muta; `X-Requested-With: epp-web` obligatorio en todo `POST/PUT/PATCH/DELETE` (sin CORS habilitado, un origen ajeno no puede enviar esa cabecera) |
| **T**ampering | Alguien modifica un objeto ya subido al lake | La SA de la Pi solo tiene `storage.objectCreator`; los objetos se suben con `if_generation_match=0` (create-only); nadie, ni la propia Pi, puede sobrescribirlos después |
| **R**epudiation | Un operador pausa el dispositivo o descarga una captura original y lo niega | `audit_log` (colección en `(default)`) registra `email`, `action`, `target`, `ts` en cada acción sensible (`login`, `login_denied`, `desired_state`, `live_start`, `live_end`, `capture_original`, `user_create/update/delete`); TTL de 365 días |
| **I**nformation disclosure | Video o capturas de trabajadores expuestos sin autenticación | El dashboard completo exige login + lista blanca; las capturas se sirven por URL firmada v4 de 10 minutos (`lake.py`), nunca con una URL pública fija; ver también `docs/PRIVACY.md` |
| **I**nformation disclosure | Una key de servicio filtrada en el repositorio público | Ver sección 3 (incidentes) y sección 5 (riesgos aceptados): Cloud Run no usa ningún archivo de key (SA asociada); la Pi sí necesita una (hardware sin Workload Identity), con permisos mínimos |
| **D**enial of service | Alguien deja una pestaña de "En vivo" abierta o abusa del login | `MAX_LIVE_VIEWERS` (tope global de espectadores concurrentes), rate limit de `check_login_rate` (10 intentos por IP por minuto en `/api/auth/google`), cierre automático del WebSocket de video por inactividad (15 min) o pestaña oculta (30 s) desde el frontend |
| **D**enial of service | Costo de Cloud Run sin techo | `--max-instances=1` fijo (invariante que el plan prohíbe subir); alerta de presupuesto con umbrales y pronóstico (pendiente de configurar por el usuario, `docs/COSTS.md`) |
| **E**levation of privilege | Un `viewer` intenta pausar el dispositivo o administrar usuarios | Jerarquía de roles (`viewer < operator < admin`) revalidada en cada request (caché de 60 s contra `dashboard_users`: dar de baja a alguien tiene efecto en ≤ 60 s); un admin no puede desactivarse ni quitarse el rol a sí mismo, ni dejar el sistema sin ningún admin activo (`routers/admin.py`, `_guard_last_admin`) |

## 2. Inventario de credenciales (nombres, no valores)

| Credencial | Dónde vive | Quién la usa | Rotación |
|---|---|---|---|
| Secreto `device-token` (Secret Manager) | Secret Manager del proyecto `epp-detection-v1`; copiado a `/etc/epp-edge/edge.env` en la Pi (variable `DEVICE_TOKEN`) | Cloud Run (lo monta como `DEVICE_TOKEN`) y la Pi (uplink de video) | Manual: generar una nueva versión del secreto, copiar el nuevo valor a la Pi, redesplegar el backend; ver `docs/OPERATIONS_RUNBOOK.md` |
| Secreto `session-secret` (Secret Manager) | Secret Manager; montado en Cloud Run como `SESSION_SECRET` | Firma y valida la cookie `epp_session` | Manual; rotarlo invalida **todas** las sesiones activas (es intencional ante un incidente) |
| Clave de SA `raspberry-pi-worker` (archivo JSON) | Solo en la Pi física, `/etc/epp-edge/gcp-key.json`, `root:epp 0640`; **nunca** dentro del árbol de código ni en git | La Pi, para autenticarse contra Firestore y GCS (no hay Workload Identity en hardware fuera de GCP) | Manual, vía consola IAM; ver incidentes abajo — es la credencial que se filtró dos veces |
| Clave de SA de Cloud Run | **No existe.** `platform-runtime` es una SA asociada al servicio, sin archivo de key; firma URLs con `signBlob` sobre sí misma | — | No aplica |
| Client ID de OAuth (`GOOGLE_OAUTH_CLIENT_ID`) | Variable de entorno del backend; expuesto a propósito en `/api/auth/config` | El frontend, para iniciar el flujo de Google Identity Services | No es secreto; no se rota salvo que se recree el cliente OAuth |
| Credenciales ADC locales (`gcloud auth application-default login`) | `%APPDATA%\gcloud\application_default_credentials.json` en la máquina de quien administra | Scripts de `infra/`, `bootstrap_admin.py`, `reconcile_lake.py` | Expiran solas; se renuevan con el mismo comando |

## 3. Incidentes de credenciales filtradas (ya revocados)

| Commit | Fecha | Qué se filtró | Estado |
|---|---|---|---|
| `243d2de` | 2025-06-10 | Credencial de servicio incluida en un commit temprano del backend | **Revocada.** El historial de git no se reescribió (regla dura del proyecto): la credencial vieja fue invalidada en IAM, no borrada del historial. |
| `3f95ee1` | 2026-09-23 | `raspberrypi/script-rpi-v0.1.zip`, versionado en ese commit, contenía `gcp-key.json` de `raspberry-pi-worker` (`private_key_id` que empieza en `52096d10`) | **Revocada** en la Fase 0 del plan de remediación. El ZIP se quitó del árbol actual (`git rm --cached`) pero sigue siendo recuperable desde ese commit — es aceptado: la mitigación es la revocación, no la reescritura de historial. |

Ambos incidentes se resolvieron de la misma forma: **rotar la credencial, nunca reescribir
git**. Cualquier incidente futuro debe seguir el mismo patrón. Si se detecta una tercera key
filtrada, revisar Cloud Logging (`protoPayload.authenticationInfo.principalEmail=
"raspberry-pi-worker@epp-detection-v1.iam.gserviceaccount.com"`) por uso indebido en los 30 días
anteriores a la revocación, si los Data Access audit logs de Firestore estaban activos en ese
periodo (si no lo estaban, es una limitación a anotar: no hay forma de reconstruir el uso
histórico).

## 4. Prevención

- `.gitignore` cubre explícitamente `*key*.json`, `service_account*.json`, `credentials*.json`,
  `client_secret*.json`, `.env`/`.env.*`/`*.env` (con excepción de `*.env.template`), `.pem`,
  `.p12` (Fase 8 del plan).
- `gitleaks` en pre-commit y en CI (job `secrets`): el historial seguirá marcando `243d2de` y
  `3f95ee1` como hallazgos — es esperado y se documenta aquí como ya resuelto, no se actúa de
  nuevo sobre ellos.
- Pendiente de que el usuario active en GitHub (gratis en repos públicos): *Secret scanning* y
  *Push protection*.

## 5. Riesgos aceptados (documentados, no son bugs)

### Por qué `--allow-unauthenticated` es aceptable

Cloud Run exige `--allow-unauthenticated` porque dos tipos de cliente sin identidad de Google se
conectan al mismo servicio: los navegadores de cualquier visitante (antes de iniciar sesión) y la
Raspberry Pi (que se autentica con `DEVICE_TOKEN`, no con IAM). Esto **no** significa que el
dashboard esté abierto: cada ruta bajo `/api/*` exige la cookie de sesión (`auth.current_user`), la
cookie exige haber pasado por Google Identity Services, y el login exige estar en la lista blanca
de `dashboard_users` con `active=true`. La capa de autorización vive en la aplicación, no en IAM de
Cloud Run — es una decisión consciente del plan de remediación (decisión U3), no un descuido.

### `roles/datastore.user` sin granularidad por colección

Firestore IAM no permite limitar un rol a una colección específica. Tanto `platform-runtime` como
`raspberry-pi-worker` tienen `datastore.user` a nivel de **proyecto**, es decir, en teoría podrían
leer o escribir cualquier colección de la base a la que tienen acceso (incluida
`device_control`, que la Pi solo debería leer). Mitigación: el código de la Pi nunca escribe en
`device_control` (solo lee), el código del backend es la única superficie de escritura de verdad;
una key de la Pi filtrada igual queda acotada por no tener acceso a GCS más allá de `objectCreator`
y por la rotación documentada en la sección 2.

### Google Auth Platform en modo Testing (no Production)

La app de OAuth ("EPP Monitor") quedó en audiencia **External, modo Testing**, con
`admin@viz-ia.com` como único usuario de prueba (tope de 100). Publicarla a producción exige un
dominio propio en "Authorized domains"; `*.run.app` es un sufijo público que Google no acepta ahí.
Mientras el servicio use la URL de Cloud Run, cada usuario nuevo del dashboard debe añadirse
también a mano en Audience → Test users de la consola, además de en `dashboard_users`. Ver también
`docs/adr/0008-auth-google-identity-cookie.md`.

## 6. Checklist de seguridad final

Pendiente de ejecutar en la Fase 10 (`docs/PLAN_REMEDIACION.md` sección 16.3), **no verificado por
este documento**: una sola key activa en `raspberry-pi-worker` y fuera del repo; Cloud Run sin
archivos de key; secretos solo en Secret Manager o en `edge.env` de la Pi; `gitleaks` limpio salvo
los dos incidentes ya documentados; reglas de Firestore deny-all publicadas en ambas bases; bucket
del lake con `publicAccessPrevention: enforced`; acceso al dashboard verificado solo para la lista
blanca con una cuenta real.
