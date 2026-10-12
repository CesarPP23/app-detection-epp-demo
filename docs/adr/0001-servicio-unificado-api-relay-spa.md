# 0001 — Un solo servicio de Cloud Run para API, relay de video y SPA

**Para quién es:** quien evalúe por qué no hay un servicio separado por componente.
**Última verificación:** 2026-10-11.

## Contexto

El sistema necesita tres piezas que hablan entre sí: una API REST (eventos, resumen, auth), un
relay de video en memoria (frames de la Pi hacia el navegador) y una SPA estática servida al
navegador. La versión anterior ya tenía la Pi apuntando a una sola URL de Cloud Run
(`orchestrator-service`) para el video, y un dashboard separado en Flet que leía Firestore
directamente con credenciales propias.

## Decisión

API, relay de WebSocket y la SPA compilada viven en **un solo servicio de Cloud Run**
(`orchestrator-service`), separados por código en `backend/` y `frontend/` pero empaquetados en
una sola imagen (`Dockerfile` multi-etapa en la raíz: compila `frontend/` con Node, copia el
resultado a `static/` dentro de la imagen de `backend/`). FastAPI sirve la API bajo `/api`, los
WebSockets bajo `/ws` y `/api/devices/{id}/live`, y el resto de rutas devuelve la SPA (routing del
lado del cliente).

## Alternativas consideradas

- **Dos servicios** (API+relay en Cloud Run, SPA en un bucket + CDN o Firebase Hosting): un
  servicio menos que mantener se pierde a cambio de resolver CORS y de una segunda URL. El relay
  de video ya vive en memoria de proceso — separar la SPA no cambia esa restricción, solo agrega
  complejidad.
- **Tres servicios** (API, relay, SPA): mismo problema, multiplicado. También multiplica el
  cómputo mínimo facturable y la superficie de IAM.
- **Mantener el dashboard en Flet, aparte del orquestador** (como la versión anterior): el
  dashboard leía Firestore con las credenciales de la Pi (hallazgo S5) y mantenía un WebSocket de
  Flet por pestaña abierta todo el tiempo que la pestaña existiera (hallazgo C7), cosa que factura
  Cloud Run en continuo incluso sin nadie mirando video.

## Consecuencias

- Un solo salto de red para el video (Pi → Cloud Run → navegador) y mismo origen para la SPA y la
  API: sin CORS, cookies `Secure`/`SameSite=Strict` simples.
- Una sola instancia que pagar, un solo `max-instances=1` que vigilar.
- El estado del relay de video vive en memoria de un único proceso: por eso `max-instances=1` es
  una invariante dura (dos instancias significarían "pantalla negra" para quien mire el video si
  cae en la instancia que no tiene al publicador conectado).
- Un cambio en el frontend obliga a reconstruir y redesplegar el mismo contenedor que el backend
  (el `Dockerfile` ya compila ambos en una sola imagen); no hay despliegues independientes de la
  SPA.
