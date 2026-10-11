#!/usr/bin/env bash
# Fase 4.7 + 5.3 - BigQuery: dataset epp_lake, conexión al lake, tablas externas, tabla curada, vistas y
# consulta programada diaria.
# Mutaciones: crea el dataset y la conexión CLOUD_RESOURCE (lake-conn); da roles/storage.objectViewer a la SA de
# la conexión sobre el bucket; ejecuta los .sql 01, 02, 03 y 05 (CREATE OR REPLACE / IF NOT EXISTS);
# crea la consulta programada curate_events (04_merge_daily.sql, diaria 06:00 UTC) si no existe.
# Orden: ejecutar DESPUÉS de 05_lake_bucket.sh. La carga inicial (99_initial_load.sql) se ejecuta a mano.
# A COMPROBAR con --help: bq mk --connection, bq mk --transfer_config.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

BQ_DIR="$INFRA_DIR/bigquery"
DATASET="epp_lake"
CONN="lake-conn"

if exists bq --project_id="$PROJECT" --location="$REGION" show "$PROJECT:$DATASET"; then
  log "El dataset $DATASET ya existe"
else
  run bq --project_id="$PROJECT" --location="$REGION" mk --dataset "$PROJECT:$DATASET"
fi

if exists bq --project_id="$PROJECT" show --connection "$PROJECT.$REGION.$CONN"; then
  log "La conexión $CONN ya existe"
else
  run bq mk --connection --location="$REGION" --project_id="$PROJECT" --connection_type=CLOUD_RESOURCE "$CONN"
fi

# La SA de la conexión se lee de `bq show --connection` (es un identificador, no un secreto).
if [[ "$DRY_RUN" == "1" ]]; then
  CONN_SA="<serviceAccountId de la conexión>"
  echo "# (DRY_RUN) CONN_SA se obtendría con: bq show --connection --format=json $PROJECT.$REGION.$CONN"
else
  CONN_SA="$(bq show --connection --format=json "$PROJECT.$REGION.$CONN" \
    | "$PYTHON" -c 'import json,sys;print(json.load(sys.stdin)["cloudResource"]["serviceAccountId"])')"
fi
run gcloud storage buckets add-iam-policy-binding "gs://$LAKE_BUCKET" \
  --member="serviceAccount:$CONN_SA" --role=roles/storage.objectViewer

for f in 01_raw_events.sql 02_raw_images.sql 03_events_curated.sql 05_views.sql; do
  log "Ejecutando $f"
  if [[ "$DRY_RUN" == "1" ]]; then
    echo "+ bq --project_id=$PROJECT --location=$REGION query --use_legacy_sql=false < $BQ_DIR/$f"
  else
    bq --project_id="$PROJECT" --location="$REGION" query --use_legacy_sql=false < "$BQ_DIR/$f"
  fi
done

if exists bash -c "bq ls --transfer_config --transfer_location='$REGION' --project_id='$PROJECT' --format=json | grep -q curate_events"; then
  log "La consulta programada curate_events ya existe"
else
  QUERY_JSON="$("$PYTHON" -c 'import json,sys;print(json.dumps({"query": open(sys.argv[1], encoding="utf-8").read()}))' "$BQ_DIR/04_merge_daily.sql")"
  run bq mk --transfer_config --project_id="$PROJECT" --location="$REGION" \
    --data_source=scheduled_query --display_name=curate_events \
    --schedule="every day 06:00" --params="$QUERY_JSON"
fi
