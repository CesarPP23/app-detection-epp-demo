"""Lee el estado de control y de salud del dispositivo (reemplaza utils/check_firestore.py).

Uso:
    python -m tools.check_firestore
    python -m tools.check_firestore --device-id raspberry-pi-01

Solo lectura. Muestra `device_control` y `device_health` de la base de control.
"""

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device-id", default=os.environ.get("DEVICE_ID", ""))
    ap.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", "epp-detection-v1"))
    ap.add_argument("--database", default=os.environ.get("CONTROL_DATABASE_ID", "(default)"))
    args = ap.parse_args(argv)
    if not args.device_id:
        print("Falta --device-id (o la variable DEVICE_ID).", file=sys.stderr)
        return 2

    from google.api_core import exceptions
    from google.cloud import firestore

    try:
        db = firestore.Client(project=args.project, database=args.database)
        for coll in ("device_control", "device_health"):
            snap = db.collection(coll).document(args.device_id).get()
            print(f"\n{coll}/{args.device_id} (base '{args.database}')")
            if not snap.exists:
                print("  (no existe)")
                continue
            for k, v in sorted(snap.to_dict().items()):
                print(f"  {k}: {v}")
    except exceptions.GoogleAPICallError as e:
        print(f"Error de Firestore: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 - p. ej. credenciales ausentes
        print(f"No se pudo consultar Firestore: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
