"""Vuelca el OpenAPI de la API a un archivo, sin arrancar el servidor ni tocar GCP.

Uso (desde la raíz del repo):
    python backend/scripts/dump_openapi.py frontend/openapi.json

El frontend lo convierte en tipos con `pnpm gen:api` (openapi-typescript). Los valores de entorno
son ficticios: solo hacen falta para que Settings valide al importar la app.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

for key, value in {
    "DEVICE_TOKEN": "x" * 32,
    "SESSION_SECRET": "y" * 32,
    "ALLOWED_DEVICES": "raspberry-pi-01",
    "AUTH_MODE": "dev",
    "STATIC_DIR": "/nonexistent",
}.items():
    os.environ.setdefault(key, value)
os.environ.pop("K_SERVICE", None)

from epp_api.main import app  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("openapi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(app.openapi(), indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"OpenAPI escrito en {out}")


if __name__ == "__main__":
    main()
