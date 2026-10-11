"""Escribe `device_control/{device_id}` en la base de control (reemplaza utils/create.py).

Uso (en la Pi, con las variables de edge.env cargadas):
    python -m tools.set_state running
    python -m tools.set_state stopped --device-id raspberry-pi-01

Solo toca `desired_state`, `updated_by` y `updated_at`; no modifica el lease del streaming.
"""
import argparse
import getpass
import os
import sys


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("state", choices=["running", "stopped"])
    ap.add_argument("--device-id", default=os.environ.get("DEVICE_ID", ""))
    ap.add_argument("--project", default=os.environ.get("GCP_PROJECT_ID", "epp-detection-v1"))
    ap.add_argument("--database", default=os.environ.get("CONTROL_DATABASE_ID", "(default)"))
    args = ap.parse_args(argv)
    if not args.device_id:
        print("Falta --device-id (o la variable DEVICE_ID).", file=sys.stderr)
        return 2

    from google.cloud import firestore  # import tardío

    db = firestore.Client(project=args.project, database=args.database)
    db.collection("device_control").document(args.device_id).set(
        {
            "desired_state": args.state,
            "updated_by": f"cli:{getpass.getuser()}",
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    print(f"device_control/{args.device_id}: desired_state={args.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
