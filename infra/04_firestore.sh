#!/usr/bin/env bash
# Fase 4.4 - Firestore: índices compuestos, TTL de audit_log, protección contra borrado, backups semanales.
# Mutaciones:
#   - 4 índices compuestos en dbraspberry (eventos por dispositivo/fecha asc y desc, por clase,
#     daily_stats);
#   - TTL sobre el campo expire_at de audit_log en (default);
#   - delete protection en (default) y dbraspberry;
#   - un backup semanal (domingo, retención 4 semanas) por base, si aún no existe.
# Reglas de seguridad: NO se despliegan desde gcloud. Pegar infra/firestore.rules en
#   Consola -> Firestore -> cada base -> Reglas -> Publicar  (deny-all; todo el acceso es por SDK de servidor).
# Datos iniciales: ver infra/bootstrap_admin.py (device_control y el admin).
#
# A COMPROBAR con `--help` en la versión instalada antes de ejecutar:
#   gcloud firestore indexes composite create --help        (--field-config, --collection-group, --database)
#   gcloud firestore fields ttls update --help               (--enable-ttl, --collection-group, --database)
#   gcloud firestore databases update --help                 (--delete-protection)
#   gcloud firestore backups schedules create --help         (--recurrence, --day-of-week, --retention)
set -euo pipefail
source "$(dirname "$0")/lib.sh"

DETECTIONS_DB="dbraspberry"
CONTROL_DB="(default)"

log "Índices compuestos en $DETECTIONS_DB"
run_create gcloud firestore indexes composite create --project="$PROJECT" \
  --database="$DETECTIONS_DB" --collection-group=detecciones_confirmadas \
  --field-config=field-path=device_id,order=ascending \
  --field-config=field-path=end_ts,order=descending

# repo.py:_newer_than (ventana incremental) filtra end_ts > fecha y ordena end_ts ascendente: el
# índice de arriba (descendente) no sirve para esa consulta. Sin este, Firestore devuelve
# FailedPrecondition en cuanto una instancia de Cloud Run cachea una ventana y pide lo nuevo
# (detectado el 2026-10-16 al conectar el nodo con pose; antes casi no se disparaba porque cada
# arranque en frío usa la consulta inicial, que sí tiene índice).
run_create gcloud firestore indexes composite create --project="$PROJECT" \
  --database="$DETECTIONS_DB" --collection-group=detecciones_confirmadas \
  --field-config=field-path=device_id,order=ascending \
  --field-config=field-path=end_ts,order=ascending

run_create gcloud firestore indexes composite create --project="$PROJECT" \
  --database="$DETECTIONS_DB" --collection-group=detecciones_confirmadas \
  --field-config=field-path=device_id,order=ascending \
  --field-config=field-path=nombre_clase,order=ascending \
  --field-config=field-path=end_ts,order=descending

run_create gcloud firestore indexes composite create --project="$PROJECT" \
  --database="$DETECTIONS_DB" --collection-group=daily_stats \
  --field-config=field-path=device_id,order=ascending \
  --field-config=field-path=date,order=ascending

log "TTL de audit_log (campo expire_at) en $CONTROL_DB"
run gcloud firestore fields ttls update expire_at --project="$PROJECT" \
  --collection-group=audit_log --enable-ttl --database="$CONTROL_DB"

for db in "$CONTROL_DB" "$DETECTIONS_DB"; do
  log "Protección contra borrado y backups en $db"
  run gcloud firestore databases update --database="$db" --delete-protection --project="$PROJECT"
  if exists bash -c "gcloud firestore backups schedules list --database='$db' --project='$PROJECT' --format='value(name)' | grep -q ."; then
    log "$db ya tiene un backup programado: no se crea otro"
  else
    run gcloud firestore backups schedules create --database="$db" --project="$PROJECT" \
      --recurrence=weekly --day-of-week=SUN --retention=4w
  fi
done

echo
echo "Pendiente manual: publicar infra/firestore.rules (deny-all) en la consola, en AMBAS bases."
