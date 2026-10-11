-- Carga inicial (ejecutar UNA vez a mano, antes de que corra la consulta programada):
-- mismo MERGE que 04 pero sin filtros de fecha.
MERGE `epp-detection-v1.epp_lake.events` T
USING (
  SELECT * EXCEPT(device, dt, rn) FROM (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY event_id ORDER BY end_ts DESC) rn
    FROM `epp-detection-v1.epp_lake.raw_events`
  ) WHERE rn = 1
) S
ON T.event_id = S.event_id
WHEN NOT MATCHED THEN INSERT ROW;
