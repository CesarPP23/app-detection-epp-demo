-- Fase 5.3 / 03 - Tabla curada (plata), particionada por día y agrupada; la puebla el MERGE diario (04).
CREATE TABLE IF NOT EXISTS `epp-detection-v1.epp_lake.events`
PARTITION BY DATE(end_ts) CLUSTER BY device_id, nombre_clase AS
SELECT * EXCEPT(device, dt) FROM `epp-detection-v1.epp_lake.raw_events` WHERE FALSE;
