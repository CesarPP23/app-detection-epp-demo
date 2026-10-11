-- Fase 5.3 / 05 - Vistas oro.
CREATE OR REPLACE VIEW `epp-detection-v1.epp_lake.v_events_with_capture` AS
SELECT e.*, i.size AS capture_object_bytes, i.updated AS capture_uploaded_at, i.ref AS capture_ref
FROM `epp-detection-v1.epp_lake.events` e
LEFT JOIN `epp-detection-v1.epp_lake.raw_images` i ON i.uri = e.capture.uri;

CREATE OR REPLACE VIEW `epp-detection-v1.epp_lake.v_daily_by_class` AS
SELECT device_id, DATE(end_ts, 'America/Lima') AS dia, nombre_clase,
       COUNT(*) AS eventos, SUM(duration_seconds) AS segundos,
       AVG(confianza_promedio) AS confianza_media,
       COUNTIF(capture.status = 'ok') AS eventos_con_captura
FROM `epp-detection-v1.epp_lake.events`
WHERE NOT is_simulated
GROUP BY 1, 2, 3;
