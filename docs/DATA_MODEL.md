# Modelo de datos

**Para quién es:** quien escriba o lea datos del sistema (backend, borde, BigQuery) o investigue un
evento. **Última verificación:** 2026-10-16 (agrega el contrato de episodio de `edge_pose/`, nodo
activo en `raspberry-pi-01` desde esa fecha — ver sección 1-bis).

Dos bases de Firestore en el proyecto `epp-detection-v1`, sin migración de datos entre ellas
(decisión U1 de `docs/PLAN_REMEDIACION.md`):

- **`dbraspberry`** — eventos y agregados (`EVENTS`/`EVENTS` en `backend/epp_api/repo.py`,
  `EVENTS_COLLECTION`/`STATS_COLLECTION` en `raspberrypi/epp_edge/outbox.py`).
- **`(default)`** — control, salud, usuarios del dashboard y auditoría.

## 1. Contrato v2 del evento (sidecar JSON del lake = documento de Firestore)

Lo construye `raspberrypi/epp_edge/aggregator.py` (campos base) + `captures.py` (sección
`capture`). Es la misma estructura en los tres lugares: el JSON de `raw/events/.../{event_id}.json`
en GCS, el documento `detecciones_confirmadas/{event_id}` en Firestore (con `start_ts`/`end_ts`/
`capture.captured_at` como `Timestamp` nativo en vez de string, y `created_at` agregado por el
servidor) y la fila de `epp_lake.raw_events`/`epp_lake.events` en BigQuery.

```json
{
  "schema_version": 2,
  "event_id": "0192f6c4-8a1e-7c3b-9f2d-4b8e1a2c3d4e",
  "event_type": "deteccion_confirmada",
  "device_id": "raspberry-pi-01",
  "clase_id": 3,
  "nombre_clase": "latex_gloves",
  "confianza_promedio": 0.8048,
  "confianza_max": 0.9312,
  "frame_count": 30,
  "start_ts": "2026-10-10T14:03:11.204Z",
  "end_ts": "2026-10-10T14:03:20.388Z",
  "duration_seconds": 9.18,
  "is_confirmed": true,
  "closed_reason": "gap",
  "is_simulated": false,
  "capture": {
    "status": "ok",
    "uri": "gs://epp-detection-v1-lake/raw/images/device=raspberry-pi-01/dt=2026-10-10/0192f6c4-....jpg",
    "thumb_uri": "gs://epp-detection-v1-lake/raw/thumbs/device=raspberry-pi-01/dt=2026-10-10/0192f6c4-....jpg",
    "captured_at": "2026-10-10T14:03:15.870Z",
    "sha256": "…", "width": 1280, "height": 720, "bytes": 184233,
    "thumb_sha256": "…", "thumb_bytes": 28110,
    "frame_seq": 81234,
    "confidence_at_capture": 0.9312
  },
  "detections_at_capture": [
    {"clase_id": 3, "nombre_clase": "latex_gloves", "confianza": 0.9312, "bbox_xyxy_norm": [0.41, 0.52, 0.55, 0.71]},
    {"clase_id": 0, "nombre_clase": "hairnet", "confianza": 0.77, "bbox_xyxy_norm": [0.38, 0.05, 0.52, 0.18]}
  ],
  "edge": {"edge_version": "2.0.0", "model_version": "a1b2c3d4e5f6", "camera_source": "picamera2"}
}
```

### Campos

| Campo | Tipo | Notas |
|---|---|---|
| `schema_version` | int | `2` actualmente. Cualquier cambio de contrato lo incrementa y se documenta aquí. |
| `event_id` | string (UUIDv7) | Generado por `ids.uuid7()`; ID del documento, nombre base de los objetos de GCS y clave en BigQuery. |
| `clase_id` / `nombre_clase` | int / string | Vienen del modelo YOLO (`model.names`). |
| `confianza_promedio` / `confianza_max` | float [0-1] | Promedio y máximo de la sesión. |
| `frame_count` | int | Inferencias que contribuyeron (una por tick, aunque haya dos cajas de la misma clase en el mismo frame). |
| `start_ts` / `end_ts` | Timestamp / ISO-8601 Z | `end_ts` es la hora de la **última** detección, consistente con `duration_seconds` (corrige el hallazgo D5: antes `end_ts` usaba el gap de cierre pero la duración se medía hasta la última detección). |
| `duration_seconds` | float | `(end_ts - start_ts)`. |
| `is_confirmed` | bool | Siempre `true` en esta colección (solo se persisten sesiones confirmadas). |
| `closed_reason` | string | `gap` (se dejó de ver la clase), `stopped` (se pausó el dispositivo), `shutdown` (apagado del servicio), `camera_lost`, o `legacy` (fila migrada desde la tabla vieja `offline_detections`, ver `docs/DATA_LAKE.md`). |
| `is_simulated` | bool | `true` si `CAMERA_SOURCE=file:...` (requiere `ALLOW_TEST_VIDEO=true`). La API y la UI lo ocultan por defecto. |
| `capture.status` | string | `ok` \| `missing` (no había frame o falló la codificación) \| `dropped_disk_full` (se descartó por `OUTBOX_MAX_BYTES`) \| `missing_legacy` (migración) \| `expired` (calculado por la API cuando `end_ts` supera `LAKE_IMAGE_RETENTION_DAYS`; nunca se guarda así). |
| `capture.uri` / `thumb_uri` | string (`gs://...`) | `null` si `status != ok`. |
| `detections_at_capture` | array | **Todas** las cajas del frame elegido (de todas las clases), no solo la de la clase del evento: es el contexto completo de la captura. |
| `edge` | object | `edge_version` (de `epp_edge/__init__.py`), `model_version` (SHA-256 corto del modelo NCNN), `camera_source`. |

## 1-bis. Contrato de episodio (edge_pose, nodo activo desde 2026-10-16)

`edge_pose/epp_edge/cloud_v2.py::build_event()` arma el **mismo contrato v2** de la sección 1 (y
usa el mismo outbox idempotente, el mismo esquema de nombres de `lake_path`, el mismo
`detections_at_capture`) pero a partir de un **episodio** (presencia continua de una o más
personas) en vez de una sesión por clase. Para no tener que tocar la API ni el panel, el episodio
se disfraza de detección:

- `event_type` = `"episodio"` (en vez de `"deteccion_confirmada"`).
- `clase_id` / `nombre_clase` = `100` / `"episodio_conforme"` si todas las personas cumplen,
  `101` / `"episodio_no_conforme"` si alguna no — así `daily_stats.by_class` y los gráficos por
  clase del panel lo cuentan sin cambios de código (`EPISODE_CLASS` en `cloud_v2.py`).
- `detections_at_capture` trae las cajas de Cofia/Guante/Mascarilla del frame de mejor confianza
  (igual que en el contrato de un solo modelo), para que el visor de cajas del panel siga
  funcionando igual.
- Los campos propios del episodio viajan **además** de los de arriba; la API y el panel los
  ignoran porque no los conocen, pero quedan en Firestore y en BigQuery (columnas agregadas por
  `06_episodios.sql`, ver `docs/DATA_LAKE.md`):

| Campo | Tipo | Notas |
|---|---|---|
| `zona` | string \| null | Zona/etiqueta de la cámara, si está configurada en el nodo. |
| `cumple` | bool | Veredicto final del episodio (determina `clase_id`/`nombre_clase` de arriba). |
| `faltantes` | array\<string\> | EPP que nunca se detectó en ninguna persona evaluable del episodio. |
| `mal_colocados` | array\<string\> | EPP detectado pero con colocación incorrecta (`placement.py`) en algún momento. |
| `no_evaluables` | array\<string\> | EPP que no se pudo evaluar (p. ej. mano fuera de cuadro, ángulo malo). |
| `personas_max` | int | Máximo de personas simultáneas vistas durante el episodio. |
| `n_personas` | int | Personas distintas (por id de ByteTrack) evaluadas en el episodio. |
| `personas` | array\<object\> | Detalle por persona (id de tracking, veredicto, EPP evaluado) — ver `edge_pose/epp_edge/state_machine.py::PersonStats`. |
| `ratios.{cofia,guante,mascarilla}` | float [0-1] \| null | Fracción de frames evaluables en los que esa prenda estuvo bien puesta, por tipo de EPP, agregado de todas las personas. |
| `epp_fps` | float \| null | FPS real del modelo de EPP durante el episodio (puede ser menor que el nominal si `ACTIVE` compite por CPU con `Pose`). |
| `episodio_local_id` | int | ID autoincremental de la fila en la SQLite local del nodo (`epp_edge.db`), para correlación con logs del dispositivo. |

Esto resuelve, **solo para los dispositivos que corren `edge_pose/`**, la limitación D9 descrita en
la sección 3: el episodio ya es una unidad por grupo de personas presentes (con veredicto y conteo
`n_personas`/`personas_max`), no una sesión por nombre de clase. `raspberrypi/` (nodo de respaldo,
no desplegado) sigue teniendo la limitación D9 tal cual está documentada.

## 2. Colecciones de Firestore

### `dbraspberry` (eventos)

| Colección | Documento | Quién escribe | Campos clave |
|---|---|---|---|
| `detecciones_confirmadas` | `{event_id}` | solo la Pi (`Uploader._firestore`, `batch.create`) | contrato v2 completo + `created_at` (`SERVER_TIMESTAMP`) |
| `daily_stats` | `{device_id}_{YYYY-MM-DD}` | solo la Pi (mismo batch, `merge=True`) | `device_id`, `date`, `events` (`Increment`), `seconds` (`Increment`), `by_class.{nombre_clase}` (`Increment`), `updated_at` |
| `training_sessions` | `{session_id}` (`{device_id}-{ts_ms}`, lo genera el backend) | el backend la crea (`repo.start_training_capture`); la Pi la actualiza y la cierra (`edge_pose/epp_edge/training_capture.py:CaptureWorker`, `merge=True`) | `device_id`, `session_id`, `started_at`, `started_by`, `frame_count` (`Increment`, cada ~5 s), `status` (`capturing`\|`completed`), `ended_at`, `closed_reason` (`stopped_by_operator`\|`max_duration`) |

Índices compuestos (creados por `infra/04_firestore.sh`, 5 en total):
- `detecciones_confirmadas`: `device_id ASC, end_ts DESC` (lista general) y
  `device_id ASC, nombre_clase ASC, end_ts DESC` (filtro por clase).
- `detecciones_confirmadas`: `device_id ASC, end_ts ASC` (agregado 2026-10-16: lo usa
  `repo.py:_newer_than` para la comparación "¿hay eventos más nuevos que X?" del polling del
  panel; Firestore exige dirección exacta por campo en consultas con rango + orden, así que el
  índice `DESC` de la lista general no sirve para esta — faltaba y causaba 500 intermitentes).
- `daily_stats`: `device_id ASC, date ASC`.
- `training_sessions`: `device_id ASC, started_at DESC` (mismo motivo que el de `detecciones_confirmadas`
  de arriba: `repo.py:list_training_sessions` filtra por `device_id` y pagina/ordena por
  `started_at` descendente — sin el índice, `/api/devices/{id}/training-captures` da 500).

### `(default)` (control, salud, usuarios, auditoría)

| Colección | Documento | Quién escribe | Campos clave |
|---|---|---|---|
| `device_control` | `{device_id}` | solo el backend (`repo.py`: `set_desired_state`, `set_stream_requested`, `renew_lease`, `start_training_capture`, `stop_training_capture`) | `desired_state` (`running`\|`stopped`), `stream_requested` (bool), `lease_renewed_at` (`SERVER_TIMESTAMP`), `lease_seconds`, `capture_requested` (bool, toggle persistente, sin lease), `capture_session_id` (lo lee la Pi para saber qué sesión de `training_sessions` abrir/cerrar), `capture_requested_by`, `updated_by`, `updated_at` |
| `device_health` | `{device_id}` | solo la Pi (`control.HealthReporter`) | `reported_state`, `streaming`, `camera_ok`, `camera_source`, `last_seen` (`SERVER_TIMESTAMP`), `inference_fps`, `inference_ms_p50/p95`, `cpu_temp_c`, `outbox_pending`, `outbox_bytes`, `edge_version`, `model_version` |
| `dashboard_users` | `{email en minúsculas}` | solo el backend (`routers/admin.py`) | `role` (`viewer`\|`operator`\|`admin`), `active` (bool), `created_by`, `created_at` |
| `audit_log` | autogenerado (`.add()`) | solo el backend (`audit.py`) | `ts`, `email`, `action`, `target`, `detail`, `expire_at` (TTL de Firestore a 365 días, `infra/04_firestore.sh`) |

Acciones auditadas (`action`): `login`, `login_denied`, `logout` (no se escribe explícitamente hoy;
la cookie se borra sin más), `desired_state`, `live_start`, `live_end`, `capture_original`,
`training_capture_start`, `training_capture_stop`, `training_capture_download`, `user_create`,
`user_update`, `user_delete`.

### Campos y colecciones obsoletos

| Nombre | Estado |
|---|---|
| `(default)/device_status/{device_id}` | Reemplazado por `device_control` + `device_health` (separar "deseado" de "real" corrige la ambigüedad del campo `state` de doble uso de la versión anterior). Se borra 30 días después del primer deploy exitoso con OK del usuario (handoff, sección 7, pendiente). |
| `dbraspberry/detecciones_por_tiempo` | Colección del pipeline Pub/Sub + Eventarc legacy, ya eliminado (decisión U2). Debe seguir vacía; `infra/07_pubsub_teardown.sh` lo verifica antes de borrar la tubería. |
| `offline_detections` (tabla SQLite local de la Pi, no Firestore) | Cola offline de la versión anterior, sin captura. `outbox.Outbox._migrate_legacy()` la importa fila por fila (asignando un `event_id` nuevo y `capture.status="missing_legacy"`) y la elimina (`DROP TABLE`) en el primer arranque de la versión nueva. |

## 3. Reglas anti-ruido (líneas de tiempo de ejemplo)

El agregador (`raspberrypi/epp_edge/aggregator.py`) mantiene **una sesión activa por nombre de
clase** (`_ActiveSession`), no por persona (limitación D9 del plan; dos personas con guantes se ven
como una sola sesión de `latex_gloves`). Este apartado describe ese nodo de respaldo
(`raspberrypi/`, no desplegado). El nodo activo (`edge_pose/`) no tiene este problema: ver
sección 1-bis — ahí la unidad es el episodio (grupo de personas presentes), con `n_personas` y
veredicto por persona en vez de una sesión por clase.

```
Parámetros de ejemplo: MIN_SECONDS_TO_CONFIRM=1.0  MIN_FRAMES_TO_CONFIRM=3  SESSION_GAP_SECONDS=4.0

Caso 1 — ruido descartado (parpadeo de un solo frame):
  t=0.0s  hairnet aparece (frame 1)
  t=0.2s  hairnet desaparece
  -> a los 4.2s sin verse, se cierra sin haber llegado a MIN_SECONDS_TO_CONFIRM: noise_discarded_count += 1,
     no genera evento ni captura.

Caso 2 — evento confirmado con una oclusión corta tolerada:
  t=0.0s  latex_gloves aparece
  t=1.0s  frame_count=3 y han pasado >= 1.0s -> CONFIRMADO (se loguea "Evento CONFIRMADO en vivo")
  t=3.5s  oclusión momentánea (la mano sale del cuadro)
  t=5.0s  reaparece (gap de 1.5s < SESSION_GAP_SECONDS=4.0) -> SIGUE SIENDO LA MISMA SESIÓN (mismo event_id)
  t=9.18s última detección; a partir de aquí, 4.0s sin verse -> se cierra con closed_reason="gap"
  -> un solo evento, end_ts = t=9.18s (última detección, no el momento del cierre), duration_seconds = 9.18.

Caso 3 — cierre por pausa o apagado con una sesión abierta (corrige D1):
  Una sesión confirmada está en curso cuando el usuario pausa el dispositivo o el proceso recibe SIGTERM.
  -> Pipeline.stop(reason) llama a aggregator.flush_all(reason); la sesión se cierra con
     closed_reason="stopped" o "shutdown" en vez de perderse al destruir el agregador.
```

La captura (imagen + miniatura) corresponde siempre al frame de **mayor confianza** visto durante
la sesión (`best_frame`/`best_detections`), no al primer ni al último frame.
