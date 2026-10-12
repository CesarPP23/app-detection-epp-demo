# Arquitectura

**Para quién es:** quien necesite entender cómo está armado el sistema para modificarlo, depurarlo
o evaluarlo (otro desarrollador, un revisor de la tesis). **Última verificación:** 2026-10-11.

Fuente de diseño: `docs/PLAN_REMEDIACION.md` secciones 1, 5, 6, 7 y 15. Este documento describe el
código tal como quedó implementado en `backend/epp_api/` y `raspberrypi/epp_edge/`; donde el plan y
el código difieren en un detalle menor, manda el código (se anota en el texto).

## 1. Componentes

### 1.1 Borde — `raspberrypi/epp_edge/`

Un solo proceso (`python -m epp_edge`, entrypoint `app.py`) con varios hilos que se comunican por
dos estructuras en memoria (`LatestSlot`, uno por tipo de dato) y una base SQLite local (el
outbox). Ningún hilo bloquea a otro: el productor siempre sobrescribe, el consumidor siempre lee
lo más reciente.

| Hilo / componente | Archivo | Rol | Slot que produce / consume |
|---|---|---|---|
| `CaptureThread` | `capture.py` | Lee la cámara (picamera2 / USB / archivo) con reintento y backoff si falla | produce en `frames: LatestSlot[Frame]` |
| `InferenceWorker` | `inference.py` | Corre YOLO (NCNN, `imgsz=416`) a lo sumo a `INFERENCE_FPS`; calcula `model_version` (SHA-256 del modelo) | consume `frames`, produce en `results: LatestSlot[Inference]`, llama a `Pipeline._on_result` |
| `DetectionAggregator` | `aggregator.py` | Convierte detecciones frame a frame en sesiones por clase (anti-ruido); una sesión por **clase**, no por persona | no es un hilo: lo invoca `_on_result` bajo `_agg_lock` |
| `StreamWorker` | `streamer.py` | Sube video por WebSocket a Cloud Run, **solo** si `control.should_stream()` es verdadero | consume `frames` y `results` (para dibujar las cajas de la última inferencia sobre el frame más fresco) |
| `Uploader` | `outbox.py` | Hilo que corre siempre (también con la detección pausada); vacía el outbox hacia GCS y Firestore | consume la tabla `outbox` de SQLite |
| `DeviceControl` | `control.py` | Listener (`on_snapshot`) de `device_control`; decide `should_run()` / `should_stream()`; conserva el último estado si Firestore falla | hilo de respaldo (`control-safety`) que relee cada `CONTROL_SAFETY_POLL_SECONDS` |
| `HealthReporter` | `control.py` | Escribe `device_health` cada `HEARTBEAT_SECONDS` y en cada transición (debounce de 2 s) | hilo propio |

`Pipeline` (en `app.py`) agrupa captura + inferencia + streaming + agregador y se crea/destruye
según `desired_state`; al detenerse cierra las sesiones abiertas con `flush_all()` para no perderlas
(hallazgo D1 del plan). El bucle principal de `main()` también llama `sd_notify("WATCHDOG=1")` solo
si `pipeline.healthy()`, de modo que systemd reinicia el proceso si la inferencia se cuelga.

### 1.2 Nube — `backend/epp_api/` (Cloud Run, servicio `orchestrator-service`)

Un solo servicio FastAPI con `--workers 1` y `max-instances=1` (el estado del relay de video vive
en memoria de proceso: dos instancias significarían "pantalla negra" para quien mire el video si
cae en la otra). Módulos:

| Módulo | Rol |
|---|---|
| `relay.py` | `/ws/video/{device_id}`: recibe los frames de la Pi y los publica en un `DeviceChannel` en memoria (un `asyncio.Condition` por dispositivo) |
| `live.py` | `/api/devices/{id}/live`: WebSocket al navegador; reenvía lo que publica `relay.py`, acota a `LIVE_MAX_FPS` y gestiona el lease de streaming |
| `lease.py` | `StreamingLease`: cuenta espectadores por dispositivo; con ≥1 escribe `device_control.stream_requested=True` y renueva el lease cada `LEASE_SECONDS/3`; con 0 lo apaga |
| `auth.py` | ID token de Google → cookie de sesión firmada (`itsdangerous`); CSRF; roles; autenticación de WebSockets |
| `repo.py` | Acceso a Firestore con caché incremental compartida entre usuarios (sin listeners: la instancia no tiene CPU entre requests con `--cpu-throttling`) |
| `lake.py` | URLs firmadas v4 de GCS sin archivo de key (`signBlob` de la propia SA) |
| `audit.py` | Escritura asíncrona de `audit_log` (no bloquea la request que la dispara) |
| `routers/{auth,devices,events,admin}.py` | Endpoints REST (contrato en `docs/API.md`) |
| `main.py` | Cablea los routers, monta la SPA estática y expone `/healthz` |

### 1.3 Navegador — `frontend/src/`

React 19 + TypeScript + Vite. Habla con el backend solo por `api/client.ts` (REST) y `api/live.ts`
(WebSocket de video). `hooks/ActivityProvider.tsx` apaga los `refetchInterval` de TanStack Query y
cierra el WebSocket de video cuando el usuario está inactivo (`useIdle`, 15 min) o la pestaña está
oculta — es lo que mantiene el costo de Cloud Run bajo con el dashboard abierto.

## 2. Secuencia: abrir "En vivo"

```mermaid
sequenceDiagram
    participant U as Usuario (navegador)
    participant FE as SPA (LivePlayer)
    participant CR as Cloud Run (live.py + lease.py)
    participant FS as Firestore (default)/device_control
    participant PI as Pi (DeviceControl + StreamWorker)
    participant RL as Cloud Run (relay.py)

    U->>FE: abre /live
    FE->>CR: WS /api/devices/{id}/live (cookie epp_session)
    CR->>CR: authenticate_ws (cookie + Origin == PUBLIC_BASE_URL)
    CR->>FS: stream_requested=true, lease_renewed_at=now (StreamingLease.acquire)
    FS-->>PI: on_snapshot notifica el cambio (listener en vivo)
    PI->>PI: control.should_stream() = true
    PI->>RL: WS /ws/video/{id} (Authorization: Bearer DEVICE_TOKEN)
    loop cada 1/STREAM_FPS
        PI->>RL: frame (8 bytes captured_at + JPEG)
        RL->>CR: DeviceChannel.publish() -> notify_all()
        CR->>FE: frame binario (acotado a LIVE_MAX_FPS)
        FE->>FE: createImageBitmap + canvas; actualiza fps/latencia "≈"
    end
    loop cada LEASE_SECONDS/3
        CR->>FS: renew_lease() -> lease_renewed_at=now
    end
    U->>FE: cierra la pestaña o entra en inactividad
    FE->>CR: cierra el WebSocket
    CR->>FS: stream_requested=false (StreamingLease.release, último espectador)
    PI->>PI: el lease vence (<= LEASE_SECONDS) -> should_stream() = false
    PI->>RL: cierra el WebSocket de uplink
```

Puntos de diseño: la Pi nunca abre el stream sola (salvo `AUTO_STREAM_ON_DETECTION=true`, apagado
por defecto); el relay nunca sondea, el `asyncio.Condition` despierta al espectador en cuanto llega
un frame nuevo; si el espectador es lento, `next_frame()` siempre entrega el **más reciente** (se
saltan frames, nunca se acumulan).

## 3. Secuencia: evento confirmado (captura → lake → Firestore → BigQuery)

```mermaid
sequenceDiagram
    participant INF as InferenceWorker
    participant AGG as DetectionAggregator
    participant CAP as captures.build_capture
    participant OUT as Outbox (SQLite)
    participant UP as Uploader
    participant GCS as GCS data lake
    participant FS as Firestore dbraspberry
    participant BQ as BigQuery epp_lake

    INF->>AGG: update(inference, frame)
    AGG->>AGG: ¿clase sostenida >= MIN_SECONDS_TO_CONFIRM y >= MIN_FRAMES_TO_CONFIRM?
    AGG-->>INF: ClosedEvent (al cerrar la sesión por gap, stop o cámara perdida)
    INF->>CAP: build_capture(ClosedEvent)
    CAP->>CAP: encode original.jpg + thumb.jpg, calcula sha256, arma capture.uri/thumb_uri
    CAP->>OUT: outbox.put(payload, pending_dir)  [INSERT OR IGNORE, idempotente por event_id]
    loop Uploader.run() (siempre corriendo)
        UP->>OUT: due(10)
        UP->>GCS: blob.upload_from_filename(..., if_generation_match=0)
        Note over UP,GCS: PreconditionFailed = ya existía: éxito idempotente
        UP->>OUT: advance(event_id, "sidecar")
        UP->>GCS: sube raw/events/.../{event_id}.json
        UP->>OUT: advance(event_id, "firestore")
        UP->>FS: batch.create(detecciones_confirmadas/{event_id}) + daily_stats Increment
        Note over UP,FS: Conflict (AlreadyExists) = ya estaba escrito: éxito
        UP->>OUT: ack(event_id)  [borra la fila y el directorio pendiente]
    end
    Note over BQ: consulta programada diaria (04_merge_daily.sql, 06:00 UTC)
    GCS->>BQ: raw_events (tabla externa) + raw_images (object table)
    BQ->>BQ: MERGE por event_id -> epp_lake.events (curada)
```

El orden imágenes → sidecar → Firestore garantiza que si el documento existe en Firestore, su
captura **ya** está en el lake. Si falta el frame de mejor confianza o el disco se queda sin
espacio, el evento **nunca se descarta**: se registra con `capture.status` = `missing` o
`dropped_disk_full` (ver `docs/DATA_MODEL.md`).

## 4. Modos de falla

| Falla | Comportamiento |
|---|---|
| Sin red (Pi) | `Uploader` sigue encolando y reintentando con backoff exponencial (`min(300, 2**attempts)` segundos); `device_health.outbox_pending` crece; nada se pierde mientras haya espacio en disco |
| Disco lleno (`OUTBOX_MAX_BYTES`) | Se borran las **imágenes pendientes** más viejas (`enforce_cap`), nunca los eventos; quedan con `capture.status="dropped_disk_full"` |
| Sin cámara | `CaptureThread` reintenta con backoff (1, 2, 4… 60 s); si no hay cámara y `ALLOW_TEST_VIDEO` no está activo, **nunca** cae al video de prueba: `camera_ok=false` y cero eventos (corrige el hallazgo D2 del plan) |
| Cámara se pierde con una sesión abierta | `Pipeline.tick()` cuenta cuánto lleva caída; al superar `SESSION_GAP_SECONDS` cierra las sesiones confirmadas con `closed_reason="camera_lost"` |
| Firestore no responde (control) | `DeviceControl` conserva el último `desired_state`/lease conocido; solo usa el valor por defecto si nunca hubo una lectura exitosa (corrige D6) |
| Apagado / reinicio del servicio (`SIGTERM`) | `Pipeline.stop("shutdown")` cierra las sesiones abiertas con `flush_all()`, el `Uploader.drain()` tiene hasta 10 s para subir lo pendiente antes de que el proceso termine |
| Instancia de Cloud Run cae con el relay activo | El consumidor (`live.py`) recibe `frame=None` en `next_frame` y manda `{"type":"status","state":"ended"}`; la SPA no muestra un frame viejo como "en vivo" |
| Cliente de "En vivo" se desconecta abruptamente | `StreamingLease.release` es idempotente; `asyncio.shield` en `live.py` garantiza que el lease se libera aunque el handler se cancele |
| Espectador lento | `relay.next_frame` y `live._pump` siempre entregan el frame más reciente: un cliente lento se salta frames, nunca acumula atraso |

## 5. Presupuesto de latencia

Estimaciones comparadas con el flujo anterior (Pub/Sub + polling + Flet); **a validar con
mediciones reales en la Fase 10** (`docs/PLAN_REMEDIACION.md` sección 16).

| Tramo | Antes | Después (estimado, pendiente de medir) |
|---|---|---|
| Arranque del stream | 0-5 s (polling de la Pi) + 0-2 s (dashboard) + cold start | < 1 s (listener de Firestore) + cold start (1-3 s) |
| Captura → frame disponible | Crecía con el buffer de V4L2 | ≤ 1 frame (`buffer=1`, `LatestSlot`) |
| Frame → envío | Captura + inferencia + dibujo + JPEG en serie | Dibujo + JPEG (~10-20 ms a 640 px); la inferencia corre en paralelo en otro hilo |
| Relay (Cloud Run) | 0-30 ms (polling cada 30 ms) | ~0 ms (`asyncio.Condition.notify_all`) |
| Saltos de red del video | Pi → Cloud Run → backend del dashboard → navegador | Pi → Cloud Run → navegador (un salto menos) |
| Entrega al navegador | Polling a 8 fps + base64 por el WebSocket de Flet + re-render de Flutter | WebSocket binario, reenvío inmediato (tope `LIVE_MAX_FPS`), `createImageBitmap` + `<canvas>` |
| Primera carga del dashboard | Runtime de Flutter/CanvasKit (varios MB) | SPA < 300 KB gzip; después solo `index.html` y JSON de la API (assets con hash cacheados un año) |

Región: se mantiene `us-central1`. Moverla a Sudamérica bajaría el RTT desde Perú pero encarece
cómputo y egress; solo se reconsideraría si una medición real muestra que la red es el cuello de
botella (no se ha hecho esa medición).
