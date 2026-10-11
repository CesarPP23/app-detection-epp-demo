# Plan de auditoría y remediación — `app-detection-epp-demo`

> **Documento de ejecución para un agente de código.** Está escrito para seguirse en orden,
> fase por fase, con criterios de aceptación verificables al final de cada una.
> Auditoría: 2026-10-10 · Revisión 3 (incorpora las decisiones del usuario del mismo día:
> dashboard por internet, data lake con captura por evento, borrado de Pub/Sub y frontend
> reescrito en TypeScript/React con backend en Python).
> Commit base: `c75bb91` (+ cambios sin commitear en `front-end/core/config.py` y
> `gcp/orchestrator_main.py`, que este plan **reemplaza**: no deben desplegarse).

---

## 0. Reglas para el agente ejecutor (leer antes de tocar nada)

1. **El repositorio es PÚBLICO y debe seguir público** (proyecto de tesis). Nunca escribas
   secretos, tokens, claves privadas ni el contenido de `.env` en ningún archivo versionado,
   commit, log, issue o PR.
2. **NO reescribas el historial de git** (`git filter-repo`, `push --force`, BFG). Las
   credenciales filtradas se neutralizan **revocándolas**. No lo vuelvas a proponer.
3. **Toda mutación en GCP requiere el OK explícito del usuario antes de ejecutarse**, aunque
   esté en este plan. Antes de cada bloque marcado con 🔒 **GCP**, muéstrale los comandos exactos
   y espera la confirmación. El `gcloud` local tiene el token roto (`invalid_grant`): pídele que
   ejecute `! gcloud auth login` y `! gcloud config set project epp-detection-v1` antes de la
   Fase 0.
4. Todas las decisiones de producto ya están tomadas (secciones 3.1 y 3.2). Las de la sección 3.3
   se ejecutan con su **valor por defecto, sin preguntar**. Solo se detiene la ejecución para
   pedir el OK de los bloques 🔒 **GCP** y para las acciones manuales del usuario listadas en 3.4.
5. Trabaja en la rama `remediacion/auditoria-2026-10`. Un commit por sub-fase, mensajes en
   español en modo imperativo (sigue el estilo de `git log`).
6. Al final de cada fase corre sus **criterios de aceptación**. Si uno falla, para y reporta.
7. No subas `min-instances` por encima de 0. El servicio cloud **debe** tener
   `max-instances=1` (el estado del video vive en memoria).
8. Al terminar una sesión de pruebas con streaming real: detener procesos, dejar el dispositivo
   en `stopped`, y **revisar de nuevo el costo en Billing → Reports al día siguiente** (la
   facturación tarda hasta 24 h en reflejarse).
9. Se trabaja **en el proyecto existente `epp-detection-v1`**, con sus bases actuales:
   `(default)` para control/estado y `dbraspberry` para eventos. **No migrar datos entre bases.**

---

## 1. Resumen ejecutivo

El sistema funciona como demo, pero **no está listo para operar 24/7 ni para exponerse a
internet**. Hay cuatro fuentes de costo que crecen sin control, dos vulnerabilidades críticas de
credenciales, una pérdida de datos reproducible, un pipeline de visión que serializa
captura → inferencia → envío, y un dashboard sin autenticación.

### 1.1 Arquitectura objetivo

```
┌──────────────────────── Raspberry Pi (24/7, systemd) ────────────────────────┐
│ CaptureThread ─► LatestSlot ─┬─► InferenceWorker (NCNN, imgsz 416, ≤5 fps)   │
│ (picamera2/USB, buffer=1)    │      └─► Aggregator ─► Outbox SQLite (WAL)     │
│                              │           (event_id = UUIDv7)   │              │
│                              │                                 ▼              │
│                              │                   Uploader (idempotente):      │
│                              │     1) imagen original + miniatura ──────────► GCS data lake (raw/)
│                              │     2) sidecar JSON del evento ──────────────► GCS data lake (raw/)
│                              │     3) evento + daily_stats ─────────────────► Firestore dbraspberry
│                              └─► StreamWorker (solo con lease activo)         │
│                                   640px · q60 · ≤8 fps · sin deflate ─────────┼─┐
│ DeviceControl ◄── on_snapshot (default)/device_control                       │ │
│ HealthReporter ─► (default)/device_health  (cada 5 min + cambios)            │ │
└───────────────────────────────────────────────────────────────────────────────┘ │
                                                                                   │ wss (DEVICE_TOKEN)
┌──────── Cloud Run "orchestrator-service" (max-instances=1, 1 worker) ────────┐  │
│ backend/ — FastAPI (Python)                                                  │◄─┘
│  ├─ /ws/video/{device}        uplink de la Pi; relay en memoria (solo frames NUEVOS)
│  ├─ /api/auth/*               Google Identity Services → cookie de sesión firmada
│  ├─ /api/...                  REST JSON (resumen, eventos, dispositivo, admin) con
│  │                            caché incremental compartida sobre Firestore
│  ├─ /api/devices/{id}/live    WebSocket al navegador (JPEG binario); abre/renueva el lease
│  ├─ /api/events/{id}/capture  redirección a URL firmada de GCS (10 min)
│  └─ /*                        SPA estática (frontend/dist), assets con hash e immutable
└──────────────────────────────────────────────────────────────────────────────┘
            ▲ HTTPS (mismo origen: sin CORS)                │ lectura
   frontend/ — React + TypeScript (Vite) en el navegador     │
   · polling con TanStack Query (se pausa con la pestaña oculta y por inactividad)
   · video en <canvas> desde el WebSocket; capturas con cajas en SVG
                                                    BigQuery `epp_lake`
                                                   ├─ raw_events  (externa, JSON del lake)
                                                   ├─ raw_images  (object table, imágenes)
                                                   ├─ events      (curada, MERGE diario por event_id)
                                                   └─ vistas gold (cumplimiento diario, eventos+captura)
```

### 1.2 Principios de diseño

- **Un solo identificador para todo:** `event_id` (UUIDv7, ordenable por tiempo) es el ID del
  documento en Firestore, el nombre de los objetos en GCS y la clave de BigQuery. Permite
  cruzar la data estructurada con la no estructurada sin ambigüedad.
- **Streaming bajo demanda real:** la Pi sube video **solo** mientras alguien autenticado está en
  la pestaña "En vivo" (lease renovado por el servidor). Una detección **no** abre el stream:
  genera el evento y su captura.
- **Un solo salto de red para el video:** Pi → Cloud Run → navegador. La API, el relay y la SPA
  viven en el mismo servicio y en el mismo origen: sin segunda descarga MJPEG, sin token de
  espectador y sin CORS.
- **Frontend y backend separados por contrato:** la SPA (TypeScript) solo habla con la API
  REST/WebSocket. Los tipos del frontend se generan desde el OpenAPI de FastAPI, así que un cambio
  de contrato rompe el build y no falla en silencio en producción.
- **Lecturas mínimas contra GCP:** la Pi usa un listener de Firestore (tiene CPU propia). El
  backend usa una caché incremental compartida entre usuarios: solo pide a Firestore los
  documentos más nuevos que los que ya tiene. No usa listeners porque, con CPU solo durante
  requests, quedarían congelados entre una request y otra.
- **Outbox transaccional en el borde:** todo evento y su imagen se escriben primero en disco
  local y luego se suben con escrituras idempotentes. Ni pérdidas ni duplicados.
- **Mínimo privilegio y sin archivos de key en la nube:** Cloud Run usa una SA asociada (sin
  key). La SA de la Pi solo puede **crear** objetos en el lake: no puede leerlos ni borrarlos.
- **Fail-closed:** si falta un secreto, el servicio no arranca. Si el usuario no está en la lista
  blanca, no ve nada.

---

## 2. Hallazgos priorizados

**P0** = arreglar ya. **P1** = antes de producción/exposición a internet. **P2** = robustez y
calidad. **P3** = higiene.

### 2.1 Costos (GCP)

| ID | Sev | Hallazgo (evidencia) | Impacto | Fase |
|---|---|---|---|---|
| C1 | P0 | `gcp/orchestrator_main.py:107-119`: `viewer_stream_generator` reenvía el **mismo** frame cada 30 ms (≈33 fps) aunque la Pi envíe 5-10 fps, y **nunca termina** cuando la Pi se desconecta (`latest_frames` jamás se limpia). | Egress ×3-6 y requests huérfanas que mantienen la instancia facturando. Es la causa más probable del cargo de S/.25 del 2026-09-20. | 1 |
| C2 | P0 | `front-end/main.py:374-376`: cada pestaña ejecuta `get_recent_detections(limit=200)` cada 7 s, más `get_device_state()` cada 2 s por pestaña y otra vez en el lector global. | ≈2.47 M lecturas de Firestore por día **por pestaña abierta**. Ahora que el dashboard va a internet, el costo escala con cada usuario. | 3 |
| C3 | P0 | `front-end/main.py:78-111`: el lector global descarga el stream mientras `state == "streaming"` **aunque no haya ninguna pestaña abierta**. | Egress sin nadie mirando. | 3 |
| C4 | P0 (prod) | `raspberrypi/main.py:345-348`: cualquier detección confirmada abre el stream. Con la Pi 24/7, el stream queda abierto casi toda la jornada. | Cloud Run cobra por cada segundo de WebSocket abierto: ~US$0.09/h con 1 vCPU, ≈ US$27/mes con 10 h/día. | 2 |
| C5 | P1 | `raspberrypi/main.py:354`: JPEG a resolución completa con q70 para el stream. | 3-5× más bytes de los necesarios. | 2 |
| C6 | P1 | Cloud Run sin `max-instances` explícito (default 100) con estado en memoria. | Sin techo de costo y bug funcional (la Pi y el espectador en instancias distintas = pantalla negra). | 0, 4 |
| C7 | P1 | Dashboard en internet: con Flet, cada pestaña abierta mantiene un WebSocket permanente y con él la instancia facturando. | Una pestaña olvidada = ~US$65/mes de cómputo. Se resuelve con una SPA que hace requests cortas (la instancia solo factura mientras responde), WebSocket solo en "En vivo" y pausa por inactividad (Fase 3). | 3 |
| C13 | P2 | Flet Web descarga un runtime pesado (Flutter/CanvasKit) en cada carga y renderiza cada frame como base64 a través de su propio WebSocket. | Más egress por visita, mayor latencia de video y una UX limitada (sin `<canvas>` ni MJPEG nativo). | 3 |
| C8 | P1 | Tubería legacy (Eventarc + `guardar-detecciones-firestore` + topic `yolo-detections`) activa sin uso; la SA de la Pi (con keys filtradas) tiene `Pub/Sub Publisher`. | Vector de abuso y recursos ociosos. **Decisión del usuario: borrar.** | 0, 4 |
| C9 | P2 | Polling de `device_status` cada 5 s en la Pi (`main.py:263-274`) = 17 280 lecturas/día. | Lecturas sin valor. | 2 |
| C10 | P2 | Timeout de Cloud Run de 300 s: los WebSockets (Pi y video al navegador) se cortan cada 5 min. | Reconexiones y huecos en el video. | 4 |
| C11 | P2 | Budget alert solo a administradores de facturación, umbral S/.75. | Aviso tardío. | 4 |
| C12 | P3 | Artifact Registry y bucket de fuentes de Cloud Build sin limpieza; proyecto huérfano `My First Project`; access log de uvicorn duplicado. | Costo pequeño acumulativo. | 1, 4 |

### 2.2 Seguridad y fuga de información

| ID | Sev | Hallazgo (evidencia) | Impacto | Fase |
|---|---|---|---|---|
| S1 | **P0** | `raspberrypi/script-rpi-v0.1.zip` está versionado (commit `3f95ee1`, en GitHub) y contiene `gcp-key.json` de `raspberry-pi-worker` con `private_key_id` que empieza en **`52096d10`**. No es la key del incidente anterior (`609ac33d…`, commit `243d2de`) ni la actual (`b78e4fdf…`). | Si sigue activa: lectura/escritura en Firestore y publicación en Pub/Sub para cualquiera. | 0 |
| S2 | **P0** | `gcp/orchestrator_main.py:49-50,69-70`: `if auth_token != UPLINK_TOKEN`. Si falta la variable, `None != None` es falso y una conexión **sin header** pasa. La comparación no es de tiempo constante. | Bypass total del uplink. | 1 |
| S3 | **P0** | Dashboard en internet **sin autenticación** (`front-end/main.py:625`): cualquiera vería video y capturas de trabajadores y podría pausar la detección. | Fuga de datos personales y sabotaje. | 3 |
| S4 | P1 | Cambio sin commitear: el token va como `?token=` en la URL y se expone en el botón "Abrir en pestaña nueva". Es el mismo token de la Pi. | Token en los logs de Cloud Run, en el historial del navegador y en capturas de pantalla. | 1, 3 |
| S5 | P1 | `front-end/core/firestore_client.py:64`: el dashboard usa por defecto la **key de la Pi**. | Credencial compartida entre dos superficies. | 3, 4 |
| S6 | P1 | El orquestador sirve el último frame a espectadores nuevos aunque la Pi se haya ido. | Imagen vieja de personas presentada como "en vivo". | 1 |
| S7 | P1 | Imágenes de trabajadores en un data lake: datos personales sin retención, control de acceso ni aviso definidos. | Cumplimiento de la Ley N.º 29733 (Perú) y de la ética de la tesis. | 5, 8 |
| S8 | P2 | SA default de Compute con rol `Editor`. | Escalada de privilegios. | 4 |
| S9 | P2 | `.gitignore` ignora `*.json`/`*.yml` (oculta índices, políticas y workflows) y no hay escaneo de secretos; la key del ZIP pasó sin ser detectada. | Fugas futuras. | 7 |
| S10 | P2 | Reglas de seguridad de Firestore sin verificar en `(default)` y `dbraspberry`. | Acceso por API cliente si están abiertas. | 4 |
| S11 | P3 | `raspberrypi/gcp-key-new.json` vacío; credenciales dentro del árbol del repo. | Commit accidental. | 0, 6 |

### 2.3 Pérdida e integridad de datos

| ID | Sev | Hallazgo (evidencia) | Fase |
|---|---|---|---|
| D1 | P1 | Las sesiones abiertas del agregador se **pierden** al salir del loop. En `raspberrypi/logs/pi_simulator.log`: `latex_gloves` confirmado a las 04:15:21, loop detenido a las 04:18:06, nunca guardado. | 2 |
| D2 | P1 | `raspberrypi/main.py:131-151`: sin cámara, usa **en silencio** el video de prueba. En producción, eso escribe detecciones **falsas** como reales (y ahora también en el lake). | 2 |
| D3 | P1 | `flush_offline_cache` usa `add()` con ID aleatorio, así que un reintento duplica. Solo se reintenta con el loop activo. `asyncio.create_task` sin referencia pierde errores. | 2 |
| D4 | P1 | No existe captura por evento; el requisito del usuario es **una captura por cada evento registrado**, enlazada por un identificador. | 2, 5 |
| D5 | P2 | `utils/aggregator.py:131`: `end_ts` es la hora de cierre (última detección + ≥4 s de gap), pero `duration_seconds` se mide hasta la última detección. Datos inconsistentes. | 2 |
| D6 | P2 | `poll_desired_state` asume `stopped` ante cualquier error transitorio y apaga la detección. | 2 |
| D7 | P2 | Sin protección contra borrado ni backups en Firestore; sin soft delete verificado en GCS. | 4 |
| D8 | P2 | KPI "Eventos confirmados" calculado sobre los últimos 200 eventos, no sobre el total. | 3 |
| D9 | P2 | Una sesión por **clase**, no por persona. Limitación del modelo de negocio: se documenta y el tracking queda como mejora. | 6, 8 |

### 2.4 Latencia y pipeline de visión

| ID | Sev | Hallazgo | Fase |
|---|---|---|---|
| L1 | P1 | Loop serial en el event loop de asyncio: leer → YOLO → dibujar → JPEG → enviar. El video sale a los FPS de inferencia. | 2 |
| L2 | P1 | `FRAME_SKIP` por conteo de frames + buffer V4L2: con cámara real, la latencia crece. | 2 |
| L3 | P1 | `best.pt` en CPU ARM, con un `resize(640x480)` que deforma la imagen antes del letterbox. NCNN es el formato recomendado por Ultralytics para Raspberry Pi. | 6 |
| L4 | P1 | `cv2.VideoCapture(0)` no sirve para cámaras CSI (libcamera) en Raspberry Pi OS actual; se necesita `picamera2`. | 2 |
| L5 | P1 | Video en dos saltos (Pi → Cloud Run → backend del dashboard → navegador) más polling de 5 s + 2 s para arrancar. | 2, 3 |
| L6 | P2 | El orquestador consulta el frame cada 30 ms en vez de reenviarlo apenas llega. | 1 |
| L7 | P2 | `permessage-deflate` activo: comprimir JPEG gasta CPU sin reducir bytes. | 1, 2 |

### 2.5 Mantenibilidad

| ID | Sev | Hallazgo | Fase |
|---|---|---|---|
| M1 | P2 | Código muerto: `raspberrypi/worker.py`, `utils/detector.py`, `utils/suscriber.py`, `utils/check_firestore.py`, `utils/create.py`, `scripts/create_database.py`, `gcp/data/`, `script-rpi-v0.1.zip`, `riesgos.txt`; en el orquestador `/gastos`, `/ws/control`, `/request_stream`; en `config.py` `ENDPOINTS`, `FAKE_ADMIN_TOKEN`. | 1-3 |
| M2 | P2 | Sin tests ni CI; `gcp/requirements.txt` sin versiones; `front-end/pyproject.toml` **vacío**. | 1, 3, 7 |
| M3 | P2 | Requirements de la Pi con `fastapi`, `uvicorn`, `google-cloud-pubsub` sin usar y `opencv-python` (con GUI) en vez de `-headless`. | 2 |
| M4 | P2 | Sin systemd, instalador ni procedimiento de actualización para la Pi. | 6 |
| M5 | P2 | Los eventos no registran la versión del modelo ni del software. | 2, 7 |

---

## 3. Decisiones

### 3.1 Tomadas por el usuario (2026-10-10) — NO volver a preguntar

| ID | Decisión | Consecuencia en el plan |
|---|---|---|
| U1 | Se sigue en el proyecto actual `epp-detection-v1`. La cuota gratuita **no** es un criterio. | Sin migración de bases: control en `(default)`, eventos en `dbraspberry`. El costo sigue siendo la prioridad 1, por eso se mantienen los listeners y el streaming bajo demanda. |
| U2 | Pub/Sub se **borra** porque no se usa. | Fase 0.3 y 4.8 obligatorias (con OK antes de ejecutar). |
| U3 | El dashboard se usa **por internet**. | Login con Google y lista blanca, dashboard dentro del servicio de Cloud Run (Fase 3), cierre por inactividad. |
| U4 | **Una captura por cada evento**, con un identificador para reconocerla después, en un **data lake** que mezcla data estructurada y no estructurada. | Fase 5 (data lake): GCS por zonas, `event_id` UUIDv7, BigQuery con object table + tabla de eventos. Sin límite por hora para capturas. |
| U5 | Mejorar el frontend y su **UX/UI/diseño**; el frontend puede estar en otro lenguaje, el **backend sigue en Python**. | Se elimina Flet. Frontend en **TypeScript + React (Vite)**, backend **FastAPI**. Rediseño completo en la Fase 3 con sistema de diseño, pantallas y criterios de usabilidad. |

| U6 | Las **imágenes** del lake se conservan **60 días** (los JSON de eventos, sin datos personales, se conservan indefinidamente). | Lifecycle: Standard 0-30 d → Nearline 30-60 d → borrado a los 60 d (4.6). |
| U7 | Administrador inicial: **`admin@viz-ia.com`** con rol `admin` (control total). | Se crea en `dashboard_users` antes del primer deploy (4.4). Es el único usuario inicial; los demás los da de alta él desde *Administración*. |
| U8 | Tema **claro por defecto**, oscuro disponible con el selector. **Sin logo** por ahora. | Tokens 3.8 y pantalla de login 3.10. El nombre "EPP Monitor" va como texto. |

### 3.2 Decisiones de arquitectura de este plan (recomendadas; el usuario puede objetar)

| ID | Decisión | Por qué |
|---|---|---|
| A1 | API, relay y SPA en **un solo servicio** de Cloud Run (`orchestrator-service`, la misma URL que ya usa la Pi). El código se separa en `backend/` y `frontend/`. | Un salto de red menos, mismo origen (cookies seguras sin CORS), una sola instancia que pagar, sin `VIEWER_TOKEN` y sin key de SA para el dashboard. |
| A2 | Video al navegador por WebSocket binario (JPEG con timestamp) dibujado en `<canvas>`. WebRTC/Cloudflare queda como trabajo futuro (ADR). | Permite controlar la contrapresión, mostrar latencia y estado "señal congelada", y atar el lease a la vida de la conexión. Sin dependencias nuevas en la Pi. |
| A3 | Lake en GCS + BigQuery (object tables), sin Dataflow ni Dataproc. | La escala es de un dispositivo; consultas programadas de BigQuery cubren la capa curada. |
| A4 | Stack del frontend: **React 19 + TypeScript estricto + Vite**, React Router, TanStack Query, Tailwind CSS v4 (tokens en variables CSS), primitivas accesibles **Radix UI**, íconos `lucide-react`, gráficos **Recharts** envueltos en componentes propios, `date-fns` + `date-fns-tz`. Tests con Vitest + Testing Library + Playwright (con MSW). pnpm y Node LTS vigente. | El ecosistema con más documentación y más fácil de mantener para cualquier persona que herede la tesis. Radix resuelve la accesibilidad (foco, teclado, ARIA) sin estilos impuestos. |
| A5 | Login con **Google Identity Services** (ID token verificado en el backend) y **cookie de sesión propia** (`HttpOnly`, `Secure`, `SameSite=Strict`). | No hace falta un client secret ni guardar tokens de Google en el navegador. La revocación de un usuario tarda como máximo 60 s. |

### 3.3 Decisiones menores — aplicar el valor por defecto sin preguntar

| ID | Tema | Valor que se aplica |
|---|---|---|
| Q3 | ¿Dominio propio para el dashboard o la URL `*.run.app`? | URL `*.run.app`. |
| Q4 | Alerta de "Pi caída" por healthchecks.io | Dejar el código listo (`HEALTHCHECK_URL` vacío = desactivado) y el procedimiento en el runbook; el usuario crea la cuenta cuando quiera. |
| Q5 | ¿Quitar el rol `Editor` de la SA default de Compute (tras configurar una SA de build)? | Sí, solo si el deploy con la SA de build funciona (Fase 4.9). |
| Q6 | ¿Publicar `edge.env.template` / `cloud.env.template` con valores ficticios? (`5fce9d4` dejó de versionar `.env.example`.) | Sí, con gitleaks en pre-commit. |
| Q7 | ¿Difuminar rostros en las imágenes del lake? | No (se documenta como riesgo y mejora futura en `PRIVACY.md`). |

### 3.4 Acciones que solo puede hacer el usuario (pedirlas en el momento en que se necesiten)

| Cuándo | Acción |
|---|---|
| Antes de la Fase 0 | `gcloud auth login` y `gcloud config set project epp-detection-v1` en su terminal. |
| Fase 4.2 | Crear el cliente OAuth web (orígenes autorizados) y entregar el **client ID** (no es secreto). |
| Fase 4.2 / 6 | Copiar el `device-token` de Secret Manager al `edge.env` de la Pi (el agente no lee secretos). |
| Fase 4.9 | Agregar su correo como destinatario del presupuesto (Billing → Manage access). |
| Fase 6 | Todo lo que requiere la Raspberry Pi física (instalar, probar cámara, benchmark). Si no hay Pi disponible, el agente deja los scripts listos y valida con el simulador en la PC. |
| Fase 8 | Activar *Secret scanning* y *Push protection* en GitHub. |

---

## 4. Fase 0 — Contención inmediata

### 0.1 Revocar la key filtrada en el ZIP (S1) 🔒 GCP

```bash
gcloud iam service-accounts keys list \
  --iam-account=raspberry-pi-worker@epp-detection-v1.iam.gserviceaccount.com \
  --managed-by=user --format="table(name.basename(), validAfterTime, disabled)"
```

- Si aparece una key que empieza en `52096d10` o `609ac33d`, borrarla con OK del usuario:
  `gcloud iam service-accounts keys delete <KEY_ID> --iam-account=raspberry-pi-worker@epp-detection-v1.iam.gserviceaccount.com`.
- Solo debe quedar la actual (`b78e4fdf…`). Revocar cualquier otra que el usuario no reconozca.
- Buscar uso indebido en Cloud Logging (últimos 30 días):
  `protoPayload.authenticationInfo.principalEmail="raspberry-pi-worker@epp-detection-v1.iam.gserviceaccount.com"`.
  Si los Data Access audit logs de Firestore no están activos, anotarlo en `SECURITY.md`.

### 0.2 Sacar el ZIP y archivos sensibles del árbol (S1, S11)

```bash
git rm --cached raspberrypi/script-rpi-v0.1.zip
rm raspberrypi/script-rpi-v0.1.zip raspberrypi/gcp-key-new.json
printf '\n*.zip\n*.tar.gz\n*.7z\n' >> .gitignore      # la reescritura completa va en la Fase 7
```

El ZIP queda en `3f95ee1` (no se reescribe el historial); la key que contiene queda inerte tras 0.1.

### 0.3 Quitar roles de Pub/Sub a la SA de la Pi (C8, U2) 🔒 GCP

```bash
for role in roles/pubsub.publisher roles/pubsub.subscriber; do
  gcloud projects remove-iam-policy-binding epp-detection-v1 \
    --member=serviceAccount:raspberry-pi-worker@epp-detection-v1.iam.gserviceaccount.com --role=$role
done
```

(El borrado completo de la tubería Pub/Sub está en la Fase 4.8.)

### 0.4 Techo de instancias (C6) 🔒 GCP

```bash
gcloud run services update orchestrator-service --region=us-central1 --max-instances=1 --min-instances=0
```

### Criterios de aceptación — Fase 0

- [ ] Una sola key gestionada por el usuario en `raspberry-pi-worker`.
- [ ] `git ls-files | grep -E '\.zip$|gcp-key|service_account'` no devuelve nada.
- [ ] `raspberry-pi-worker` sin roles de Pub/Sub.
- [ ] `orchestrator-service` con `maxScale: '1'`.

---

## 5. Fase 1 — Backend Python y relay de video (`backend/`)

### 1.1 Nueva estructura del repositorio (reemplaza `gcp/` y `front-end/`)

```
backend/                       # Python 3.12 · FastAPI · se despliega en Cloud Run
  epp_api/
    __init__.py                # __version__ = "2.0.0"
    settings.py                # Settings.from_env(), fail-closed
    main.py                    # app FastAPI: routers + middleware + SPA estática
    relay.py                   # DeviceChannel + uplink /ws/video (esta fase)
    live.py                    # WebSocket al navegador + lease (Fase 3)
    auth.py                    # Google ID token → cookie de sesión, roles, CSRF (Fase 3)
    repo.py                    # acceso a Firestore con caché incremental compartida (Fase 3)
    lake.py                    # URLs firmadas de GCS (Fase 3)
    audit.py                   # escritura de audit_log
    security_headers.py        # CSP, HSTS, etc.
    schemas.py                 # modelos Pydantic = contrato de la API (fuente del OpenAPI)
    routers/
      auth.py  devices.py  events.py  admin.py
  tests/
  requirements.txt             # versiones exactas
  requirements-dev.txt
frontend/                      # TypeScript · React · Vite (Fase 3)
Dockerfile                     # multi-etapa en la RAÍZ: compila frontend/ y empaqueta backend/
.dockerignore
```

Borrar `gcp/` (incluido `gcp/data/`) y `front-end/` cuando el backend y el frontend nuevos pasen
sus criterios de aceptación. De `front-end/main.py` se rescatan como **referencia funcional** (no
como código): las definiciones de KPIs (`compute_kpis`), los formatos de fecha y duración, y la
paleta, que se migra a tokens de diseño (Fase 3.6).

### 1.2 Protocolo de video (Pi → backend → navegador)

Cada mensaje binario de WebSocket es:

```
offset 0..7   uint64 big-endian   captured_at en milisegundos Unix (reloj de la Pi, sincronizado por NTP)
offset 8..    JPEG                 empieza con FF D8
```

El backend lo reenvía **sin modificarlo** al navegador. El navegador calcula la latencia
aproximada como `Date.now() - captured_at` (se muestra con "≈" porque depende de la
sincronización de relojes) y detecta la señal congelada si no llega un frame en 2 s.

### 1.3 `backend/epp_api/relay.py`

```python
"""Relay en memoria: la Pi publica frames; los espectadores (WebSocket del navegador,
en este mismo proceso) esperan el siguiente frame NUEVO. Requiere max-instances=1 y 1 worker."""
import asyncio
import hmac
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .settings import settings

log = logging.getLogger("relay")
router = APIRouter()
_HEADER = 8
_JPEG_SOI = b"\xff\xd8"


def token_ok(provided: str | None, expected: str) -> bool:
    if not provided:
        return False
    if provided.startswith("Bearer "):
        provided = provided[7:]
    return hmac.compare_digest(provided.encode(), expected.encode())


def valid_frame(data: bytes) -> bool:
    return _HEADER + 2 < len(data) <= settings.max_frame_bytes and data[_HEADER:_HEADER + 2] == _JPEG_SOI


class DeviceChannel:
    def __init__(self) -> None:
        self.frame: bytes | None = None
        self.seq = 0
        self.publisher_id = 0                 # la conexión más nueva de la Pi gana
        self._cond = asyncio.Condition()

    @property
    def live(self) -> bool:
        return self.frame is not None

    async def publish(self, frame: bytes) -> None:
        async with self._cond:
            self.frame, self.seq = frame, self.seq + 1
            self._cond.notify_all()

    async def close(self, publisher_id: int) -> None:
        async with self._cond:
            if publisher_id != self.publisher_id:
                return
            self.frame, self.seq = None, self.seq + 1
            self._cond.notify_all()

    async def next_frame(self, last_seq: int, timeout: float) -> tuple[int, bytes | None]:
        """(seq, frame). frame=None => la Pi se desconectó. asyncio.TimeoutError => sin frames."""
        async with self._cond:
            await asyncio.wait_for(self._cond.wait_for(lambda: self.seq != last_seq), timeout)
            return self.seq, self.frame


channels: dict[str, DeviceChannel] = {}


def channel(device_id: str) -> DeviceChannel:
    return channels.setdefault(device_id, DeviceChannel())


@router.websocket("/ws/video/{device_id}")
async def ws_video(websocket: WebSocket, device_id: str):
    if device_id not in settings.allowed_devices or not token_ok(
        websocket.headers.get("authorization"), settings.device_token
    ):
        log.warning("Uplink rechazado device=%s", device_id)
        await websocket.close(code=1008)
        return
    await websocket.accept()
    ch = channel(device_id)
    ch.publisher_id += 1
    my_id = ch.publisher_id
    log.info("Uplink conectado device=%s", device_id)
    try:
        while True:
            data = await asyncio.wait_for(websocket.receive_bytes(), timeout=settings.publisher_idle_seconds)
            if valid_frame(data):
                await ch.publish(data)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        await ch.close(my_id)
        log.info("Uplink cerrado device=%s", device_id)
```

**Eliminado y por qué:** `/ws/control` y `/request_stream` (la Pi nunca procesa comandos y un
WebSocket de control abierto 24/7 facturaría Cloud Run todo el día); `/gastos` (valor fijo de
prueba); `/video_stream` MJPEG público (lo reemplaza `/api/devices/{id}/live`, autenticado);
el token en la query string.

### 1.4 `backend/epp_api/settings.py` (fail-closed)

`@dataclass(frozen=True)` construido una vez. Lanza `RuntimeError` si un valor obligatorio falta o
es inválido.

| Variable | Obligatoria | Default / validación |
|---|---|---|
| `DEVICE_TOKEN` | sí | ≥ 32 caracteres (secreto) |
| `SESSION_SECRET` | sí | ≥ 32 caracteres (secreto); firma la cookie de sesión |
| `GOOGLE_OAUTH_CLIENT_ID` | sí si `AUTH_MODE=google` | ID del cliente web (no es secreto) |
| `PUBLIC_BASE_URL` | sí en Cloud Run | `https://orchestrator-service-...run.app`; se usa para validar `Origin` |
| `ALLOWED_DEVICES` | sí | `raspberry-pi-01` (lista separada por comas) |
| `AUTH_MODE` | no | `google`. `dev` (usuario `dev@localhost`, rol `admin`) solo si **no** existe `K_SERVICE` (fuera de Cloud Run) |
| `GCP_PROJECT_ID` | no | `epp-detection-v1` |
| `CONTROL_DATABASE_ID` | no | `(default)` (control, salud, usuarios, auditoría) |
| `DETECTIONS_DATABASE_ID` | no | `dbraspberry` (eventos, `daily_stats`) |
| `LAKE_BUCKET` | no | `epp-detection-v1-lake` |
| `RUNTIME_SA_EMAIL` | sí en Cloud Run | para firmar URLs de GCS sin archivo de key |
| `MAX_FRAME_BYTES` / `PUBLISHER_IDLE_SECONDS` | no | `400000` / `15` |
| `LIVE_MAX_FPS` | no | `10` (tope por espectador) |
| `MAX_LIVE_VIEWERS` | no | `10` |
| `LEASE_SECONDS` | no | `90` |
| `SESSION_MAX_HOURS` | no | `12` (vida absoluta de la cookie) |
| `CACHE_TTL_SECONDS` | no | `10` |
| `SITE_TIMEZONE` | no | `America/Lima` |
| `STATIC_DIR` | no | `/app/static` (build de la SPA) |
| `LOG_LEVEL` | no | `INFO` |

### 1.5 `backend/epp_api/main.py` (esqueleto)

```python
app = FastAPI(title="EPP Monitor API", version=__version__,
              docs_url=None if settings.on_cloud_run else "/api/docs", redoc_url=None,
              openapi_url="/api/openapi.json" if not settings.on_cloud_run else None)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)      # JSON; los JPEG no se comprimen
app.include_router(relay.router)                           # /ws/video/{device}
app.include_router(live.router)                            # /api/devices/{id}/live
app.include_router(routers.auth.router,    prefix="/api/auth")
app.include_router(routers.devices.router, prefix="/api/devices")
app.include_router(routers.events.router,  prefix="/api/events")
app.include_router(routers.admin.router,   prefix="/api/admin")

@app.get("/healthz")
async def healthz():
    return {"ok": True, "version": __version__}

# SPA: assets con hash => caché de 1 año e immutable; index.html => no-store; rutas desconocidas
# fuera de /api y /ws => index.html (routing del lado del cliente).
app.mount("/assets", ImmutableStaticFiles(directory=f"{settings.static_dir}/assets"), name="assets")
@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str):
    if path.startswith(("api/", "ws/")):
        raise HTTPException(404)
    return FileResponse(f"{settings.static_dir}/index.html", headers={"Cache-Control": "no-store"})
```

`SecurityHeadersMiddleware` (ajustar `connect-src` al host real):

```
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: camera=(), microphone=(), geolocation=()
Content-Security-Policy: default-src 'self';
  script-src 'self' https://accounts.google.com/gsi/client;
  style-src 'self' 'unsafe-inline' https://accounts.google.com/gsi/style;
  frame-src https://accounts.google.com/gsi/;
  connect-src 'self' wss://<host> https://accounts.google.com/gsi/;
  img-src 'self' data: blob: https://storage.googleapis.com https://*.googleusercontent.com;
  font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
```

(Los dominios de Google Identity Services son los que exige su documentación para la CSP; el
agente debe verificarlos ahí.)

### 1.6 `Dockerfile` (raíz, multi-etapa)

```dockerfile
# --- Etapa 1: build del frontend ---
FROM node:24-slim AS web
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build                                   # => /web/dist

# --- Etapa 2: runtime Python ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 STATIC_DIR=/app/static
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt && useradd --uid 10001 --no-create-home app
COPY backend/epp_api/ epp_api/
COPY --from=web /web/dist/ static/
USER app
CMD exec uvicorn epp_api.main:app --host 0.0.0.0 --port ${PORT:-8080} \
    --workers 1 --no-access-log --ws-per-message-deflate false --ws-max-size 1048576 \
    --proxy-headers --forwarded-allow-ips="*" --timeout-keep-alive 75
```

`--workers 1` es obligatorio (estado en memoria). `.dockerignore` en la raíz: `raspberrypi/`,
`docs/`, `infra/`, `**/node_modules`, `**/__pycache__`, `**/tests`, `.git`, `.env*`, `*key*.json`,
`frontend/dist`.

`backend/requirements.txt`: en un venv limpio con Python 3.12 instalar `fastapi`,
`uvicorn[standard]`, `google-cloud-firestore`, `google-cloud-storage`, `google-auth`, `requests`
(transporte de `google-auth`), `itsdangerous`; luego `pip freeze` con **todas** las versiones
exactas.

### 1.7 Tests (`backend/tests/test_relay.py`)

Fijar las variables de entorno antes de importar (`monkeypatch.setenv` + `importlib.reload`).

1. Sin `DEVICE_TOKEN` o sin `SESSION_SECRET` → `RuntimeError` al importar.
2. WS sin header, con token incorrecto o con `device_id` fuera de `ALLOWED_DEVICES` → cierre 1008.
3. Publicar 3 frames distintos → `next_frame` entrega exactamente 3 (sin duplicados).
4. Cerrar el publicador → el consumidor recibe `frame=None`.
5. Frame sin cabecera de 8 bytes, sin `FF D8` en el offset 8 o mayor a `MAX_FRAME_BYTES` → ignorado.
6. Dos publicadores seguidos: cerrar el **viejo** no apaga el canal del nuevo.

### Criterios de aceptación — Fase 1

- [ ] `pytest -q backend/tests` en verde.
- [ ] `grep -rn "UPLINK_TOKEN\|/gastos\|request_stream\|ws/control\|token=" backend/` sin resultados.
- [ ] `docker build .` funciona; la imagen pesa < 350 MB.

---

## 6. Fase 2 — Pipeline de borde en la Raspberry Pi (`raspberrypi/`)

### 2.1 Estructura

```
raspberrypi/
  epp_edge/
    __init__.py          # __version__ = "2.0.0"
    __main__.py          # from .app import main; main()
    config.py            # Settings.from_env() con validación
    ids.py               # uuid7()
    logging_setup.py
    slots.py             # LatestSlot
    capture.py           # picamera2 / usb / file + CaptureThread
    inference.py         # InferenceWorker
    aggregator.py        # movido desde utils/ con los cambios de 2.6
    captures.py          # construcción de imagen original + miniatura anotada + metadatos
    outbox.py            # Outbox SQLite + Uploader (lake + Firestore)
    control.py           # DeviceControl (on_snapshot) + HealthReporter
    streamer.py          # StreamWorker
    annotate.py          # dibujo de cajas
    systemd.py           # sd_notify
    app.py               # wiring, señales, ciclo de vida
  tools/
    set_state.py         # reemplaza utils/create.py (escribe device_control)
    check_firestore.py   # reemplaza utils/check_firestore.py (lee control + health)
    export_model.py      # Fase 6
    benchmark.py         # Fase 6
    reconcile_lake.py    # Fase 5
  deploy/
    epp-edge.service
    install.sh
    update.sh
    edge.env.template    # si Q6 = sí
  tests/
  requirements.txt
  requirements-dev.txt
```

**Borrar:** `main.py`, `worker.py`, `utils/`, `scripts/`, `config/`, `requirements_raspberrypi.txt`.
**Fuera de git:** `torch/`, `media/`, `database/`, `logs/`, cualquier `*.json` de credenciales.

### 2.2 Variables de entorno (`config.py`)

`Settings` (`@dataclass(frozen=True)`) valida al arrancar y falla con un mensaje claro. Nunca
usar un token "de desarrollo" en silencio (como hace hoy `main.py:69-71`).

| Variable | Default | Notas |
|---|---|---|
| `DEVICE_ID` | — (obligatoria) | `^[a-z0-9-]{3,40}$` |
| `GCP_PROJECT_ID` | `epp-detection-v1` | |
| `CONTROL_DATABASE_ID` | `(default)` | `device_control`, `device_health` |
| `DETECTIONS_DATABASE_ID` | `dbraspberry` | `detecciones_confirmadas`, `daily_stats` |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/etc/epp-edge/gcp-key.json` | advertir si los permisos son más abiertos que 0640 |
| `LAKE_BUCKET` | `epp-detection-v1-lake` | |
| `ORCHESTRATOR_HOST` | — (obligatoria) | sin esquema; se arma `wss://{host}/ws/video/{DEVICE_ID}` |
| `DEVICE_TOKEN` | — (obligatoria) | ≥ 32 caracteres |
| `CAMERA_SOURCE` | `auto` | `auto` \| `picamera2` \| `usb:<idx>` \| `file:<ruta>` |
| `ALLOW_TEST_VIDEO` | `false` | `file:` solo con `true`; los eventos llevan `is_simulated=true` |
| `CAPTURE_WIDTH` / `CAPTURE_HEIGHT` / `CAPTURE_FPS` | `1280` / `720` / `30` | |
| `MODEL_PATH` | `/var/lib/epp-edge/models/best_ncnn_model` | |
| `MODEL_IMGSZ` | `416` | múltiplo de 32 |
| `INFERENCE_FPS` | `5` | tope (temperatura/CPU) |
| `CONFIDENCE_THRESHOLD` / `IOU_THRESHOLD` | `0.5` / `0.45` | |
| `MIN_SECONDS_TO_CONFIRM` / `MIN_FRAMES_TO_CONFIRM` | `1.0` / `3` | |
| `SESSION_GAP_SECONDS` | `4.0` | |
| `CAPTURE_JPEG_QUALITY` | `90` | imagen original del lake |
| `THUMB_WIDTH` / `THUMB_JPEG_QUALITY` | `480` / `75` | miniatura anotada para el dashboard |
| `STREAM_FPS` / `STREAM_WIDTH` / `STREAM_JPEG_QUALITY` | `8` / `640` / `60` | |
| `AUTO_STREAM_ON_DETECTION` | `false` | `true` = comportamiento viejo y costoso |
| `HEARTBEAT_SECONDS` | `300` | |
| `CONTROL_SAFETY_POLL_SECONDS` | `300` | respaldo del listener |
| `DEFAULT_DESIRED_STATE` | `running` | si el doc de control no existe |
| `DATA_DIR` | `/var/lib/epp-edge` | outbox, capturas pendientes, caché de Ultralytics |
| `OUTBOX_MAX_BYTES` | `5_000_000_000` | tope de disco para capturas pendientes |
| `SITE_TIMEZONE` | `America/Lima` | |
| `HEALTHCHECK_URL` | vacío | Q4 |
| `LOG_LEVEL` / `LOG_TO_FILE` | `INFO` / `false` | bajo systemd se usa journald |

Eliminar: `PUB_SUB_TOPIC_ID`, `FRAME_SKIP`, `STATE_POLL_INTERVAL`, `STREAM_IDLE_TIMEOUT`,
`UPLINK_TOKEN`, `CLOUD_RUN_URL`, `FORCE_TEST_VIDEO`, `FIRESTORE_DATABASE_ID`.

### 2.3 `ids.py` — identificador único del evento

```python
import os
import time
import uuid


def uuid7() -> str:
    """UUID versión 7 (RFC 9562): 48 bits de timestamp en ms + aleatorio.
    Único globalmente y ordenable por tiempo: sirve como ID en Firestore,
    nombre de objeto en GCS y clave en BigQuery."""
    if hasattr(uuid, "uuid7"):                       # Python >= 3.14
        return str(uuid.uuid7())
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")     # 80 bits
    value = ((ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (((rand >> 68) & 0xFFF) << 64) \
        | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return str(uuid.UUID(int=value))
```

Test: 10 000 IDs únicos, `uuid.UUID(x).version == 7` y orden lexicográfico coherente con el tiempo
cuando se generan con 2 ms de diferencia.

### 2.4 `slots.py`

```python
import threading
from typing import Generic, TypeVar

T = TypeVar("T")


class LatestSlot(Generic[T]):
    """Buffer de un elemento: el productor sobrescribe y el consumidor siempre
    obtiene lo más reciente. Ningún consumidor lento acumula atraso."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._item: T | None = None
        self._seq = 0

    def put(self, item: T) -> None:
        with self._cond:
            self._item, self._seq = item, self._seq + 1
            self._cond.notify_all()

    def latest(self) -> T | None:
        with self._cond:
            return self._item

    def wait_newer(self, seq: int, timeout: float) -> tuple[int, T | None]:
        with self._cond:
            if not self._cond.wait_for(lambda: self._seq != seq, timeout):
                return seq, None
            return self._seq, self._item
```

### 2.5 `capture.py`

`Frame(image: np.ndarray (BGR), ts_mono: float, ts_wall: datetime (UTC), seq: int)`.

Fuentes con `open()`, `read() -> ndarray | None`, `close()`:

- **`PiCameraSource`**: `from picamera2 import Picamera2` (import dentro de `open()`);
  `create_video_configuration(main={"size": (w, h), "format": "RGB888"}, buffer_count=2, controls={"FrameRate": fps})`.
  En picamera2, `RGB888` entrega los píxeles en orden **B,G,R**, que es el que usa OpenCV.
- **`OpenCVSource(index)`**: `cv2.VideoCapture(index, cv2.CAP_V4L2)` en Linux (`CAP_ANY` en otros
  SO); FOURCC `MJPG`, ancho/alto/fps, `CAP_PROP_BUFFERSIZE=1`.
- **`FileSource(path)`**: respeta el FPS del archivo y hace loop. Solo con `ALLOW_TEST_VIDEO=true`.
- `auto`: `picamera2` → `usb:0`. **Si ninguna abre, NO usar el video de prueba**: `ERROR` en el
  log, `camera_ok=false` en `device_health` y reintento con backoff (1, 2, 4… 60 s). Corrige D2.

`CaptureThread` (daemon): lee y hace `slot.put(Frame(...))`. Si `read()` devuelve `None` 10 veces
seguidas, cierra y reabre la fuente con backoff. Expone `fps_measured` y `ok`.

### 2.6 `inference.py`

```python
class InferenceWorker(threading.Thread):
    def run(self):
        model = YOLO(str(self.s.model_path), task="detect")
        model.predict(np.zeros((self.s.model_imgsz, self.s.model_imgsz, 3), np.uint8),
                      imgsz=self.s.model_imgsz, verbose=False)                      # warm-up
        period, seq = 1.0 / self.s.inference_fps, 0
        self.ready.set()
        while not self.stop.is_set():
            seq, frame = self.frames.wait_newer(seq, timeout=1.0)
            if frame is None:
                continue
            t0 = time.monotonic()
            r = model.predict(frame.image, imgsz=self.s.model_imgsz, conf=self.s.confidence_threshold,
                              iou=self.s.iou_threshold, verbose=False)[0]
            h, w = frame.image.shape[:2]
            dets = [Detection(clase_id=int(c), nombre_clase=r.names[int(c)], confianza=float(p),
                              bbox_xyxy_norm=(x1 / w, y1 / h, x2 / w, y2 / h))
                    for c, p, (x1, y1, x2, y2) in zip(r.boxes.cls.tolist(), r.boxes.conf.tolist(),
                                                     r.boxes.xyxy.tolist())]
            inf = Inference(frame_seq=frame.seq, ts_mono=frame.ts_mono, ts_wall=frame.ts_wall,
                            detections=dets, latency_ms=(time.monotonic() - t0) * 1000)
            self.results.put(inf)
            self.on_result(inf, frame)
            self.stop.wait(max(0.0, period - (time.monotonic() - t0)))
```

- **No** redimensionar antes de `predict`: Ultralytics aplica letterbox respetando el aspecto.
- Las cajas se guardan **normalizadas (0-1)**: sirven para cualquier resolución de imagen del
  lake y para reentrenar.
- `model_version` = primeros 12 caracteres del SHA-256 del modelo (en NCNN, de `model.ncnn.bin`),
  calculado al arrancar. `YOLO_CONFIG_DIR` apunta a `DATA_DIR/ultralytics`.
- Latencias p50/p95 en una ventana móvil, reportadas en `device_health`.

### 2.7 `aggregator.py` (mover desde `utils/` y modificar)

1. `_ActiveSession` agrega `event_id: str = field(default_factory=uuid7)`, `start_wall`,
   `last_wall` (`datetime`), `best_frame: Frame | None`, `best_detections: list[Detection]`.
   Eliminar `start_ts_iso`.
2. `update(inference, frame)`:
   - Por clase, tomar solo la detección de **mayor** confianza del tick (hoy dos cajas de la
     misma clase suman dos veces a `frame_count`).
   - Si `confianza > max_confidence`: `best_frame = frame` y
     `best_detections = inference.detections` (todas las cajas de ese frame, de todas las
     clases: es el contexto completo de la captura).
   - Confirmación: `(now - first_seen) >= MIN_SECONDS_TO_CONFIRM` **y**
     `frame_count >= MIN_FRAMES_TO_CONFIRM`.
3. Al cerrar: `end_ts = last_wall` y `duration_seconds = (last_wall - start_wall).total_seconds()`.
   Corrige D5.
4. `flush_all(reason) -> list[ClosedEvent]` cierra las sesiones confirmadas abiertas con
   `closed_reason` = `"shutdown"`, `"stopped"` o `"camera_lost"`. Corrige D1.
5. `ClosedEvent(payload: dict, best_frame: Frame, best_detections: list[Detection])`. El payload
   es el **contrato v2** (sección 7.3). Nunca incluye la imagen.
6. Mantener `frames_processed` y `noise_discarded_count`.

### 2.8 `captures.py` — una captura por evento (U4, D4)

```python
def build_capture(ev: ClosedEvent, s: Settings) -> CaptureFiles:
    """Genera, en DATA_DIR/pending/{event_id}/:
       - original.jpg : frame SIN anotar (CAPTURE_JPEG_QUALITY). Es el dato para análisis/reentrenamiento.
       - thumb.jpg    : miniatura anotada (THUMB_WIDTH, cajas + clase + confianza). Es lo que muestra el dashboard.
       - event.json   : sidecar del lake (contrato 7.3), con sha256, dimensiones y bytes de ambas imágenes.
    Escritura atómica: cada archivo como .tmp + os.replace; el directorio se crea antes.
    Devuelve rutas y metadatos."""
```

- El frame elegido es el de **mayor confianza** de la sesión (`best_frame`). Si por alguna razón
  no hay frame (no debería pasar), el evento se registra igual con `capture.status = "missing"` y
  el motivo; **nunca** se descarta el evento.
- **No hay límite por hora**: cada evento confirmado tiene su captura (U4). El único tope es
  `OUTBOX_MAX_BYTES`, que protege el disco si la Pi está mucho tiempo sin red; al superarlo, se
  borran las capturas **pendientes** más viejas (no sus eventos) con log `CRITICAL` y
  `capture.status = "dropped_disk_full"`.

### 2.9 `outbox.py` — persistencia local e idempotencia

```sql
CREATE TABLE IF NOT EXISTS outbox (
    event_id        TEXT PRIMARY KEY,
    payload         TEXT NOT NULL,     -- JSON del contrato v2
    pending_dir     TEXT,              -- DATA_DIR/pending/{event_id} o NULL
    stage           TEXT NOT NULL DEFAULT 'images',  -- images → sidecar → firestore → done
    created_at      REAL NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    last_error      TEXT
);
CREATE INDEX IF NOT EXISTS outbox_due ON outbox(next_attempt_at);
```

- Una sola conexión (`check_same_thread=False`) protegida con `threading.Lock`;
  `PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;`.
- `put(event, capture_files)` → `INSERT OR IGNORE`.
- `stage` avanza solo después de que el paso termina, así que un reinicio retoma donde quedó.
- **Migración**: si existe la tabla vieja `offline_detections`, importar cada fila con un `uuid7()`
  nuevo, `capture.status="missing_legacy"`, `stage='firestore'`, y luego `DROP TABLE`.

**Uploader** (hilo que corre **siempre**, también con la detección pausada). El orden garantiza que
si un evento existe en Firestore, su captura ya está en el lake:

```python
def _process(self, row):
    p = json.loads(row.payload)
    prefix = lake_prefix(p)   # device=<id>/dt=<YYYY-MM-DD en SITE_TIMEZONE, según end_ts>
    if row.stage == "images":
        for name, kind in (("original.jpg", "images"), ("thumb.jpg", "thumbs")):
            blob = self.bucket.blob(f"raw/{kind}/{prefix}/{p['event_id']}.jpg")
            blob.metadata = image_metadata(p, kind)       # event_id, device_id, nombre_clase, confianza_max, model_version, captured_at, sha256
            try:
                blob.upload_from_filename(f"{row.pending_dir}/{name}", content_type="image/jpeg",
                                          if_generation_match=0, timeout=60)
            except PreconditionFailed:
                pass                                       # ya existía: idempotente
        self.outbox.advance(p["event_id"], "sidecar")
    if row.stage in ("images", "sidecar"):
        blob = self.bucket.blob(f"raw/events/{prefix}/{p['event_id']}.json")
        try:
            blob.upload_from_string(json.dumps(p, separators=(",", ":"), default=iso),   # UNA línea (NDJSON)
                                    content_type="application/json", if_generation_match=0, timeout=30)
        except PreconditionFailed:
            pass
        self.outbox.advance(p["event_id"], "firestore")
    batch = self.events_db.batch()
    batch.create(self.events.document(p["event_id"]), to_firestore(p))   # datetimes nativos + created_at SERVER_TIMESTAMP
    batch.set(self.stats.document(f"{p['device_id']}_{local_day(p)}"), {
        "device_id": p["device_id"], "date": local_day(p),
        "events": firestore.Increment(1),
        "seconds": firestore.Increment(p["duration_seconds"]),
        "by_class": {p["nombre_clase"]: firestore.Increment(1)},
        "updated_at": firestore.SERVER_TIMESTAMP,
    }, merge=True)
    try:
        batch.commit(timeout=15)
    except Conflict:                                       # AlreadyExists: ya estaba escrito
        pass
    self.outbox.ack(p["event_id"])                         # borra la fila y DATA_DIR/pending/{event_id}
```

- Errores → `nack`: `attempts+1`, `next_attempt_at = now + min(300, 2**attempts)`.
- Bucle: `due(10)`, dormir 1 s si hubo trabajo y 5 s si no. En el apagado, drenar 10 s como máximo.
- La SA de la Pi solo tiene `storage.objectCreator`: puede crear pero no leer ni sobrescribir.
  `if_generation_match=0` + `PreconditionFailed` hace la subida idempotente sin necesitar lectura.

### 2.10 `control.py` — control por listener y salud

Documentos en `(default)` (reemplazan el campo de doble uso `device_status.state`):

- `device_control/{device_id}` — lo escribe **solo el servidor del dashboard**:
  `desired_state` (`running`|`stopped`), `stream_requested` (bool), `lease_renewed_at`
  (SERVER_TIMESTAMP), `lease_seconds` (90), `updated_by` (email), `updated_at`.
- `device_health/{device_id}` — lo escribe **solo la Pi**: `reported_state`
  (`running`|`stopped`|`offline`), `streaming`, `camera_ok`, `camera_source`, `last_seen`
  (SERVER_TIMESTAMP), `inference_fps`, `inference_ms_p50`, `inference_ms_p95`, `cpu_temp_c`,
  `outbox_pending`, `outbox_bytes`, `edge_version`, `model_version`.

`DeviceControl`:

```python
def _apply(self, snap) -> None:
    data = snap.to_dict() if snap.exists else {}
    with self._lock:
        self.desired_state = data.get("desired_state", self.default_state)
        renewed, lease = data.get("lease_renewed_at"), float(data.get("lease_seconds", 90))
        if not data.get("stream_requested") or renewed is None:
            self._lease_deadline = 0.0
        elif renewed != self._last_renewed:
            # read_time y lease_renewed_at son hora del SERVIDOR: no hay desfase con el reloj de la Pi.
            age = (snap.read_time - renewed).total_seconds()
            self._lease_deadline = time.monotonic() + max(0.0, lease - age)
            self._last_renewed = renewed
        self.last_sync = time.monotonic()
        self._ever_synced = True
```

- `start()`: `doc_ref.on_snapshot(lambda docs, ch, rt: docs and self._apply(docs[0]))` más un hilo
  de respaldo que cada `CONTROL_SAFETY_POLL_SECONDS` hace `get()` + `_apply()` y vuelve a
  suscribirse si `last_sync` tiene más de 2× ese intervalo.
- Ante un error de Firestore **se conserva el último estado conocido** (corrige D6).
  `DEFAULT_DESIRED_STATE` solo se usa si nunca hubo una lectura exitosa.
- `should_run()`, `should_stream()` (run y (lease vigente o auto-stream)), y
  `bump_auto_stream(seconds)` (solo si `AUTO_STREAM_ON_DETECTION=true`).

`HealthReporter`: escribe cada `HEARTBEAT_SECONDS` y en cada transición (debounce de 2 s); hace
`GET HEALTHCHECK_URL` (o `/fail` si `camera_ok=false`) en cada heartbeat si está configurado; al
apagarse escribe `reported_state="offline"`.

### 2.11 `streamer.py` — subida de video solo bajo demanda

```python
from websockets.sync.client import connect

def run(self):
    backoff = 1.0
    while not self.stop.is_set():
        if not self.control.should_stream():
            self.stop.wait(0.25)
            continue
        try:
            with connect(self.url, additional_headers={"Authorization": f"Bearer {self.s.device_token}"},
                         compression=None, open_timeout=10, close_timeout=2, max_size=2**20) as ws:
                self.health.set_streaming(True)
                backoff, seq, period = 1.0, 0, 1.0 / self.s.stream_fps
                while self.control.should_stream() and not self.stop.is_set():
                    t0 = time.monotonic()
                    seq, frame = self.frames.wait_newer(seq, timeout=1.0)
                    if frame is None:
                        continue
                    img = resize_to_width(annotate(frame, self.results.latest()), self.s.stream_width)
                    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.s.stream_jpeg_quality])
                    if ok:
                        ws.send(struct.pack(">Q", int(frame.ts_wall.timestamp() * 1000)) + buf.tobytes())  # protocolo 1.2
                    self.stop.wait(max(0.0, period - (time.monotonic() - t0)))
        except Exception as e:
            self.log.warning("Streaming caído: %s (reintento en %.0fs)", e, backoff)
            self.stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            self.health.set_streaming(False)
```

El video sale a `STREAM_FPS` independiente de `INFERENCE_FPS`: se envía el frame más fresco con
las cajas de la última inferencia (corrige L1). `annotate()` trabaja sobre una copia y dibuja la
hora de captura con milisegundos en una esquina, para medir la latencia (Fase 9).

### 2.12 `app.py` — ciclo de vida

```python
def main() -> None:
    s = Settings.from_env(); setup_logging(s)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    control_db = firestore.Client(project=s.gcp_project_id, database=s.control_database_id)
    events_db = firestore.Client(project=s.gcp_project_id, database=s.detections_database_id)
    bucket = storage.Client(project=s.gcp_project_id).bucket(s.lake_bucket)
    outbox = Outbox(s.data_dir / "outbox.db", s)
    uploader = Uploader(outbox, events_db, bucket, s, stop, log); uploader.start()
    health = HealthReporter(control_db, s, outbox, log); health.start()
    control = DeviceControl(control_db, s, log); control.start()
    pipeline = None
    sd_notify("READY=1")
    try:
        while not stop.is_set():
            if control.should_run() and pipeline is None:
                pipeline = Pipeline(s, control, health, outbox, log).start()
            elif not control.should_run() and pipeline is not None:
                pipeline.stop(reason="stopped"); pipeline = None          # flush_all → outbox
            if pipeline is None or pipeline.healthy(max_silence=30):
                sd_notify("WATCHDOG=1")                                   # solo si la inferencia avanza
            stop.wait(1.0)
    finally:
        sd_notify("STOPPING=1")
        if pipeline is not None:
            pipeline.stop(reason="shutdown")
        uploader.drain(timeout=10); control.close(); health.final("offline")
```

`Pipeline` agrupa captura, inferencia, streaming y agregador; `on_result` llama
`aggregator.update()` y, por cada evento cerrado, `build_capture()` + `outbox.put()`.

`systemd.py`:

```python
def sd_notify(msg: str) -> None:
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr or not hasattr(socket, "AF_UNIX"):
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
        s.connect(addr); s.sendall(msg.encode())
```

### 2.13 `requirements.txt` (Pi)

Fijar versiones exactas con `pip freeze` **en la Pi** (o en un contenedor arm64):

```
ultralytics
ncnn
opencv-python-headless
numpy                 # misma versión MAYOR que la numpy del sistema (python3-picamera2 viene de apt)
websockets>=13
google-cloud-firestore
google-cloud-storage
python-dotenv
```

`picamera2` se instala con `apt` y el venv usa `--system-site-packages`. Verificar:
`python -c "import numpy, picamera2, cv2, ultralytics; print(numpy.__version__)"`.

### 2.14 Tests

- `test_aggregator.py`: parpadeo descartado; sesión sostenida → `end_ts == last_wall`; oclusión
  < gap → mismo `event_id`; `flush_all`; dos cajas de la misma clase → `frame_count` +1;
  `MIN_FRAMES_TO_CONFIRM`; `best_frame` = frame de mayor confianza.
- `test_ids.py`: unicidad, versión 7, orden temporal.
- `test_outbox.py`: idempotencia de `put`; persistencia tras reabrir; backoff; `stage` se retoma
  tras un reinicio simulado; migración de `offline_detections`; tope de bytes.
- `test_uploader.py`: con un bucket falso que lanza `PreconditionFailed` y un batch falso que
  lanza `Conflict` → `ack` sin duplicar; si falla el sidecar, Firestore **no** se escribe.
- `test_control.py`: lease vigente/vencido/sin cambios; error de Firestore conserva el estado.
- Ningún test importa `ultralytics` ni `picamera2` (para que corran en CI).

### Criterios de aceptación — Fase 2

- [ ] `pytest -q raspberrypi/tests` en verde.
- [ ] Simulación en la PC (`CAMERA_SOURCE=file:media/prueba1.mp4`, `ALLOW_TEST_VIDEO=true`): por
      cada evento hay en el lake `raw/images/.../{event_id}.jpg`, `raw/thumbs/.../{event_id}.jpg`
      y `raw/events/.../{event_id}.json`, y en Firestore el documento `{event_id}` con
      `is_simulated=true`, `capture_uri` y `thumb_uri`.
- [ ] Sin lease: cero conexiones a Cloud Run.
- [ ] `Ctrl+C` con una sesión abierta → evento con `closed_reason="shutdown"` y su captura.
- [ ] Sin red durante 3 eventos → `outbox_pending=3`; al volver la red se suben, sin duplicados.
- [ ] Sin cámara y sin `ALLOW_TEST_VIDEO` → cero eventos y `camera_ok=false`.

---

## 7. Fase 3 — Dashboard web: API (Python) + SPA (TypeScript/React) + rediseño UX/UI

Esta fase reemplaza por completo el dashboard en Flet. Se divide en: **A)** API del backend,
**B)** arquitectura del frontend y **C)** diseño (UX/UI). El orden de ejecución es 3.1 → 3.11; el
diseño (C) se define **antes** de escribir componentes.

### A) Backend — API para el dashboard

#### 3.1 Autenticación, sesión y autorización (`auth.py`, `routers/auth.py`) — prerrequisito (S3)

Flujo (Google Identity Services, sin client secret):

1. La SPA carga `https://accounts.google.com/gsi/client` y muestra el botón "Continuar con Google".
2. GIS entrega un **ID token** (`credential`) → `POST /api/auth/google {credential}`.
3. El backend verifica con `google.oauth2.id_token.verify_oauth2_token(credential,
   google.auth.transport.requests.Request(), settings.google_oauth_client_id)` y exige
   `iss ∈ {accounts.google.com, https://accounts.google.com}` y `email_verified == True`.
4. Busca `(default)/dashboard_users/{email_en_minúsculas}` con `active == True`. Si no existe:
   `403` + registro en `audit_log` (`login_denied`).
5. Emite la cookie `epp_session`: payload `{email, role, iat}` firmado con `itsdangerous`
   (`URLSafeTimedSerializer(SESSION_SECRET)`), `HttpOnly; Secure; SameSite=Strict; Path=/`,
   `Max-Age = SESSION_MAX_HOURS`. No se guarda nada de Google en el navegador.
6. Cada request autenticada: valida la firma y la edad, y **revalida el rol** contra
   `dashboard_users` con una caché de 60 s (dar de baja a alguien tiene efecto en ≤ 60 s).
7. `POST /api/auth/logout` borra la cookie. `GET /api/auth/me` → `{email, name, picture, role}`.

- **CSRF:** `SameSite=Strict` + todo `POST/PUT/PATCH/DELETE` exige la cabecera
  `X-Requested-With: epp-web` (sin CORS habilitado, otro origen no puede enviarla).
- **WebSockets del navegador:** validar la cookie **y** que `Origin == PUBLIC_BASE_URL`.
- **Roles:** `viewer` (todo lectura), `operator` (+ pausar/reanudar detección), `admin`
  (+ usuarios y auditoría). Dependencias de FastAPI: `require_role("operator")`, etc.
- **Rate limit** simple en memoria para `/api/auth/google`: 10 intentos por IP por minuto.
- `AUTH_MODE=dev` solo fuera de Cloud Run (sin `K_SERVICE`); en Cloud Run lanza `RuntimeError`.

#### 3.2 Acceso a datos con caché incremental compartida (`repo.py`)

El backend **no** usa listeners de Firestore (con `--cpu-throttling` la instancia no tiene CPU
entre requests y los listeners quedarían congelados). En su lugar:

- **Eventos recientes por dispositivo:** se guarda en memoria la ventana de los últimos 500
  eventos. En cada refresco (como mucho uno cada `CACHE_TTL_SECONDS`, compartido entre todos los
  usuarios), se consulta solo `where(device_id == X).where(end_ts > max_end_ts_cacheado)
  .order_by(end_ts)`. Si no hay nada nuevo, cuesta 1 lectura.
- **`device_control`, `device_health`:** `get()` con TTL de 5 s.
- **`daily_stats`:** consulta de los últimos 60 días con TTL de 60 s.
- **Historial más allá de la ventana** (explorador con filtros o fechas viejas): consulta directa
  paginada por cursor, sin caché.
- Las escrituras (`desired_state`, lease, usuarios) invalidan la entrada de caché que
  corresponda.
- Un `asyncio.Lock` por clave evita que 20 requests simultáneas disparen 20 consultas iguales.

Índices compuestos necesarios (Fase 4.4): `device_id ASC, end_ts DESC` y, para filtros,
`device_id ASC, nombre_clase ASC, end_ts DESC`.

#### 3.3 Endpoints (`schemas.py` = contrato; todos bajo `/api`, JSON, fechas ISO-8601 UTC)

| Método y ruta | Rol | Respuesta |
|---|---|---|
| `POST /auth/google` · `POST /auth/logout` · `GET /auth/me` | — / sesión | ver 3.1 |
| `GET /devices` | viewer | `[DeviceStatus]`: id, nombre, `state` (`detecting`\|`live`\|`paused`\|`offline`\|`camera_error`), `last_seen`, `streaming`, salud (`inference_fps`, `inference_ms_p50/p95`, `cpu_temp_c`, `outbox_pending`, `edge_version`, `model_version`, `camera_source`) |
| `GET /devices/{id}/summary?range=24h\|7d\|30d` | viewer | KPIs del periodo **y** del periodo anterior (para el delta): `events`, `epp_seconds`, `avg_confidence`, `capture_coverage` (% de eventos con `capture.status=ok`), `top_class`; serie temporal (`bucket`=hora para 24h, día para 7d/30d) con conteo total y por clase; desglose por clase |
| `GET /devices/{id}/events?cursor=&limit=24&clase=&min_conf=&from=&to=&include_simulated=false` | viewer | página de `EventSummary` (id, clase, confianzas, inicio/fin, duración, `thumb_url`, `capture_status`, `is_simulated`) + `next_cursor` (opaco: `end_ts|event_id` en base64) |
| `GET /events/{event_id}` | viewer | `EventDetail`: contrato v2 completo + `thumb_url` + `original_url` |
| `GET /events/{event_id}/capture?kind=thumb\|original` | viewer | `302` a una URL firmada de GCS (v4, 10 min). `original` queda registrado en `audit_log` |
| `PUT /devices/{id}/desired-state {state: running\|stopped}` | operator | `DeviceStatus`; registrado en `audit_log` |
| `WS /devices/{id}/live` | viewer | frames binarios (1.2) + mensajes de texto JSON `{type: "status", ...}`; ver 3.4 |
| `GET/POST/PATCH/DELETE /admin/users[/{email}]` | admin | CRUD de `dashboard_users` (no se puede quitar el último admin ni a uno mismo) |
| `GET /admin/audit?cursor=&action=&email=` | admin | página de `audit_log` |

- `lake.py` firma URLs **sin archivo de key** usando la SA del servicio:
  `blob.generate_signed_url(version="v4", expiration=timedelta(minutes=10), method="GET",
  service_account_email=settings.runtime_sa_email, access_token=creds.token)` (refrescar `creds`
  antes). Requiere `roles/iam.serviceAccountTokenCreator` de la SA sobre sí misma (Fase 4.3).
  Cachear cada URL firmada 9 min por objeto para que el navegador pueda reutilizar su caché.
- Errores en formato uniforme: `{"error": {"code": "...", "message": "..."}}` con mensajes en
  español.
- **Tipos compartidos:** en desarrollo, `pnpm gen:api` ejecuta
  `openapi-typescript http://127.0.0.1:8080/api/openapi.json -o src/api/schema.d.ts`. El archivo
  generado se versiona y la CI verifica que no haya cambios pendientes de regenerar.

#### 3.4 `live.py` — video al navegador y lease

```python
@router.websocket("/api/devices/{device_id}/live")
async def live(ws: WebSocket, device_id: str):
    user = await authenticate_ws(ws)                        # cookie + Origin; si falla: close(1008)
    if user is None or device_id not in settings.allowed_devices:
        return
    if lease.viewers >= settings.max_live_viewers:
        await ws.close(code=1013); return                    # "intenta más tarde"
    await ws.accept()
    await lease.acquire(device_id, user.email)               # 0→1: device_control.stream_requested=True
    audit.write(user.email, "live_start", device_id)
    ch, seq, min_interval, last_sent = relay.channel(device_id), 0, 1 / settings.live_max_fps, 0.0
    try:
        await ws.send_json({"type": "status", "state": "waiting" if not ch.live else "live"})
        while True:
            try:
                seq, frame = await ch.next_frame(seq, timeout=5)
            except asyncio.TimeoutError:
                await ws.send_json({"type": "status", "state": "waiting"}); continue
            if frame is None:
                await ws.send_json({"type": "status", "state": "ended"}); continue
            now = time.monotonic()
            if now - last_sent >= min_interval:
                await ws.send_bytes(frame); last_sent = now  # si el cliente es lento, se saltan frames
    except WebSocketDisconnect:
        pass
    finally:
        await lease.release(device_id, user.email)           # 1→0: stream_requested=False
        audit.write(user.email, "live_end", device_id)
```

`StreamingLease`: cuenta los espectadores por dispositivo; con ≥ 1 renueva
`device_control.lease_renewed_at` cada `LEASE_SECONDS/3` en una tarea asyncio (mientras haya un
WebSocket abierto, la instancia tiene CPU, así que la tarea corre). Con 0 cancela la tarea y
escribe `stream_requested=False`.

### B) Frontend — arquitectura (`frontend/`)

#### 3.5 Stack y estructura

Stack según A4: React 19 + TypeScript (`strict`, `noUncheckedIndexedAccess`) + Vite, React
Router, TanStack Query, Tailwind CSS v4, Radix UI, `lucide-react`, Recharts, `date-fns` +
`date-fns-tz` (locale `es`, zona `America/Lima`). Tests: Vitest + Testing Library + MSW;
Playwright para e2e. ESLint (`typescript-eslint`, `jsx-a11y`, `react-hooks`) + Prettier. pnpm,
Node LTS vigente (`.nvmrc`). Fijar versiones exactas con `pnpm-lock.yaml`.

```
frontend/
  index.html
  vite.config.ts               # proxy /api y /ws → http://127.0.0.1:8080 en desarrollo
  src/
    main.tsx  App.tsx  routes.tsx
    api/
      schema.d.ts              # generado desde OpenAPI (no editar a mano)
      client.ts                # fetch con credentials:'same-origin', X-Requested-With, manejo de 401/403
      queries.ts               # hooks de TanStack Query por recurso (useSummary, useEvents, ...)
      live.ts                  # cliente WebSocket del video (reconexión, parseo de cabecera)
    auth/                      # AuthProvider, GoogleButton, RequireRole
    design/
      tokens.css               # variables CSS (3.6), tema claro/oscuro
      classColors.ts           # mapeo fijo clase → slot categórico
      format.ts                # números, %, duraciones, fechas relativas/absolutas en es-PE
    components/                # sistema de componentes (3.8)
    features/
      overview/  live/  events/  device/  admin/
    hooks/
      useIdle.ts               # inactividad global (3.9)
      usePageVisible.ts
  tests/  e2e/
```

Reglas de datos en el cliente:

- `QueryClient`: `staleTime` 10 s, `refetchOnWindowFocus: true`, `refetchIntervalInBackground: false`.
  Intervalos: resumen y eventos 30 s; dispositivo 10 s (5 s en "En vivo"). **Todo intervalo se
  apaga cuando el usuario está inactivo** (3.9).
- `client.ts`: un `401` lleva al login conservando la ruta (`?next=`); un `403` muestra la
  pantalla "Sin permiso", no un error genérico.
- Sin `localStorage` para datos ni tokens; solo para preferencias (tema, densidad de la tabla),
  con `try/catch`.
- Presupuesto de rendimiento: JS inicial < 180 KB gzip (code splitting por ruta; Recharts solo en
  las rutas con gráficos), LCP < 2.5 s en 4G, CLS < 0.05 (todas las imágenes con `aspect-ratio`
  fijo).

#### 3.6 `api/live.ts` + `LivePlayer` — reproductor de video

```ts
ws.binaryType = "arraybuffer";
ws.onmessage = async (ev) => {
  if (typeof ev.data === "string") { onStatus(JSON.parse(ev.data)); return; }
  const buf = ev.data as ArrayBuffer;
  const capturedAt = Number(new DataView(buf).getBigUint64(0));          // ms Unix
  const bitmap = await createImageBitmap(new Blob([buf.slice(8)], { type: "image/jpeg" }));
  ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height); bitmap.close();
  metrics.push({ capturedAt, receivedAt: Date.now() });                  // fps y latencia ≈
};
```

- Si llega un frame mientras se decodifica otro, se descarta el viejo (se dibuja siempre el más
  reciente).
- Estados visibles: `conectando` → `esperando al dispositivo` (lease enviado, la Pi aún no
  transmite; mostrar "Puede tardar unos segundos") → `en vivo` → `señal congelada` (> 2 s sin
  frames; se atenúa el último frame con un aviso) → `transmisión finalizada`.
- Reconexión con backoff 1-2-4-8 s (máx. 5 intentos) y botón "Reintentar".
- El WebSocket se cierra al salir de la ruta, al ocultar la pestaña más de 30 s y al entrar en
  inactividad. **Esto es lo que apaga el streaming de la Pi y el costo.**

### C) Diseño — UX/UI

#### 3.7 Principios de diseño (documentar en `docs/DESIGN_SYSTEM.md`)

1. **Estado de un vistazo:** en < 3 s cualquier usuario debe saber si el dispositivo está
   detectando, en vivo, pausado o caído. El estado se ve en todas las pantallas.
2. **Evidencia primero:** cada número lleva a los eventos que lo producen y cada evento lleva a su
   captura. Nada de cifras sin trazabilidad.
3. **Calma por defecto:** neutros para la estructura, un acento azul para la interacción y la
   magnitud, y los colores de estado **solo** para el estado (nunca decorativos).
4. **Honestidad de los datos:** periodos explícitos ("últimos 7 días", "hora de Lima"), eventos
   simulados ocultos por defecto y marcados cuando se muestran, la latencia se muestra como
   aproximada.
5. **Accesible y en español:** WCAG 2.2 AA, teclado completo, es-PE.

#### 3.8 Tokens de diseño (`design/tokens.css`)

Se toman de la paleta de referencia del skill `dataviz` (la misma familia que ya usa el dashboard
actual, para no reinventar decisiones ya tomadas con el usuario). Definirlos como variables CSS y
consumirlos desde Tailwind (`@theme`). Oscuro y claro son temas **elegidos**, no una inversión
automática. **El tema por defecto es claro (U8)**: `:root` define los valores claros sin importar
la preferencia del sistema operativo, y el oscuro se aplica **solo** con `[data-theme="dark"]`
cuando el usuario lo elige en el selector de la barra superior. La elección se guarda en
`localStorage` (con `try/catch`) y se aplica antes del primer render con un script en línea en
`index.html` para evitar el destello de tema incorrecto.

| Token | Claro | Oscuro | Uso |
|---|---|---|---|
| `--page` | `#f9f9f7` | `#0d0d0d` | fondo de la app |
| `--surface-1` | `#fcfcfb` | `#1a1a19` | tarjetas, paneles, gráficos |
| `--surface-2` | `#f0efec` | `#232322` | hover, filas alternas, inputs |
| `--border` | `rgba(11,11,11,.10)` | `rgba(255,255,255,.10)` | bordes de 1 px |
| `--text-primary` | `#0b0b0b` | `#ffffff` | texto principal y valores |
| `--text-secondary` | `#52514e` | `#c3c2b7` | etiquetas, descripciones |
| `--text-muted` | `#898781` | `#898781` | ejes, metadatos, placeholders |
| `--grid` | `#e1e0d9` | `#2c2c2a` | líneas de grilla |
| `--axis` | `#c3c2b7` | `#383835` | línea base |
| `--accent` | `#2a78d6` | `#3987e5` | botones primarios, enlaces, foco, serie de magnitud |
| `--status-good` | `#0ca30c` | `#0ca30c` | Detectando / OK |
| `--status-warning` | `#fab219` | `#fab219` | Pausado / atención |
| `--status-serious` | `#ec835a` | `#ec835a` | Cámara con problemas / outbox creciendo |
| `--status-critical` | `#d03b3b` | `#d03b3b` | Sin conexión / error |
| `--delta-good-text` | `#006300` | `#0ca30c` | texto de variación positiva |

- **Tipografía:** `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` (sin fuentes web:
  carga instantánea). Escala 12 / 14 / 16 / 20 / 24 / 32 / 48 px; pesos 400/500/600.
  `tabular-nums` solo en tablas y ejes; los valores de los KPI usan cifras proporcionales.
- **Espaciado:** grilla de 4 px (4, 8, 12, 16, 24, 32, 48). **Radios:** 6 (controles),
  10 (tarjetas), 14 (paneles de video). **Elevación:** solo bordes de 1 px; sombra únicamente en
  diálogos y menús.
- **Movimiento:** 150-200 ms con `ease-out`; nada animado en bucle salvo el punto "en vivo"; todo
  desactivado con `prefers-reduced-motion`.
- **Estados siempre con ícono + texto + color**, nunca solo color: `CheckCircle2` Detectando,
  `Radio` En vivo (punto pulsante con `--accent`, no rojo: el rojo es "crítico"), `PauseCircle`
  Pausado, `WifiOff` Sin conexión, `CameraOff` Cámara no disponible.
- **Colores por clase de EPP** (identidad, no magnitud), mapeo **fijo por nombre de clase**,
  nunca por ranking ni por orden de aparición:

  | Clase | Slot | Claro | Oscuro |
  |---|---|---|---|
  | `hairnet` | 1 azul | `#2a78d6` | `#3987e5` |
  | `lab_coat` | 2 naranja | `#eb6834` | `#d95926` |
  | `face_mask` | 3 aqua | `#1baf7a` | `#199e70` |
  | `latex_gloves` | 4 amarillo | `#eda100` | `#c98500` |
  | cualquier clase nueva | slots 5-8 en orden, luego "Otras" en gris | | |

  Con 4 clases, las formas adyacentes (barras apiladas, líneas) son seguras ante daltonismo, pero
  las **etiquetas directas son obligatorias** (amarillo y naranja comparten pantalla) y el slot
  amarillo en tema claro está por debajo de 3:1, así que siempre lleva su etiqueta o la vista de
  tabla. Validar el set con el validador del skill `dataviz`
  (`node scripts/validate_palette.js "#2a78d6,#eb6834,#1baf7a,#eda100" --mode light` y el
  equivalente `--mode dark` con los valores oscuros) y pegar el resultado en
  `docs/DESIGN_SYSTEM.md`. **Las chips de clase usan texto en tinta normal y un punto o borde del
  color de la clase**: el texto nunca va en el color de la serie.

#### 3.9 Estructura de navegación y layout

- **Escritorio (≥ 1024 px):** barra lateral de 232 px, colapsable a 64 px (solo íconos con
  tooltip), con *Resumen*, *En vivo*, *Eventos*, *Dispositivo* y, para `admin`, *Administración*.
  Barra superior de 56 px: selector de dispositivo (preparado para varios; hoy uno), **píldora de
  estado global**, selector de tema y menú de usuario (foto, email, rol, cerrar sesión).
  Contenido con un máximo de 1440 px y márgenes de 24 px.
- **Tablet (768-1023 px):** barra lateral colapsada por defecto.
- **Móvil (< 768 px):** navegación inferior con 4 destinos (Resumen, En vivo, Eventos, Más),
  márgenes de 16 px, sin scroll horizontal, objetivos táctiles ≥ 44 px.
- **Inactividad (`useIdle`):** sin interacción (mouse, teclado, toque, scroll) durante
  `15 min` → se detienen todos los intervalos de TanStack Query, se cierra el WebSocket de video y
  se muestra una capa: "Sesión en pausa para ahorrar recursos · Los datos se actualizarán al
  volver · [Reanudar]". Con la pestaña oculta, TanStack ya no refresca y el video se cierra a los
  30 s.
- Atajos de teclado (con ayuda en `?`): `g r` Resumen, `g v` En vivo, `g e` Eventos, `f` pantalla
  completa en "En vivo", `/` buscar evento por ID.

#### 3.10 Pantallas

Cada pantalla define estados de **carga** (esqueletos con la forma final, nunca spinners de
página completa), **vacío** (qué significa y qué hacer), **error** (mensaje claro + "Reintentar")
y **sin permiso**.

**1. Inicio de sesión.** Centrado: el nombre "EPP Monitor" en texto (sin logo, U8), una línea de propósito ("Monitoreo
de equipos de protección personal"), botón oficial de Google, aviso de privacidad breve con enlace
a `PRIVACY.md` publicado como página `/privacidad`. Si la cuenta no está autorizada: "Tu cuenta
(x@y) no tiene acceso. Pide acceso al administrador." sin revelar nada más.

**2. Resumen** (`/`):

```
┌ Estado del dispositivo ─────────────────────────────────────────────────────────┐
│ ● Detectando · raspberry-pi-01 · última señal hace 2 min · 4.8 fps · 52 °C   [Ver en vivo →] │
└──────────────────────────────────────────────────────────────────────────────────┘
[ 24 h | 7 días | 30 días ]                                        hora de Lima
┌ Eventos ─────────┐┌ Tiempo con EPP ──┐┌ Confianza media ─┐┌ Cobertura de capturas ┐
│ 1 284            ││ 6 h 12 m         ││ 84 %             ││ 99,8 %                 │
│ ▲ 12 % vs 7 d ant││ ▲ 5 %            ││ ▼ 2 pts          ││ 2 sin captura → ver    │
│ ▁▂▄▃▅▆▇ (sparkl.)││ ▁▂▃▃▄▅▆          ││                  ││                        │
└──────────────────┘└──────────────────┘└──────────────────┘└────────────────────────┘
┌ Actividad (eventos por hora/día) ───────────────────┐┌ Por clase de EPP ──────────┐
│ columnas en un solo tono (--accent), grilla tenue,   ││ barras horizontales en un   │
│ tooltip por columna; conmutador [Total | Por clase]  ││ solo tono, ordenadas desc., │
│ (por clase = columnas apiladas con colores de clase, ││ valor al final de la barra  │
│ leyenda + etiquetas); botón "Ver como tabla"         ││ y clic → Eventos filtrados  │
└──────────────────────────────────────────────────────┘└────────────────────────────┘
┌ Últimos eventos ─────────────────────────────────────────────────── Ver todos → ┐
│ [miniatura 16:9] latex_gloves · 93 % · 9 s · hace 3 min   (6 tarjetas, scroll-x en móvil) │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- KPIs como *stat tiles* (no gráficos de una barra). El delta lleva flecha + texto y usa
  `--delta-good-text` / `--status-critical` según si la variación es buena o mala para el
  negocio (más eventos de EPP = bueno; menos confianza = malo). Si el periodo anterior no tiene
  datos, se muestra "—", no "+∞ %".
- **Cobertura de capturas** es un KPI de calidad de datos que hace visible cualquier pérdida en el
  lake. Clic → Eventos filtrados por `capture_status != ok`.
- Gráficos según `dataviz`: marcas finas, extremos redondeados de 4 px anclados a la base,
  separación de 2 px entre columnas, un solo eje Y, grilla tenue, tooltip por marca con un área
  de clic mayor que la marca, leyenda presente cuando hay ≥ 2 series, vista de tabla disponible.
  Un solo tono para magnitud; colores de clase solo en el modo "Por clase".

**3. En vivo** (`/live`):

```
┌──────────────────────────────────────────────────────┐┌ Panel lateral (320 px) ────┐
│ ● EN VIVO   ≈ 420 ms   8 fps          [⛶] [⏸ Pausar*] ││ Estado del dispositivo     │
│                                                      ││  inferencia 4.9 fps · p50   │
│                <canvas 16:9, letterbox negro>        ││  180 ms · 52 °C · outbox 0  │
│                                                      ││  modelo a1b2c3 · v2.0.0     │
│  (estado: conectando / esperando / señal congelada)  ││ Detecciones recientes       │
└──────────────────────────────────────────────────────┘│  [mini] hairnet · hace 10 s │
 * solo operator/admin, con diálogo de confirmación     │  [mini] lab_coat · hace 1 m │
                                                        └────────────────────────────┘
```

- La latencia "≈" y los fps salen de las métricas del reproductor (3.6). La insignia EN VIVO usa
  el acento, no el rojo.
- Pantalla completa con `f`; en móvil, el panel lateral pasa debajo del video.
- "Pausar detección" abre un diálogo: "La cámara dejará de registrar eventos hasta que alguien la
  reanude. ¿Continuar?" Después: toast "Detección pausada por x@y" y la píldora global en
  `Pausado`.

**4. Eventos** (`/events`): explorador con la evidencia.

- Fila de filtros arriba (un solo renglón, se pliega en móvil): rango de fechas (presets: hoy,
  24 h, 7 días, 30 días y personalizado), clase (multiselección con chips de color), confianza
  mínima (slider 0-100 %), "incluir simulados" (apagado por defecto), búsqueda por `event_id`.
  Los filtros viven en la URL (`?clase=hairnet&from=...`) para poder compartir un enlace.
- Vista **galería** (por defecto): tarjetas con la miniatura 16:9 (`loading="lazy"`,
  `decoding="async"`, `aspect-ratio` fijo), chip de clase, confianza con barra-medidor, duración,
  hora relativa con la absoluta en el tooltip y una marca "Simulado" si corresponde.
- Vista **tabla**: columnas Hora (absoluta, `tabular-nums`), Clase, Duración, Confianza
  prom./máx., Frames, Captura (✓ / ícono de advertencia + motivo), ID (truncado, copiable).
  Ordenable por hora y confianza; densidad cómoda/compacta.
- Paginación por cursor con "Cargar más" (no páginas numeradas: el cursor es lo correcto con
  Firestore).

**5. Detalle de evento** (panel lateral de 560 px en escritorio, pantalla completa en móvil;
ruta `/events/:eventId` para enlazarlo directamente):

- Imagen original con **las cajas superpuestas en SVG** desde `detections_at_capture` (coordenadas
  normalizadas → `viewBox="0 0 1 1"`). Énfasis: la clase del evento con su color de clase y trazo
  de 2 px; las demás detecciones en gris neutro y trazo de 1 px. Etiqueta de cada caja con fondo
  `--surface-1` al 85 % y texto en tinta normal. Interruptor "Mostrar cajas".
- Datos: clase, confianza promedio/máxima (medidor), inicio, fin y duración con una **línea de
  tiempo** (barra de la sesión con un marcador en el momento de la captura), frames, motivo de
  cierre, dispositivo, versión del modelo y del software, `event_id` con botón copiar, enlace al
  objeto del lake (solo `admin`).
- Acciones: "Descargar original" (queda registrado en la auditoría), "Copiar enlace al evento".
- Navegación ← / → entre eventos de la lista actual.

**6. Dispositivo** (`/device`): estado detallado y, para `operator`, pausar/reanudar con
confirmación; tarjetas de salud (inferencia, temperatura con umbrales de estado: < 70 °C bien,
70-80 atención, > 80 crítico; outbox pendiente; última señal; cámara; versiones). Mensajes de
diagnóstico accionables, por ejemplo "Sin señal desde hace 25 min. Revisa la alimentación y la red
de la Pi (ver runbook)".

**7. Administración** (`/admin`, solo `admin`): pestaña *Usuarios* (tabla con email, rol,
activo y fecha de alta; agregar con validación de email; cambiar rol; desactivar con
confirmación; no se puede dejar el sistema sin admin) y pestaña *Auditoría* (tabla filtrable por
acción y usuario, paginada).

#### 3.11 Componentes del sistema (`components/`)

`AppShell`, `Sidebar`, `TopBar`, `BottomNav`, `StatusPill`, `StatTile` (valor, delta, sparkline
opcional), `Sparkline`, `ActivityChart`, `ClassBreakdown`, `ClassChip`, `ConfidenceMeter`,
`EventCard`, `EventTable`, `EventDetailPanel`, `CaptureViewer` (imagen + overlay SVG),
`SessionTimeline`, `LivePlayer`, `FilterBar` (`DateRangePicker` con presets, `ClassMultiSelect`),
`EmptyState`, `ErrorState`, `Skeleton`, `ConfirmDialog`, `Toast` (`aria-live="polite"`),
`IdleOverlay`, `CopyButton`, `RequireRole`.

- Cada componente con su historia/estado documentado en `docs/DESIGN_SYSTEM.md` (captura en ambos
  temas). Opcional: Storybook si el usuario lo quiere para la tesis.
- Los gráficos se envuelven en componentes propios que **imponen** las reglas de `dataviz`
  (grosor, radios, separación, grilla, tooltip, tabla alternativa). Las páginas no configuran
  Recharts directamente.

#### 3.12 Accesibilidad (WCAG 2.2 AA) y contenido

- Contraste de texto ≥ 4.5:1 (≥ 3:1 para texto grande y elementos de interfaz); verificar ambos
  temas con axe (Playwright + `@axe-core/playwright`) en la CI.
- Foco visible de 2 px con `--accent` y desplazamiento de 2 px; orden de foco lógico; "Saltar al
  contenido".
- Todo funciona con teclado (Radix ya resuelve diálogos, menús y pestañas).
- `alt` de las capturas: "Captura del evento: {clase}, {hora}, confianza {x} %".
- Cambios de estado del dispositivo anunciados con `aria-live="polite"`.
- Cada gráfico tiene título, descripción breve (`aria-describedby`) y vista de tabla.
- Textos en español neutro (es-PE), en segunda persona, sin jerga técnica en la UI (por ejemplo
  "Sin señal del dispositivo", no "WebSocket closed 1006"). Números con `Intl.NumberFormat("es-PE")`.

#### 3.13 Validación de UX (para la tesis)

- Prueba de usabilidad con 5 participantes y 5 tareas cronometradas: (1) "¿El dispositivo está
  funcionando ahora?", (2) "¿Cuántos eventos de guantes hubo ayer?", (3) "Muéstrame la captura del
  último evento de mascarilla", (4) "Abre el video en vivo", (5) "Comparte el enlace de un evento".
  Meta: ≥ 80 % de éxito sin ayuda; tarea 1 en < 5 s. Cuestionario SUS (meta ≥ 75). Registrar los
  resultados en `docs/DESIGN_SYSTEM.md`.
- Revisión heurística (Nielsen) antes de la prueba, con los hallazgos corregidos.

### Criterios de aceptación — Fase 3

- [ ] Ventana privada → solo pantalla de login. Cuenta no autorizada → mensaje de acceso denegado y
      `login_denied` en `audit_log`. Sin cookie: `curl /api/devices` → 401.
- [ ] `POST /api/devices/x/desired-state` sin `X-Requested-With` → 403; con rol `viewer` → 403.
- [ ] WebSocket `/live` con `Origin` ajeno → rechazado.
- [ ] 3 usuarios con "Resumen" abierto 10 min → como mucho ~1 lectura de Firestore cada 10 s
      (caché compartida); con todos inactivos o con la pestaña oculta → 0 requests.
- [ ] Nadie en "En vivo" → la Pi no transmite. Al entrar → primer frame en < 5 s con la instancia
      caliente; la latencia "≈" y los fps se muestran. Al salir el último espectador → la Pi corta
      en ≤ 90 s.
- [ ] 15 min sin interacción → capa de pausa, WebSocket cerrado y sin requests en los logs.
- [ ] Clic en un evento → la captura correcta con sus cajas; `/events/<id>` abre el mismo
      detalle directamente.
- [ ] Lighthouse (móvil) en `/`: Performance ≥ 90, Accesibilidad ≥ 95; axe sin violaciones
      serias en las 7 pantallas y en ambos temas.
- [ ] `pnpm test`, `pnpm test:e2e`, `pnpm lint` y `pnpm typecheck` en verde; `schema.d.ts`
      sincronizado con el OpenAPI.
- [ ] Revisión visual en 360 px, 768 px y 1440 px, en tema claro y oscuro: sin solapamientos ni
      scroll horizontal.

---

## 8. Fase 4 — Infraestructura GCP 🔒 GCP (todo este bloque requiere OK)

Scripts idempotentes y comentados en `infra/`; políticas JSON en `infra/policies/`.

### 4.1 Prerrequisitos

```bash
gcloud services enable secretmanager.googleapis.com bigquery.googleapis.com \
  bigqueryconnection.googleapis.com bigquerydatatransfer.googleapis.com
gcloud firestore databases list --format="table(name,locationId,deleteProtectionState)"
```

### 4.2 Secretos

```bash
gen() { python -c "import secrets;print(secrets.token_urlsafe(48),end='')"; }
gen | gcloud secrets create device-token --data-file=-
gen | gcloud secrets create session-secret --data-file=-
```

**Cliente OAuth (lo crea el usuario en la consola):** Google Auth Platform → Branding (nombre
"EPP Monitor", correo de soporte, enlace a `/privacidad`), Audience **External** (los alcances
`openid email profile` no son sensibles y no requieren verificación), Clients → *Web application*
con **Authorized JavaScript origins** = `PUBLIC_BASE_URL` (y `http://localhost:5173` para
desarrollo). Con Google Identity Services y el flujo de ID token **no hay client secret**: solo el
*client ID*, que no es secreto y va como variable de entorno.

El agente **no** lee ni imprime los secretos. Para la Pi, el usuario ejecuta
`gcloud secrets versions access latest --secret=device-token` y lo pega en
`/etc/epp-edge/edge.env`. El viejo `UPLINK_TOKEN` se elimina del servicio en 4.3.

### 4.3 SA de runtime y deploy del servicio

```bash
gcloud iam service-accounts create platform-runtime --display-name="Cloud Run: API + relay + SPA"
SA=platform-runtime@epp-detection-v1.iam.gserviceaccount.com
for s in device-token session-secret; do
  gcloud secrets add-iam-policy-binding $s --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
done
gcloud projects add-iam-policy-binding epp-detection-v1 --member=serviceAccount:$SA --role=roles/datastore.user
gcloud storage buckets add-iam-policy-binding gs://epp-detection-v1-lake --member=serviceAccount:$SA --role=roles/storage.objectViewer
# Firmar URLs de GCS sin archivo de key (signBlob sobre sí misma):
gcloud iam service-accounts add-iam-policy-binding $SA --member=serviceAccount:$SA --role=roles/iam.serviceAccountTokenCreator
```

`infra/deploy_cloud.sh`:

```bash
# --source=. usa el Dockerfile multi-etapa de la raíz (compila frontend/ y empaqueta backend/)
gcloud run deploy orchestrator-service --source=. --region=us-central1 \
  --service-account=platform-runtime@epp-detection-v1.iam.gserviceaccount.com \
  --min-instances=0 --max-instances=1 --concurrency=40 --session-affinity \
  --cpu=1 --memory=512Mi --cpu-throttling --no-cpu-boost --timeout=3600 \
  --set-secrets=DEVICE_TOKEN=device-token:latest,SESSION_SECRET=session-secret:latest \
  --set-env-vars=ALLOWED_DEVICES=raspberry-pi-01,AUTH_MODE=google,PUBLIC_BASE_URL=<URL>,GOOGLE_OAUTH_CLIENT_ID=<ID>,LAKE_BUCKET=epp-detection-v1-lake,RUNTIME_SA_EMAIL=platform-runtime@epp-detection-v1.iam.gserviceaccount.com \
  --remove-env-vars=UPLINK_TOKEN \
  --allow-unauthenticated
```

- Se conserva el nombre `orchestrator-service` para que la URL de la Pi no cambie.
- `--cpu-throttling` = facturación por request (no cambiar). `--timeout=3600` reduce las
  reconexiones (C10).
- `--concurrency=40` = tope de requests simultáneas (API + WebSockets de video + Pi) en la única
  instancia. Las requests REST duran milisegundos, así que alcanza de sobra para decenas de usuarios.
- `--allow-unauthenticated` es necesario (navegadores y Pi sin identidad de Google); la
  protección la dan el login de Google + lista blanca + cookie de sesión y `DEVICE_TOKEN`. Documentarlo en `SECURITY.md`.
- Rollback: `gcloud run services update-traffic orchestrator-service --region=us-central1 --to-revisions=<anterior>=100`.

### 4.4 Firestore: índices, reglas, TTL, protección y backups

```bash
gcloud firestore indexes composite create --database=dbraspberry --collection-group=detecciones_confirmadas \
  --field-config=field-path=device_id,order=ascending --field-config=field-path=end_ts,order=descending
gcloud firestore indexes composite create --database=dbraspberry --collection-group=detecciones_confirmadas   --field-config=field-path=device_id,order=ascending --field-config=field-path=nombre_clase,order=ascending   --field-config=field-path=end_ts,order=descending
gcloud firestore indexes composite create --database=dbraspberry --collection-group=daily_stats \
  --field-config=field-path=device_id,order=ascending --field-config=field-path=date,order=ascending
gcloud firestore fields ttls update expire_at --collection-group=audit_log --enable-ttl --database="(default)"
for db in "(default)" dbraspberry; do
  gcloud firestore databases update --database="$db" --delete-protection
  gcloud firestore backups schedules create --database="$db" --recurrence=weekly --day-of-week=SUN --retention=4w
done
```

(Comprobar las flags con `--help` de la versión instalada.)

**Reglas** (Consola → Firestore → cada base → Reglas; copia en `infra/firestore.rules`). Todo el
acceso es por SDK de servidor; ningún cliente debe poder leer ni escribir:

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    match /{document=**} { allow read, write: if false; }
  }
}
```

Datos iniciales: `device_control/raspberry-pi-01` = `{desired_state: "stopped", stream_requested: false}`
y `dashboard_users/admin@viz-ia.com` = `{role: "admin", active: true, created_by: "bootstrap",
created_at: SERVER_TIMESTAMP}` (U7), con el script idempotente `infra/bootstrap_admin.py`
(no sobrescribe si ya existe). El backend nunca permite desactivar ni degradar al último `admin`. Marcar `device_status`
como obsoleto en `DATA_MODEL.md` y borrarlo después de 30 días con OK.

### 4.5 SA de la Pi (mínimo privilegio)

- `raspberry-pi-worker`: `roles/datastore.user` (proyecto) y `roles/storage.objectCreator`
  **solo** en `gs://epp-detection-v1-lake`. Nada más.
- Riesgo residual (documentar): Firestore no limita por colección en IAM, así que una key filtrada
  podría escribir en `device_control`. Se mitiga con la rotación documentada y con la auditoría.

### 4.6 Data lake: bucket (ver diseño completo en la Fase 5)

```bash
gcloud storage buckets create gs://epp-detection-v1-lake --location=us-central1 \
  --uniform-bucket-level-access --public-access-prevention --soft-delete-duration=7d
gcloud storage buckets update gs://epp-detection-v1-lake --lifecycle-file=infra/policies/lake-lifecycle.json
gcloud storage buckets add-iam-policy-binding gs://epp-detection-v1-lake \
  --member=serviceAccount:raspberry-pi-worker@epp-detection-v1.iam.gserviceaccount.com --role=roles/storage.objectCreator
```

`infra/policies/lake-lifecycle.json` (U6: imágenes 60 días; los JSON de eventos no expiran):

```json
{"rule": [
  {"action": {"type": "SetStorageClass", "storageClass": "NEARLINE"},
   "condition": {"age": 30, "matchesPrefix": ["raw/images/", "raw/thumbs/"]}},
  {"action": {"type": "Delete"},
   "condition": {"age": 60, "matchesPrefix": ["raw/images/", "raw/thumbs/"]}}
]}
```

Nearline tiene una duración mínima de 30 días: pasar a Nearline el día 30 y borrar el día 60 la
cumple exactamente, sin cargo por borrado anticipado. Cuando una imagen se borra, su evento y su
JSON se conservan con `capture_uri` apuntando a un objeto que ya no existe: la API debe responder
`capture_status = "expired"` (calculado: `end_ts` hace más de 60 días) y la UI mostrar
"Captura eliminada por política de retención (60 días)", no un error. Agregar
`LAKE_IMAGE_RETENTION_DAYS=60` a la configuración del backend.

Activar los Data Access audit logs de Cloud Storage **solo en este bucket** no es posible por
bucket; activarlos para el servicio `storage.googleapis.com` (DATA_READ) es opcional y genera
volumen de logs: dejarlo como decisión documentada en `PRIVACY.md`.

### 4.7 BigQuery (ver DDL en la Fase 5)

```bash
bq --location=us-central1 mk --dataset epp-detection-v1:epp_lake
bq mk --connection --location=us-central1 --project_id=epp-detection-v1 --connection_type=CLOUD_RESOURCE lake-conn
bq show --connection epp-detection-v1.us-central1.lake-conn     # copiar serviceAccountId
gcloud storage buckets add-iam-policy-binding gs://epp-detection-v1-lake \
  --member=serviceAccount:<serviceAccountId> --role=roles/storage.objectViewer
```

### 4.8 Borrar la tubería Pub/Sub (U2)

Antes de borrar: verificar que `dbraspberry/detecciones_por_tiempo` siga vacía (si no, algo
sigue publicando y hay que investigarlo primero).

```bash
gcloud eventarc triggers list --location=us-central1
gcloud eventarc triggers delete <TRIGGER> --location=us-central1
gcloud run services delete guardar-detecciones-firestore --region=us-central1
gcloud pubsub subscriptions list --format="value(name)"        # borrar todas las asociadas a yolo-detections
gcloud pubsub subscriptions delete eventarc-us-central1-pubsub-to-firestore-sub-983
gcloud pubsub topics delete yolo-detections
gcloud projects remove-iam-policy-binding epp-detection-v1 \
  --member=serviceAccount:eventarc-trigger-invoker@epp-detection-v1.iam.gserviceaccount.com --role=roles/run.invoker
gcloud iam service-accounts delete eventarc-trigger-invoker@epp-detection-v1.iam.gserviceaccount.com
gcloud services disable eventarc.googleapis.com pubsub.googleapis.com
```

Si `gcloud services disable` avisa de dependencias, **parar** y reportar (no usar `--force`).

### 4.9 Limpieza, SA de build y presupuesto

- **Artifact Registry** (`infra/policies/ar-cleanup.json`):
  ```json
  [{"name": "keep-last-3", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
   {"name": "delete-old", "action": {"type": "Delete"}, "condition": {"olderThan": "30d"}}]
  ```
  `gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy --location=us-central1 --policy=infra/policies/ar-cleanup.json`.
  Revisar el repo `gcr.io`; borrarlo con OK si no tiene imágenes en uso (la imagen del servicio
  legacy deja de usarse tras 4.8).
- **Bucket de fuentes de Cloud Build** (`run-sources-*`): lifecycle `Delete` a los 7 días.
- **SA de build (Q5)**: crear `cloud-build-deployer` con `roles/run.builder`, desplegar con
  `--build-service-account=projects/epp-detection-v1/serviceAccounts/cloud-build-deployer@epp-detection-v1.iam.gserviceaccount.com`
  y, **solo si funciona**, quitar `roles/editor` a `540868081764-compute@developer.gserviceaccount.com`.
- **Presupuesto**: umbrales 25/50/90/100 % y alerta por **pronóstico** al 100 %. El monto lo
  define el usuario según el costo esperado de la sección 13. Agregar
  `cesarestefanop@gmail.com` como destinatario (lo hace el usuario en Billing → Manage access; la
  automatización ya fue bloqueada dos veces).
- **Proyecto huérfano** `My First Project` (`woven-surface-469602-g1`): preguntar y, si no se usa,
  `gcloud billing projects unlink woven-surface-469602-g1`.

### Criterios de aceptación — Fase 4

- [ ] El servicio tiene maxScale 1, minScale 0, timeout 3600, SA `platform-runtime`, secretos
      montados y no tiene `UPLINK_TOKEN`.
- [ ] No existen topic/subscriptions de `yolo-detections`, el trigger de Eventarc ni el servicio
      `guardar-detecciones-firestore`.
- [ ] Reglas deny-all, protección contra borrado y backups semanales en ambas bases.
- [ ] El bucket del lake tiene `publicAccessPrevention: enforced`, UBLA, soft delete de 7 días
      y lifecycle.
- [ ] `raspberry-pi-worker` solo tiene `datastore.user` + `storage.objectCreator` (en el bucket).

---

## 9. Fase 5 — Data lake (estructurado + no estructurado)

### 5.1 Zonas y layout en GCS (`gs://epp-detection-v1-lake`)

```
raw/                                   # BRONCE: inmutable, escrito solo por la Pi (create-only)
  images/device=<id>/dt=<YYYY-MM-DD>/<event_id>.jpg   # frame original sin anotar (no estructurado)
  thumbs/device=<id>/dt=<YYYY-MM-DD>/<event_id>.jpg   # miniatura anotada (para el dashboard)
  events/device=<id>/dt=<YYYY-MM-DD>/<event_id>.json  # sidecar NDJSON de 1 línea (estructurado)
exports/                               # opcional: datasets armados para etiquetado/reentrenamiento
```

- Particiones estilo Hive (`device=`, `dt=`). Las claves son `device`/`dt` y **no** `device_id`/`date`
  para que no choquen con campos del JSON al consultar en BigQuery.
- `dt` = fecha local (`SITE_TIMEZONE`) de `end_ts`.
- Los objetos son **inmutables**: escritos con `if_generation_match=0`; la Pi no puede
  sobrescribir ni borrar.
- **Metadata en cada imagen** (`x-goog-meta-*`): `event_id`, `device_id`, `nombre_clase`,
  `confianza_max`, `model_version`, `captured_at`, `sha256`, `kind` (`original`|`thumb`). La imagen
  se describe sola aunque se pierda el resto.
- **Plata (curada)** y **oro (marts)** viven en BigQuery (5.3). No se escriben archivos curados.

### 5.2 Contrato del evento v2 (JSON del sidecar = documento de Firestore)

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

- En Firestore, `start_ts`/`end_ts`/`capture.captured_at` se guardan como Timestamp nativo y se
  agrega `created_at` (SERVER_TIMESTAMP). Los demás campos son idénticos.
- `capture.status`: `ok` | `missing` | `dropped_disk_full` | `missing_legacy`.
- Cualquier cambio de contrato incrementa `schema_version` y se documenta en `DATA_MODEL.md`.

### 5.3 BigQuery (`infra/bigquery/*.sql`, en orden)

**01 — tabla externa sobre los eventos (bronce):**

```sql
CREATE OR REPLACE EXTERNAL TABLE `epp-detection-v1.epp_lake.raw_events` (
  schema_version INT64, event_id STRING, event_type STRING, device_id STRING,
  clase_id INT64, nombre_clase STRING, confianza_promedio FLOAT64, confianza_max FLOAT64,
  frame_count INT64, start_ts TIMESTAMP, end_ts TIMESTAMP, duration_seconds FLOAT64,
  is_confirmed BOOL, closed_reason STRING, is_simulated BOOL,
  capture STRUCT<status STRING, uri STRING, thumb_uri STRING, captured_at TIMESTAMP, sha256 STRING,
                 width INT64, height INT64, bytes INT64, thumb_sha256 STRING, thumb_bytes INT64,
                 frame_seq INT64, confidence_at_capture FLOAT64>,
  detections_at_capture ARRAY<STRUCT<clase_id INT64, nombre_clase STRING, confianza FLOAT64,
                                     bbox_xyxy_norm ARRAY<FLOAT64>>>,
  edge STRUCT<edge_version STRING, model_version STRING, camera_source STRING>
)
WITH PARTITION COLUMNS (device STRING, dt DATE)
WITH CONNECTION `epp-detection-v1.us-central1.lake-conn`
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  uris = ['gs://epp-detection-v1-lake/raw/events/*'],
  hive_partition_uri_prefix = 'gs://epp-detection-v1-lake/raw/events',
  ignore_unknown_values = TRUE
);
```

**02 — object table sobre las imágenes (no estructurado):**

```sql
CREATE OR REPLACE EXTERNAL TABLE `epp-detection-v1.epp_lake.raw_images`
WITH CONNECTION `epp-detection-v1.us-central1.lake-conn`
OPTIONS (
  object_metadata = 'SIMPLE',
  uris = ['gs://epp-detection-v1-lake/raw/images/*'],
  metadata_cache_mode = 'AUTOMATIC',
  max_staleness = INTERVAL 1 DAY
);
```

(Columnas disponibles: `uri`, `generation`, `content_type`, `size`, `md5_hash`, `updated`,
`metadata` (incluye la metadata `x-goog-meta-*`, entre ella `event_id`) y `ref`, que permite
pasar la imagen a funciones de ML de BigQuery más adelante.)

**03 — tabla curada (plata), particionada y deduplicada por `event_id`:**

```sql
CREATE TABLE IF NOT EXISTS `epp-detection-v1.epp_lake.events`
PARTITION BY DATE(end_ts) CLUSTER BY device_id, nombre_clase AS
SELECT * EXCEPT(device, dt) FROM `epp-detection-v1.epp_lake.raw_events` WHERE FALSE;
```

**04 — consulta programada diaria (MERGE idempotente de los últimos 3 días):**

```sql
MERGE `epp-detection-v1.epp_lake.events` T
USING (
  SELECT * EXCEPT(device, dt, rn) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY end_ts DESC) rn
    FROM `epp-detection-v1.epp_lake.raw_events`
    WHERE dt >= DATE_SUB(CURRENT_DATE('America/Lima'), INTERVAL 3 DAY)
  ) WHERE rn = 1
) S
ON T.event_id = S.event_id AND DATE(T.end_ts) >= DATE_SUB(CURRENT_DATE('America/Lima'), INTERVAL 4 DAY)
WHEN NOT MATCHED THEN INSERT ROW;
```

Programar con
`bq mk --transfer_config --data_source=scheduled_query --display_name=curate_events --schedule="every day 06:00" --location=us-central1 --params='{"query":"<contenido de 04>"}'`
(zona del schedule: UTC; elegir una hora posterior a la medianoche de Lima). Para la carga
inicial, ejecutar una vez el MERGE sin filtro de fecha.

**05 — vistas oro:**

```sql
CREATE OR REPLACE VIEW `epp-detection-v1.epp_lake.v_events_with_capture` AS
SELECT e.*, i.size AS capture_object_bytes, i.updated AS capture_uploaded_at, i.ref AS capture_ref
FROM `epp-detection-v1.epp_lake.events` e
LEFT JOIN `epp-detection-v1.epp_lake.raw_images` i ON i.uri = e.capture.uri;

CREATE OR REPLACE VIEW `epp-detection-v1.epp_lake.v_daily_by_class` AS
SELECT device_id, DATE(end_ts, 'America/Lima') AS dia, nombre_clase,
       COUNT(*) AS eventos, SUM(duration_seconds) AS segundos,
       AVG(confianza_promedio) AS confianza_media,
       COUNTIF(capture.status = 'ok') AS eventos_con_captura
FROM `epp-detection-v1.epp_lake.events`
WHERE NOT is_simulated
GROUP BY 1, 2, 3;
```

### 5.4 Control de calidad y reconciliación (pérdida de datos)

`raspberrypi/tools/reconcile_lake.py --date YYYY-MM-DD` (se puede correr desde cualquier PC con
permisos de lectura) compara, para un día:

1. `daily_stats.events` (Firestore),
2. el número de documentos de `detecciones_confirmadas` con `end_ts` en ese día,
3. el número de objetos en `raw/events/.../dt=<día>/`,
4. el número de objetos en `raw/images/.../dt=<día>/`,
5. el número de filas en `epp_lake.events` para ese día.

Reporta los `event_id` que faltan en alguna capa. Criterio: 1 = 2 = 3, y 4 = eventos con
`capture.status = "ok"`. Documentarlo en el runbook como chequeo semanal.

### 5.5 Usos previstos (documentar en `docs/DATA_LAKE.md`)

- Reportes de cumplimiento (`v_daily_by_class`).
- Auditoría de un evento: `event_id` → documento → imagen original + cajas.
- Reentrenamiento: exportar a `exports/<dataset>/` las imágenes con `confianza_max` entre 0.5 y
  0.7 (las dudosas) junto con sus cajas normalizadas, en formato YOLO, para etiquetarlas.
- Evaluación por versión del modelo (`edge.model_version`).

### Criterios de aceptación — Fase 5

- [ ] Tras correr el simulador, `SELECT COUNT(*) FROM epp_lake.raw_events` = eventos generados, y
      `v_events_with_capture` tiene `capture_object_bytes` no nulo en todos.
- [ ] `reconcile_lake.py` sin diferencias para el día de la prueba.
- [ ] Una consulta por un `event_id` devuelve los datos estructurados y el `uri` de la imagen; ese
      `uri` abre la imagen correcta.

---

## 10. Fase 6 — Operación 24/7 en la Raspberry Pi

### 6.1 Hardware y SO (documentar en `docs/EDGE_DEPLOYMENT.md`)

- Raspberry Pi 5 de 4-8 GB (recomendada) o Pi 4 de 4 GB+, disipador activo y fuente oficial.
- **Raspberry Pi OS Lite 64-bit** (necesario para los wheels aarch64 de torch).
- SSD por USB 3/NVMe para `DATA_DIR` (escrituras del outbox y de las capturas pendientes); como
  mínimo una microSD *High Endurance*. Opcional: UPS HAT.
- Cámara CSI (picamera2) o USB UVC con MJPG.

### 6.2 `deploy/install.sh` (idempotente, con `sudo`)

1. `apt install -y python3-venv python3-picamera2 git unattended-upgrades`.
2. Usuario de sistema `epp`: `useradd --system --home-dir /var/lib/epp-edge --shell /usr/sbin/nologin --groups video epp`.
3. `install -d -o epp -g epp -m 0750 /var/lib/epp-edge /var/lib/epp-edge/models /var/lib/epp-edge/pending`;
   `install -d -o root -g epp -m 0750 /etc/epp-edge`.
4. Código en `/opt/epp-edge/app`; venv `/opt/epp-edge/venv` con `--system-site-packages`;
   `pip install -r raspberrypi/requirements.txt`.
5. `edge.env.template` → `/etc/epp-edge/edge.env` solo si no existe; `root:epp 0640`.
6. Key de la SA en `/etc/epp-edge/gcp-key.json`, `root:epp 0640`; **nunca** dentro de
   `/opt/epp-edge/app`.
7. Modelo exportado (Fase 7) en `/var/lib/epp-edge/models/`.
8. Instalar el unit, `systemctl daemon-reload && systemctl enable --now epp-edge`.
9. `/etc/systemd/journald.conf.d/epp.conf`: `SystemMaxUse=200M`.
10. Watchdog de hardware: `dtparam=watchdog=on` en `/boot/firmware/config.txt` y
    `RuntimeWatchdogSec=15` en `/etc/systemd/system.conf.d/watchdog.conf`.
11. SSH solo con llave; `ufw default deny incoming` + SSH desde la LAN; Tailscale opcional para
    administración remota.
12. NTP activo (`timedatectl show -p NTPSynchronized`): los timestamps del lake dependen de él.

### 6.3 `deploy/epp-edge.service`

```ini
[Unit]
Description=EPP Edge - deteccion YOLO, capturas al data lake y streaming bajo demanda
Wants=network-online.target time-sync.target
After=network-online.target time-sync.target
StartLimitIntervalSec=0

[Service]
Type=notify
NotifyAccess=main
User=epp
Group=epp
SupplementaryGroups=video
WorkingDirectory=/opt/epp-edge/app/raspberrypi
EnvironmentFile=/etc/epp-edge/edge.env
Environment=HOME=/var/lib/epp-edge YOLO_CONFIG_DIR=/var/lib/epp-edge/ultralytics PYTHONUNBUFFERED=1
ExecStart=/opt/epp-edge/venv/bin/python -m epp_edge
Restart=always
RestartSec=5
WatchdogSec=60
TimeoutStartSec=180
TimeoutStopSec=30
KillSignal=SIGTERM
StateDirectory=epp-edge
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/epp-edge
MemoryMax=75%

[Install]
WantedBy=multi-user.target
```

No usar `PrivateDevices=` (picamera2 necesita `/dev/media*`, `/dev/video*` y `/dev/dma_heap`).

### 6.4 Actualizaciones y runbook

- `deploy/update.sh`: `git pull --ff-only` → `pip install -r` → `pytest -q raspberrypi/tests` →
  `systemctl restart epp-edge` → esperar a que `device_health.edge_version` cambie; si falla,
  volver al commit anterior y reiniciar.
- `docs/OPERATIONS_RUNBOOK.md`: logs (`journalctl -u epp-edge -f`), reinicio, rotación de la key
  de la Pi y del `device-token`, restauración desde backup, revisión del outbox
  (`sqlite3 /var/lib/epp-edge/outbox.db "select stage, count(*) from outbox group by 1"`),
  reconciliación semanal del lake, alta/baja de usuarios del dashboard y cierre de una sesión de
  pruebas con verificación de costos.

### Criterios de aceptación — Fase 6

- [ ] `systemctl kill -s SIGKILL epp-edge` → vuelve a correr en ≤ 10 s.
- [ ] Congelar la inferencia → reinicio por watchdog en ≤ 90 s.
- [ ] Reiniciar la Pi → arranca solo y `last_seen` se actualiza.
- [ ] `/etc/epp-edge` con 0640 `root:epp`; ninguna key dentro de `/opt/epp-edge/app`.

---

## 11. Fase 7 — Modelo de visión

- `tools/export_model.py`: `YOLO("torch/best.pt").export(format="ncnn", imgsz=416)` (opciones
  `--imgsz`, `--format ncnn|onnx|openvino`).
- `tools/benchmark.py`: `{pt, ncnn} × {320, 416, 640}`, 10 de warm-up + 100 inferencias sobre
  `media/prueba1.mp4`; p50/p95, FPS, temperatura de la CPU y `mAP50-95` si hay `data.yaml` de
  validación. Resultado en `docs/MODEL.md`.
- **Regla de selección:** la combinación más rápida con `mAP50-95` ≤ 2 puntos por debajo de
  `pt@640` y p95 < 0.7 / `INFERENCE_FPS`. Sin dataset de validación: `ncnn@416`, documentado
  como supuesto.
- Pesos fuera de git (GitHub Release o almacenamiento privado, según el usuario).
  `docs/MODEL.md` registra el SHA-256, las clases (`model.names`), `imgsz`, la fecha y las
  métricas. Cada evento guarda `edge.model_version`.
- Para la tesis (documentar, no implementar): el modelo detecta **presencia** de EPP; el
  indicador de cumplimiento requiere la clase `person` o clases de ausencia, y tracking por
  persona (`model.track(persist=True, tracker="bytetrack.yaml")`). El lake (capturas + cajas)
  es la base para ese reentrenamiento.

---

## 12. Fase 8 — Calidad, CI y prevención de fugas

### 8.1 `.gitignore` (reescritura)

```gitignore
# Entornos y secretos
venv/
.venv/
.env
.env.*
*.env
!*.env.template
*key*.json
service_account*.json
credentials*.json
client_secret*.json
*.pem
*.p12

# Artefactos
__pycache__/
*.py[cod]
*.zip
*.tar.gz
*.7z
*.pt
*.onnx
*_ncnn_model/
*.db
*.db-wal
*.db-shm
*.log
logs/

# Medios
*.mp4
*.avi
*.jpg
*.jpeg
*.png
!docs/**/*.png

# Notebooks e IDE
notebooks/
*.ipynb
.idea/
.vscode/
.DS_Store
```

Deja de ignorar `*.json` y `*.yml` en general (necesarios para `infra/policies/` y
`.github/workflows/`); las credenciales siguen cubiertas por patrones explícitos y por gitleaks.

### 8.2 Prevención de secretos

- `.pre-commit-config.yaml`: `gitleaks`, `ruff`, `ruff-format`.
- Pedir al usuario que active en GitHub *Secret scanning* y *Push protection* (gratis en repos
  públicos).

### 8.3 CI — `.github/workflows/ci.yml`

Sin credenciales de GCP y sin despliegues. Jobs en paralelo:

1. **secrets:** `gitleaks`.
2. **python:** `ruff check` + `ruff format --check` → `pytest` en `backend/` y `raspberrypi/`
   (sin `ultralytics` ni `picamera2`).
3. **frontend:** `pnpm install --frozen-lockfile` → `pnpm lint` → `pnpm typecheck` → `pnpm test` →
   `pnpm build` (fallar si el JS inicial supera el presupuesto de 3.5) → verificación de que
   `src/api/schema.d.ts` coincide con el OpenAPI generado por el backend.
4. **e2e:** Playwright con MSW (sin backend real) + `@axe-core/playwright` en las 7 pantallas y
   ambos temas.
5. **image:** `docker build .`.

### 8.4 Herramientas

- `pyproject.toml` en la raíz con `[tool.ruff]` (`line-length = 110`, `target-version = "py311"`,
  reglas `E,F,W,I,B,UP,S`, con `S101` ignorado en tests) y `[tool.pytest.ini_options]`.
- `frontend/`: `eslint.config.js` (typescript-eslint estricto, `jsx-a11y`, `react-hooks`),
  `.prettierrc`, `tsconfig.json` estricto, `.nvmrc`. Scripts de `package.json`: `dev`, `build`,
  `preview`, `lint`, `typecheck`, `test`, `test:e2e`, `gen:api`.
- Desarrollo local: `uvicorn epp_api.main:app --reload --port 8080` con `AUTH_MODE=dev` y
  `pnpm dev` (Vite en 5173 con proxy de `/api` y `/ws` al 8080). Documentarlo en el README.
- `.gitignore`: agregar `node_modules/`, `frontend/dist/`, `playwright-report/`, `test-results/`.

### Criterios de aceptación — Fase 8

- [ ] `pre-commit run --all-files` limpio y CI en verde.
- [ ] `gitleaks detect --source . --no-git` sin hallazgos. (El historial mostrará `243d2de` y
      `3f95ee1`; se documentan como revocados en `SECURITY.md`, no se borran.)

---

## 13. Fase 9 — Documentación

Cada documento abre con "Para quién es" y "Última verificación: fecha".

| Archivo | Contenido mínimo |
|---|---|
| `README.md` | Qué es, diagrama Mermaid de la sección 1.1, capturas de pantalla del dashboard (claro y oscuro), quickstart (Pi simulada; backend local con `AUTH_MODE=dev`; `pnpm dev`), enlaces a `docs/`, "Trabajo futuro" (tracking por persona, clases de ausencia de EPP, WebRTC, difuminado de rostros). Eliminar lo que describe el flujo viejo (Pub/Sub, `device_status`, `FRAME_SKIP`, auto-stream, MJPEG público). |
| `docs/ARCHITECTURE.md` | Componentes; hilos de la Pi y sus slots; secuencias "abrir En vivo" (login → WS `/live` → lease → listener de la Pi → uplink → relay → canvas) y "evento confirmado" (agregador → captura → outbox → lake → Firestore → BigQuery); modos de falla; presupuesto de latencia medido. |
| `docs/DATA_MODEL.md` | Contrato v2 (5.2); colecciones `detecciones_confirmadas`, `daily_stats` (`dbraspberry`); `device_control`, `device_health`, `dashboard_users`, `audit_log` (`(default)`); índices; obsoletos (`device_status`, `detecciones_por_tiempo`); reglas anti-ruido con líneas de tiempo de ejemplo. |
| `docs/DATA_LAKE.md` | Zonas, layout, inmutabilidad, metadata de objetos, tablas y vistas de BigQuery, MERGE programado, reconciliación, retención, ejemplos de consulta (por `event_id`, cumplimiento diario, exportación para reentrenamiento). |
| `docs/EDGE_DEPLOYMENT.md` | Hardware, flasheo, `install.sh` paso a paso, verificación, actualización, desinstalación. |
| `docs/CLOUD_DEPLOYMENT.md` | Todo `infra/`: secretos, cliente OAuth, SAs, deploy, índices, reglas, backups, bucket, BigQuery, rollback. |
| `docs/SECURITY.md` | Modelo de amenazas (STRIDE breve); inventario de credenciales y dónde vive cada una; rotación; incidentes (`243d2de` y el ZIP de `3f95ee1`, ambos revocados, con fecha); riesgos aceptados (`--allow-unauthenticated` con OAuth, `datastore.user` sin granularidad por colección). |
| `docs/PRIVACY.md` | Datos personales tratados (imágenes de trabajadores), aviso y base legal (Ley 29733), retención (60 días para imágenes, U6), quién accede (roles), auditoría, cómo atender una solicitud de eliminación (borrar objetos por `event_id` con una cuenta admin; la Pi no puede), riesgo de no difuminar rostros (Q7). |
| `docs/COSTS.md` | Sección 14 con las fórmulas, palancas, verificación al día siguiente y qué no hacer. |
| `docs/MODEL.md` | Fase 7. |
| `docs/OPERATIONS_RUNBOOK.md` | Fase 6.4 + "el stream no arranca", "la Pi aparece offline", "el outbox crece", "faltan capturas" (reconciliación), "subió la factura", "un usuario no puede entrar". |
| `docs/DESIGN_SYSTEM.md` | Principios (3.7), tokens (3.8) con la salida del validador de paleta, layout y navegación (3.9), una ficha por pantalla con capturas (3.10), catálogo de componentes (3.11), accesibilidad (3.12) y resultados de la prueba de usabilidad (3.13). |
| `docs/API.md` | Autenticación (cookie, CSRF, roles), tabla de endpoints (3.3), protocolo binario del video (1.2), formato de errores y cómo regenerar `schema.d.ts`. |
| `frontend/README.md` | Cómo correr, probar y compilar; estructura de carpetas; convenciones (consultas, componentes, tokens); cómo agregar una pantalla. |
| `docs/adr/0001-servicio-unificado-api-relay-spa.md`, `0007-frontend-react-typescript.md`, `0008-auth-google-identity-cookie.md`, `0002-video-cloud-run-vs-webrtc.md`, `0003-streaming-bajo-demanda-lease.md`, `0004-outbox-idempotente.md`, `0005-data-lake-gcs-bigquery.md`, `0006-event-id-uuidv7.md` | Contexto, decisión, alternativas, consecuencias (formato MADR corto). |
| `CHANGELOG.md` | `2.0.0` con los cambios de este plan. |

Borrar `riesgos.txt` (su contenido pasa a `EDGE_DEPLOYMENT.md` y al runbook).

---

## 14. Modelo de costos (para `docs/COSTS.md`)

Precios aproximados de referencia (`us-central1`). **Verificar en las páginas de precios vigentes
antes de publicarlos.**

| Recurso | Precio aprox. |
|---|---|
| Cloud Run vCPU (facturación por request) | US$0.000024 / vCPU-s → ≈ US$0.086/h a 1 vCPU |
| Cloud Run memoria | US$0.0000025 / GiB-s → ≈ US$0.009/h a 1 GiB |
| Egress de Cloud Run a internet | ≈ US$0.085-0.12 / GB |
| Firestore lecturas / escrituras | ≈ US$0.03-0.06 / 100 k · ≈ US$0.09-0.18 / 100 k |
| GCS Standard / Nearline / Coldline | ≈ US$0.020 / 0.010 / 0.004 por GB-mes (Nearline y Coldline cobran recuperación y duración mínima) |
| BigQuery | almacenamiento ≈ US$0.02 / GB-mes; consultas on-demand ≈ US$6.25 / TiB |

**Cómputo de Cloud Run.** Con `--cpu-throttling` la instancia factura solo mientras procesa
requests. La SPA hace requests de milisegundos cada 10-30 s (≈ 1-2 % del tiempo), así que tener el
dashboard abierto cuesta muy poco. Lo que factura en continuo son los **WebSockets de video**
(uplink de la Pi + navegador) mientras alguien está en "En vivo". Ejemplo: 1 h/día de video ≈
30 h/mes ≈ US$3/mes de cómputo; navegación normal del dashboard 8 h/día ≈ < US$1/mes.

**Egress de video por hora mirando.** `bytes/h = tamaño_frame × fps × 3600`.
Antes: frame completo q70 (estimado 80-150 KB, medir en la Pi) reenviado a ~33 fps → **≈ 10-18 GB/h**.
Después: 640 px q60 (≈ 25-35 KB) a 8 fps → **≈ 0.7-1.0 GB/h** (US$0.06-0.12/h), y solo con alguien
en "En vivo". Las miniaturas se sirven por URL firmada directamente desde GCS (≈ 30 KB por imagen
vista, cacheables por el navegador durante la vigencia de la URL).

**Carga de la SPA.** Los assets con hash se cachean un año en el navegador: la primera visita
descarga < 300 KB comprimidos; las siguientes, solo `index.html` y los JSON de la API. Flet
descargaba varios MB de runtime en cada sesión nueva.

**Firestore.** Por día, con un dispositivo y E eventos: ~300 escrituras de heartbeat + 2E (evento +
`daily_stats`) + 120/h de lease mientras se mira; las lecturas son del mismo orden (listeners) más
lo que piden las cachés del backend: como máximo 1 consulta incremental cada 10 s **mientras haya
algún usuario activo** (compartida entre todos) más ~500 lecturas por arranque de instancia. Con
E = 500, el costo es de centavos por mes.

**Data lake.** Por evento: original ≈ 150-250 KB (1280×720 q90) + miniatura ≈ 30 KB + JSON ≈ 2 KB.
Con 500 eventos/día ≈ 100-140 MB/día ≈ 3-4 GB/mes nuevos. Con la retención de 60 días (U6), en
régimen hay ≈ 6-8 GB de imágenes (mitad Standard, mitad Nearline) → **≈ US$0.10-0.15/mes**. La subida desde la Pi
(ingress) no se cobra. BigQuery: centavos (datos chicos, particiones por fecha).

**Riesgos residuales de costo:** un usuario autorizado que deja una pestaña abierta (acotado por
la inactividad); una `DEVICE_TOKEN` filtrada (acotada por `ALLOWED_DEVICES`, `max-instances=1` y
la rotación); alerta de presupuesto por pronóstico como red de seguridad.

---

## 15. Presupuesto de latencia (para `docs/ARCHITECTURE.md`)

Estimaciones, a validar con la medición de la Fase 10:

| Tramo | Antes | Después |
|---|---|---|
| Arranque del stream | 0-5 s (polling de la Pi) + 0-2 s (dashboard) + cold start | < 1 s (listener) + cold start (1-3 s) |
| Captura → frame disponible | crece con el buffer V4L2 | ≤ 1 frame (buffer=1, LatestSlot) |
| Frame → envío | captura + **inferencia** + dibujo + JPEG en serie | dibujo + JPEG (~10-20 ms a 640 px); la inferencia corre en paralelo |
| Relay | 0-30 ms (polling) | ~0 ms (notify) |
| Saltos de red del video | Pi → Cloud Run → backend del dashboard → navegador | Pi → Cloud Run → navegador |
| Entrega al navegador | Flet: polling a 8 fps + base64 por el WebSocket de Flet + re-render de Flutter | WebSocket binario, reenvío inmediato (tope `LIVE_MAX_FPS`), `createImageBitmap` + `<canvas>` |
| Primera carga del dashboard | runtime de Flutter/CanvasKit (varios MB) | SPA < 300 KB gzip, después desde la caché |

Región: se mantiene `us-central1`. Moverla a Sudamérica bajaría el RTT desde Perú pero encarece
cómputo y egress; solo se reconsidera si la medición muestra que la red es el cuello de botella.

---

## 16. Fase 10 — Verificación final y cierre

1. **Punta a punta** (Pi real o simulador):
   - 30 min en `running` sin nadie en "En vivo" → 0 conexiones de video en Cloud Run; cada evento
     con sus 3 objetos en el lake y su documento en Firestore; `device_health` cada 5 min.
   - Login con un usuario autorizado y 10 min en "En vivo": registrar p50/p95 de la latencia "≈"
     que calcula el reproductor y contrastarla una vez apuntando la cámara a un cronómetro en
     pantalla.
   - Cerrar la pestaña → la Pi corta en ≤ 90 s. Dejar otra pestaña abierta sin tocarla → a los
     15 min aparece la capa de pausa y en los logs de Cloud Run dejan de llegar requests.
   - Recorrer las 7 pantallas en móvil (360 px) y escritorio, en tema claro y oscuro, con un
     usuario `viewer` y uno `admin`.
   - Pi sin red 5 min con actividad → al volver, `reconcile_lake.py` sin diferencias.
   - `sudo systemctl restart epp-edge` con una sesión abierta → evento con
     `closed_reason="shutdown"` y su captura.
   - Ejecutar el MERGE de BigQuery y consultar `v_events_with_capture` por un `event_id`.
2. **Cierre de costos:** detener procesos, `desired_state="stopped"`, revisar Cloud Run, Firestore
   y Cloud Storage, y **repetir en Billing → Reports al día siguiente**. Anotar el resultado en
   `docs/COSTS.md`.
3. **Checklist de seguridad:** una sola key en `raspberry-pi-worker` y fuera del repo; Cloud Run
   sin archivos de key; secretos solo en Secret Manager o `edge.env`; gitleaks limpio; reglas
   deny-all; bucket con PAP enforced; acceso al dashboard solo para la lista blanca.
4. PR `remediacion/auditoria-2026-10` → `main` con el resumen por fase y la tabla de hallazgos con
   su estado. No hacer merge sin la revisión del usuario.

---

## 17. Qué NO hacer (resumen)

- No reescribir el historial de git ni proponerlo; no hacer privado el repo.
- No migrar datos entre `(default)` y `dbraspberry`.
- No subir `min-instances` ni quitar `max-instances=1`; no usar más de 1 worker de uvicorn.
- No exponer el dashboard sin el login y la lista blanca funcionando.
- No volver a Flet ni agregar otro framework de UI: el frontend es la SPA de `frontend/`.
- No consultar Firestore desde el navegador ni darle credenciales de GCP: todo pasa por la API.
- No configurar Recharts directamente en las páginas: usar los componentes de gráficos que imponen
  las reglas de `dataviz`. No inventar colores fuera de los tokens (3.8).
- No mantener intervalos de refresco ni el WebSocket de video activos con el usuario inactivo o la
  pestaña oculta.
- No poner tokens en query strings ni en nada que llegue al navegador.
- No dar a la Pi permisos de lectura o borrado sobre el lake.
- No caer al video de prueba en producción; no abrir el stream por detección salvo
  `AUTO_STREAM_ON_DETECTION=true` pedido explícitamente.
- No descartar un evento porque su captura falló: se registra con `capture.status`.
- No ejecutar nada en GCP sin el OK del usuario en esa misma sesión.
- No dar por bueno un "$0" el mismo día de las pruebas.
