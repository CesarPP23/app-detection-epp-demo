import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class Config:
    """
    Configuración centralizada de la aplicacion.
    """

    # URL base del backend (FastApi en Cloud Run)
    BACKEND_URL: str = os.getenv("BACKEND_URL", "https://orchestrator-service-540868081764.us-central1.run.app/").rstrip("/")

    # Identificador del dispositivo IoT para pruebas
    DEVICE_ID: str = os.getenv("DEVICE_ID", "raspberry-pi-01")

    # Endpoints de API
    ENDPOINTS = {
        "login_admin": f"{BACKEND_URL}/login/admin",
        "login_google": f"{BACKEND_URL}/login/google",
        "google_callback": f"{BACKEND_URL}/auth/google/callback",
        "start_stream": f"{BACKEND_URL}/request_stream/{{device_id}}/start_stream",
        "stop_stream": f"{BACKEND_URL}/request_stream/{{device_id}}/stop_stream",
        "video_stream": f"{BACKEND_URL}/video_stream/{{device_id}}",
        "reports": f"{BACKEND_URL}/reports/{{device_id}}"
    }

    # URL directa del stream MJPEG en vivo para este dispositivo
    VIDEO_STREAM_URL: str = f"{BACKEND_URL}/video_stream/{DEVICE_ID}"

    # Token temporal de sesión (placeholder hasta que implementemos auth real)
    FAKE_ADMIN_TOKEN: str = "fake_token_123"

# Instancia global para importación directa
config = Config()


