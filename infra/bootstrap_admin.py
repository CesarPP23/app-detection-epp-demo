"""Datos iniciales de Firestore (Fase 4.4). Idempotente: no sobrescribe documentos existentes.

Crea en la base de control ((default)):
  - device_control/raspberry-pi-01 = {desired_state: "stopped", stream_requested: False}
  - dashboard_users/admin@viz-ia.com = {role: "admin", active: True, created_by: "bootstrap", created_at}

Credenciales: ADC (`gcloud auth application-default login`) o la SA del entorno. No lee ni escribe secretos.

Uso:
    python infra/bootstrap_admin.py [--project epp-detection-v1] [--database "(default)"] [--dry-run]
"""

from __future__ import annotations

import argparse
from typing import Any

DEVICE_ID = "raspberry-pi-01"
ADMIN_EMAIL = "admin@viz-ia.com"


def bootstrap(db: Any, server_timestamp: Any, device_id: str = DEVICE_ID, admin_email: str = ADMIN_EMAIL) -> dict[str, str]:
    """Crea los documentos si faltan. Devuelve {ruta: 'creado' | 'existente'}."""
    wanted = {
        f"device_control/{device_id}": {"desired_state": "stopped", "stream_requested": False},
        f"dashboard_users/{admin_email.lower()}": {
            "role": "admin",
            "active": True,
            "created_by": "bootstrap",
            "created_at": server_timestamp,
        },
    }
    result: dict[str, str] = {}
    for path, data in wanted.items():
        ref = db.document(path)
        if ref.get().exists:
            result[path] = "existente"
        else:
            ref.create(data)  # create() falla si otro proceso lo creó entre medias: nunca sobrescribe
            result[path] = "creado"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default="epp-detection-v1")
    parser.add_argument("--database", default="(default)")
    parser.add_argument("--dry-run", action="store_true", help="solo muestra qué se crearía")
    args = parser.parse_args()

    if args.dry_run:
        print(f"[dry-run] crearía device_control/{DEVICE_ID} y dashboard_users/{ADMIN_EMAIL} en {args.database} (si faltan)")
        return

    from google.cloud import firestore

    db = firestore.Client(project=args.project, database=args.database)
    for path, estado in bootstrap(db, firestore.SERVER_TIMESTAMP).items():
        print(f"{path}: {estado}")


if __name__ == "__main__":
    main()
