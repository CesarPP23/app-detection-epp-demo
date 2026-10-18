# Nodo con pose (edge_pose/) integrado con la nube 2.0.0

**Para quién es:** quien integre el nodo de la tesis (Marcelo) con la nube de este repositorio. Explica qué se
integró en el nodo, cómo probarlo sin tocar Google Cloud, cómo conectarlo a la nube real y qué conviene
adaptar del lado de la nube. **Fecha:** 16 de octubre de 2026.

> **Estado 2026-10-16 (Cesar):** este nodo ya está en producción en `raspberry-pi-01`, reemplazando al
> nodo de una sola clase (`raspberrypi/`), que quedó apagado en esa Pi. Antes de fusionar se revisó
> `cloud_v2.py`/`state_machine.py` completos y se aplicó un arreglo de robustez — ver
> `edge_pose/CAMBIOS_CESAR.md` (AS-IS/TO-BE, qué se evaluó y qué no se tocó). La sección 3 de abajo
> ("Conectarlo a la nube real") ya se ejecutó tal cual con una diferencia: se usó `device.id =
> raspberry-pi-01` (no `pi-envasado-01`) para reutilizar el dispositivo y las credenciales que ya
> existían, en vez de dar de alta uno nuevo. La sección 4 ("qué queda pendiente") sigue vigente sin
> cambios: el detalle de evento con `cumple`/`faltantes`/`mal_colocados` no se pintó en el frontend —
> decisión explícita de mantener el alcance acotado por ahora.


## 0. Modelos (no se versionan)

`edge_pose/models/` está en `.gitignore`. Hay dos formas de obtener los tres modelos NCNN que usa el nodo:

1. Copiarlos del paquete de despliegue de la tesis (`08 Raspberry Pi/epp-edge/models/`): `epp_yolov8n_640_ncnn_model/`,
   `pose_yolov8n_320_ncnn_model/` y, opcional, `epp_yolo11n_640_ncnn_model/`.
2. Regenerarlos en Linux o en la Pi (Windows con Smart App Control bloquea `torch`, ver `docs/MODEL.md`):
   ```bash
   cd training && pip install -r requirements.txt
   python export.py --epp-weights ../torch/best.pt --tag yolov8n   # EPP 640 (el best.pt de docs/MODEL.md) y pose 320 -> edge_pose/models/
   ```

## 1. Qué es este nodo y qué cambió

La lógica del modelo es la de la tesis y **no cambia**:

- YOLOv8n entrenado con nuestro dataset (3 clases: Cofia, Guante, Mascarilla), exportado a NCNN 640.
- YOLOv8n-pose (NCNN 320) + ByteTrack.
- Máquina de estados IDLE → PERSON_CHECK → ACTIVE con sustracción de fondo MOG2.
- Reglas geométricas de colocación por persona y por mano.
- Agregación por **episodio**: una presencia continua es un episodio con veredicto (cumple / no cumple), EPP
  faltantes, mal colocados y estado por persona.

Lo que se añadió es `edge_pose/epp_edge/cloud_v2.py`: el nodo publica sus episodios usando **exactamente los servicios y
protocolos de la versión 2.0.0** de tu repositorio, en lugar de la API `epp-api` del prototipo.

| Pieza de tu nube | Cómo la usa el nodo |
|---|---|
| Contrato de evento v2 + `event_id` UUIDv7 | Cada episodio cerrado recibe un UUIDv7 una sola vez (se conserva en todos los reintentos) |
| Outbox idempotente | Tabla `outbox_v2` en la misma SQLite del nodo; etapas `images → sidecar → firestore`; espera `min(300, 2^n)` s |
| Lago de datos (bronce) | `raw/images` (frame original sin anotar), `raw/thumbs` (miniatura anotada, 480 px), `raw/events` (sidecar NDJSON); particiones `device=`/`dt=` con la fecha de Lima; `if_generation_match=0` |
| Firestore `dbraspberry` | `detecciones_confirmadas/{event_id}` + `daily_stats/{device}_{fecha}` en un `batch.commit()` atómico (`Conflict` = ya estaba) |
| Firestore `(default)` | Lee `device_control/{device}` (`desired_state`, `stream_requested`, `lease_renewed_at`, `lease_seconds`) con `on_snapshot` + sondeo de respaldo; escribe `device_health/{device}` cada 300 s y en cada cambio |
| Relay de video | Solo con *lease* vigente: `wss://<host>/ws/video/{device}` con `Authorization: Bearer DEVICE_TOKEN`; mensaje = 8 bytes (uint64 BE, ms) + JPEG 640 px, calidad 60, ≤ 8 fps |
| Pausa desde el panel (rol operator) | `desired_state = stopped` → el nodo cierra el episodio en curso (`closed_reason = stopped`) y deja de inferir; la outbox sigue vaciándose |

### Cómo se ve un episodio en tu contrato

- `nombre_clase` = `episodio_conforme` (clase_id 100) o `episodio_no_conforme` (101). Tu panel ya lo muestra (como
  «episodio conforme» / «episodio no conforme», porque `classLabel()` reemplaza `_` por espacio) y `daily_stats.by_class`
  lo cuenta: el gráfico «Por clase» queda como conformes frente a no conformes.
- `confianza_promedio` / `confianza_max`: promedio y máximo de las detecciones de EPP del episodio.
- `frame_count`, `start_ts`, `end_ts`, `duration_seconds`, `closed_reason` (`gap` | `stopped` | `shutdown`),
  `is_simulated` (fuente = archivo de video).
- `detections_at_capture`: cajas de Cofia / Guante / Mascarilla (clase_id 0/1/2) del frame de mayor puntaje, con
  `bbox_xyxy_norm`. Tu detalle de evento las dibuja sobre la imagen original.
- `edge`: `edge_version = pose-2.0.0`, `model_version` = SHA-256 corto de `model.ncnn.bin`, `camera_source`.
- **Campos adicionales del episodio** (tu API los ignora sin romperse; quedan en Firestore y en el sidecar):
  `cumple`, `faltantes`, `mal_colocados`, `no_evaluables`, `personas_max`, `n_personas`, `personas` (estado por persona
  y por mano), `ratios` (cofia, guante, mascarilla), `epp_fps`, `zona`, `episodio_local_id`.

## 2. Probarlo sin Google Cloud (5 minutos, cualquier PC)

El modo `emulate_dir` escribe el lago y Firestore en una carpeta local, con la misma semántica (solo crear, batch
atómico, incrementos):

```bash
cd edge_pose
pip install -r requirements-pi.txt            # en la PC: ultralytics, ncnn, opencv, google-cloud-*
python -m pytest tests -q                     # 15 pruebas, incluidas 8 de la integración
# en epp_edge/config.yaml (o una copia): cloud_v2.emulate_dir: "../data/e2e/cloud"
python -m epp_edge.main --config <copia>.yaml --source <video de planta>.mp4 --max-seconds 70
```

Resultado esperado en `edge_pose/data/e2e/cloud/`:

```
gcs/epp-detection-v1-lake/raw/images/device=pi-envasado-01/dt=YYYY-MM-DD/<event_id>.jpg
gcs/epp-detection-v1-lake/raw/thumbs/device=pi-envasado-01/dt=YYYY-MM-DD/<event_id>.jpg
gcs/epp-detection-v1-lake/raw/events/device=pi-envasado-01/dt=YYYY-MM-DD/<event_id>.json
firestore/dbraspberry/detecciones_confirmadas/<event_id>.json
firestore/dbraspberry/daily_stats/pi-envasado-01_YYYY-MM-DD.json
firestore/default/device_health/pi-envasado-01.json
```

Verificado el 16-oct-2026 con los videos de planta: IMG_9704 → `episodio_conforme`; IMG_9901 →
`episodio_no_conforme` con `faltantes = ["Guante"]` (el falso incumplimiento documentado en la tesis).

## 3. Conectarlo a la nube real

1. **Dispositivo permitido.** El `device_id` del nodo es `pi-envasado-01` (`config.yaml → device.id`). Agrégalo a
   `ALLOWED_DEVICES` del servicio y crea su `device_control` (con `infra/bootstrap_admin.py` o a mano, `desired_state:
   running`). Si prefieres usar `raspberry-pi-01`, cambia `device.id` en el nodo.
2. **Credenciales en el Pi** (carpeta `edge_pose/secrets/`, fuera de git, permisos 600):
   - `gcp-key.json`: clave de la cuenta `raspberry-pi-worker` (`storage.objectCreator` en el bucket +
     `datastore.user`). El nodo no la necesita para nada más.
   - `device_token.txt`: `gcloud secrets versions access latest --secret=device-token`.
3. **`config.yaml → cloud_v2`**: `orchestrator_host` (host de Cloud Run, sin `https://`), `project_id`,
   `lake_bucket`, bases de Firestore. Quita `emulate_dir`.
4. `bash install.sh` en el Pi (instala `google-cloud-storage` y `google-cloud-firestore`) y revisa
   `journalctl -u epp-edge@epp -f`: debe aparecer «Nube 2.0.0 activa» y, tras cada presencia, «Episodio … publicado
   como evento …».
5. Verifica con tu `raspberrypi/tools/reconcile_lake.py --device pi-envasado-01 --date <día>`: daily_stats =
   documentos = sidecars, e imágenes = eventos con captura.

## 4. Lo que ya se adaptó en este repositorio y lo que queda

Incluido en esta rama (revisar y ajustar):

1. **Etiquetas del panel** (`frontend/src/design/classColors.ts`): `Cofia → Cofia`, `Guante → Guantes`,
   `Mascarilla → Mascarilla`, `episodio_conforme → Cumple EPP`, `episodio_no_conforme → No cumple EPP`.
   (Hasta el modelo `epp_final_v2`/2026-10-18 el modelo entregaba la clase como `"Gorra"` y esta tabla la
   traducía a `"Cofia"` para el panel; el modelo actual ya entrega `"Cofia"` directamente, así que ahora es
   una traducción identidad — se deja explícita por consistencia con las otras clases.)
2. **BigQuery** (`infra/bigquery/06_episodios.sql`): recrea `raw_events` con las columnas del episodio (la tabla actual
   usa `ignore_unknown_values = TRUE`, así que hoy no se rompe, pero esos campos no llegan a BigQuery) y crea la vista
   `v_cumplimiento_diario` (episodios, conformes, tasa de cumplimiento y EPP más faltante por día).

Pendiente (no se tocó):

3. **Detalle del evento**: mostrar `cumple`, `faltantes`, `mal_colocados` y el estado por persona. Requiere añadir esos
   campos opcionales a `EventDetail` (`backend/epp_api/schemas.py`), regenerar `schema.d.ts` (`pnpm gen:api`) y
   pintarlos en `features/events`.
4. **Resumen**: un KPI «tasa de cumplimiento» = conformes / episodios del periodo (con `daily_stats.by_class` alcanza).
5. Si se mantiene tu nodo por clase (`raspberrypi/`) en paralelo, distinguir ambos por `edge.edge_version`.

## 5. Archivos del nodo

```
edge_pose/epp_edge/cloud_v2.py       integración con la nube 2.0.0 (outbox, uploader, control, salud, video, emulación)
edge_pose/epp_edge/state_machine.py  guarda el frame original del mejor momento, sus cajas y las confianzas del episodio
edge_pose/epp_edge/main.py           arranque de cloud_v2, pausa remota, vaciado de la outbox al apagar
edge_pose/epp_edge/config.yaml       sección cloud_v2 (la API del prototipo queda como modo legado: cloud.enabled)
edge_pose/tests/test_cloud_v2.py     8 pruebas: UUIDv7, rutas del lago, contrato, publicación idempotente, reintentos, lease, protocolo
```

Privacidad: `privacy.blur_faces: true` difumina la región de la cabeza también en la imagen original que sube al lago
(recomendado si el sistema opera con trabajadores reales; ver `docs/PRIVACY.md` de tu repositorio).
