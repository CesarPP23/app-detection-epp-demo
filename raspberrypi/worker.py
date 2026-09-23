# worker.py

import cv2
import time
import json
from ultralytics import YOLO
from pathlib import Path
from utils.detector import process_frame_and_get_detections
import sqlite3
import logging # NUEVO: Importar el módulo de logging

# 1. Importar las librerías de Google Cloud
from google.cloud import firestore
from google.cloud import pubsub_v1

# ... (La configuración de rutas y GCP se queda igual) ...
BASE_DIR = Path(__file__).resolve()
MODEL_PATH = BASE_DIR.parent / "torch" / "best.pt"
VIDEO_PATH = BASE_DIR.parent / "media" / "prueba1.mp4"
DB_PATH = BASE_DIR.parent / "database" / "offline_cache.db"
GCP_PROJECT_ID = "epp-detection-v1" 
PUB_SUB_TOPIC_ID = "yolo-detections"
DEVICE_ID = "raspberry-pi-01"

def setup_local_database():
    """Crea la tabla para la cola si no existe."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS offline_detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def main():
    # NUEVO: Configurar el logging básico
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    logging.info("Iniciando worker de detección...")
    model = YOLO(MODEL_PATH)
    cap = cv2.VideoCapture(str(VIDEO_PATH))

    try:
        db = firestore.Client()
        publisher = pubsub_v1.PublisherClient()
        topic_path = publisher.topic_path(GCP_PROJECT_ID, PUB_SUB_TOPIC_ID)
        doc_ref = db.collection("device_status").document(DEVICE_ID)
        logging.info("Conectado a Firestore y Pub/Sub exitosamente.")
    except Exception as e:
        # NUEVO: Usar logging.error con exc_info para capturar el error completo
        logging.error("Error al conectar con GCP. ¿Estableciste la variable de entorno?", exc_info=True)
        return

    if not cap.isOpened():
        logging.error("Error: No se puede acceder al video/cámara.")
        return

    setup_local_database()
    logging.info(f"Worker iniciado para el dispositivo '{DEVICE_ID}'. Presiona CTRL+C para detener.")

    try:
        while True:
            try:
                doc = doc_ref.get()
                if doc.exists:
                    data = doc.to_dict()
                    if data is not None:
                        current_state = data.get("state", "stopped")
                    else:
                        current_state = "stopped"
                else:
                    logging.warning(f"El documento '{DEVICE_ID}' no existe en Firestore. Asumiendo estado 'stopped'.")
                    current_state = "stopped"
            except Exception as e:
                logging.error("Error al leer de Firestore. Asumiendo estado 'stopped'.", exc_info=True)
                current_state = "stopped"

            # El resto del código es igual...
            if current_state == "running":
                try:
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute("SELECT id, payload FROM offline_detections ORDER BY id ASC LIMIT 10")
                    cached_detections = cursor.fetchall()
                    
                    if cached_detections:
                        logging.info(f"Se encontraron {len(cached_detections)} detecciones en la cola local. Intentando re-enviar...")
                        for det_id, payload in cached_detections:
                            future = publisher.publish(topic_path, data=payload.encode("utf-8"))
                            future.result()
                            cursor.execute("DELETE FROM offline_detections WHERE id = ?", (det_id,))
                        conn.commit()
                        logging.info("Lote de datos de la cola enviado exitosamente.")
                    conn.close()
                except Exception as e:
                    logging.warning("Aún no hay conexión. Los datos siguen en la cola local.", exc_info=True)

                success, frame = cap.read()
                if not success:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue

                detection_results = process_frame_and_get_detections(frame, model)
                
                try:
                    data_str = json.dumps(detection_results)
                    data_bytes = data_str.encode("utf-8")
                    future = publisher.publish(topic_path, data_bytes)
                    future.result()
                    logging.info("Mensaje publicado DIRECTAMENTE a Pub/Sub.")
                
                except Exception as e:
                    logging.warning("No se pudo conectar a Pub/Sub. Guardando en la cola local.", exc_info=True)
                    conn = sqlite3.connect(DB_PATH)
                    cursor = conn.cursor()
                    cursor.execute("INSERT INTO offline_detections (payload) VALUES (?)", (json.dumps(detection_results),))
                    conn.commit()
                    conn.close()
            
            else: # NUEVO: Este es el bloque que faltaba
                logging.info(f"Estado: {current_state}. El worker está en pausa.")
            
            time.sleep(5)

    except KeyboardInterrupt:
        logging.info("Deteniendo el worker...")
    finally:
        cap.release()
        logging.info("Recursos liberados. Worker detenido.")

if __name__ == "__main__":
    main()