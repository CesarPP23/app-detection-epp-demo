#!/usr/bin/env bash
# Fase 4.1 - Prerrequisitos: habilita las APIs que usan las fases siguientes y muestra las bases de Firestore.
# Mutaciones: habilitar APIs (idempotente: si ya están habilitadas no cambia nada).
# Uso: DRY_RUN=1 ./infra/00_prereqs.sh   (solo imprime)   |   ./infra/00_prereqs.sh
set -euo pipefail
source "$(dirname "$0")/lib.sh"

log "Habilitando APIs necesarias en $PROJECT"
run gcloud services enable \
  secretmanager.googleapis.com \
  bigquery.googleapis.com \
  bigqueryconnection.googleapis.com \
  bigquerydatatransfer.googleapis.com \
  --project="$PROJECT"

log "Bases de Firestore existentes (solo lectura)"
# Esperado: (default) para control y dbraspberry para eventos. No se migran datos entre ellas.
run gcloud firestore databases list --project="$PROJECT" \
  --format="table(name,locationId,deleteProtectionState)"
