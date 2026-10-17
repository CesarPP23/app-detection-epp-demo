# 0002 — Video por WebSocket binario sobre Cloud Run, no WebRTC

**Para quién es:** quien considere migrar el video en vivo a WebRTC.
**Última verificación:** 2026-10-11.

## Contexto

El video en vivo necesita viajar de la Pi al navegador con baja latencia, controlando la
contrapresión (qué pasa si el espectador es más lento que el emisor) y permitiendo mostrar
métricas de latencia aproximada y detectar "señal congelada". WebRTC es la tecnología estándar
para video en tiempo real en el navegador, pero exige señalización (SDP/ICE) y, salvo que las dos
partes estén en la misma red, un servidor STUN/TURN.

## Decisión

El video viaja por un **WebSocket binario simple**: cada mensaje es 8 bytes de `captured_at`
(uint64 big-endian, milisegundos Unix) seguidos del JPEG completo (protocolo descrito en
`docs/API.md` sección 3). El backend retransmite el frame más reciente sin decodificarlo
(`relay.py`); el navegador lo dibuja con `createImageBitmap` sobre un `<canvas>`
(`frontend/src/api/live.ts`, `LivePlayer`).

## Alternativas consideradas

- **WebRTC** (con un servidor de señalización y TURN propios, o un servicio gestionado como
  Cloudflare Calls): menor latencia percibida en el caso general, pero agrega una pieza de
  infraestructura nueva (señalización, STUN/TURN) y una dependencia nueva en la Pi
  (`aiortc` o similar) que el plan de remediación explícitamente evita para no introducir más
  superficie en el borde. Queda como trabajo futuro si la latencia medida en la Fase 10 resulta
  insuficiente.
- **MJPEG `multipart/x-mixed-replace`** (lo que hacía la versión anterior): no se puede autenticar
  por WebSocket con cookies de forma tan directa, no permite empaquetar metadata (el timestamp de
  captura) junto al frame, y el dashboard anterior en Flet no podía consumirlo nativamente (tenía
  que reempaquetarlo a base64 por su propio WebSocket).
- **HLS/DASH** (segmentación y manifiestos): pensado para video pregrabado o con mayor tolerancia
  a latencia; la latencia de los segmentos (segundos) no es aceptable para un video "en vivo" de
  verificación de seguridad.

## Consecuencias

- Latencia dominada por la codificación JPEG (~10-20 ms a 640 px) y el tamaño de frame, no por un
  protocolo de video con estado; es simple de depurar (se puede inspeccionar cada mensaje a mano).
- No hay control de tasa adaptativa (bitrate) como en WebRTC: `STREAM_WIDTH`/`STREAM_JPEG_QUALITY`
  son fijos por configuración, no se ajustan solos a la red del espectador.
- Si en el futuro la latencia medida (Fase 10) resulta insuficiente para el caso de uso, WebRTC
  queda como la alternativa evaluada y descartada por ahora, no por desconocimiento.
