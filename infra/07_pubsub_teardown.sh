#!/usr/bin/env bash
# Fase 4.8 (U2) - Borra la tubería legacy de Pub/Sub: trigger de Eventarc, servicio guardar-detecciones-firestore,
# suscripciones y topic yolo-detections, SA eventarc-trigger-invoker, y deshabilita eventarc/pubsub.
# DESTRUCTIVO: pide confirmación interactiva en cada tramo. NUNCA usa --force; cualquier fallo detiene el script.
#
# Comprobación previa OBLIGATORIA (manual): dbraspberry/detecciones_por_tiempo debe seguir vacía. Si tiene
# documentos nuevos, algo sigue publicando: investigar antes de borrar nada.
#
# Variables opcionales para evitar adivinar nombres:
#   EVENTARC_TRIGGERS="nombre1 nombre2"  (si no se define, el script lista los triggers y se detiene)
#   PUBSUB_SUBSCRIPTIONS="sub1 sub2"     (si no se define, el script lista las suscripciones y se detiene)
# Si `gcloud services disable` avisa de dependencias, el script se detiene (no se fuerza).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

LEGACY_RUN_SERVICE="guardar-detecciones-firestore"
TOPIC="yolo-detections"
INVOKER_SA="eventarc-trigger-invoker@${PROJECT}.iam.gserviceaccount.com"

confirm "Confirmas que dbraspberry/detecciones_por_tiempo está vacía y que quieres borrar la tubería Pub/Sub?"

log "1/6 Triggers de Eventarc"
if [[ -z "${EVENTARC_TRIGGERS:-}" ]]; then
  run gcloud eventarc triggers list --location="$REGION" --project="$PROJECT"
  if [[ "$DRY_RUN" != "1" ]]; then
    echo "Define EVENTARC_TRIGGERS con los nombres a borrar y vuelve a ejecutar." >&2
    exit 3
  fi
else
  for t in $EVENTARC_TRIGGERS; do
    run gcloud eventarc triggers delete "$t" --location="$REGION" --project="$PROJECT" --quiet
  done
fi

log "2/6 Servicio de Cloud Run legacy"
run gcloud run services delete "$LEGACY_RUN_SERVICE" --region="$REGION" --project="$PROJECT" --quiet

log "3/6 Suscripciones y topic"
if [[ -z "${PUBSUB_SUBSCRIPTIONS:-}" ]]; then
  run gcloud pubsub subscriptions list --project="$PROJECT" --format="value(name)"
  if [[ "$DRY_RUN" != "1" ]]; then
    echo "Define PUBSUB_SUBSCRIPTIONS con las asociadas a $TOPIC y vuelve a ejecutar." >&2
    exit 3
  fi
else
  for s in $PUBSUB_SUBSCRIPTIONS; do
    run gcloud pubsub subscriptions delete "$s" --project="$PROJECT" --quiet
  done
fi
run gcloud pubsub topics delete "$TOPIC" --project="$PROJECT" --quiet

log "4/6 Rol run.invoker de la SA del trigger"
run gcloud projects remove-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$INVOKER_SA" --role=roles/run.invoker --condition=None

log "5/6 Borrado de la SA del trigger"
run gcloud iam service-accounts delete "$INVOKER_SA" --project="$PROJECT" --quiet

log "6/6 Deshabilitar APIs (sin --force: si hay dependencias se detiene)"
run gcloud services disable eventarc.googleapis.com pubsub.googleapis.com --project="$PROJECT"
