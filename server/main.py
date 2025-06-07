from server.model_cv import run_detection
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import JSONResponse, FileResponse
import os
import uuid
from datetime import datetime

app = FastAPI()

UPLOAD_DIR = "server/uploads"
DETECTED_DIR = "server/detected"
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(DETECTED_DIR, exist_ok=True)

# Variables globales para mantener el último resultado
latest_detection_result = {
    "timestamp": None,
    "objects": [],
    "image_path": None
}

@app.post("/upload")
async def upload_image(image: UploadFile = File(...)):
    # Guardar el archivo temporalmente
    contents = await image.read()
    filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{uuid.uuid4().hex[:6]}.jpg"
    image_path = os.path.join(UPLOAD_DIR, filename)
    
    with open(image_path, "wb") as f:
        f.write(contents)

    # Ejecutar detección
    result_image_path, detections = run_detection(image_path)

    # Actualizar último resultado
    latest_detection_result.update({
        "timestamp": datetime.now().isoformat(),
        "objects": detections,
        "image_path": result_image_path
    })

    return JSONResponse({"status": "ok", "detections": detections})

@app.get("/latest-detection")
def get_latest_detection():
    if latest_detection_result["timestamp"] is None:
        return JSONResponse({"error": "No detecciones aún"}, status_code=404)

    return {
        "timestamp": latest_detection_result["timestamp"],
        "objects": latest_detection_result["objects"],
        "image_path": latest_detection_result["image_path"]
    }

@app.get("/latest-image")
def get_latest_image():
    path = latest_detection_result.get("image_path")
    if path and os.path.exists(path):
        return FileResponse(path, media_type="image/jpeg")
    return JSONResponse({"error": "No imagen disponible"}, status_code=404)
