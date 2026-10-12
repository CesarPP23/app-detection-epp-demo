# Despliegue en GCP

**Para quién es:** quien despliegue, redespliegue o audite la infraestructura del proyecto
`epp-detection-v1`. **Última verificación:** 2026-10-11.

Este documento describe **qué hacen** los scripts de `infra/` (todos ya escritos y comentados en
el repo) y el estado real reportado en `docs/HANDOFF_CLAUDE_CODE.md`. Ninguno de estos comandos se
ejecutó como parte de escribir esta documentación — este agente tiene prohibido mutar GCP. Todos
los scripts son idempotentes, soportan `DRY_RUN=1` (ver `infra/lib.sh`) y deben correrse desde la
**raíz del repo**.

## 0. Antes de ejecutar nada

```bash
gcloud auth login
gcloud config set project epp-detection-v1
# Para scripts Python (bootstrap_admin.py, reconcile_lake.py):
gcloud auth application-default login
```

Variables comunes (`infra/lib.sh`): `PROJECT=epp-detection-v1`, `REGION=us-central1`,
`SERVICE=orchestrator-service`, `LAKE_BUCKET=epp-detection-v1-lake`. `DRY_RUN=1` antes de cualquier
script imprime los comandos que ejecutaría sin llamar a `gcloud`/`bq`/`gsutil`.

## 1. `infra/00_prereqs.sh`

Habilita las APIs que usan las fases siguientes (`secretmanager`, `bigquery`,
`bigqueryconnection`, `bigquerydatatransfer`) y lista las bases de Firestore existentes (solo
lectura, para confirmar que son `(default)` y `dbraspberry`).

## 2. `infra/01_secrets.sh`

Crea los secretos `device-token` y `session-secret` en Secret Manager, generados con
`secrets.token_urlsafe(48)` **dentro de un pipe hacia `gcloud secrets create`**: el script nunca
imprime, guarda en una variable ni escribe en disco el valor. Si un secreto ya existe, no lo toca
(rotar es un procedimiento manual, ver `docs/OPERATIONS_RUNBOOK.md`). Al final recuerda las dos
acciones manuales pendientes: crear el cliente OAuth en la consola y copiar `device-token` a
`/etc/epp-edge/edge.env` en la Pi.

## 3. `infra/02_iam_runtime.sh`

Crea la SA `platform-runtime@epp-detection-v1.iam.gserviceaccount.com` (si falta) y le da:

- `roles/secretmanager.secretAccessor` sobre `device-token` y `session-secret`.
- `roles/datastore.user` a nivel de **proyecto** (no hay granularidad por colección en Firestore
  IAM — riesgo aceptado, documentado en `docs/SECURITY.md`).
- `roles/storage.objectViewer` sobre el bucket del lake (para firmar URLs de lectura).
- `roles/iam.serviceAccountTokenCreator` sobre **sí misma** (necesario para firmar URLs v4 de GCS
  sin archivo de key: `signBlob`).

Y le da a la SA de la Pi (`raspberry-pi-worker@...`) el mínimo privilegio: `roles/datastore.user`
a nivel de proyecto y `roles/storage.objectCreator` **solo** en el bucket del lake. Termina
imprimiendo los roles actuales de la SA de la Pi para verificar que sean exactamente esos dos.
Debe correrse **después** de `01_secrets.sh` y `05_lake_bucket.sh` (necesita que el secreto y el
bucket ya existan).

## 4. `infra/deploy_cloud.sh` (atajo: `infra/03_deploy_cloud.sh`)

```bash
./infra/deploy_cloud.sh <PUBLIC_BASE_URL> <GOOGLE_OAUTH_CLIENT_ID> [--build-sa]
```

Construye y despliega `orchestrator-service` desde la raíz del repo (`--source=.`, usa el
`Dockerfile` multi-etapa). Invariantes que el script fija y que **no deben cambiarse**
(`docs/PLAN_REMEDIACION.md` sección 17): `--min-instances=0 --max-instances=1` (el estado del
relay de video vive en memoria de un solo proceso), `--cpu-throttling` (facturación solo por
request), 1 worker de uvicorn (lo fija el `Dockerfile`, no esta flag). También fija
`--concurrency=40 --session-affinity`, `--cpu=1 --memory=512Mi --no-cpu-boost --timeout=3600`
(reduce las reconexiones de WebSocket del límite de 300 s por defecto), los secretos
(`DEVICE_TOKEN`, `SESSION_SECRET` desde Secret Manager) y las variables de entorno
(`ALLOWED_DEVICES`, `AUTH_MODE=google`, `PUBLIC_BASE_URL`, `GOOGLE_OAUTH_CLIENT_ID`, `LAKE_BUCKET`,
`RUNTIME_SA_EMAIL`, `LAKE_IMAGE_RETENTION_DAYS=60`). `--allow-unauthenticated` es obligatorio
(navegadores y la Pi no tienen identidad de Google) — ver por qué esto es aceptable en
`docs/SECURITY.md`. Con `--build-sa` usa la SA de build (`cloud-build-deployer`, creada por
`08_cleanup_billing.sh`) en vez de la SA default de Compute.

**Rollback:**
```bash
gcloud run services update-traffic orchestrator-service --region=us-central1 --to-revisions=<revisión-anterior>=100
```

## 5. `infra/04_firestore.sh`

- Crea los 3 índices compuestos que necesita `repo.py` (ver `docs/DATA_MODEL.md` sección 2) en
  `dbraspberry`, con `run_create` (idempotente: si ya existe, lo registra y sigue en vez de
  fallar).
- Activa el TTL sobre `audit_log.expire_at` en `(default)`.
- Activa `--delete-protection` en ambas bases y crea un backup semanal (domingo, retención de 4
  semanas) por base, si no existe ya uno programado.
- Recuerda al final que las **reglas** (`infra/firestore.rules`, deny-all) se publican a mano en
  la consola (Firestore → cada base → Reglas): `gcloud` no las despliega.

## 6. `infra/bootstrap_admin.py`

Script Python (no `gcloud`) idempotente: crea `device_control/raspberry-pi-01` (`desired_state:
"stopped"`, `stream_requested: false`) y `dashboard_users/admin@viz-ia.com` (`role: "admin"`,
`active: true`) en `(default)`, usando `ref.create()` — si el documento ya existe, `create()`
lanza una excepción en vez de sobrescribirlo, así que nunca pisa datos reales. Usa ADC, no lee ni
escribe secretos.

```bash
python infra/bootstrap_admin.py [--project epp-detection-v1] [--database "(default)"] [--dry-run]
```

## 7. `infra/05_lake_bucket.sh`

Crea `gs://epp-detection-v1-lake` con `--uniform-bucket-level-access --public-access-prevention
--soft-delete-duration=7d` (si no existe) y le aplica siempre el lifecycle de
`infra/policies/lake-lifecycle.json` (Nearline a los 30 días, borrado a los 60; ver
`docs/DATA_LAKE.md`). Debe correrse **antes** de `02_iam_runtime.sh` (ese script necesita que el
bucket ya exista para asignarle permisos).

## 8. `infra/06_bigquery.sh`

Crea el dataset `epp_lake` y la conexión `lake-conn` (`CLOUD_RESOURCE`, `us-central1`); lee el
`serviceAccountId` real de la conexión (`bq show --connection`, un identificador, no un secreto) y
le da `roles/storage.objectViewer` sobre el bucket del lake. Ejecuta en orden los `.sql` 01, 02,
03 y 05 de `infra/bigquery/` (ver `docs/DATA_LAKE.md` sección 4) y programa la consulta diaria
`curate_events` (`04_merge_daily.sql`, `06:00 UTC`) si no existe. Debe correrse **después** de
`05_lake_bucket.sh`. La carga inicial (`99_initial_load.sql`) se ejecuta a mano, una sola vez.

**Estado reportado:** este script se corrió parcialmente — el dataset, la conexión y el permiso
quedaron creados, pero la tabla externa `raw_events` falló porque el bucket todavía no tenía
ningún objeto (bucket vacío = sin particiones Hive que leer). Es relanzable: reintentar después de
la primera subida real de eventos.

## 9. `infra/07_pubsub_teardown.sh` (decisión U2: Pub/Sub se borra)

**Destructivo.** Pide confirmación interactiva (`escribe SI para continuar`) y nunca usa
`--force`: si `gcloud services disable` reporta dependencias, el script se detiene en vez de
forzar. Antes de ejecutarlo hay que verificar a mano que `dbraspberry/detecciones_por_tiempo` siga
vacía (si tiene documentos nuevos, algo sigue publicando y hay que investigar antes de borrar
nada). Borra, en orden: los triggers de Eventarc (requiere `EVENTARC_TRIGGERS`, si no los lista y
se detiene), el servicio Cloud Run legacy `guardar-detecciones-firestore`, las suscripciones de
Pub/Sub asociadas (requiere `PUBSUB_SUBSCRIPTIONS`, igual) y el topic `yolo-detections`, el rol
`run.invoker` y la SA `eventarc-trigger-invoker`, y finalmente deshabilita `eventarc.googleapis.com`
y `pubsub.googleapis.com`.

## 10. `infra/08_cleanup_billing.sh`

1. Política de limpieza de Artifact Registry (`infra/policies/ar-cleanup.json`: conservar las 3
   versiones más recientes, borrar lo que tenga más de 30 días) sobre el repo
   `cloud-run-source-deploy`. Recuerda revisar a mano el repo `gcr.io` (imagen del servicio legacy).
2. Lifecycle de 7 días (`infra/policies/sources-lifecycle.json`) sobre los buckets de fuentes de
   Cloud Build que existan (`<proyecto>_cloudbuild`, `run-sources-<proyecto>-<región>`).
3. Crea la SA de build `cloud-build-deployer` con `roles/run.builder` (decisión Q5).
4. Paso aparte, **solo con `REMOVE_COMPUTE_EDITOR=1`** y tras confirmar que un deploy con
   `--build-sa` funcionó: quita `roles/editor` a la SA default de Compute
   (`540868081764-compute@developer.gserviceaccount.com`).

Termina recordando lo que no se automatiza: crear la alerta de presupuesto (umbrales 25/50/90/100 %
+ pronóstico al 100 %, monto según `docs/COSTS.md`), agregar el correo del usuario como
destinatario en Billing → Manage access (la automatización de este paso ya fue bloqueada dos
veces por el modo seguro de la sesión), y decidir sobre el proyecto huérfano `woven-surface-
469602-g1` ("My First Project").

## 11. Orden recomendado

```
00_prereqs.sh → 05_lake_bucket.sh → 02_iam_runtime.sh → 01_secrets.sh → deploy_cloud.sh
            → 04_firestore.sh → bootstrap_admin.py → 06_bigquery.sh → 07_pubsub_teardown.sh
            → 08_cleanup_billing.sh
```

(`01_secrets.sh` puede ir antes de `02_iam_runtime.sh`; ese script solo necesita que el secreto y
el bucket existan, en cualquier orden entre ellos. `deploy_cloud.sh` necesita los secretos y la SA
de runtime ya creados.)

## 12. Qué NO ejecuta ningún script

- Las reglas de Firestore (`infra/firestore.rules`): se publican a mano en la consola, en ambas
  bases.
- El cliente OAuth (Google Auth Platform → Clients → Web application): se crea a mano; el
  **client ID** resultante (no es secreto) se pasa como segundo argumento a `deploy_cloud.sh`.
- Copiar `device-token` a la Pi: el usuario ejecuta él mismo `gcloud secrets versions access
  latest --secret=device-token` y lo pega en `/etc/epp-edge/edge.env` — ningún script ni agente lee
  ese valor.
- La alerta de presupuesto y su destinatario (Billing → Budgets & alerts / Manage access).
