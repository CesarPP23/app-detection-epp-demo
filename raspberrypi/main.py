# main.py — Simulador unificado de la Raspberry Pi (corre igual en la Pi real o en esta PC)
#
# Arquitectura evento-driven:
# - Procesa video (webcam si hay, si no el video de prueba) con YOLO en un solo loop.
# - Las detecciones crudas pasan por un agregador anti-ruido (utils/aggregator.py) que
#   solo confirma un evento si la clase se sostiene un tiempo mínimo real, y agrupa
#   detecciones continuas en una sola sesión en vez de una fila por frame.
# - Los eventos YA confirmados y cerrados (datos limpios) se guardan directo en
#   Firestore (dbraspberry/detecciones_confirmadas) — ver nota en publish_event()
#   sobre por qué no se usa el Pub/Sub -> Cloud Run existente para esto.
# - El streaming hacia Cloud Run (orchestrator-service) se prende y apaga solo:
#   se activa cuando hay un evento confirmado y se apaga tras STREAM_IDLE_TIMEOUT
#   segundos sin ninguna detección confirmada nueva. Así Cloud Run no recibe
#   tráfico de streaming fuera de eventos reales.

import os
import cv2
import time
import json
import sqlite3
import logging
import asyncio
from logging.handlers import RotatingFileHandler
from ultralytics import YOLO
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

from utils.aggregator import DetectionAggregator
from utils.stream_manager import StreamManager

from google.cloud import firestore

# ==============================================================================
# --- CONFIGURACIÓN CENTRALIZADA ---
# ==============================================================================
BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
MODEL_PATH = BASE_DIR / "torch" / "best.pt"
DB_PATH = BASE_DIR / "database" / "offline_cache.db"
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

load_dotenv(REPO_ROOT / ".env")

_creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
if _creds_path and not os.path.isabs(_creds_path):
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str((REPO_ROOT / _creds_path).resolve())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(LOG_DIR / "pi_simulator.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
    ],
)
log = logging.getLogger("pi_simulator")

DEVICE_ID = os.getenv("DEVICE_ID", "raspberry-pi-01")
GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "epp-detection-v1")
PUB_SUB_TOPIC_ID = os.getenv("PUB_SUB_TOPIC_ID", "yolo-detections")  # ya no se usa para eventos confirmados, ver publish_event()
FIRESTORE_DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "(default)")  # device_status (control) vive aquí
DETECTIONS_DATABASE_ID = os.getenv("DETECTIONS_DATABASE_ID", "dbraspberry")  # detecciones (crudas y confirmadas) viven aquí

CLOUD_RUN_URL = os.getenv("CLOUD_RUN_URL", "orchestrator-service-540868081764.us-central1.run.app")
UPLINK_TOKEN = os.getenv("UPLINK_TOKEN")
if not UPLINK_TOKEN:
    UPLINK_TOKEN = "dev-only-token-set-UPLINK_TOKEN-in-.env"
    log.warning("UPLINK_TOKEN no está definido en .env — usando un token de desarrollo, el streaming real fallará.")
CONTROL_URL = f"wss://{CLOUD_RUN_URL}/ws/control/{DEVICE_ID}"
VIDEO_URL = f"wss://{CLOUD_RUN_URL}/ws/video/{DEVICE_ID}"

# -- Parámetros de detección / rendimiento --
PROCESS_WIDTH = 640
PROCESS_HEIGHT = 480
FRAME_SKIP = int(os.getenv("FRAME_SKIP", "4"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.5"))
VIDEO_PATH = BASE_DIR / "media" / "prueba1.mp4"
FORCE_TEST_VIDEO = os.getenv("FORCE_TEST_VIDEO", "").lower() in ("1", "true", "s", "si", "sí")

# -- Parámetros de negocio anti-ruido / evento-driven (ajustables por .env) --
MIN_SECONDS_TO_CONFIRM = float(os.getenv("MIN_SECONDS_TO_CONFIRM", "1.0"))
SESSION_GAP_SECONDS = float(os.getenv("SESSION_GAP_SECONDS", "4.0"))
STREAM_IDLE_TIMEOUT = float(os.getenv("STREAM_IDLE_TIMEOUT", "60.0"))
STATE_POLL_INTERVAL = float(os.getenv("STATE_POLL_INTERVAL", "5.0"))

COLORS = [
    (0, 255, 0), (255, 128, 0), (0, 128, 255),
    (255, 0, 255), (255, 255, 0), (128, 0, 255),
]
BOX_THICKNESS = 3
FONT_SCALE = 0.9
FONT_THICKNESS = 2


# ==============================================================================
# --- FUNCIONES AUXILIARES ---
# ==============================================================================

def get_color_for_class(class_id: int) -> tuple:
    return COLORS[class_id % len(COLORS)]


def draw_detections(frame, results, model, scale_x: float, scale_y: float):
    if not results or results[0].boxes is None:
        return
    for box in results[0].boxes:
        confidence = float(box.conf[0])
        if confidence < CONFIDENCE_THRESHOLD:
            continue
        class_id = int(box.cls[0])
        class_name = model.names[class_id]
        x1, y1, x2, y2 = [int(coord) for coord in box.xyxy[0]]
        x1, x2 = int(x1 * scale_x), int(x2 * scale_x)
        y1, y2 = int(y1 * scale_y), int(y2 * scale_y)
        color = get_color_for_class(class_id)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color=color, thickness=BOX_THICKNESS)
        label = f"{class_name}: {confidence:.2f}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE, FONT_THICKNESS)
        txt_y1 = max(0, y1 - th - 8)
        cv2.rectangle(frame, (x1, txt_y1), (x1 + tw + 10, y1), color, thickness=-1)
        cv2.putText(frame, label, (x1 + 5, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, FONT_SCALE,
                    (255, 255, 255), FONT_THICKNESS, cv2.LINE_AA)


def initialize_camera():
    """Usa la webcam física si está disponible; si no, cae al video de prueba.
    Sin prompts interactivos: debe poder correr desatendido (systemd, docker, etc.)."""
    if not FORCE_TEST_VIDEO:
        cam = cv2.VideoCapture(0)
        if cam.isOpened():
            ok, _ = cam.read()
            if ok:
                log.info("Cámara física (index 0) detectada y funcionando. Usando webcam en vivo.")
                cam.set(cv2.CAP_PROP_POS_FRAMES, 0) if False else None
                return cam, False
            cam.release()
        log.info("No se detectó webcam física utilizable, se usará el video de prueba.")

    if not VIDEO_PATH.exists():
        log.error(f"Archivo de video de prueba no encontrado: {VIDEO_PATH}")
        return None, True
    cam = cv2.VideoCapture(str(VIDEO_PATH))
    if not cam.isOpened():
        log.error(f"No se pudo abrir el video de prueba: {VIDEO_PATH}")
        return None, True
    fps = cam.get(cv2.CAP_PROP_FPS)
    log.info(f"Usando video de prueba: {VIDEO_PATH.name} ({fps:.1f} FPS).")
    return cam, True


def setup_local_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS offline_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_detections_from_results(results, model) -> list[dict]:
    detections = []
    if not results or len(results) == 0 or results[0].boxes is None:
        return detections
    for box in results[0].boxes:
        confidence = float(box.conf[0])
        if confidence < CONFIDENCE_THRESHOLD:
            continue
        class_id = int(box.cls[0])
        detections.append({
            "clase_id": class_id,
            "nombre_clase": model.names[class_id],
            "confianza": confidence,
        })
    return detections


CONFIRMED_EVENTS_COLLECTION = "detecciones_confirmadas"


def _event_to_firestore_doc(event: dict) -> dict:
    """Convierte start_ts/end_ts (ISO string) a datetime real para que Firestore
    los guarde como Timestamp nativo (permite ordenar/filtrar por fecha en el
    dashboard sin parsear strings)."""
    doc = dict(event)
    for key in ("start_ts", "end_ts"):
        if isinstance(doc.get(key), str):
            doc[key] = datetime.fromisoformat(doc[key])
    return doc


async def publish_event(events_col, event: dict):
    """Guarda UN evento ya confirmado y cerrado por el agregador (datos limpios,
    no detecciones crudas por frame) DIRECTAMENTE en Firestore.

    NOTA IMPORTANTE (2026-09-20): originalmente esto publicaba a Pub/Sub para
    que el Cloud Run ya desplegado `guardar-detecciones-firestore` lo guardara.
    Se comprobó end-to-end que ESE servicio tiene un esquema fijo heredado
    (nombre_clase/clase_id/confianza/bounding_box) y descarta silenciosamente
    cualquier campo que no reconoce — nuestros eventos agregados llegaban con
    'confianza': 0.0 (el campo que sí llenamos es confianza_promedio/max, que
    ese servicio no conoce) y sin duration_seconds/frame_count/is_confirmed,
    es decir, el dato quedaba corrupto, peor que el ruido que queríamos evitar.
    Como no está autorizado modificar ese Cloud Run, la Pi escribe directo a
    Firestore (ya tiene el rol Cloud Datastore User) en una colección nueva
    y propia, sin pasar por ese servicio. El topic Pub/Sub `yolo-detections`
    y su suscriptor siguen existiendo para quien los use, pero el flujo de
    datos limpios ya no depende de ellos."""
    doc = _event_to_firestore_doc(event)
    data_str = json.dumps(event)
    try:
        await asyncio.to_thread(events_col.add, doc)
        log.info(f"Guardado en Firestore ({CONFIRMED_EVENTS_COLLECTION}): {event['nombre_clase']} "
                 f"(conf. prom {event['confianza_promedio']}, {event['duration_seconds']}s).")
    except Exception as e:
        log.warning(f"No se pudo guardar en Firestore ({e}); guardando en caché local SQLite.")
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("INSERT INTO offline_detections (payload) VALUES (?)", (data_str,))
            conn.commit()
            conn.close()
        except sqlite3.Error as db_error:
            log.error(f"CRÍTICO: fallo también al guardar en caché local: {db_error}", exc_info=True)


async def flush_offline_cache(events_col, limit: int = 5):
    """Reintenta guardar en Firestore eventos que quedaron en la cola local por caídas de red."""
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT id, payload FROM offline_detections ORDER BY id ASC LIMIT ?", (limit,)
        ).fetchall()
        if not rows:
            conn.close()
            return
        for row_id, payload in rows:
            event = json.loads(payload)
            await asyncio.to_thread(events_col.add, _event_to_firestore_doc(event))
            conn.execute("DELETE FROM offline_detections WHERE id = ?", (row_id,))
        conn.commit()
        conn.close()
        log.info(f"Cola local: {len(rows)} evento(s) pendientes guardados exitosamente en Firestore.")
    except Exception:
        pass  # sigue sin conexión, se reintenta en la próxima pasada


async def set_device_status(doc_ref, state: str):
    try:
        await asyncio.to_thread(
            doc_ref.set, {"state": state, "updated_at": datetime.now(timezone.utc).isoformat()}, True
        )
    except Exception as e:
        log.warning(f"No se pudo actualizar device_status en Firestore: {e}")


async def poll_desired_state(doc_ref, shared: dict, interval: float):
    """Tarea de fondo: lee cada `interval` segundos qué quiere el operador
    (stopped / running / streaming-forzado) desde el dashboard vía Firestore."""
    while True:
        try:
            doc = await asyncio.to_thread(doc_ref.get)
            state = doc.to_dict().get("state", "stopped") if doc.exists else "stopped"
        except Exception as e:
            log.error(f"Error leyendo device_status de Firestore, asumiendo 'stopped': {e}")
            state = "stopped"
        shared["desired_state"] = state
        await asyncio.sleep(interval)


# ==============================================================================
# --- LOOP PRINCIPAL ---
# ==============================================================================

async def run_active_loop(model, camera, is_test_video, gcp_clients, doc_ref, shared):
    """Corre mientras desired_state sea 'running' o 'streaming'. Un solo lugar
    lee la cámara y corre YOLO — el streaming solo reutiliza esos mismos frames,
    nunca abre un segundo lector de cámara en paralelo."""
    events_col = gcp_clients["events_col"]
    aggregator = DetectionAggregator(
        device_id=DEVICE_ID,
        min_seconds_to_confirm=MIN_SECONDS_TO_CONFIRM,
        session_gap_seconds=SESSION_GAP_SECONDS,
        logger=log,
    )

    async def on_stream_state_change(new_state: str):
        await set_device_status(doc_ref, new_state)

    stream_manager = StreamManager(CONTROL_URL, VIDEO_URL, UPLINK_TOKEN, log, on_stream_state_change)
    last_confirmed_activity_ts: float | None = None
    forced_streaming_requested = False
    frame_count = 0
    consecutive_failures = 0

    try:
        while True:
            if shared["desired_state"] == "stopped":
                log.info("Estado deseado = 'stopped'. Saliendo del loop activo.")
                break

            want_forced_stream = shared["desired_state"] == "streaming"
            if want_forced_stream and not forced_streaming_requested:
                forced_streaming_requested = True
                last_confirmed_activity_ts = time.monotonic()  # da un margen de gracia inicial
                await stream_manager.start(reason="solicitado manualmente desde el dashboard")
            elif not want_forced_stream and forced_streaming_requested:
                forced_streaming_requested = False
                if stream_manager.active:
                    await stream_manager.stop(reason="el operador desactivó el streaming manual",
                                               next_state="running")

            success, frame = camera.read()
            if not success:
                consecutive_failures += 1
                if is_test_video and consecutive_failures < 20:
                    camera.set(cv2.CAP_PROP_POS_FRAMES, 0)  # loop del video de prueba
                    await asyncio.sleep(0.1)
                    continue
                log.error("No se pudo leer frame de la fuente de video. Deteniendo loop activo.")
                break
            consecutive_failures = 0
            frame_count += 1

            if frame_count % FRAME_SKIP != 0:
                await asyncio.sleep(0.005)
                continue

            frame_resized = cv2.resize(frame, (PROCESS_WIDTH, PROCESS_HEIGHT))
            results = model(frame_resized, verbose=False)
            raw_detections = get_detections_from_results(results, model)

            now = time.monotonic()
            closed_events, activity_now = aggregator.update(raw_detections, now)

            for event in closed_events:
                asyncio.create_task(publish_event(events_col, event))

            if activity_now:
                last_confirmed_activity_ts = now
                if not stream_manager.active:
                    await stream_manager.start(reason="detección confirmada en curso")

            if stream_manager.active:
                scale_x = frame.shape[1] / PROCESS_WIDTH
                scale_y = frame.shape[0] / PROCESS_HEIGHT
                draw_detections(frame, results, model, scale_x, scale_y)
                ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                if ok:
                    await stream_manager.send_frame(buffer.tobytes())

                idle_for = now - (last_confirmed_activity_ts or now)
                if idle_for > STREAM_IDLE_TIMEOUT:
                    await stream_manager.stop(
                        reason=f"{STREAM_IDLE_TIMEOUT:.0f}s sin detecciones confirmadas nuevas",
                        next_state="running",
                    )
                    forced_streaming_requested = False

            if frame_count % 25 == 0:
                await flush_offline_cache(events_col)

            await asyncio.sleep(0.01)
    finally:
        if stream_manager.active:
            await stream_manager.stop(reason="fin del loop activo", next_state="stopped")
        log.info(
            f"Loop activo detenido. Frames procesados por YOLO: {aggregator.frames_processed}, "
            f"detecciones descartadas como ruido: {aggregator.noise_discarded_count}."
        )


async def main():
    log.info("Iniciando simulador de Raspberry Pi (evento-driven, anti-ruido).")
    log.info(f"device_id={DEVICE_ID} | confirm>= {MIN_SECONDS_TO_CONFIRM}s | "
             f"gap<= {SESSION_GAP_SECONDS}s | stream idle timeout={STREAM_IDLE_TIMEOUT}s")

    try:
        model = YOLO(MODEL_PATH)
        log.info("Modelo YOLO cargado.")
    except Exception as e:
        log.critical(f"No se pudo cargar el modelo YOLO: {e}", exc_info=True)
        return

    setup_local_database()
    try:
        control_db = firestore.Client(database=FIRESTORE_DATABASE_ID)
        detections_db = (
            control_db if DETECTIONS_DATABASE_ID == FIRESTORE_DATABASE_ID
            else firestore.Client(database=DETECTIONS_DATABASE_ID)
        )
        gcp_clients = {
            "events_col": detections_db.collection(CONFIRMED_EVENTS_COLLECTION),
        }
        doc_ref = control_db.collection("device_status").document(DEVICE_ID)
        log.info(
            f"Clientes de Firestore inicializados: control='{FIRESTORE_DATABASE_ID}', "
            f"detecciones='{DETECTIONS_DATABASE_ID}' (colección '{CONFIRMED_EVENTS_COLLECTION}')."
        )
    except Exception as e:
        log.critical(f"Error al conectar con GCP: {e}", exc_info=True)
        return

    shared = {"desired_state": "stopped"}
    poll_task = asyncio.create_task(poll_desired_state(doc_ref, shared, STATE_POLL_INTERVAL))

    camera = None
    try:
        while True:
            # espera activa a que el operador ponga running/streaming
            while shared["desired_state"] == "stopped":
                await asyncio.sleep(STATE_POLL_INTERVAL)

            camera, is_test_video = initialize_camera()
            if camera is None:
                log.error("No hay fuente de video disponible. Reintentando en 10s.")
                await asyncio.sleep(10)
                continue

            await run_active_loop(model, camera, is_test_video, gcp_clients, doc_ref, shared)
            camera.release()
            camera = None
    except (KeyboardInterrupt, asyncio.CancelledError):
        log.info("Deteniendo simulador (señal de salida)...")
    finally:
        poll_task.cancel()
        if camera is not None:
            camera.release()
        log.info("Recursos liberados. Simulador detenido.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logging.getLogger("pi_simulator").critical(f"Error fatal: {e}", exc_info=True)
