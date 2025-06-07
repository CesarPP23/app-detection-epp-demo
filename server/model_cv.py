from ultralytics import YOLO
import os
import cv2
from datetime import datetime

# Cargar el modelo entrenado solo una vez
MODEL_PATH = r"C:\Users\cesar\app-detection-epp\server\best.pt"  # Cambia si tu ruta es diferente
model = YOLO(MODEL_PATH)

DETECTED_DIR = "server/detected"
os.makedirs(DETECTED_DIR, exist_ok=True)

def run_detection(image_path):
    # Ejecutar inferencia
    results = model(image_path)
    result = results[0]

    # Leer imagen original
    image = cv2.imread(image_path)

    detections = []

    for box in result.boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        label = model.names[cls_id]

        # Extraer coordenadas
        x1, y1, x2, y2 = map(int, box.xyxy[0])
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(image, f"{label} {conf:.2f}", (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

        detections.append({
            "class": label,
            "confidence": round(conf, 2),
            "box": [x1, y1, x2, y2]
        })

    # Guardar imagen con resultados
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    result_image_path = os.path.join(DETECTED_DIR, f"detection_{timestamp}.jpg")
    cv2.imwrite(result_image_path, image)

    return result_image_path, detections