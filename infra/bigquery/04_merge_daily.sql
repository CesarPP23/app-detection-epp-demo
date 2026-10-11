-- Fase 5.3 / 04 - Consulta programada diaria: MERGE idempotente de los últimos 3 días por event_id.
-- La programa 06_bigquery.sh (schedule "every day 06:00", zona UTC = después de la medianoche de Lima).
-- Para la carga inicial, ejecutar una vez esta misma sentencia SIN los filtros de fecha.
MERGE `epp-detection-v1.epp_lake.events` T
USING (
  SELECT * EXCEPT(device, dt, rn) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY end_ts DESC) rn
    FROM `epp-detection-v1.epp_lake.raw_events`
    WHERE dt >= DATE_SUB(CURRENT_DATE('America/Lima'), INTERVAL 3 DAY)
  ) WHERE rn = 1
) S
ON T.event_id = S.event_id AND DATE(T.end_ts) >= DATE_SUB(CURRENT_DATE('America/Lima'), INTERVAL 4 DAY)
WHEN NOT MATCHED THEN INSERT ROW;
