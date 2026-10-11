#!/usr/bin/env bash
# Utilidades comunes de los scripts de infra/. Se carga con `source "$(dirname "$0")/lib.sh"`.
#
# DRY_RUN=1  -> solo imprime los comandos que ejecutaría (no llama a gcloud/bq/gsutil).
#              Las comprobaciones de existencia se omiten y se asume "no existe".
# Nada de este directorio lee ni imprime valores de Secret Manager.

PROJECT="${PROJECT:-epp-detection-v1}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-orchestrator-service}"
RUNTIME_SA_NAME="platform-runtime"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
PI_SA="raspberry-pi-worker@${PROJECT}.iam.gserviceaccount.com"
BUILD_SA_NAME="cloud-build-deployer"
BUILD_SA="${BUILD_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
LAKE_BUCKET="${LAKE_BUCKET:-${PROJECT}-lake}"
INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="${DRY_RUN:-0}"
# Intérprete de Python (en Windows/Git Bash `python3` puede ser un alias de la Store que no funciona).
if [[ -z "${PYTHON:-}" ]]; then
  if python3 -c "" >/dev/null 2>&1; then PYTHON=python3; else PYTHON=python; fi
fi

log() { printf '== %s\n' "$*"; }

# Ejecuta un comando de mutación (o solo lo imprime con DRY_RUN=1).
run() {
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '+ %q' "$1"; shift
    for a in "$@"; do printf ' %q' "$a"; done
    printf '\n'
  else
    "$@"
  fi
}

# Ejecuta una comprobación de solo lectura. Con DRY_RUN=1 devuelve "no existe".
exists() {
  if [[ "$DRY_RUN" == "1" ]]; then
    return 1
  fi
  "$@" >/dev/null 2>&1
}

# Pide confirmación interactiva antes de un paso destructivo (se salta en DRY_RUN).
confirm() {
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "# (DRY_RUN) pediría confirmación: $*"
    return 0
  fi
  read -r -p "$* [escribe SI para continuar] " ans
  [[ "$ans" == "SI" ]] || { echo "Cancelado."; exit 1; }
}

# Ejecuta una creación idempotente: si falla porque el recurso ya existe, lo registra y sigue;
# cualquier otro error detiene el script (set -e). Con DRY_RUN=1 solo imprime.
run_create() {
  if [[ "$DRY_RUN" == "1" ]]; then
    run "$@"
    return 0
  fi
  local out rc=0
  out="$("$@" 2>&1)" || rc=$?
  if [[ $rc -eq 0 ]]; then
    printf '%s\n' "$out"
  elif grep -qiE 'already exists|ALREADY_EXISTS' <<<"$out"; then
    log "ya existía (sin cambios): $1 $2 $3"
  else
    printf '%s\n' "$out" >&2
    return "$rc"
  fi
}
