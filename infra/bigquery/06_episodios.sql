-- 06 - Episodios del nodo con pose (edge_pose/): columnas propias del episodio y vista de cumplimiento.
-- Se ejecuta DESPUÉS de 01 y 03 (lo hace 06_bigquery.sh). Es idempotente.
-- Las columnas nuevas se añaden AL FINAL y en el mismo orden en raw_events y en events, porque el MERGE diario
-- (04_merge_daily.sql) usa INSERT ROW, que exige el mismo esquema en origen y destino.
-- Para los eventos del nodo por clase (raspberrypi/) estas columnas quedan en NULL.
-- 2026-10-18: el modelo pasó de llamar a esta clase "Gorra" a llamarla "Cofia" (edge_pose/epp_edge/cloud_v2.py).
-- Se conserva `ratios.gorra` para los eventos ya guardados en el lago (JSON inmutable) y se agrega `ratios.cofia`
-- para los eventos nuevos; BigQuery no permite renombrar un campo de un STRUCT ya creado.

CREATE OR REPLACE EXTERNAL TABLE `epp-detection-v1.epp_lake.raw_events` (
  schema_version INT64, event_id STRING, event_type STRING, device_id STRING,
  clase_id INT64, nombre_clase STRING, confianza_promedio FLOAT64, confianza_max FLOAT64,
  frame_count INT64, start_ts TIMESTAMP, end_ts TIMESTAMP, duration_seconds FLOAT64,
  is_confirmed BOOL, closed_reason STRING, is_simulated BOOL,
  capture STRUCT<status STRING, uri STRING, thumb_uri STRING, captured_at TIMESTAMP, sha256 STRING,
                 width INT64, height INT64, bytes INT64, thumb_sha256 STRING, thumb_bytes INT64,
                 frame_seq INT64, confidence_at_capture FLOAT64>,
  detections_at_capture ARRAY<STRUCT<clase_id INT64, nombre_clase STRING, confianza FLOAT64,
                                     bbox_xyxy_norm ARRAY<FLOAT64>>>,
  edge STRUCT<edge_version STRING, model_version STRING, camera_source STRING>,
  -- episodio (nodo con pose)
  zona STRING, cumple BOOL, faltantes ARRAY<STRING>, mal_colocados ARRAY<STRING>, no_evaluables ARRAY<STRING>,
  personas_max INT64, n_personas INT64,
  ratios STRUCT<gorra FLOAT64, cofia FLOAT64, guante FLOAT64, mascarilla FLOAT64>,
  epp_fps FLOAT64, episodio_local_id STRING
)
WITH PARTITION COLUMNS (device STRING, dt DATE)
WITH CONNECTION `epp-detection-v1.us-central1.lake-conn`
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  uris = ['gs://epp-detection-v1-lake/raw/events/*'],
  hive_partition_uri_prefix = 'gs://epp-detection-v1-lake/raw/events',
  ignore_unknown_values = TRUE
);

ALTER TABLE `epp-detection-v1.epp_lake.events`
  ADD COLUMN IF NOT EXISTS zona STRING,
  ADD COLUMN IF NOT EXISTS cumple BOOL,
  ADD COLUMN IF NOT EXISTS faltantes ARRAY<STRING>,
  ADD COLUMN IF NOT EXISTS mal_colocados ARRAY<STRING>,
  ADD COLUMN IF NOT EXISTS no_evaluables ARRAY<STRING>,
  ADD COLUMN IF NOT EXISTS personas_max INT64,
  ADD COLUMN IF NOT EXISTS n_personas INT64,
  ADD COLUMN IF NOT EXISTS ratios STRUCT<gorra FLOAT64, guante FLOAT64, mascarilla FLOAT64>,
  ADD COLUMN IF NOT EXISTS epp_fps FLOAT64,
  ADD COLUMN IF NOT EXISTS episodio_local_id STRING;

-- BigQuery NO soporta agregar un campo a un STRUCT ya existente via SQL DDL (ni con la sintaxis de
-- ruta de puntos ratios.cofia, ni con backticks: da "Syntax error: Unexpected '.'" o "Illegal field
-- name"). La unica forma es parchar el esquema con `bq update` (API de esquema), asi:
--   bq show --schema --format=prettyjson epp-detection-v1:epp_lake.events > schema.json
--   # editar schema.json: agregar {"name": "cofia", "type": "FLOAT"} dentro de fields[] del campo "ratios"
--   bq update epp-detection-v1:epp_lake.events schema.json
-- Ya aplicado a mano el 2026-10-18 (events.ratios.cofia existe). Si se necesita repetir esto para otro
-- campo nuevo en el futuro, repetir el mismo procedimiento (no agregar otro ALTER TABLE con ruta de
-- puntos aqui, no funciona).

-- Vista oro: tasa de cumplimiento por dispositivo y día (hora de Lima) y EPP más faltante.
CREATE OR REPLACE VIEW `epp-detection-v1.epp_lake.v_cumplimiento_diario` AS
WITH ep AS (
  SELECT * FROM `epp-detection-v1.epp_lake.events`
  WHERE event_type = 'episodio' AND NOT is_simulated
)
SELECT
  device_id,
  DATE(end_ts, 'America/Lima') AS dia,
  COUNT(*) AS episodios,
  COUNTIF(cumple) AS conformes,
  SAFE_DIVIDE(COUNTIF(cumple), COUNT(*)) AS tasa_cumplimiento,
  SUM(duration_seconds) AS segundos_observados,
  COUNTIF('Guante' IN UNNEST(faltantes)) AS falta_guante,
  COUNTIF('Mascarilla' IN UNNEST(faltantes)) AS falta_mascarilla,
  -- 'Gorra' en episodios anteriores al 2026-10-18, 'Cofia' desde entonces (ver nota de arriba)
  COUNTIF('Gorra' IN UNNEST(faltantes) OR 'Cofia' IN UNNEST(faltantes)) AS falta_cofia,
  COUNTIF(ARRAY_LENGTH(mal_colocados) > 0) AS con_epp_mal_colocado
FROM ep
GROUP BY 1, 2;
