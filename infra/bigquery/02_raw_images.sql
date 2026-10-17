-- Fase 5.3 / 02 - Object table sobre las imágenes originales (no estructurado).
-- Columnas: uri, generation, content_type, size, md5_hash, updated, metadata (incluye x-goog-meta-*) y ref.
CREATE OR REPLACE EXTERNAL TABLE `epp-detection-v1.epp_lake.raw_images`
WITH CONNECTION `epp-detection-v1.us-central1.lake-conn`
OPTIONS (
  object_metadata = 'SIMPLE',
  uris = ['gs://epp-detection-v1-lake/raw/images/*'],
  metadata_cache_mode = 'AUTOMATIC',
  max_staleness = INTERVAL 1 DAY
);
