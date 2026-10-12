#!/usr/bin/env bash
# Fase 6.4 - Actualizacion de epp-edge ya instalado en la Raspberry Pi.
#
# Secuencia: git pull --ff-only -> pip install -r -> pytest -q raspberrypi/tests ->
#            systemctl restart epp-edge -> espera a que device_health confirme la actualizacion.
# Si cualquier paso falla, revierte al commit anterior, reinstala sus dependencias y reinicia
# el servicio con el codigo viejo (nunca deja la Pi corriendo un intento fallido).
#
# Uso: sudo bash raspberrypi/deploy/update.sh
#
# Variables opcionales:
#   HEALTH_TIMEOUT   segundos a esperar la confirmacion en device_health tras el reinicio
#                    (por defecto 120; en una Pi lenta cargando el modelo puede necesitar mas).
set -euo pipefail

APP_DIR=/opt/epp-edge/app
VENV=/opt/epp-edge/venv
SERVICE_NAME=epp-edge
ENV_FILE=/etc/epp-edge/edge.env
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-120}"

log() { printf '== %s\n' "$*"; }
warn() { printf 'ATENCION: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Ejecuta este script con sudo (reinicia el servicio y escribe en $APP_DIR)."
[[ -d "$APP_DIR/.git" ]] || die "$APP_DIR no es un clon de git. Corre install.sh primero."
[[ -x "$VENV/bin/python" ]] || die "No existe $VENV. Corre install.sh primero."
[[ -f "$ENV_FILE" ]] || die "No existe $ENV_FILE. Corre install.sh primero."

version_of() {
  # El paquete epp_edge se importa desde raspberrypi/ (asi corre tambien el servicio: ver
  # WorkingDirectory en epp-edge.service).
  (cd "$APP_DIR/raspberrypi" && "$VENV/bin/python" -c 'from epp_edge import __version__; print(__version__)')
}

rollback() {
  local reason="$1"
  warn "Revirtiendo $APP_DIR a $PREV_COMMIT ($reason)"
  # Cada paso usa `|| true`: aunque alguno falle (p. ej. el propio git reset), seguimos hasta
  # el reinicio final en vez de dejar el servicio a medio actualizar sin reintentar nada.
  git -C "$APP_DIR" reset --hard "$PREV_COMMIT" || warn "git reset --hard $PREV_COMMIT fallo"
  "$VENV/bin/pip" install -r "$APP_DIR/raspberrypi/requirements.txt" || warn "pip install de rollback fallo"
  systemctl restart "$SERVICE_NAME" || warn "systemctl restart de rollback fallo"
  die "Actualizacion revertida: $reason"
}

cd "$APP_DIR"
PREV_COMMIT="$(git rev-parse HEAD)"
PREV_VERSION="$(version_of)"
log "Commit actual: $PREV_COMMIT (version $PREV_VERSION)"

log "git pull --ff-only"
git pull --ff-only
NEW_COMMIT="$(git rev-parse HEAD)"

if [[ "$NEW_COMMIT" == "$PREV_COMMIT" ]]; then
  log "Ya estaba al dia; no se reinicia el servicio."
  exit 0
fi

NEW_VERSION="$(version_of)"
log "Nuevo commit: $NEW_COMMIT (version $NEW_VERSION)"

log "Instalando dependencias (requirements.txt + requirements-dev.txt, necesario para pytest)"
"$VENV/bin/pip" install -r raspberrypi/requirements.txt -r raspberrypi/requirements-dev.txt

log "Corriendo pruebas: pytest -q raspberrypi/tests"
if ! "$VENV/bin/python" -m pytest -q raspberrypi/tests; then
  rollback "las pruebas de raspberrypi/tests fallaron"
fi

# Se leen y validan ANTES de reiniciar: si falta algo en edge.env, abortar aqui no deja el
# servicio reiniciado sin forma de verificarlo ni revertirlo.
# (con `|| true` para que, si falta la variable, sea el chequeo `:?` de abajo el que avise con
#  un mensaje claro y no un error crudo de pipefail).
GCP_PROJECT_ID="$(grep -E '^GCP_PROJECT_ID=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
CONTROL_DATABASE_ID="$(grep -E '^CONTROL_DATABASE_ID=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
DEVICE_ID="$(grep -E '^DEVICE_ID=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
GOOGLE_APPLICATION_CREDENTIALS="$(grep -E '^GOOGLE_APPLICATION_CREDENTIALS=' "$ENV_FILE" | head -n1 | cut -d= -f2- || true)"
: "${GCP_PROJECT_ID:?falta GCP_PROJECT_ID en $ENV_FILE}"
: "${CONTROL_DATABASE_ID:?falta CONTROL_DATABASE_ID en $ENV_FILE}"
: "${DEVICE_ID:?falta DEVICE_ID en $ENV_FILE}"
: "${GOOGLE_APPLICATION_CREDENTIALS:?falta GOOGLE_APPLICATION_CREDENTIALS en $ENV_FILE}"
export GOOGLE_APPLICATION_CREDENTIALS

RESTART_EPOCH="$(date +%s)"
log "Reiniciando $SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

log "Esperando confirmacion en device_health/$DEVICE_ID (maximo ${HEALTH_TIMEOUT}s)"
if [[ "$NEW_VERSION" != "$PREV_VERSION" ]]; then
  log "Se espera edge_version=$NEW_VERSION (antes era $PREV_VERSION)"
else
  warn "Esta actualizacion no cambio __version__ ($NEW_VERSION); solo se podra confirmar que" \
    "el servicio volvio a escribir device_health despues del reinicio, no que corre el codigo nuevo." \
    "Si puedes, sube __version__ en raspberrypi/epp_edge/__init__.py en cada release."
fi

if ! "$VENV/bin/python" - "$GCP_PROJECT_ID" "$CONTROL_DATABASE_ID" "$DEVICE_ID" \
    "$PREV_VERSION" "$NEW_VERSION" "$RESTART_EPOCH" "$HEALTH_TIMEOUT" <<'PY'
import sys
import time
from datetime import datetime, timezone

from google.cloud import firestore

project, database, device_id, prev_version, new_version, restart_epoch, timeout = sys.argv[1:8]
timeout = float(timeout)
restart_at = datetime.fromtimestamp(float(restart_epoch), tz=timezone.utc)
expect_new_version = new_version != prev_version

db = firestore.Client(project=project, database=database)
doc_ref = db.collection("device_health").document(device_id)
deadline = time.monotonic() + timeout

while time.monotonic() < deadline:
    snap = doc_ref.get()
    if snap.exists:
        data = snap.to_dict() or {}
        edge_version = data.get("edge_version")
        last_seen = data.get("last_seen")
        fresh = last_seen is not None and last_seen > restart_at
        if expect_new_version:
            if fresh and edge_version == new_version:
                print(f"OK: edge_version={edge_version} last_seen={last_seen}")
                sys.exit(0)
        elif fresh:
            print(f"OK: last_seen={last_seen} (version sin cambios: {edge_version})")
            sys.exit(0)
    time.sleep(3)

print("TIMEOUT: device_health no confirmo la actualizacion", file=sys.stderr)
sys.exit(1)
PY
then
  rollback "no se confirmo la actualizacion en device_health en ${HEALTH_TIMEOUT}s"
fi

log "Actualizacion completa: $PREV_COMMIT -> $NEW_COMMIT (version $NEW_VERSION)"
