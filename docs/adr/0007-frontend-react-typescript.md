# 0007 — Frontend reescrito en React + TypeScript (Vite), backend se mantiene en Python

**Para quién es:** quien se pregunte por qué se abandonó Flet.
**Última verificación:** 2026-10-11.

## Contexto

El dashboard anterior estaba hecho en Flet (Python puro sobre Flutter Web). Entre los hallazgos de
la auditoría: cada pestaña abierta mantenía un WebSocket de Flet permanente, facturando Cloud Run
mientras existiera (hallazgo C7: una pestaña olvidada ≈ US$65/mes de cómputo); el runtime de
Flutter/CanvasKit se descargaba completo en cada sesión nueva (varios MB, hallazgo C13); embeber
un stream MJPEG no era directo porque el motor de renderizado de Flutter Web espera una imagen
completa, no un flujo infinito, así que el propio backend del dashboard tenía que reempaquetar
cada JPEG a base64. El usuario pidió explícitamente mejorar la UX/UI del dashboard (decisión U5) y
autorizó cambiar de lenguaje en el frontend, manteniendo el backend en Python.

## Decisión

El frontend se reescribió completo en **React 19 + TypeScript estricto + Vite**, con React
Router, TanStack Query (caché y refetch), Tailwind CSS v4 (tokens en variables CSS), Radix UI
(accesibilidad de diálogos/menús/pestañas), `lucide-react`, Recharts envuelto en componentes
propios que imponen las reglas de visualización del proyecto, y `date-fns`/`date-fns-tz` para
fechas en `es-PE`/`America/Lima`. El backend se mantiene en **Python + FastAPI**, como pidió el
usuario.

## Alternativas consideradas

- **Mantener Flet y optimizar su uso** (cerrar el WebSocket cuando la pestaña no está activa,
  etc.): no resuelve el problema de fondo — el modelo de Flet Web es "una conexión persistente por
  sesión", que es exactamente lo que hay que evitar para mantener el costo bajo con `--cpu-
  throttling`. Tampoco da acceso a un `<canvas>` nativo ni a MJPEG directo para el video.
- **Vue o Svelte** en vez de React: viables técnicamente, pero React tiene el ecosistema más
  grande de documentación y de mantenedores potenciales — relevante porque este es un proyecto de
  tesis que alguien más podría heredar.
- **Server-side rendering** (Next.js u otro framework con SSR): innecesario para un dashboard
  autenticado detrás de login; SSR agrega complejidad de despliegue sin un beneficio de SEO o
  carga inicial que importe aquí (nadie llega a estas pantallas sin iniciar sesión primero).

## Consecuencias

- Los tipos del frontend se generan desde el OpenAPI del backend (`pnpm gen:api`,
  `openapi-typescript`): un cambio de contrato en el backend rompe el build del frontend en vez de
  fallar en silencio en producción (ver `docs/API.md` sección 5).
- El video en vivo ahora es un WebSocket binario dibujado en `<canvas>` (ver ADR 0002), sin
  reempaquetar a base64 en ningún punto del camino.
- Se introduce un paso de build de Node (`pnpm build` dentro del `Dockerfile`) que no existía
  antes; el `Dockerfile` multi-etapa lo resuelve sin que el runtime final necesite Node instalado
  (solo Python 3.12-slim).
- El backend no cambió de lenguaje (sigue en FastAPI): la decisión del usuario de mantenerlo en
  Python se respetó íntegramente.
