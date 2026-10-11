#!/usr/bin/env bash
# Fase 4.3 + 4.5 - SA de runtime del servicio (platform-runtime) y mínimo privilegio de la SA de la Pi.
# Mutaciones:
#   - crea la SA platform-runtime (si falta);
#   - secretAccessor sobre device-token y session-secret;
#   - roles/datastore.user en el proyecto; storage.objectViewer en el bucket del lake;
#   - serviceAccountTokenCreator sobre sí misma (firmar URLs de GCS sin archivo de key);
#   - raspberry-pi-worker: datastore.user (proyecto) y storage.objectCreator SOLO en el bucket del lake.
# Orden: ejecutar DESPUÉS de 01_secrets.sh y 05_lake_bucket.sh (necesita secretos y bucket).
# Los add-iam-policy-binding son idempotentes. No se eliminan roles aquí (Pub/Sub ya se quitó en la Fase 0.3).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

log "SA de runtime: $RUNTIME_SA"
if exists gcloud iam service-accounts describe "$RUNTIME_SA" --project="$PROJECT"; then
  log "La SA ya existe"
else
  run gcloud iam service-accounts create "$RUNTIME_SA_NAME" \
    --display-name="Cloud Run: API + relay + SPA" --project="$PROJECT"
fi

for s in device-token session-secret; do
  run gcloud secrets add-iam-policy-binding "$s" \
    --member="serviceAccount:$RUNTIME_SA" --role=roles/secretmanager.secretAccessor --project="$PROJECT"
done

run gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/datastore.user --condition=None

run gcloud storage buckets add-iam-policy-binding "gs://$LAKE_BUCKET" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/storage.objectViewer

# Firmar URLs v4 sin archivo de key: signBlob de la SA sobre sí misma.
run gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/iam.serviceAccountTokenCreator --project="$PROJECT"

log "SA de la Pi: mínimo privilegio ($PI_SA)"
run gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$PI_SA" --role=roles/datastore.user --condition=None
# Solo CREAR objetos y solo en el lake: no puede leer, sobrescribir ni borrar.
run gcloud storage buckets add-iam-policy-binding "gs://$LAKE_BUCKET" \
  --member="serviceAccount:$PI_SA" --role=roles/storage.objectCreator

log "Roles actuales de la SA de la Pi (solo lectura, deben ser únicamente datastore.user)"
run gcloud projects get-iam-policy "$PROJECT" --flatten="bindings[].members" \
  --filter="bindings.members:$PI_SA" --format="value(bindings.role)"
