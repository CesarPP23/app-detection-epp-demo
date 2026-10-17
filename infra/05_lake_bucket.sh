#!/usr/bin/env bash
# Fase 4.6 - Bucket del data lake (zona raw/). Ver el diseño completo en docs/DATA_LAKE.md.
# Mutaciones: crea gs://<proyecto>-lake (UBLA, Public Access Prevention enforced, soft delete 7 días)
#             y le aplica el lifecycle (imágenes: Nearline a los 30 d, borrado a los 60 d; JSON sin expirar).
# Los permisos de las SAs (objectCreator de la Pi, objectViewer del runtime) los pone 02_iam_runtime.sh;
# el de la conexión de BigQuery lo pone 06_bigquery.sh. Orden: ejecutar ANTES de 02_iam_runtime.sh.
# A COMPROBAR con --help: gcloud storage buckets create (--public-access-prevention, --soft-delete-duration),
#                         gcloud storage buckets update (--lifecycle-file).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if exists gcloud storage buckets describe "gs://$LAKE_BUCKET" --project="$PROJECT"; then
  log "El bucket gs://$LAKE_BUCKET ya existe: solo se reaplican las políticas"
else
  run gcloud storage buckets create "gs://$LAKE_BUCKET" --project="$PROJECT" --location="$REGION" \
    --uniform-bucket-level-access --public-access-prevention --soft-delete-duration=7d
fi

run gcloud storage buckets update "gs://$LAKE_BUCKET" --project="$PROJECT" \
  --lifecycle-file="$INFRA_DIR/policies/lake-lifecycle.json"

log "Verificación (solo lectura): debe mostrar publicAccessPrevention=enforced, UBLA y soft delete"
run gcloud storage buckets describe "gs://$LAKE_BUCKET" --project="$PROJECT" \
  --format="yaml(public_access_prevention,uniform_bucket_level_access,soft_delete_policy,lifecycle_config)"
