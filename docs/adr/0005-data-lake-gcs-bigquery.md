# 0005 — Data lake en GCS + BigQuery (object tables), sin Dataflow ni Dataproc

**Para quién es:** quien evalúe si el lake necesita un motor de procesamiento distribuido.
**Última verificación:** 2026-10-11.

## Contexto

El requisito del usuario (decisión U4) es una captura por cada evento confirmado, en un data lake
que mezcle datos estructurados (el evento) y no estructurados (la imagen), consultables juntos. El
volumen esperado es el de **un dispositivo**: cientos de eventos por día, no miles por segundo.

## Decisión

El lake vive en un bucket de GCS (`gs://epp-detection-v1-lake`, zona bronce `raw/`) y en un
dataset de BigQuery (`epp_lake`): una **tabla externa** (`raw_events`) lee directamente los
sidecars NDJSON del bucket con particiones estilo Hive (`device=`, `dt=`); una **object table**
(`raw_images`) expone los metadatos de las imágenes (incluida su metadata `x-goog-meta-*`) sin
copiarlas; una tabla curada (`events`, plata) se puebla con un `MERGE` programado una vez al día
(`infra/bigquery/04_merge_daily.sql`), y dos vistas (oro) resuelven los cruces y agregados más
comunes. Todo esto se crea con scripts de `infra/` y archivos `.sql` versionados, sin ningún motor
de procesamiento distribuido.

## Alternativas consideradas

- **Dataflow** (Apache Beam gestionado) para transformar y cargar los eventos: pensado para
  volúmenes y transformaciones mucho mayores que un MERGE diario de unos pocos miles de filas;
  agregaría una pieza de infraestructura (jobs, workers) y costo fijo sin un beneficio claro a
  esta escala.
- **Dataproc** (Spark/Hadoop gestionado): mismo razonamiento, pensado para procesamiento por lotes
  a gran escala.
- **Cargar las imágenes dentro de BigQuery** (en vez de una object table que solo referencia GCS):
  BigQuery no está pensado para almacenar binarios grandes como filas; la object table resuelve
  exactamente este caso (metadatos consultables, dato real en GCS) sin esa fricción.
- **Un pipeline de ETL propio** (Cloud Function disparada por cada subida): más piezas móviles que
  una consulta programada de BigQuery, y el MERGE diario ya es idempotente y re-ejecutable sin
  ese costo operativo.

## Consecuencias

- El costo adicional de este diseño es, en la práctica, cercano a cero a esta escala (centavos de
  dólar por mes, ver `docs/COSTS.md`), porque tanto las consultas externas como el MERGE
  diario escanean poco dato.
- La tabla curada no está disponible al instante: hay que esperar al MERGE diario (`06:00 UTC`) o
  ejecutarlo a mano para ver un evento reciente reflejado en `epp_lake.events` (la tabla externa
  `raw_events` sí refleja los datos de inmediato, en cuanto el objeto JSON está en GCS).
- Si el volumen crece varios órdenes de magnitud (más dispositivos, mayor frecuencia), este diseño
  debería revisarse; hoy no hay evidencia de que haga falta.
