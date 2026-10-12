# 0004 — Outbox local idempotente en el borde

**Para quién es:** quien necesite entender por qué la Pi no escribe a Firestore/GCS directamente
desde el momento en que cierra un evento. **Última verificación:** 2026-10-11.

## Contexto

La Pi puede perder red en cualquier momento (WiFi intermitente, corte de energía, mantenimiento).
En la versión anterior, los reintentos de la cola offline usaban `add()` con un ID aleatorio, así
que un reintento después de una falla parcial **duplicaba** el documento (hallazgo D3); además la
tarea de reintento se lanzaba con `asyncio.create_task` sin guardar la referencia, por lo que sus
errores se perdían en silencio. El requisito nuevo de una captura por evento (dos imágenes + un
JSON por cada evento confirmado) añade más pasos que pueden fallar a mitad de camino.

## Decisión

Cada evento cerrado se escribe primero en una tabla `outbox` de SQLite local
(`raspberrypi/epp_edge/outbox.py`), con una columna `stage` (`images → sidecar → firestore`) que
solo avanza cuando el paso anterior termina. Un hilo `Uploader` separado, que corre **siempre**
(incluso con la detección pausada), vacía la tabla: sube las dos imágenes y el JSON con
`if_generation_match=0` (create-only; `PreconditionFailed` se trata como éxito porque significa
"ya existía") y escribe el documento de Firestore + el incremento de `daily_stats` en un solo
`batch.commit()` atómico (`Conflict`/`AlreadyExists` también se trata como éxito). El evento se
identifica con un `event_id` (UUIDv7) generado una sola vez al confirmarse — nunca con un ID
aleatorio nuevo en cada intento.

## Alternativas consideradas

- **Escribir directo a Firestore/GCS sin cola local**: cualquier corte de red entre la detección y
  la escritura pierde el evento sin rastro. Inaceptable dado que "nunca descartar un evento
  porque falló la captura" es una regla dura del plan de remediación.
- **Cola en memoria (sin persistir a disco)**: sobrevive a un fallo de red, pero no a un reinicio
  del proceso o un corte de energía — justo el escenario que una Raspberry Pi 24/7 sin UPS puede
  sufrir.
- **ID aleatorio por intento** (comportamiento anterior): más simple de escribir, pero duplica
  datos ante cualquier reintento parcial. Se descartó explícitamente por ser la causa raíz del
  hallazgo D3.

## Consecuencias

- Un evento puede tardar en aparecer en el dashboard si la Pi está sin red, pero nunca se pierde
  ni se duplica: el `event_id` fijo + `if_generation_match=0` + `batch.commit()` atómico lo
  garantizan con independencia de cuántas veces se reintente.
- El `Uploader` necesita correr incluso con la detección en pausa (`desired_state=stopped`): hay
  que drenar lo que ya está en el outbox aunque no se esté generando nada nuevo.
- Hay un tope de disco (`OUTBOX_MAX_BYTES`): si se supera, se descartan las **imágenes pendientes**
  más antiguas (nunca los eventos, que siguen su camino con `capture.status="dropped_disk_full"`).
- La migración desde la cola vieja (`offline_detections`, con IDs y sin captura) se hace una sola
  vez al arrancar la versión nueva, asignando un `event_id` nuevo a cada fila migrada.
