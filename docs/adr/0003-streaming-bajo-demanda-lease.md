# 0003 — Streaming de video bajo demanda, con lease renovado por el servidor

**Para quién es:** quien necesite entender por qué la Pi no transmite todo el tiempo.
**Última verificación:** 2026-10-11.

## Contexto

La versión anterior abría el stream de video automáticamente ante cualquier detección confirmada
(`AUTO_STREAM_ON_DETECTION` hoy, apagado por defecto) y lo mantenía según un timeout de
inactividad local en la Pi. Con la Pi corriendo 24/7, eso significaba el WebSocket de video
abierto casi toda la jornada, y Cloud Run facturando por cada segundo de esa conexión
(hallazgo C4). Además, el video en vivo es un canal de **observación humana**, no la fuente de los
eventos persistidos — no debería acoplarse a cuándo hay una detección.

## Decisión

El video solo sale de la Pi mientras exista un **lease activo**: `device_control.stream_requested
= true` con `lease_renewed_at` reciente. El backend (`lease.py`, `StreamingLease`) enciende el
lease cuando el primer espectador abre `/api/devices/{id}/live` y lo apaga cuando el último se va;
mientras haya al menos un espectador, una tarea `asyncio` renueva `lease_renewed_at` cada
`LEASE_SECONDS/3` (30 s con el valor por defecto de 90 s). La Pi (`control.py`,
`DeviceControl.should_stream()`) lee ese documento por un listener de Firestore (`on_snapshot`) y
solo abre el `StreamWorker` mientras el lease esté vigente; si nadie renueva, el lease vence en
`LEASE_SECONDS` como máximo y la Pi corta la conexión sola, sin que el servidor tenga que
avisarle explícitamente.

## Alternativas consideradas

- **Seguir abriendo el stream por detección** (comportamiento anterior): sigue disponible detrás
  de `AUTO_STREAM_ON_DETECTION=true` para quien lo quiera explícitamente, pero apagado por defecto
  porque es la causa de costo más directa identificada en la auditoría.
- **Comando explícito de "parar" enviado por el servidor** en vez de un lease con vencimiento: más
  frágil ante una desconexión del servidor o un cierre abrupto del navegador (el servidor podría
  no llegar a enviar el "parar"). Un lease que vence solo es correcto por diseño ante cualquier
  tipo de desconexión, incluida la caída del propio servidor.
- **Que la Pi decida sola cuándo parar** (timeout local, como antes): la Pi no sabe cuántos
  espectadores hay ni si siguen conectados; mover esa decisión al servidor (que sí lo sabe,
  `StreamingLease._counts`) es más preciso.

## Consecuencias

- Sin ningún espectador en "En vivo", cero conexiones de la Pi a Cloud Run — verificado como
  criterio de aceptación de la Fase 2 y la Fase 3 del plan de remediación.
- El corte del lado de la Pi tarda como máximo `LEASE_SECONDS` (90 s) tras irse el último
  espectador, no es instantáneo; es un costo aceptado a cambio de robustez ante desconexiones.
- Si el listener de Firestore de la Pi se desincroniza, un hilo de respaldo
  (`CONTROL_SAFETY_POLL_SECONDS`, cada 5 min) vuelve a leer el estado y se resuscribe si hace
  mucho que no hay sincronía — nunca se queda "congelada" transmitiendo ni dejando de transmitir
  por un listener roto.
