#!/usr/bin/env bash
# Fase 4.3 - Despliegue de orchestrator-service (API + relay + SPA) con el Dockerfile multi-etapa de la raíz.
# Uso: ./infra/deploy_cloud.sh <PUBLIC_BASE_URL> <GOOGLE_OAUTH_CLIENT_ID> [--build-sa]
#   PUBLIC_BASE_URL         p. ej. https://orchestrator-service-540868081764.us-central1.run.app (sin barra final)
#   GOOGLE_OAUTH_CLIENT_ID  ID del cliente web de OAuth (no es secreto)
#   --build-sa              usa la SA cloud-build-deployer para construir (Q5, ver 08_cleanup_billing.sh)
# Ambos argumentos son obligatorios y no tienen valor por defecto.
# DRY_RUN=1 solo imprime el comando.
#
# Invariantes que NO se cambian (plan, sección 17): max-instances=1 (el estado del video vive en memoria),
# min-instances=0, 1 worker de uvicorn (lo fija el Dockerfile), --cpu-throttling (facturación por request).
# --allow-unauthenticated es necesario (navegadores y Pi no tienen identidad de Google); la protección la dan
# el login de Google + lista blanca + cookie de sesión y DEVICE_TOKEN (documentado en docs/SECURITY.md).
# Rollback: gcloud run services update-traffic orchestrator-service --region=us-central1 --to-revisions=<anterior>=100
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ $# -lt 2 || -z "${1:-}" || -z "${2:-}" ]]; then
  echo "Uso: $0 <PUBLIC_BASE_URL> <GOOGLE_OAUTH_CLIENT_ID> [--build-sa]" >&2
  exit 2
fi
PUBLIC_BASE_URL="${1%/}"
GOOGLE_OAUTH_CLIENT_ID="$2"
BUILD_SA_ARGS=()
if [[ "${3:-}" == "--build-sa" ]]; then
  BUILD_SA_ARGS=(--build-service-account="projects/${PROJECT}/serviceAccounts/${BUILD_SA}")
fi

ENV_VARS="ALLOWED_DEVICES=raspberry-pi-01,AUTH_MODE=google,PUBLIC_BASE_URL=${PUBLIC_BASE_URL}"
ENV_VARS+=",GOOGLE_OAUTH_CLIENT_ID=${GOOGLE_OAUTH_CLIENT_ID},LAKE_BUCKET=${LAKE_BUCKET}"
ENV_VARS+=",RUNTIME_SA_EMAIL=${RUNTIME_SA},LAKE_IMAGE_RETENTION_DAYS=60"

cd "$INFRA_DIR/.."   # --source=. usa el Dockerfile de la raíz del repo

run gcloud run deploy "$SERVICE" --source=. --region="$REGION" --project="$PROJECT" \
  --service-account="$RUNTIME_SA" \
  --min-instances=0 --max-instances=1 --concurrency=40 --session-affinity \
  --cpu=1 --memory=512Mi --cpu-throttling --no-cpu-boost --timeout=3600 \
  --set-secrets=DEVICE_TOKEN=device-token:latest,SESSION_SECRET=session-secret:latest \
  --set-env-vars="$ENV_VARS" \
  --remove-env-vars=UPLINK_TOKEN \
  --allow-unauthenticated \
  ${BUILD_SA_ARGS[@]+"${BUILD_SA_ARGS[@]}"}

log "Verifica el resultado (solo lectura)"
run gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" \
  --format="yaml(spec.template.metadata.annotations,spec.template.spec.timeoutSeconds,spec.template.spec.serviceAccountName)"
