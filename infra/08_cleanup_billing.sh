#!/usr/bin/env bash
# Fase 4.9 - Limpieza de Artifact Registry y del bucket de fuentes de Cloud Build, SA de build y presupuesto.
# Mutaciones (pasos 1-3): política de limpieza de Artifact Registry; lifecycle de 7 días en el bucket de fuentes
# de Cloud Build; crea la SA cloud-build-deployer con roles/run.builder.
# El paso 4 (quitar roles/editor a la SA default de Compute) está APARTE y SOLO se ejecuta con
# REMOVE_COMPUTE_EDITOR=1, y únicamente después de comprobar que un deploy con --build-sa funcionó:
#   ./infra/deploy_cloud.sh <URL> <CLIENT_ID> --build-sa
# El presupuesto NO se automatiza (la automatización ya fue bloqueada dos veces): ver instrucciones al final.
# A COMPROBAR con --help: gcloud artifacts repositories set-cleanup-policies (--policy, --dry-run),
#                         gcloud storage buckets update (--lifecycle-file).
set -euo pipefail
source "$(dirname "$0")/lib.sh"

AR_REPO="cloud-run-source-deploy"
COMPUTE_SA="540868081764-compute@developer.gserviceaccount.com"
# Bucket de fuentes de Cloud Build. gcloud builds submit usa <proyecto>_cloudbuild; run deploy --source usa
# run-sources-<proyecto>-<region>. Se aplica el lifecycle a los que existan.
SOURCE_BUCKETS=("${PROJECT}_cloudbuild" "run-sources-${PROJECT}-${REGION}")

log "1/4 Política de limpieza de Artifact Registry ($AR_REPO): conservar 3 versiones, borrar > 30 días"
run gcloud artifacts repositories set-cleanup-policies "$AR_REPO" --location="$REGION" --project="$PROJECT" \
  --policy="$INFRA_DIR/policies/ar-cleanup.json" --no-dry-run
echo "# Revisar a mano el repo gcr.io: borrarlo (con OK) si no tiene imágenes en uso."

log "2/4 Lifecycle de 7 días en los buckets de fuentes de Cloud Build"
for b in "${SOURCE_BUCKETS[@]}"; do
  if exists gcloud storage buckets describe "gs://$b" --project="$PROJECT"; then
    run gcloud storage buckets update "gs://$b" --project="$PROJECT" --lifecycle-file="$INFRA_DIR/policies/sources-lifecycle.json"
  else
    log "El bucket gs://$b no existe (o DRY_RUN): se omite"
  fi
done

log "3/4 SA de build $BUILD_SA"
if exists gcloud iam service-accounts describe "$BUILD_SA" --project="$PROJECT"; then
  log "La SA de build ya existe"
else
  run gcloud iam service-accounts create "$BUILD_SA_NAME" --display-name="Cloud Build: deploy de Cloud Run" --project="$PROJECT"
fi
run gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$BUILD_SA" --role=roles/run.builder --condition=None
# Si el build falla por permisos faltantes (logging, Artifact Registry), añadir solo los roles que el error indique.

log "4/4 Quitar roles/editor a la SA default de Compute (Q5) - SOLO si REMOVE_COMPUTE_EDITOR=1"
if [[ "${REMOVE_COMPUTE_EDITOR:-0}" == "1" ]]; then
  confirm "Verificaste que el deploy con --build-sa funcionó? Esto quita roles/editor a $COMPUTE_SA."
  run gcloud projects remove-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$COMPUTE_SA" --role=roles/editor --condition=None
else
  echo "# Omitido. Ejecutar de nuevo con REMOVE_COMPUTE_EDITOR=1 tras verificar el deploy con la SA de build."
fi

cat <<'MSG'

== Presupuesto (manual, no automatizado) ==
  1. Billing -> Budgets & alerts: umbrales 25/50/90/100 % y una alerta por PRONÓSTICO al 100 %.
     El monto lo define el usuario según docs/COSTS.md.
  2. Billing -> Account management -> Manage access: agregar el correo del usuario como destinatario.
  3. Proyecto huérfano "My First Project" (woven-surface-469602-g1): preguntar al usuario; si no se usa,
     desvincular la facturación con `gcloud billing projects unlink woven-surface-469602-g1`.
MSG
