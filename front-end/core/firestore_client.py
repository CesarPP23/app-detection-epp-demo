"""Cliente de datos del dashboard: lee/escribe Firestore directamente.

Contrato de datos (definido por el subagente de Ingeniería de Datos):
- Historial de eventos: DB `dbraspberry`, colección `detecciones_confirmadas`.
- Estado en vivo del dispositivo: DB `(default)`, colección `device_status`.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import firestore

ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")

_cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "raspberrypi/gcp-key.json")
if not os.path.isabs(_cred_path):
    _cred_path = str(ROOT_DIR / _cred_path)
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _cred_path

STATE_DATABASE_ID = os.getenv("FIRESTORE_DATABASE_ID", "(default)")
DETECTIONS_DATABASE_ID = os.getenv("DETECTIONS_DATABASE_ID", "dbraspberry")
DEVICE_ID = os.getenv("DEVICE_ID", "raspberry-pi-01")

_state_client = firestore.Client(database=STATE_DATABASE_ID)
_detections_client = firestore.Client(database=DETECTIONS_DATABASE_ID)


def get_recent_detections(limit: int = 100) -> list[dict]:
    docs = (
        _detections_client.collection("detecciones_confirmadas")
        .order_by("end_ts", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    result = []
    for d in docs:
        data = d.to_dict()
        data["id"] = d.id
        result.append(data)
    return result


def get_device_state() -> str:
    doc = _state_client.collection("device_status").document(DEVICE_ID).get()
    if doc.exists:
        return (doc.to_dict() or {}).get("state", "stopped")
    return "stopped"


def set_device_state(new_state: str) -> None:
    _state_client.collection("device_status").document(DEVICE_ID).set(
        {"state": new_state}, merge=True
    )
