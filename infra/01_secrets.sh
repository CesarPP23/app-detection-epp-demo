#!/usr/bin/env bash
# Fase 4.2 - Secretos en Secret Manager: device-token y session-secret.
# Los valores se generan en un pipe y van directo a gcloud: NUNCA se imprimen, guardan en variables
# ni escriben en disco. Este script no lee ningún secreto (no usa `versions access`).
# Si el secreto ya existe NO se toca (para rotar, ver docs/OPERATIONS_RUNBOOK.md).
# Mutaciones: crea los secretos device-token y session-secret (con una versión inicial cada uno).
# Para la Pi: el usuario ejecuta él mismo `gcloud secrets versions access latest --secret=device-token`
# y lo pega en /etc/epp-edge/edge.env.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

# Genera 48 bytes aleatorios en urlsafe base64 y los escribe a stdout sin salto de línea.
gen() { "$PYTHON" -c "import secrets,sys;sys.stdout.write(secrets.token_urlsafe(48))"; }

for name in device-token session-secret; do
  if exists gcloud secrets describe "$name" --project="$PROJECT"; then
    log "El secreto $name ya existe: no se modifica"
  else
    log "Creando el secreto $name"
    if [[ "$DRY_RUN" == "1" ]]; then
      echo "+ python -c '<genera token>' | gcloud secrets create $name --data-file=- --project=$PROJECT"
    else
      gen | gcloud secrets create "$name" --data-file=- --replication-policy=automatic --project="$PROJECT"
    fi
  fi
done

cat <<'MSG'

Pendiente manual (el agente no lo hace):
  1. Crear el cliente OAuth web en la consola (Google Auth Platform) con el origen autorizado = PUBLIC_BASE_URL.
  2. Copiar el device-token a /etc/epp-edge/edge.env en la Pi.
MSG
