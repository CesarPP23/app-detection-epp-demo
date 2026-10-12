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
- **Desde 2026-10-11 hay Pi física disponible** (`edge-vizia-sac`, usuario `vizia`), instalada y funcionando — ya no hace falta el simulador (`CAMERA_SOURCE=file:...`, `ALLOW_TEST_VIDEO=true`) salvo para pruebas rápidas sin tocar el hardware real.

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
- Servicio Cloud Run `orchestrator-service`: `https://orchestrator-service-540868081764.us-central1.run.app`. Decisión confirmada 2026-10-11: queda **público** (`allow-unauthenticated`), protegido por login Google + whitelist; el costo de una visita anónima es despreciable porque lo caro (streaming) está detrás del login. Falta configurar la alerta de presupuesto (acción manual del usuario).
- SA runtime: `platform-runtime@epp-detection-v1.iam.gserviceaccount.com`. SA de la Pi: `raspberry-pi-worker@epp-detection-v1.iam.gserviceaccount.com` — roles confirmados (solo lectura, 2026-10-11): `roles/datastore.user` a nivel de proyecto + `roles/storage.objectCreator` en `gs://epp-detection-v1-lake` (mínimo privilegio, correcto). **Dos keys activas ahora**: la vieja `b78e4fdf...` (2026-09-20, archivo perdido al cambiar de laptop, nadie la puede usar ya) y una nueva creada hoy (key id `bbb3d16c...`) ya copiada a `/etc/epp-edge/gcp-key.json` en la Pi física y funcionando. **Pendiente**: revocar `b78e4fdf...` con OK del usuario, por higiene (ya no sirve para nada, no tiene sentido dejarla viva).
- Secretos en Secret Manager: `device-token`, `session-secret` (nunca leer valores; `device-token` ya se copió a la Pi real y el streaming funciona con él).
- OAuth: cliente web "EPP Monitor web", client ID `540868081764-dl5ke27nl1grk10gt6u2t1a0ottsci15.apps.googleusercontent.com` (no secreto). Orígenes autorizados: la URL de Cloud Run y `http://localhost:5173`. Client secret no se usa (GIS) y nunca se copió.
- Proyecto huérfano `woven-surface-469602-g1`: preguntar al usuario antes de tocarlo.
- Billing account: la del proyecto; hay línea base de costos en `docs/COSTS.md` (estimada, aún sin verificar contra Billing real).
- Cuenta gcloud usada en la laptop nueva: `pruebavizia7@gmail.com`, rol `Owner` en el proyecto (verificado). `gcloud` no estaba instalado en esta laptop; se instaló con `winget install --id Google.CloudSDK`.

## 7. Estado actual (2026-10-11, sesión en laptop nueva — clon fresco)
### Hecho hoy (laptop nueva, además de lo heredado de la laptop anterior)
- Repo clonado (`--single-branch`) y luego también se trajo `origin/main` (`git fetch origin main:refs/remotes/origin/main`) porque los subagentes con `isolation: worktree` la necesitan como base.
- **Fases 6, 7, 8 y 9 completadas** vía 4 subagentes en paralelo (3 en worktrees aislados + 1 directo en el checkout principal porque necesitaba `torch/best.pt`), fusionadas sin conflictos y **pusheadas** a `origin/remediacion/auditoria-2026-10` (HEAD actual: `0eb919d`; ver `git log --oneline origin/remediacion/auditoria-2026-10` en vez de una lista fija de hashes, ya son demasiados commits).
  - Fase 6: `raspberrypi/deploy/{install.sh,epp-edge.service,update.sh}`, `docs/EDGE_DEPLOYMENT.md`, `docs/OPERATIONS_RUNBOOK.md`; `riesgos.txt` migrado y borrado.
  - Fase 7: `raspberrypi/tools/{export_model.py,benchmark.py}`, `docs/MODEL.md`. En Windows el benchmark real **no corrió** (ver quirks §8: Smart App Control bloquea `torch`); quedó `ncnn@416` documentado como supuesto del plan, no medición.
  - Fase 8: `.gitignore` reescrito, `.pre-commit-config.yaml`, `pyproject.toml`, `.github/workflows/ci.yml`. Validado localmente: backend 70/70, frontend lint/typecheck/test/build/e2e en verde (JS inicial 141 KB gzip, presupuesto 180 KB), gitleaks sin hallazgos. `docker build .` no se pudo validar local (sin Docker Desktop activo) — queda pendiente de confirmar en un run real de GitHub Actions.
  - Fase 9: `README.md`, `ARCHITECTURE.md`, `DATA_MODEL.md`, `DATA_LAKE.md`, `CLOUD_DEPLOYMENT.md`, `SECURITY.md`, `PRIVACY.md`, `COSTS.md`, `API.md`, 8 ADRs, `CHANGELOG.md` — todos verificados contra el código real, con las secciones de costos/latencia/checklist de seguridad marcadas explícitamente como "estimado, pendiente de Fase 10".
  - Bug real encontrado y corregido durante Fase 6: `install.sh` paso 8 abortaba toda la instalación (`set -e`) si el primer arranque del servicio fallaba — ahora tolera el fallo inicial (commit `0eb919d`).
- **Raspberry Pi física instalada y funcionando de punta a punta** (host `edge-vizia-sac`, usuario `vizia`): `install.sh` corrido completo (12/12 pasos), modelo `epp_yolov8n.pt` exportado a NCNN en la propia Pi (ahí sí funciona `torch`/`pnnx`, a diferencia de Windows) y copiado a `/var/lib/epp-edge/models/best_ncnn_model`, key nueva de `raspberry-pi-worker` copiada a `/etc/epp-edge/gcp-key.json`, `edge.env` completado. Servicio `epp-edge` activo, cámara USB detectada, `desired_state=running`, **streaming en vivo confirmado funcionando** desde el dashboard (`/En vivo` → `admin@viz-ia.com`).
- Como la Pi ya está subiendo eventos reales, **ya se puede reintentar `06_bigquery.sh`** (antes fallaba porque el bucket del lake estaba vacío) y correr `reconcile_lake.py` — no se ha hecho todavía esta sesión.

### Próximo foco explícito del usuario
Costos y optimización (revisar Billing, confirmar que el streaming bajo demanda no está generando cargos inesperados, y afinar antes de seguir con limpieza de GCP).

### Pendiente (en orden)
1. `07_pubsub_teardown.sh`: antes verificar que `dbraspberry/detecciones_por_tiempo` esté vacía; requiere env `EVENTARC_TRIGGERS` y `PUBSUB_SUBSCRIPTIONS`; nunca `--force`; parar ante avisos de dependencias.
2. `08_cleanup_billing.sh`: política de limpieza de Artifact Registry, lifecycle de fuentes de Cloud Build, SA `cloud-build-deployer`; borrar imagen `build-test:phase1` si sigue; `REMOVE_COMPUTE_EDITOR=1` solo si el deploy con build SA funciona.
3. Alerta de presupuesto: el usuario añade su correo como destinatario en Billing → Manage access; monto según `docs/COSTS.md`. **Es justo el foco actual del usuario** — probablemente lo primero a retomar.
4. Revocar la key vieja `b78e4fdf...` de `raspberry-pi-worker` (con OK del usuario) una vez confirmado que la nueva sigue funcionando estable.
5. Borrar `gcp/` y `front-end/` (código viejo) y verificar los criterios de aceptación de la Fase 4.
6. Fase 5: reintentar `06_bigquery.sh` (el bucket ya no está vacío), correr `reconcile_lake.py`, verificar una consulta por `event_id`.
7. Fase 7 (opcional): correr `benchmark.py` en la Pi real para tener números reales de latencia/FPS y actualizar `docs/MODEL.md` (ahora sí es posible, Windows era el único bloqueo).
8. Pedir al usuario activar GitHub Secret scanning + Push protection (acción manual, Fase 8).
9. Confirmar un run real de GitHub Actions (especialmente el job `image`, no validado localmente).
10. Fase 10: verificación extremo a extremo, cierre de costos (revisar Billing al día siguiente de pruebas con streaming real), checklist de seguridad final, y abrir el PR hacia `main` (sin merge).
11. Corregir la redacción de criterios que mencionen `capture.uri`/`capture.thumb_uri` en `docs/PLAN_REMEDIACION.md`.
12. Opcional ofrecido: revisar Cloud Logging por uso indebido de las keys viejas (si se omite, nota en SECURITY.md).

## 8. Quirks del entorno (aprendidos)
- Windows 11, Git Bash y PowerShell. Python local 3.13 (la imagen usa 3.12). Sin Docker, WSL ni admin: se usa Cloud Build.
- **gcloud/bq en Bash**: exportar `CLOUDSDK_PYTHON=/c/Python313/python.exe` (si no, `bq` busca `python3.13`).
- **ADC**: para scripts Python con Firestore hace falta `gcloud auth application-default login` (el usuario lo ejecutó con `!`; guardado en `%APPDATA%\gcloud\application_default_credentials.json`). Se usó un venv temporal con `google-cloud-firestore` para `bootstrap_admin.py`.
- El **clasificador de modo auto** bloquea muchas mutaciones de gcloud, `rm` y comandos compuestos (p. ej. deploy con `--allow-unauthenticated` = "Security Weaken"). No esquivarlo: pedir al usuario que lo ejecute con `! comando` en el prompt. Los comandos largos de gcloud pasan a segundo plano tras 120 s; usar `run_in_background` y Monitor.
- Los scripts de infra deben ejecutarse desde la raíz del repo (`--source=.`), no desde `infra/`.
- `04_firestore.sh`: `gcloud firestore indexes composite create` espera a que el índice quede READY (minutos); es idempotente.
- `.gitattributes` fuerza LF en `*.sh`. Aparecen avisos de CRLF en Windows: normales.
- Capturas de pantalla de Chrome a veces dan timeout: usar `get_page_text`/`read_page`/zoom.
- Dependencias de la Pi (`ultralytics==8.4.175`, `ncnn==1.0.20260526`) no validadas en aarch64: rehacer `pip freeze` en la Pi real.

### Laptop nueva (clon fresco, 2026-10-11)
- Clonar con `--branch remediacion/auditoria-2026-10 --single-branch` deja sin `origin/main` local. Los subagentes con `isolation: worktree` fallan ("Failed to resolve base branch origin/main") hasta hacer `git fetch origin main:refs/remotes/origin/main`.
- `git worktree remove` puede fallar en Windows por rutas largas (p. ej. caché de `node_modules`/Playwright dentro del worktree): error "Filename too long". Se resuelve vaciando con `robocopy <carpeta_vacía> <destino> /MIR` antes de borrar la carpeta.
- **Windows Smart App Control** bloquea DLLs no firmadas de `torch` (`torch_python.dll`, `shm.dll`, error `WinError 4551`) y a veces las de `numpy.random` recién instaladas por pip (afecta 11 tests de `raspberrypi/tests`). No tiene solución sin ser administrador; no bloquea Linux/la Pi real ni CI (GitHub Actions). Por eso la Fase 7 (export/benchmark NCNN) no se pudo validar en esta laptop y se hizo en la Pi real.
- `gcloud` no estaba instalado: `winget install --id Google.CloudSDK -e`. Tras instalar, el PATH no se refresca en sesiones/terminales ya abiertas (ni en la del usuario ni en la del agente) — hay que abrir una terminal nueva o usar la ruta completa `"$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"` esa primera vez.
- Exportar a NCNN con Ultralytics necesita el paquete `pnnx` además de `ultralytics`/`torch`/`ncnn` (no está en `raspberrypi/requirements.txt`). En la Pi: `sudo /opt/epp-edge/venv/bin/pip install pnnx` lo resuelve.
- El nombre de la carpeta NCNN exportada sale del nombre del `.pt` (`<stem>_ncnn_model/`), no siempre `best_ncnn_model` — si el archivo no se llama `best.pt` (p. ej. `epp_yolov8n.pt`), hay que copiarla/renombrarla a mano a `/var/lib/epp-edge/models/best_ncnn_model`.
- La terminal SSH de la Pi (usuario `vizia`, host `edge-vizia-sac`) tenía bracketed-paste roto: pegar comandos multilínea mostraba literalmente `^[[200~`. Fix: `bind 'set enable-bracketed-paste off'` (agregarlo a `~/.bashrc` para que persista).
- Al editar `edge.env` a mano con `nano`, es fácil duplicar el prefijo (queda `ORCHESTRATOR_HOST=ORCHESTRATOR_HOST=...`) si no se borra la línea completa antes de escribir el valor nuevo — causa `[Errno -2] Name or service not known` en el streaming. Mejor usar `sed -i 's/^VAR=.*/VAR=valor/'` para reemplazar la línea entera.
- picamera2 puede fallar una vez con `Camera in Configured state trying acquire()` justo después de un restart rápido del servicio (handle no liberado a tiempo); el fallback automático a `usb:0` de `capture.py` (`auto`) lo resuelve solo en el mismo intento — no es un bug.

## 9. Trailers de commit y PR
Commits terminan con:
```
Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01U5wUR7m2Zh6FDCPycv4SUM
```
La descripción del PR termina con `🤖 Generated with [Claude Code](https://claude.com/claude-code)` y el link de sesión.

## 10. Commits en la rama
Ya son demasiados para listar a mano (>70). Usar `git log --oneline origin/remediacion/auditoria-2026-10` para ver el historial completo. HEAD al cierre de esta sesión: `0eb919d`.

## 11. Cómo retomar (en esta laptop u otra)
1. `git clone --branch remediacion/auditoria-2026-10 --single-branch <url>` (o `git checkout remediacion/auditoria-2026-10` si el repo ya existe). Si se usan subagentes con `isolation: worktree`, hacer también `git fetch origin main:refs/remotes/origin/main` (ver quirks §8).
2. Instalar: gcloud CLI (en Windows sin admin, `winget install --id Google.CloudSDK -e` funciona; abrir terminal nueva después), Git Bash, Python 3.12/3.13, Node 24 + pnpm. `gcloud auth login` y `gcloud auth application-default login` con la cuenta del proyecto; `gcloud config set project epp-detection-v1`.
3. Leer este archivo y `docs/PLAN_REMEDIACION.md`; ejecutar `git status` y `git log --oneline -5` para confirmar el estado real (puede diferir de lo escrito aquí si pasó tiempo).
4. Continuar por la sección 7 → "Pendiente", pidiendo OK explícito antes de cada mutación en GCP. El foco actual del usuario es costos/optimización — probablemente lo primero a retomar es la alerta de presupuesto (pendiente 3) y revisar Billing.
