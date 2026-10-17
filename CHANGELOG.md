# Changelog

**Para quién es:** quien quiera entender de un vistazo qué cambió entre la versión original de la
tesis y la remediada. **Última verificación:** 2026-10-11.

Formato libre (no es un proyecto con versionado semántico de biblioteca); cada entrada resume una
revisión mayor del sistema. La versión de referencia de cada componente vive en
`backend/epp_api/__init__.py`, `raspberrypi/epp_edge/__init__.py` y `frontend/package.json`
(todas en `2.0.0`).

## 2.0.0 — 2026-10 (remediación de auditoría)

Reescritura completa frente a la primera versión de la tesis, siguiendo
`docs/PLAN_REMEDIACION.md` (fases 0 a 9). Resumen de lo que cambió y por qué:

### Eliminado

- **Pub/Sub + Eventarc** como transporte de eventos (`gcp/data/pubsub_to_firestore.py`, el topic
  `yolo-detections`, el servicio `guardar-detecciones-firestore`): no se usaba para los eventos
  confirmados (esos ya escribían directo a Firestore) y quedaba como vector de abuso sin valor
  (decisión U2; retirado por `infra/07_pubsub_teardown.sh`).
- **Dashboard en Flet** (`front-end/`): reemplazado por una SPA en React + TypeScript. Cada
  pestaña de Flet mantenía un WebSocket permanente que facturaba Cloud Run sin importar si había
  video activo; el runtime de Flutter/CanvasKit se descargaba completo en cada sesión.
- **`device_status`** (campo `state` de doble uso: estado deseado y estado real a la vez):
  separado en `device_control` (lo escribe el backend) y `device_health` (lo escribe la Pi).
- **`FRAME_SKIP`**: el pipeline de inferencia ya no se gobierna saltando frames por conteo; el
  `InferenceWorker` corre a un `INFERENCE_FPS` configurado, desacoplado de la captura por un
  `LatestSlot`.
- **Streaming automático por detección**: ya no se abre el video solo porque hubo una detección
  confirmada (sigue existiendo detrás de `AUTO_STREAM_ON_DETECTION`, apagado por defecto). El
  streaming ahora es bajo demanda, atado a un lease de quien mira "En vivo" (ver ADR 0003).
- **MJPEG público sin autenticación** (`/video_stream/{device_id}`): el dashboard anterior no
  pedía login; cualquiera con la URL veía video y capturas de trabajadores.
- Código muerto: `raspberrypi/worker.py`, `utils/detector.py`, `utils/suscriber.py`,
  `utils/check_firestore.py`, `utils/create.py`, `scripts/create_database.py`, `gcp/data/`,
  `raspberrypi/script-rpi-v0.1.zip` (contenía una credencial filtrada, ver `docs/SECURITY.md`),
  `riesgos.txt` (su contenido pasó a la documentación operativa).

### Agregado

- **Contrato v2 del evento** con `event_id` UUIDv7 único en Firestore, GCS y BigQuery (ADR 0006),
  y **una captura por evento confirmado** (imagen original + miniatura anotada), con
  `capture.status` para nunca descartar un evento por una captura fallida.
- **Data lake** en GCS (`raw/images`, `raw/thumbs`, `raw/events`) + BigQuery (`epp_lake`: tabla
  externa, object table, tabla curada con MERGE diario, vistas gold) — ver `docs/DATA_LAKE.md` y
  ADR 0005.
- **Outbox local idempotente** en la Pi (SQLite + `if_generation_match=0` + `event_id` fijo):
  corrige la duplicación por reintento de la cola anterior (ADR 0004).
- **Autenticación real del dashboard**: Google Identity Services + cookie de sesión propia, roles
  (`viewer`/`operator`/`admin`), lista blanca (`dashboard_users`), CSRF, auditoría (`audit_log`)
  — ver `docs/SECURITY.md` y ADR 0008.
- **Streaming bajo demanda con lease** renovado por el servidor mientras haya al menos un
  espectador en "En vivo" (ADR 0003).
- **Servicio unificado** (`orchestrator-service`): API + relay de video + SPA en el mismo origen,
  sin CORS (ADR 0001).
- **Frontend reescrito en React 19 + TypeScript + Vite** con sistema de diseño propio
  (`docs/DESIGN_SYSTEM.md`), accesibilidad WCAG 2.2 AA, tests (Vitest, Testing Library,
  Playwright) — ADR 0007.
- Infraestructura como scripts idempotentes y comentados en `infra/` (secretos, SAs, Firestore,
  bucket del lake, BigQuery, limpieza de costos), todos con `DRY_RUN=1`.
- Suite de tests: 88 tests en `raspberrypi/tests/` (incluido el simulador de cámara), 70 en
  `backend/tests/`.
- Documentación completa: este `CHANGELOG.md`, `docs/ARCHITECTURE.md`, `docs/DATA_MODEL.md`,
  `docs/DATA_LAKE.md`, `docs/CLOUD_DEPLOYMENT.md`, `docs/SECURITY.md`, `docs/PRIVACY.md`,
  `docs/COSTS.md`, `docs/API.md`, `docs/adr/0001` a `0008`.
- **Nodo de borde con dos modelos** (`edge_pose/`, 2026-10-16): sustituye en la Pi física al nodo
  de una sola clase (`raspberrypi/`, que queda en el repo sin desplegar). Detección de EPP +
  estimación de pose humana (YOLOv8n-pose + ByteTrack) para asociar cada EPP a la persona que lo
  lleva puesto y validar su colocación; máquina de estados de bajo consumo
  (IDLE/PERSON_CHECK/ACTIVE); episodios con veredicto de cumplimiento por persona y por mano.
  Publica en la misma nube 2.0.0 (contrato v2, outbox idempotente, UUIDv7) representando cada
  episodio como una pseudo-detección (`episodio_conforme`/`episodio_no_conforme`) para que el
  panel lo muestre sin cambios de backend. Detalle completo en `docs/INTEGRACION_NODO_POSE.md` y
  `edge_pose/CAMBIOS_CESAR.md`.

### Corregido

- **D1** — las sesiones abiertas del agregador ya no se pierden al apagar o pausar el
  dispositivo: `flush_all()` las cierra y las manda al outbox.
- **D2** — sin cámara disponible y sin `ALLOW_TEST_VIDEO=true`, el sistema ya no cae en silencio
  al video de prueba: se registra `camera_ok=false` y cero eventos.
- **D5** — `end_ts` y `duration_seconds` ahora son consistentes (ambos se calculan desde la
  última detección real de la sesión).
- **D6** — un error transitorio de Firestore al leer `device_control` ya no apaga la detección:
  se conserva el último estado conocido.
- **S2** — el token de uplink de la Pi se compara con `hmac.compare_digest` (tiempo constante) y
  nunca pasa si falta la cabecera (antes, `None != None` dejaba pasar una conexión sin token).
- **S4** — el token ya no viaja en la URL (`?token=`): va como cabecera `Authorization: Bearer`.
- **C1/C6** — el relay de video ya no reenvía el mismo frame por polling y el servicio tiene un
  techo explícito de instancias (`max-instances=1`).
- **Índice de Firestore faltante** (2026-10-16) — `/api/devices/{id}/summary` y `/events` daban
  500 en cuanto una instancia de Cloud Run cacheaba una ventana de eventos y pedía lo nuevo; el
  índice compuesto `detecciones_confirmadas(device_id asc, end_ts asc)` que esa consulta necesita
  no existía (el otro índice, descendente, no sirve para una consulta con filtro de rango).
- **`theme-init.js` con MIME type incorrecto** (2026-10-16) — solo `/assets/` estaba montado como
  estático; cualquier otro archivo suelto del build (como el script que aplica el tema antes del
  primer render) caía en el catch-all de la SPA y se servía como `index.html`.

### Seguridad (incidentes ya resueltos)

Dos credenciales de servicio quedaron expuestas en el historial de git en commits anteriores a
esta versión (`243d2de`, 2025-06-10, y `3f95ee1`, 2026-09-23). Ambas fueron **revocadas**; el
historial de git no se reescribió (regla dura del proyecto). Detalle completo en
`docs/SECURITY.md`.
