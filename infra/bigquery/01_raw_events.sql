-- Fase 5.3 / 01 - Tabla externa sobre los eventos (bronce): lee los sidecars NDJSON del lake.
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
  edge STRUCT<edge_version STRING, model_version STRING, camera_source STRING>
)
WITH PARTITION COLUMNS (device STRING, dt DATE)
WITH CONNECTION `epp-detection-v1.us-central1.lake-conn`
OPTIONS (
  format = 'NEWLINE_DELIMITED_JSON',
  uris = ['gs://epp-detection-v1-lake/raw/events/*'],
  hive_partition_uri_prefix = 'gs://epp-detection-v1-lake/raw/events',
  ignore_unknown_values = TRUE
);
