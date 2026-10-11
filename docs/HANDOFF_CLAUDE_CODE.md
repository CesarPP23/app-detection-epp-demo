# Handoff de contexto para Claude Code (otra laptop)

> Última actualización: 2026-10-11. Este archivo NO contiene secretos (ni tokens, ni keys, ni valores de Secret Manager, ni `.env`).
> Léelo completo antes de hacer nada. La fuente de verdad del trabajo es `docs/PLAN_REMEDIACION.md`; este archivo resume el estado y las decisiones.

## 1. Qué es el proyecto
Tesis de Cesar (repo público `app-detection-epp-demo`, proyecto GCP `epp-detection-v1`). Detección de EPP con cámara en una Raspberry Pi, subida de eventos a la nube y dashboard web.
Se ejecuta la remediación de una auditoría según `docs/PLAN_REMEDIACION.md`, fases 0→10 en orden, **una commit por sub-fase** (mensajes en español, imperativo).
Rama de trabajo: `remediacion/auditoria-2026-10` → al final PR hacia `main` (**NO hacer merge**) con resumen por fase y tabla de hallazgos.

### Rol esperado de Claude
- **Ejecutor** del plan (sin re-auditar ni rediseñar) y **orquestador**: delegar la ejecución en subagentes/forks especializados por área.
- Al final de cada fase: reporte breve (qué se hizo, criterios ✓/✗ con evidencia, commits, qué se necesita del usuario). Si un criterio falla, parar.
- Responder en español.

## 2. Reglas duras (prohibido)
1. Reescribir el historial de git o hacer force-push.
2. Escribir, imprimir o commitear secretos, tokens, keys o el contenido de `.env`.
3. Leer los valores de Secret Manager.
4. Subir `min-instances` por encima de 0 o quitar `max-instances=1`.
5. Cambiar React + TypeScript (frontend) / Python + FastAPI (backend).
6. Nada se ejecuta en GCP sin OK explícito del usuario en la misma sesión.
7. Plan §17: no hacer el repo privado, 1 solo worker de uvicorn, nada del dashboard expuesto sin login+whitelist, no usar Flet, no tokens en query strings, la Pi no puede leer/borrar el lake, no video de prueba en producción, nunca descartar un evento porque falló la captura, no confiar en un "$0" el mismo día de las pruebas.
8. Paradas manuales (plan §3.4): gcloud login, client ID OAuth, copiar el device-token a la Pi, destinatario del presupuesto, hardware de la Pi, activar GitHub secret scanning.

## 3. Decisiones fijas (no volver a preguntar)
- Base de control: `(default)`. Base de eventos: `dbraspberry` (sin migración).
- Pub/Sub eliminado.
- Dashboard público en internet con login de Google + whitelist (`dashboard_users`).
- Admin inicial: `admin@viz-ia.com`.
- Una captura por evento (`event_id` UUIDv7). Imágenes 60 días. Tema claro por defecto, sin logo.
- URL `*.run.app`; healthcheck listo pero deshabilitado; quitar rol Editor de la SA de cómputo solo si el deploy con la SA de build funciona; publicar `edge.env.template` con valores falsos + gitleaks; sin difuminado de caras.
- Sin Pi física: validar con simulador (`CAMERA_SOURCE=file:...`, `ALLOW_TEST_VIDEO=true`).

## 4. Arquitectura
- **Edge (Pi)** `raspberrypi/epp_edge/`: picamera2/USB/file → LatestSlot → InferenceWorker (NCNN, imgsz 416, ≤5 fps) → Aggregator → Outbox SQLite WAL → Uploader idempotente (imágenes → sidecar JSON → batch.create en Firestore + daily_stats Increment). StreamWorker solo con lease activo. DeviceControl por Firestore `on_snapshot`. HealthReporter.
- **Cloud Run** `orchestrator-service` (`backend/epp_api/`, max-instances=1, min 0, 1 worker uvicorn): API FastAPI + relay WebSocket + SPA React en el mismo origen.
  - Protocolo de video: 8 bytes big-endian uint64 `captured_at` ms + JPEG.
  - Lease: `device_control.stream_requested` + `lease_renewed_at` (LEASE_SECONDS 90, renovado cada /3).
  - Auth: Google Identity Services ID token → cookie firmada `epp_session` (itsdangerous); roles viewer/operator/admin; CSRF con header `X-Requested-With: epp-web`; revalidación de rol cada 60 s; rate limit 10/min/IP.
- **Data lake** en GCS bucket `epp-detection-v1-lake`: `raw/images|thumbs|events/device=<id>/dt=<fecha>/<event_id>.*`, create-only (`if_generation_match=0`).
- **BigQuery** dataset `epp_lake` (tabla externa `raw_events`, object table `raw_images`, `events` curada con MERGE diario, vistas gold).
- Contrato v2 del evento JSON: `capture` anidado (`capture.uri`, `capture.thumb_uri`).
- **Frontend** `frontend/`: React 19 + TS estricto + Vite, React Router, TanStack Query, Tailwind v4 con tokens, Radix, lucide, Recharts envuelto en componentes que respetan las reglas de dataviz, Vitest/Testing Library/MSW, Playwright + axe, pnpm. Guía de diseño en `docs/DESIGN_SYSTEM.md`.

## 5. Mapa del repo
- `docs/PLAN_REMEDIACION.md` — fuente de verdad. `docs/DESIGN_SYSTEM.md`, `docs/img/`.
- `backend/epp_api/` (settings fail-closed, relay, security_headers, auth, audit, repo, lake, schemas, summary, errors, live, lease, routers/{auth,devices,events,admin}); `backend/tests/` (70 tests); `backend/scripts/dump_openapi.py`; `requirements*.txt` (pinned).
- `frontend/` (SPA completa con features login/privacy/overview/live/events/device/admin; `openapi.json`; e2e; `scripts/check-budget.mjs`).
- `raspberrypi/epp_edge/` + `raspberrypi/tools/{set_state,check_firestore,reconcile_lake}.py` + `raspberrypi/deploy/edge.env.template` + `raspberrypi/tests/` (88 tests incl. simulador). El código viejo (`main.py`, `worker.py`, `utils/`, `scripts/`) fue eliminado con `git rm`.
- `infra/`: `lib.sh`, `00_prereqs.sh`, `01_secrets.sh`, `02_iam_runtime.sh`, `deploy_cloud.sh` (+ atajo `03_deploy_cloud.sh`), `04_firestore.sh`, `05_lake_bucket.sh`, `06_bigquery.sh`, `07_pubsub_teardown.sh`, `08_cleanup_billing.sh`, `bootstrap_admin.py`, `firestore.rules`, `policies/*.json`, `bigquery/*.sql`. Todos soportan `DRY_RUN=1`.
- `Dockerfile` en la raíz (multi-stage node:24 → python:3.12-slim).
- Aún existen `gcp/` y `front-end/` (código viejo): borrar **después** de confirmar el servicio nuevo. Tienen cambios sin commitear (`front-end/core/config.py`, `gcp/orchestrator_main.py`): se descartan junto con el código viejo, no tocar.
- Locales ignorados, no tocar: `raspberrypi/gcp-key.json`, `torch/`, `media/`, `logs/`, `database/`, `config/`.

## 6. Recursos GCP (IDs, no son secretos)
- Proyecto `epp-detection-v1`, número `540868081764`, región `us-central1`.
- Servicio Cloud Run `orchestrator-service`: `https://orchestrator-service-540868081764.us-central1.run.app`.
- SA runtime: `platform-runtime@epp-detection-v1.iam.gserviceaccount.com`. SA de la Pi: `raspberry-pi-worker@...` (la key antigua fue rotada/eliminada; incidente de key filtrada en historial público: solo rotación, sin reescribir historial).
- Secretos en Secret Manager: `device-token`, `session-secret` (nunca leer valores).
- OAuth: cliente web "EPP Monitor web", client ID `540868081764-dl5ke27nl1grk10gt6u2t1a0ottsci15.apps.googleusercontent.com` (no secreto). Orígenes autorizados: la URL de Cloud Run y `http://localhost:5173`. Client secret no se usa (GIS) y nunca se copió.
- Proyecto huérfano `woven-surface-469602-g1`: preguntar al usuario antes de tocarlo.
- Billing account: la del proyecto; hay línea base de costos en `docs/COSTS.md`.

## 7. Estado actual (2026-10-11)
### Hecho
- Fases 0–3 completas (criterios que necesitan GCP real se cierran en Fases 4/10). Fase 1: Docker verificado vía Cloud Build (imagen 70.7 MB).
- Scripts de infra (Fase 4/5) escritos y commiteados.
- Bloque 4.A ejecutado (limpieza de key vieja, roles Pub/Sub, límites de Cloud Run).
- Google Auth Platform configurado: app "EPP Monitor", audiencia **External en modo Testing**. **No se puede publicar a producción** porque Google exige un dominio privado propio en "Authorized domains" y `run.app` es sufijo público. Se dejó en Testing con `admin@viz-ia.com` como usuario de prueba (tope 100). Cada usuario nuevo del dashboard debe añadirse también en Audience → Test users. Para publicar haría falta un dominio propio (anotar como limitación en SECURITY.md/ADR).
- Bloque 4.B ejecutado:
  - `04_firestore.sh` (exit 0): 3 índices compuestos en `dbraspberry` READY, TTL de `audit_log.expire_at` en `(default)`, delete protection en ambas bases, backup semanal (domingo, 4 semanas).
  - Reglas Firestore: ya había deny-all activo en ambas bases (idéntico a `infra/firestore.rules`); no se publicó nada.
  - `bootstrap_admin.py`: creados `device_control/raspberry-pi-01` y `dashboard_users/admin@viz-ia.com` en `(default)`.
  - `06_bigquery.sh` **parcial**: creados dataset `epp_lake`, conexión `lake-conn` y permiso de lectura sobre el bucket. La tabla externa `raw_events` falla ("Cannot query hive partitioned data … without any associated files") porque el bucket está vacío. Reintentar en Fase 5 tras la primera subida de eventos (el script es relanzable).
- Deploy a Cloud Run: **hecho** (revisión `orchestrator-service-00007-vxs`, 100 % tráfico). max-instances=1, min 0, 512 Mi, CPU solo en peticiones, `UPLINK_TOKEN` ausente, secretos `DEVICE_TOKEN` y `SESSION_SECRET`. `/api/auth/config` responde con el client ID. `/healthz` devuelve 404 porque Cloud Run reserva esa ruta: usar otra ruta (p. ej. `/api/health`) para el healthcheck.

### Cambios sin commitear al cierre de esta sesión
- `infra/deploy_cloud.sh`: se quitó `--remove-env-vars=UPLINK_TOKEN` (gcloud no permite combinarlo con `--set-env-vars`, que ya reemplaza todo). Hay que commitearlo.
- Este archivo (`docs/HANDOFF_CLAUDE_CODE.md`).
- Ya existían sin commitear (dejar): `front-end/core/config.py`, `gcp/orchestrator_main.py`.

### Pendiente (en orden)
1. Commit del fix de `deploy_cloud.sh` y de este handoff.
2. Decidir con el usuario si el servicio queda público o se retira `allUsers` de `roles/run.invoker` (el usuario pidió "pausear"; se le dio el comando para quitarlo, no consta que lo haya ejecutado — verificar con `gcloud run services get-iam-policy`).
3. `07_pubsub_teardown.sh`: antes verificar que `dbraspberry/detecciones_por_tiempo` esté vacía; requiere env `EVENTARC_TRIGGERS` y `PUBSUB_SUBSCRIPTIONS`; nunca `--force`; parar ante avisos de dependencias.
4. `08_cleanup_billing.sh`: política de limpieza de Artifact Registry, lifecycle de fuentes de Cloud Build, SA `cloud-build-deployer`; borrar imagen `build-test:phase1` si sigue; `REMOVE_COMPUTE_EDITOR=1` solo si el deploy con build SA funciona.
5. Alerta de presupuesto: el usuario añade su correo como destinatario en Billing → Manage access; monto según `docs/COSTS.md`.
6. Borrar `gcp/` y `front-end/` y verificar criterios de Fase 4.
7. Fase 5: verificar conteo en `raw_events`, `reconcile_lake`, consulta por `event_id`; reintentar `06_bigquery.sh`.
8. Fase 6: scripts de despliegue en la Pi (`install.sh`, `epp-edge.service`, `update.sh`, runbook). Lo que dependa de hardware requiere la Pi; el usuario debe copiar el device-token a `edge.env` en la Pi.
9. Fase 7: `export_model`, benchmark, `docs/MODEL.md`; reescritura completa de `.gitignore`.
10. Fase 8: CI, gitleaks, pre-commit, ruff (ignorar B008); pedir al usuario activar GitHub Secret scanning + Push protection.
11. Fase 9: documentación (README, ARCHITECTURE, DATA_MODEL, DATA_LAKE, EDGE_DEPLOYMENT, CLOUD_DEPLOYMENT, SECURITY, PRIVACY, COSTS, MODEL, OPERATIONS_RUNBOOK, DESIGN_SYSTEM, API, `frontend/README`, ADRs 0001–0008, CHANGELOG); borrar `riesgos.txt`.
12. Fase 10: verificación extremo a extremo, cierre de costos (revisar Billing al día siguiente de las pruebas), checklist de seguridad, y abrir el PR (sin merge).
13. Corregir la redacción de criterios que mencionen `capture.uri`/`capture.thumb_uri`.
14. Opcional ofrecido: revisar Cloud Logging por uso indebido de la key vieja (si se omite, nota en SECURITY.md).

## 8. Quirks del entorno (aprendidos)
- Windows 11, Git Bash y PowerShell. Python local 3.13 (la imagen usa 3.12). Sin Docker, WSL ni admin: se usa Cloud Build.
- **gcloud/bq en Bash**: exportar `CLOUDSDK_PYTHON=/c/Python313/python.exe` (si no, `bq` busca `python3.13`).
- **ADC**: para scripts Python con Firestore hace falta `gcloud auth application-default login` (el usuario lo ejecutó con `!`; guardado en `%APPDATA%\gcloud\application_default_credentials.json`). Se usó un venv temporal con `google-cloud-firestore` para `bootstrap_admin.py`.
- El **clasificador de modo auto** bloquea muchas mutaciones de gcloud, `rm` y comandos compuestos (p. ej. deploy con `--allow-unauthenticated` = "Security Weaken"). No esquivarlo: pedir al usuario que lo ejecute con `! comando` en el prompt. Los comandos largos de gcloud pasan a segundo plano tras 120 s; usar `run_in_background` y Monitor.
- Los scripts de infra deben ejecutarse desde la raíz del repo (`--source=.`), no desde `infra/`.
- `04_firestore.sh`: `gcloud firestore indexes composite create` espera a que el índice quede READY (minutos); es idempotente.
- `.gitignore` histórico ignora `*.json` y `*.yml`: hay excepciones explícitas (`frontend/package.json`, `tsconfig*.json`, `infra/policies/*.json`, `docs/**/*.png`). La reescritura completa es Fase 7/8.
- `.gitattributes` fuerza LF en `*.sh`. Aparecen avisos de CRLF en Windows: normales.
- Capturas de pantalla de Chrome a veces dan timeout: usar `get_page_text`/`read_page`/zoom.
- Dependencias de la Pi (`ultralytics==8.4.175`, `ncnn==1.0.20260526`) no validadas en aarch64: rehacer `pip freeze` en la Pi real.

## 9. Trailers de commit y PR
Commits terminan con:
```
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01U5wUR7m2Zh6FDCPycv4SUM
```
La descripción del PR termina con `🤖 Generated with [Claude Code](https://claude.com/claude-code)` y el link de sesión.

## 10. Commits ya hechos en la rama (más antiguo → más reciente)
`1af8516`, `aca9b0c`, `8f0c8f2`, `3d56ad2`, `7fc1147`, `41424e8`, `5f6d7c0`, `b4d2e47`, `41d89c3`, `9c8b2ac`, `5a71d2a`, `0f6ffbf`, `0c522e7`, `34ff6c5`, `ef1c670`, `a3b6c43`, `a4dd4f2`, `e492a40`, `cdd3f16`, `50954ce`, `4398625`, `a469408`, `02db8d6`, `140211f`, `b215645`, `a1f62e0`, `b1d799a`, `09647c6`, `ec225e9`, `05ac829`, `b72f3f6`, `432df40`, `e601f1d`, `d0047bf`, `2cfeb34`, `243a094`, `d32aba2`, `a4f5e2c`, `d811073`, `44f02ae`, `02b8129`, `2d5697f`, `61a36a8`, `a17ed50`, `fef4f6e`, `27f5d45`.

## 11. Cómo retomar en la otra laptop
1. `git clone` del repo y `git checkout remediacion/auditoria-2026-10` (si esta rama no se subió, pedir al usuario hacer `git push` de la rama primero).
2. Instalar: gcloud CLI, Git Bash, Python 3.12/3.13, Node 24 + pnpm. `gcloud auth login` y `gcloud auth application-default login` con la cuenta del proyecto; `gcloud config set project epp-detection-v1`.
3. Leer este archivo y `docs/PLAN_REMEDIACION.md`; ejecutar `git status` y `git log --oneline -5` para confirmar el estado.
4. Continuar por la sección 7 → "Pendiente", pidiendo OK explícito antes de cada mutación en GCP.
