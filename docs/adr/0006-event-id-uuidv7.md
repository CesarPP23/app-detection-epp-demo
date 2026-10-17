# 0006 — `event_id` UUIDv7 como identificador único de todo el sistema

**Para quién es:** quien necesite entender por qué no hay un ID autoincremental ni un ID compuesto.
**Última verificación:** 2026-10-11.

## Contexto

Un mismo evento tiene que identificarse igual en tres sistemas distintos: el nombre de archivo en
GCS (imagen original, miniatura, sidecar JSON), el ID del documento en Firestore, y la clave de
fila en BigQuery. Sin un identificador común, cruzar la data estructurada (el evento) con la no
estructurada (la imagen) requeriría un índice secundario o una convención de nombres frágil.

## Decisión

Cada evento confirmado recibe un `event_id` generado una sola vez, al abrir la sesión en el
agregador (`raspberrypi/epp_edge/aggregator.py`, `_ActiveSession.event_id`), con
`raspberrypi/epp_edge/ids.py::uuid7()` — **UUID versión 7** (RFC 9562): 48 bits de timestamp en
milisegundos + bits aleatorios. Ese mismo string es, sin transformación: el nombre base de los
tres objetos de GCS (`{event_id}.jpg`, `{event_id}.jpg`, `{event_id}.json`), el ID del documento
`detecciones_confirmadas/{event_id}` en Firestore, y el valor de la columna `event_id` en
`epp_lake.raw_events`/`events` de BigQuery.

## Alternativas consideradas

- **UUID v4 (puramente aleatorio)**: igual de único, pero sin orden temporal. Un UUIDv7 es
  ordenable lexicográficamente por tiempo de creación, lo que ayuda a la paginación por cursor
  (`repo.py` usa `end_ts` como clave primaria de orden, pero el `event_id` ordenado por tiempo
  simplifica el desempate y la depuración manual — un listado de archivos en GCS por prefijo
  queda naturalmente ordenado por tiempo de generación).
- **ID autoincremental** (entero secuencial): requeriría una fuente centralizada de secuencia
  (un contador en Firestore, con contención) justo en el componente que menos debe depender de
  la red (el borde, que puede estar sin conectividad). Un UUIDv7 se genera localmente, sin
  coordinación con nadie.
- **Clave compuesta** (`device_id + timestamp`): funciona como clave de partición pero es
  incómoda como nombre de archivo único y como clave de BigQuery (requiere concatenar varios
  campos en cada consulta, y dos eventos del mismo dispositivo en el mismo milisegundo
  colisionarían).

## Consecuencias

- Cruzar las tres capas (Firestore, GCS, BigQuery) para un evento es tan simple como usar el mismo
  string en los tres lugares — no hace falta un mapeo ni un índice secundario.
- La generación es local y no depende de red: la Pi puede generar `event_id`s sin conectividad y
  subirlos después sin riesgo de colisión.
- `raspberrypi/tests/test_ids.py` verifica unicidad sobre 10 000 IDs generados, que
  `uuid.UUID(x).version == 7`, y que el orden lexicográfico sea coherente con el tiempo cuando se
  generan con un par de milisegundos de diferencia.
- Python < 3.14 no tiene `uuid.uuid7()` nativo (llegó en 3.14); `ids.py` implementa el algoritmo a
  mano como fallback y usa el nativo cuando está disponible, sin cambiar el comportamiento
  observable.
