import os
from pathlib import Path

# Rutas del proyecto
BASE_DIR = Path(__file__).parent.parent
MODELS_DIR = BASE_DIR / "models"
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# Configuración de la cámara (se usara el video de prueba)
CAMERA_CONFIG = {
    "source": r'C:\Users\cesar\app-detection-epp-demo\notebooks\videos\prueba2.mp4',  # 0 para cámara USB, o ruta para archivo de video
    # "width": 640,  # Ancho de la imagen
    # "height": 480,  # Alto de la imagen
    "fps": 30,  # Frames por segundo
    "scale_factor": 0.2
    
}
# Configuración de la cámara
CAMERA_CONFIG_2 = {
    "source": 0,  # 0 para cámara USB, o ruta para archivo de video
    "width": 640,  # Ancho de la imagen
    "height": 480,  # Alto de la imagen
    "fps": 30,  # Frames por segundo
}
# Configuración del modelo YOLO
MODEL_CONFIG = {
    "model_path": MODELS_DIR / "best.pt",
    "confidence_threshold": 0.5,  # Umbral de confianza para detecciones
    "device": "cpu",  # "cpu" o "cuda" si tienes GPU
    "classes_to_detect": [0, 1, 2, 3],  # IDs de las clases EPP que detectar
    "class_names": {
        0: "mascarilla",    # minúsculas
        1: "cofia",         # minúsculas
        2: "bata",          # minúsculas
        3: "guantes",       # sin guión bajo
    }
}

# Configuración del WebSocket (conexión a la nube)
WEBSOCKET_CONFIG = {
    "server_url": "ws://127.0.0.1:8000/ws",  # URL del backend en la nube
    "reconnect_interval": 5,  # Segundos entre intentos de reconexión
    "max_reconnect_attempts": 10,  # Máximo número de intentos
    "ping_interval": 30,  # Segundos entre pings para mantener conexión
}

# Configuración de FastAPI (servidor local)
FASTAPI_CONFIG = {
    "host": "0.0.0.0",  # Escuchar en todas las interfaces
    "port": 8000,  # Puerto del servidor
    "debug": True,  # Modo debug para desarrollo
}

# Configuración de la cola persistente
QUEUE_CONFIG = {
    "db_path": DATA_DIR / "queue.db",
    "max_queue_size": 1000,  # Máximo número de elementos en cola
    "retry_interval": 10,  # Segundos entre reintentos de envío
}

# Configuración de logging
LOGGING_CONFIG = {
    "level": "INFO",  # DEBUG, INFO, WARNING, ERROR
    "file_path": LOGS_DIR / "app.log",
    "max_file_size": 10 * 1024 * 1024,  # 10MB
    "backup_count": 5,  # Número de archivos de respaldo
}

# Configuración de alertas
ALERT_CONFIG = {
    "missing_epp_threshold": 120,  # Segundos sin detectar EPP antes de alerta
    "person_detection_timeout": 60,  # Segundos sin detectar persona
}

# Variables de entorno (puedes sobreescribir en .env)
# Variables de entorno (puedes sobreescribir en .env)
CLOUD_BACKEND_URL = os.getenv("CLOUD_BACKEND_URL", WEBSOCKET_CONFIG["server_url"])
DEVICE_ID = os.getenv("DEVICE_ID", "raspberrypi001")  # ← Cambiar para que coincida con Firestore