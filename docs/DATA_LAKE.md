# Data lake

**Para quién es:** quien consulte BigQuery, investigue una pérdida de datos o extienda el lake.
**Última verificación:** 2026-10-16.

Bucket `gs://epp-detection-v1-lake` (región `us-central1`) + dataset de BigQuery `epp_lake`. Diseño
completo en `docs/PLAN_REMEDIACION.md` sección 9 (Fase 5); lo que sigue describe lo ya implementado
en `raspberrypi/epp_edge/outbox.py` y `edge_pose/epp_edge/cloud_v2.py` (mismo contrato de subida en
los dos nodos de borde — ver `docs/ARCHITECTURE.md` sección 0), `infra/05_lake_bucket.sh`,
`infra/06_bigquery.sh` e `infra/bigquery/*.sql`.

## 1. Zonas y layout en GCS

```
raw/                                   # BRONCE: inmutable, escrito solo por la Pi (create-only)
  images/device=<id>/dt=<YYYY-MM-DD>/<event_id>.jpg   # frame original sin anotar (no estructurado)
  thumbs/device=<id>/dt=<YYYY-MM-DD>/<event_id>.jpg   # miniatura anotada (la que muestra el dashboard)
  events/device=<id>/dt=<YYYY-MM-DD>/<event_id>.json  # sidecar NDJSON de 1 línea (estructurado, contrato v2)
exports/                               # opcional: datasets armados a mano para etiquetado/reentrenamiento
```

- Particiones estilo Hive (`device=`, `dt=`); las claves son `device`/`dt`, **no** `device_id`/
  `date`, para no chocar con los campos homónimos del JSON al declarar la tabla externa de
  BigQuery con `WITH PARTITION COLUMNS`.
- `dt` es la fecha **local** (`SITE_TIMEZONE`, por defecto `America/Lima`) del campo `end_ts` del
  evento (`outbox.lake_prefix` / `outbox.local_day`), no la fecha UTC.
- Los objetos se suben con `if_generation_match=0` (`Uploader._process` en `outbox.py`): la Pi solo
  puede **crear**, nunca sobrescribir ni borrar (`roles/storage.objectCreator`, nada más). Un
  reintento sobre un objeto que ya existe recibe `PreconditionFailed`, que el código trata como
  éxito — la subida es idempotente sin necesitar permiso de lectura.
- **Plata** (curada) y **oro** (marts) viven en BigQuery, no como archivos nuevos en el bucket.

### Metadata de cada imagen

`outbox.image_metadata()` escribe estos pares como metadata de objeto (`x-goog-meta-*`) en cada
`.jpg` subido, para que la imagen "se describa sola" aunque se pierda el resto del sistema:

| Clave | Contenido |
|---|---|
| `event_id` | UUIDv7 del evento |
| `device_id` | Dispositivo que la generó |
| `nombre_clase` | Clase del evento |
| `confianza_max` | Confianza máxima de la sesión |
| `model_version` | SHA-256 corto del modelo usado |
| `captured_at` | Hora de captura (ISO-8601 Z) |
| `sha256` | Hash de la imagen original (o `thumb_sha256` para la miniatura) |
| `kind` | `original` \| `thumb` |

## 2. Inmutabilidad y permisos

- Bucket creado con `--uniform-bucket-level-access --public-access-prevention --soft-delete-duration=7d`
  (`infra/05_lake_bucket.sh`).
- SA de la Pi (`raspberry-pi-worker`): **solo** `roles/storage.objectCreator` en este bucket (más
  `roles/datastore.user` a nivel de proyecto, para Firestore). No puede leer, listar ni borrar
  objetos del lake — ver el riesgo residual documentado en `docs/SECURITY.md`.
- SA de runtime del backend (`platform-runtime`): `roles/storage.objectViewer`, solo para poder
  firmar URLs de lectura temporal (`lake.py`); nunca lista el bucket completo.
- SA de la conexión de BigQuery (`lake-conn`, ver más abajo): `roles/storage.objectViewer`, la
  añade `infra/06_bigquery.sh` tras leer el `serviceAccountId` real de la conexión con
  `bq show --connection` (no es un valor fijo: lo asigna GCP al crear la conexión).

## 3. Retención (60 días para imágenes, U6)

`infra/policies/lake-lifecycle.json`, aplicado por `infra/05_lake_bucket.sh`:

```json
{"rule": [
  {"action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
   "condition": {"age": 30, "matchesPrefix": ["raw/images/", "raw/thumbs/"]}},
  {"action": {"type": "Delete"},
   "condition": {"age": 60, "matchesPrefix": ["raw/images/", "raw/thumbs/"]}}
]}
```

Las imágenes pasan a Nearline a los 30 días y se borran a los 60 (Nearline exige una duración
mínima de 30 días para no cobrar por borrado anticipado: 30 + 30 = 60 cumple exacto). Los JSON de
`raw/events/` **no tienen regla de expiración**: no contienen datos personales (son estructurados,
sin imagen) y son la base para reportes históricos.

Cuando una imagen se borra por esta política, su evento y su JSON siguen existiendo con
`capture.uri` apuntando a un objeto que ya no está. La API (`backend/epp_api/lake.py`,
`capture_status()`) lo calcula en el momento: si `stored == "ok"` pero `end_ts` tiene más de
`LAKE_IMAGE_RETENTION_DAYS` (60 por defecto), responde `"expired"` en vez de `"ok"`, y el router de
eventos devuelve `410 captura_expirada` con el mensaje "Captura eliminada por política de
retención (60 días)" en vez de un error genérico.

## 4. BigQuery (`infra/bigquery/*.sql`)

Dataset `epp_lake`, conexión `lake-conn` (`CLOUD_RESOURCE`, región `us-central1`), creados y
aplicados por `infra/06_bigquery.sh` en este orden:

1. **`01_raw_events.sql`** — tabla externa `raw_events` sobre `raw/events/*` (NDJSON, particiones
   Hive `device`/`dt`). Es la capa bronce: un documento por evento, tal cual lo escribió la Pi.
2. **`02_raw_images.sql`** — object table `raw_images` sobre `raw/images/*`
   (`object_metadata = 'SIMPLE'`, `metadata_cache_mode = 'AUTOMATIC'`, refresco cada 24 h). Columnas
   disponibles: `uri`, `generation`, `content_type`, `size`, `md5_hash`, `updated`, `metadata`
   (incluye los `x-goog-meta-*` de la sección 1, entre ellos `event_id`) y `ref` (apunta al objeto
   real; sirve para pasarlo a funciones de ML de BigQuery más adelante).
3. **`03_events_curated.sql`** — tabla `events` (plata), particionada por `DATE(end_ts)` y agrupada
   por `device_id, nombre_clase`; se crea vacía (`WHERE FALSE`) y la puebla el MERGE.
4. **`04_merge_daily.sql`** — MERGE idempotente de los últimos 3 días: deduplica por `event_id`
   (`ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY end_ts DESC)`) e inserta solo lo que falta en
   `events`. Programado como consulta diaria (`bq mk --transfer_config`, `schedule="every day
   06:00"`, zona UTC — después de la medianoche de Lima). **`99_initial_load.sql`** es el mismo
   MERGE sin filtro de fecha, para la carga inicial (se ejecuta una sola vez a mano).
5. **`05_views.sql`** — vistas oro:
   - `v_events_with_capture`: `events` enriquecida con `capture_object_bytes`, `capture_uploaded_at`
     y `capture_ref` desde `raw_images` (join por `uri`).
   - `v_daily_by_class`: eventos, segundos, confianza media y cobertura de capturas (`COUNTIF(
     capture.status = 'ok')`) por dispositivo, día (hora de Lima) y clase — excluye eventos
     simulados.
6. **`06_episodios.sql`** (agregado 2026-10-16, para `edge_pose/`) — recrea `raw_events` con las
   columnas propias del episodio al final del esquema (`zona`, `cumple`, `faltantes`,
   `mal_colocados`, `no_evaluables`, `personas_max`, `n_personas`, `ratios`, `epp_fps`,
   `episodio_local_id` — ver `docs/DATA_MODEL.md` sección 1-bis), hace el `ALTER TABLE ... ADD
   COLUMN IF NOT EXISTS` equivalente sobre `events` (plata), y crea la vista oro
   `v_cumplimiento_diario` (tasa de cumplimiento y EPP más faltante, por dispositivo y día de
   Lima). Para los eventos del nodo por clase (`raspberrypi/`) estas columnas quedan en `NULL`. Se
   ejecuta después de `01` y `03` porque depende de que ambas tablas ya existan; el MERGE diario
   (`04_merge_daily.sql`) usa `INSERT ROW`, por lo que el esquema de `raw_events` y `events` debe
   coincidir exactamente — de ahí que las columnas nuevas se agreguen en el mismo orden en los dos
   `ALTER`/`CREATE`.

### Estado al cierre de la sesión 2026-10-16

`06_bigquery.sh` (incluido `06_episodios.sql`) corrió completo sobre datos reales: el dispositivo
`raspberry-pi-01` ya tenía eventos subidos al lake para cuando se ejecutó, así que `raw_events` se
creó sin el problema de partición-sin-archivos que había en el primer intento (sesión 2026-10-11,
con el bucket todavía vacío). `99_initial_load.sql` se corrió una vez a mano y
`raspberrypi/tools/reconcile_lake.py` confirmó **coherencia** entre Firestore, GCS y
`epp_lake.events` para el día verificado (sección 5). Pendiente, no bloqueante: programar
`04_merge_daily.sql` como consulta diaria vía `bq mk --transfer_config` requiere un flujo OAuth
interactivo con pegado de código de verificación que no funciona ni por la herramienta de shell de
Claude Code ni por `!`-comandos en esta máquina — se documentó como paso manual único en la consola
de BigQuery (`docs/HANDOFF_CLAUDE_CODE.md` sección 7).

## 5. Reconciliación (control de calidad y pérdida de datos)

`raspberrypi/tools/reconcile_lake.py` compara, para un dispositivo y un día local:

1. `daily_stats.events` (Firestore, contador incremental).
2. Número de documentos de `detecciones_confirmadas` con `end_ts` ese día.
3. Número de objetos `raw/events/.../dt=<día>/*.json`.
4. Número de objetos `raw/images/.../dt=<día>/*.jpg`.
5. Número de filas en `epp_lake.events` para ese día (informativo: se actualiza con el MERGE
   diario, así que un desfase aquí no es necesariamente un error).

Criterio de coherencia: 1 = 2 = 3, y 4 = eventos con `capture.status == "ok"`. Cualquier
`event_id` que falte en alguna capa se imprime explícitamente.

```bash
# Solo necesita permisos de LECTURA; se puede correr desde cualquier PC con ADC configurado.
python raspberrypi/tools/reconcile_lake.py --date 2026-10-10 --device raspberry-pi-01
python raspberrypi/tools/reconcile_lake.py --date 2026-10-10 --skip-bq   # sin tocar BigQuery
python raspberrypi/tools/reconcile_lake.py --dry-run                     # datos de prueba, sin GCP
```

Código de salida: `0` si las capas 1-4 son coherentes, `1` si hay diferencias (la capa 5 solo
genera avisos, nunca falla el chequeo). Recomendado como chequeo semanal
(`docs/OPERATIONS_RUNBOOK.md`, Fase 6.4) y obligatorio después de cualquier corte de red largo o
cambio en el pipeline de subida.

## 6. Usos previstos

- **Reportes de cumplimiento**: `v_daily_by_class` da eventos, segundos con EPP y confianza media
  por día y clase, lista para graficar en el dashboard o exportar.
- **Auditoría de un evento concreto**: `event_id` → documento de Firestore (o fila de
  `epp_lake.events`) → `capture.uri` → imagen original con sus cajas. El mismo identificador
  conecta las tres capas.
- **Reentrenamiento**: exportar a `exports/<dataset>/` las imágenes con `confianza_max` entre 0.5 y
  0.7 (las dudosas) junto con sus cajas normalizadas (`detections_at_capture[].bbox_xyxy_norm`), en
  formato YOLO, para etiquetarlas a mano y mejorar el modelo.
- **Evaluación por versión del modelo**: filtrar por `edge.model_version` para comparar el
  desempeño de dos versiones del modelo sobre el mismo periodo.

## 7. Qué no hacer con el lake

Ver `docs/PLAN_REMEDIACION.md` sección 17: no dar a la Pi permisos de lectura o borrado sobre el
bucket; no escribir archivos curados a mano (la capa plata/oro vive en BigQuery); no tratar la
capa 5 de la reconciliación como un error duro (depende del horario del MERGE); no asumir que un
evento sin imagen (`capture.status != "ok"`) es un bug — puede ser una pérdida real documentada
(`missing`, `dropped_disk_full`) o una retención vencida (`expired`), y en ningún caso se descarta
el evento por eso.
