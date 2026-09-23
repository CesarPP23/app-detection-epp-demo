# Sistema de Detección de EPP en Tiempo Real (Edge + Cloud)

**Proyecto de tesis** — sistema end-to-end de visión por computadora para verificar en tiempo real el uso de Equipo de Protección Personal (EPP: cofia, mascarilla, guantes de látex, bata de laboratorio) mediante un dispositivo de borde (Raspberry Pi + cámara), inferencia con YOLO, una lógica de negocio anti-ruido para convertir detecciones crudas en eventos confiables, y una nube (Google Cloud Platform) que persiste, transmite en vivo y expone esos eventos a través de un dashboard web.

El repositorio es público por requisito académico. Ningún secreto (claves de servicio, tokens, `.env`) vive en el historial de git — ver la sección [Configuración y variables de entorno](#configuración-y-variables-de-entorno).

---

## Tabla de contenidos

- [Resumen y motivación](#resumen-y-motivación)
- [Arquitectura del sistema](#arquitectura-del-sistema)
- [Pipeline de detección: de frame crudo a evento confiable](#pipeline-de-detección-de-frame-crudo-a-evento-confiable)
- [Streaming de video bajo demanda](#streaming-de-video-bajo-demanda)
- [Modelo de datos (Firestore)](#modelo-de-datos-firestore)
- [Stack tecnológico](#stack-tecnológico)
- [Estructura del repositorio](#estructura-del-repositorio)
- [Configuración y variables de entorno](#configuración-y-variables-de-entorno)
- [Puesta en marcha (desarrollo local / simulación)](#puesta-en-marcha-desarrollo-local--simulación)
- [Despliegue en una Raspberry Pi física](#despliegue-en-una-raspberry-pi-física)
- [Despliegue del backend en GCP](#despliegue-del-backend-en-gcp)
- [Limitaciones conocidas y trabajo futuro](#limitaciones-conocidas-y-trabajo-futuro)

---

## Resumen y motivación

En entornos donde el uso correcto de EPP es crítico (laboratorios, plantas de alimentos, áreas clínicas), la supervisión manual es costosa y poco confiable. Este proyecto explora una alternativa de bajo costo: un dispositivo de borde con una cámara y un modelo YOLO entrenado a medida, capaz de:

1. Detectar en video en vivo la presencia (o ausencia) de ítems de EPP por persona.
2. Filtrar el ruido inherente a la inferencia frame a frame (parpadeos, falsos positivos de un solo frame, oclusiones momentáneas) y convertirlo en **eventos de sesión** con significado real de negocio.
3. Persistir únicamente esos eventos confirmados en la nube, minimizando escritura y costo.
4. Permitir a un operador remoto ver el estado del dispositivo, revisar el historial y solicitar video en vivo solo cuando lo necesita — sin mantener un stream de video permanente y facturable.

El diseño prioriza **costo marginal cercano a cero**: cómputo en el borde (YOLO corre en la Pi/PC, no en la nube), servicios de GCP con *scale-to-zero* (Cloud Run `min-instances=0`), y streaming de video que se activa/desactiva solo, atado a actividad real.

## Arquitectura del sistema

```
┌───────────────────────────────┐
│   BORDE (Raspberry Pi o PC)   │
│                                │
│  Cámara/Video ──▶ YOLO ──────▶│── DetectionAggregator (anti-ruido)
│                                │        │
│                                │        ├─▶ evento confirmado ──▶ Firestore (directo)
│                                │        │
│                                │        └─▶ activity_now=True ──▶ StreamManager
│                                │                                       │
└───────────────────────────────┘                                       │ WebSocket (control + video)
                                                                          ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                          GOOGLE CLOUD PLATFORM (epp-detection-v1)             │
│                                                                                 │
│   Cloud Run: orchestrator-service (FastAPI)                                   │
│     /ws/control/{device_id}   ← comandos START_STREAM / STOP_STREAM           │
│     /ws/video/{device_id}     ← recibe frames JPEG del dispositivo            │
│     /video_stream/{device_id} → sirve MJPEG a quien esté viendo               │
│     /request_stream/...       → el dashboard pide iniciar/detener streaming   │
│                                                                                 │
│   Firestore                                                                   │
│     (default)/device_status/{device_id}      ← estado deseado y real (control)│
│     dbraspberry/detecciones_confirmadas      ← eventos limpios (fuente única  │
│                                                  de verdad para reportes)      │
└───────────────────────────────────────────────────────────────────────────────┘
                                                                          ▲
                                                                          │ lee estado / detecciones
                                                                          │ pide streaming on-demand
┌───────────────────────────────────────────────────────────────────────────────┐
│                     FRONT-END: Dashboard web (Flet / Python)                  │
│   KPIs, historial de eventos, ranking de clases, video en vivo bajo demanda   │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Por qué esta forma y no otra:**

- **Inferencia en el borde, no en la nube.** Correr YOLO en un backend central con GPU (diseño descartado) implica transmitir video permanentemente y pagar cómputo dedicado. Al inferir en el propio dispositivo, la nube solo recibe *eventos* (texto, ligero) y video *solo cuando hay algo que ver*.
- **Escritura directa a Firestore, no vía Pub/Sub.** Existe un pipeline legacy (`gcp/data/pubsub_to_firestore.py`, disparado por Eventarc) pensado para detecciones crudas por frame con un esquema fijo. Los eventos ya agregados y confirmados tienen campos que ese esquema no reconoce y **descartaría silenciosamente** (p. ej. `confianza_promedio` en vez de `confianza`). Por eso el dispositivo escribe eventos confirmados directo a `dbraspberry/detecciones_confirmadas`, y ese pipeline legacy queda aislado para su colección original (ver [Modelo de datos](#modelo-de-datos-firestore)).
- **Streaming como canal aparte del pipeline de datos.** El video en vivo (WebSocket + MJPEG) es puramente para observación humana; nunca es la fuente de los eventos guardados. Esto permite apagarlo agresivamente sin perder ni un dato.

## Pipeline de detección: de frame crudo a evento confiable

El corazón del proyecto no es "correr YOLO" sino decidir **cuándo una detección importa**. Esta lógica vive en `raspberrypi/utils/aggregator.py` (`DetectionAggregator`) y es consumida frame a frame por `raspberrypi/main.py`.

Problema original: a 30–60 fps, una sola persona sin un ítem de EPP durante unos segundos generaba decenas de filas idénticas — ruido, no información. La solución es una máquina de sesiones por clase, con reglas de negocio 100% configurables por variables de entorno (nunca hardcodeadas):

| Parámetro | Rol |
|---|---|
| `CONFIDENCE_THRESHOLD` | Descarta de plano cualquier detección cruda de YOLO por debajo de este umbral. |
| `MIN_SECONDS_TO_CONFIRM` | Una clase debe sostenerse en tiempo real (no en frames) al menos este tiempo antes de convertirse en un "evento confirmado" — filtra parpadeos de un solo frame. |
| `SESSION_GAP_SECONDS` | Si una clase desaparece y reaparece dentro de esta ventana, se trata como la **misma** sesión (tolera oclusiones cortas: alguien se agacha, un objeto tapa la cámara). |

Cuando una sesión realmente termina (gap mayor al configurado), el agregador la cierra y entrega **una sola fila agregada** — con `confianza_promedio`, `confianza_max`, `frame_count` y duración real — lista para persistir. El agregador también audita internamente `frames_processed` y `noise_discarded_count`, visibles en logs locales pero deliberadamente no persistidos (evitar reintroducir el ruido que se quiso eliminar).

## Streaming de video bajo demanda

`raspberrypi/utils/stream_manager.py` (`StreamManager`) coordina un canal de video **evento-driven**, no permanente:

1. Cuando el agregador reporta actividad confirmada en curso y el streaming está apagado, se abren dos conexiones WebSocket hacia `orchestrator-service`: una de control y una de video.
2. Mientras sigan llegando detecciones confirmadas, el streaming se mantiene.
3. Si pasan `STREAM_IDLE_TIMEOUT` segundos sin actividad confirmada nueva, el streaming se cierra automáticamente.
4. Un operador también puede forzar el streaming manualmente desde el dashboard (para inspección en vivo sin esperar una detección), sujeto al mismo timeout de inactividad.

Del lado del servidor, `orchestrator_main.py` (FastAPI en Cloud Run) actúa como *broker*: mantiene el último frame de cada dispositivo en memoria y lo redistribuye a quien esté viendo vía `multipart/x-mixed-replace` (MJPEG), sin persistir video en ningún lado.

En el dashboard (Flet Web), embeber un stream MJPEG directamente no es viable — el motor de renderizado (Flutter Web) espera una sola imagen completa, no un flujo infinito. La solución implementada: el propio backend Python del dashboard lee el MJPEG, extrae cada JPEG individual (marcadores `SOI`/`EOI`) y lo empuja a la UI como `src_base64`, con un único lector compartido por proceso (no por pestaña) para no multiplicar conexiones facturables contra Cloud Run.

## Modelo de datos (Firestore)

Dos bases de datos lógicas dentro del mismo proyecto de GCP:

### `dbraspberry` / `detecciones_confirmadas` — eventos limpios (fuente única para dashboards y reportes)

| Campo | Tipo | Descripción |
|---|---|---|
| `device_id` | string | Identificador del dispositivo, ej. `raspberry-pi-01` |
| `nombre_clase` | string | Clase YOLO detectada (`hairnet`, `lab_coat`, `face_mask`, `latex_gloves`, …) |
| `clase_id` | int | ID numérico de la clase en el modelo |
| `confianza_promedio` | float [0-1] | Confianza promedio durante toda la sesión |
| `confianza_max` | float [0-1] | Confianza máxima observada en la sesión |
| `frame_count` | int | Frames (post `FRAME_SKIP`) que contribuyeron a la sesión |
| `start_ts` / `end_ts` | Timestamp nativo | Inicio y fin real de la sesión |
| `duration_seconds` | float | Duración de la sesión |
| `is_confirmed` | bool | Siempre `true` en esta colección |

### `(default)` / `device_status` / `{device_id}` — estado en vivo / control

| Campo | Valores | Descripción |
|---|---|---|
| `state` | `stopped` \| `running` \| `streaming` | Campo de doble uso: es tanto el estado *deseado* que pide el dashboard como el estado *real* que reporta el propio dispositivo. |
| `updated_at` | string ISO | Último cambio de estado |

> El pipeline legacy (`gcp/data/pubsub_to_firestore.py`, disparado vía Pub/Sub + Eventarc) escribe detecciones crudas por frame a una colección separada con un esquema fijo distinto. No es la fuente recomendada para análisis: no aplica ninguna de las reglas anti-ruido descritas arriba.

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Visión por computadora | [Ultralytics YOLO](https://github.com/ultralytics/ultralytics), OpenCV |
| Dispositivo de borde | Python 3, `torch` (CPU), SQLite (cola offline ante fallas de red) |
| Backend de streaming | FastAPI + WebSockets, Google Cloud Run |
| Persistencia | Google Cloud Firestore (modo nativo, dos bases de datos) |
| Mensajería (pipeline legacy) | Google Cloud Pub/Sub + Eventarc |
| Dashboard | [Flet](https://flet.dev/) (Python puro, sin JS/HTML separado) |
| Infraestructura | Google Cloud Platform, contenedores Docker |

## Estructura del repositorio

```
.
├── raspberrypi/                  # Dispositivo de borde — corre igual en una Pi real o en una PC simulándola
│   ├── main.py                   # Entrypoint único: loop de video, YOLO, agregador, streaming
│   ├── utils/
│   │   ├── aggregator.py         # DetectionAggregator — lógica anti-ruido (ver arriba)
│   │   ├── stream_manager.py     # StreamManager — ciclo de vida del streaming evento-driven
│   │   ├── detector.py           # Wrapper de inferencia YOLO sobre un frame
│   │   ├── create.py             # Utilidad: crear/inicializar device_status en Firestore
│   │   ├── check_firestore.py    # Utilidad de diagnóstico de conexión a Firestore
│   │   └── suscriber.py          # Utilidad de diagnóstico del pipeline Pub/Sub legacy
│   ├── scripts/create_database.py# Crea la cola SQLite local si no existe
│   ├── worker.py                 # Variante experimental/anterior (publica crudo a Pub/Sub) — no es el flujo recomendado
│   └── requirements_raspberrypi.txt
│
├── gcp/                           # Backend en la nube
│   ├── orchestrator_main.py      # FastAPI: WebSockets de control/video + MJPEG para espectadores
│   ├── Dockerfile
│   ├── data/pubsub_to_firestore.py  # Cloud Function/Run legacy: Pub/Sub → Firestore (esquema crudo)
│   └── requirements*.txt
│
├── front-end/                     # Dashboard del operador
│   ├── main.py                   # App Flet: KPIs, historial, ranking de clases, video en vivo
│   └── core/
│       ├── config.py              # Configuración centralizada (lee .env)
│       └── firestore_client.py    # Lectura/escritura de Firestore para el dashboard
│
├── riesgos.txt                   # Notas de riesgos operativos y mitigaciones (SD card, watchdog, energía)
└── .env.example                  # Plantilla de variables de entorno (no versionada, ver abajo)
```

## Configuración y variables de entorno

Todas las reglas de negocio y credenciales se inyectan por entorno — nada se hardcodea en el código. Crea un archivo `.env` en la raíz del repo (o exporta las variables en tu shell) con las siguientes claves:

| Variable | Default | Descripción |
|---|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Ruta al JSON de la cuenta de servicio de GCP (relativa a la raíz del repo) |
| `GCP_PROJECT_ID` | — | ID del proyecto de GCP |
| `PUB_SUB_TOPIC_ID` | `yolo-detections` | Topic del pipeline legacy (ya no se usa para eventos confirmados) |
| `FIRESTORE_DATABASE_ID` | `(default)` | Base de datos donde vive `device_status` |
| `DETECTIONS_DATABASE_ID` | `dbraspberry` | Base de datos donde viven las detecciones |
| `DEVICE_ID` | `raspberry-pi-01` | Identificador único del dispositivo |
| `CLOUD_RUN_URL` | — | Host del servicio `orchestrator-service` (sin esquema, ej. `mi-servicio.a.run.app`) |
| `UPLINK_TOKEN` | — | Token compartido para autenticar los WebSockets de streaming |
| `FRAME_SKIP` | `4` | Procesa 1 de cada N frames con YOLO (rendimiento) |
| `CONFIDENCE_THRESHOLD` | `0.5` | Umbral mínimo de confianza YOLO |
| `MIN_SECONDS_TO_CONFIRM` | `1.0` | Ver [Pipeline de detección](#pipeline-de-detección-de-frame-crudo-a-evento-confiable) |
| `SESSION_GAP_SECONDS` | `4.0` | Ver [Pipeline de detección](#pipeline-de-detección-de-frame-crudo-a-evento-confiable) |
| `STREAM_IDLE_TIMEOUT` | `60.0` | Ver [Streaming de video bajo demanda](#streaming-de-video-bajo-demanda) |
| `STATE_POLL_INTERVAL` | `5.0` | Cada cuánto (segundos) el dispositivo revisa `device_status.state` |
| `FORCE_TEST_VIDEO` | `false` | Si es `true`, ignora la cámara física y usa siempre un video de prueba (útil para demos/desarrollo sin hardware) |
| `BACKEND_URL` | — | URL del `orchestrator-service` que consume el dashboard |

> **Seguridad:** `.env` y cualquier archivo `*.json` de credenciales están en `.gitignore` y nunca deben commitearse. Este repo es público por requisito de tesis — cualquier secreto expuesto debe rotarse de inmediato, nunca reescribir el historial de git como "solución".

## Puesta en marcha (desarrollo local / simulación)

No se requiere una Raspberry Pi física para desarrollar: `raspberrypi/main.py` corre igual en cualquier PC con webcam (o con `FORCE_TEST_VIDEO=true` usando un video de muestra).

```bash
# 1) Clonar y crear un entorno virtual
git clone https://github.com/CesarPP23/app-detection-epp-demo.git
cd app-detection-epp-demo
python -m venv env && source env/bin/activate   # Windows: env\Scripts\activate

# 2) Configurar variables de entorno
#    crea un archivo .env en la raíz con las claves de la tabla anterior

# 3) Simulador de la Raspberry Pi
pip install -r raspberrypi/requirements_raspberrypi.txt
cd raspberrypi && python main.py

# 4) Backend de streaming (orchestrator-service), en otra terminal
pip install -r gcp/requirements.txt
cd gcp && uvicorn orchestrator_main:app --reload

# 5) Dashboard, en otra terminal
pip install flet requests python-dotenv google-cloud-firestore
cd front-end && python main.py
# abre http://localhost:8765
```

## Despliegue en una Raspberry Pi física

El código de `raspberrypi/` está escrito para no requerir cambios entre PC y hardware real:

1. Copiar la carpeta `raspberrypi/` junto con `.env` y el JSON de credenciales (ninguno de los dos vive en git; deben transferirse por un canal seguro aparte).
2. `pip install -r raspberrypi/requirements_raspberrypi.txt`.
   > Riesgo conocido: `torch` puede no tener wheel precompilado para arquitecturas ARM de Raspberry Pi OS — validar antes de asumir instalación directa.
3. Asegurar que el modelo entrenado (`torch/best.pt`) esté presente en el dispositivo.
4. Definir `FORCE_TEST_VIDEO=false` (o eliminar la variable) para usar la cámara real en vez del video de prueba.
5. `cd raspberrypi && python main.py`.
6. Para producción real, envolver el proceso en un servicio `systemd` con `Restart=always` (ejemplo de unit file en `riesgos.txt`), de modo que un cuelgue del script no detenga la supervisión indefinidamente.

`riesgos.txt` documenta además mitigaciones para los otros dos riesgos de un dispositivo de borde: corrupción de la tarjeta SD (mover la base de datos local a almacenamiento externo) y cortes de energía (UPS + heartbeat hacia la nube).

## Despliegue del backend en GCP

`orchestrator-service` se despliega como contenedor en Cloud Run (`gcp/Dockerfile`), con `min-instances=0` para no generar costo en reposo — solo se factura cuando hay conexiones de control/video activas. El token compartido (`UPLINK_TOKEN`) se inyecta como variable de entorno del servicio, nunca hardcodeado.

El pipeline legacy (`gcp/data/pubsub_to_firestore.py`) se despliega de forma independiente, disparado por una suscripción push de Pub/Sub vía Eventarc, y queda aislado a su propia colección de Firestore (ver [Modelo de datos](#modelo-de-datos-firestore)).

## Limitaciones conocidas y trabajo futuro

- No se ha validado aún el despliegue en hardware Raspberry Pi real (todo el desarrollo se hizo simulando en PC) — el riesgo principal es la disponibilidad de `torch` para ARM.
- El campo `device_status.state` combina "estado deseado" y "estado real" en un mismo valor; un historial de auditoría de cambios de estado requeriría separar ambos conceptos.
- El pipeline legacy vía Pub/Sub sigue desplegado por compatibilidad, pero su esquema fijo no debe usarse como destino de nuevos tipos de evento sin actualizarlo explícitamente.
- Streaming, dashboard y persistencia probados con un solo dispositivo (`raspberry-pi-01`); el modelo de datos ya es multi-dispositivo (`device_id` como clave), pero no se ha probado con más de uno en paralelo.
