# Modelo de costos

**Para quién es:** quien tenga que explicar o controlar el gasto en GCP de este proyecto.
**Última verificación:** 2026-10-11.

Fórmulas y estimaciones tomadas literalmente de `docs/PLAN_REMEDIACION.md` sección 14. **Nada de
esto se verificó contra Billing real** — esa verificación es explícitamente trabajo de la Fase 10
(`docs/PLAN_REMEDIACION.md` sección 16.2) y se marca abajo en cada bloque. No se reporta ningún
costo real medido en este documento.

## 1. Precios de referencia (`us-central1`)

**Verificar en las páginas de precios vigentes antes de publicarlos** — estos son aproximados al
momento de escribir el plan, no una cotización oficial.

| Recurso | Precio aproximado |
|---|---|
| Cloud Run vCPU (facturación por request) | US$0.000024 / vCPU-s → ≈ US$0.086/h a 1 vCPU |
| Cloud Run memoria | US$0.0000025 / GiB-s → ≈ US$0.009/h a 1 GiB |
| Egress de Cloud Run a internet | ≈ US$0.085-0.12 / GB |
| Firestore lecturas / escrituras | ≈ US$0.03-0.06 / 100k · ≈ US$0.09-0.18 / 100k |
| GCS Standard / Nearline / Coldline | ≈ US$0.020 / 0.010 / 0.004 por GB-mes (Nearline y Coldline cobran recuperación y tienen duración mínima) |
| BigQuery | almacenamiento ≈ US$0.02/GB-mes; consultas on-demand ≈ US$6.25/TiB |

## 2. Cómputo de Cloud Run

Con `--cpu-throttling`, la instancia de `orchestrator-service` factura **solo mientras procesa una
request**. La SPA hace requests de milisegundos cada 10-30 s (≈ 1-2 % del tiempo real), así que
tener el dashboard abierto sin video cuesta muy poco. Lo que factura en continuo son los
**WebSockets de video** (uplink de la Pi + navegador) mientras alguien está en "En vivo": esa
conexión mantiene la instancia con CPU asignada todo el tiempo que dura.

- Ejemplo: 1 h/día de video en vivo ≈ 30 h/mes ≈ **≈ US$3/mes** de cómputo.
- Navegación normal del dashboard (sin video), 8 h/día ≈ **< US$1/mes**.

**Palanca principal:** minimizar el tiempo con "En vivo" abierto. El cierre automático por
inactividad (15 min) y por pestaña oculta (30 s) del frontend es la mitigación ya implementada.

## 3. Egress de video por hora mirando

`bytes/h = tamaño_frame × fps × 3600`.

- **Antes** (flujo legacy): frame completo a calidad JPEG 70, reenviado a ~33 fps (el bug del
  relay viejo reenviaba el mismo frame cada 30 ms sin importar el fps real de la Pi) → estimado
  **10-18 GB/h**.
- **Después** (este sistema): 640 px, calidad JPEG 60, a 8 fps → estimado **0.7-1.0 GB/h**
  (≈ US$0.06-0.12/h), y **solo** mientras alguien esté efectivamente en "En vivo" (lease atado a la
  conexión del WebSocket del navegador). Las miniaturas de la pantalla de Eventos se sirven por
  URL firmada directamente desde GCS (≈ 30 KB por imagen vista), cacheables por el navegador
  mientras la URL firmada sea válida (9 minutos de caché del lado del backend, `lake.py`).

## 4. Carga de la SPA

Los assets con hash en el nombre se cachean un año en el navegador
(`Cache-Control: public, max-age=31536000, immutable`, `main.py`): la primera visita descarga menos
de 300 KB comprimidos (presupuesto de 180 KB gzip de JS inicial, verificado por `pnpm
check:budget` en CI); las visitas siguientes solo piden `index.html` (`no-store`) y las respuestas
JSON de la API. El flujo anterior (Flet Web) descargaba varios MB de runtime (Flutter/CanvasKit) en
cada sesión nueva — ese costo de egress desapareció con el cambio de stack.

## 5. Firestore

Por día, con un dispositivo y `E` eventos confirmados:

- **Escrituras**: ~300 heartbeats de salud + `2·E` (un evento + un `daily_stats` por evento) +
  ~120/hora de renovación de lease mientras alguien mira "En vivo".
- **Lecturas**: del mismo orden en la Pi (el listener de `device_control` no cuenta como lectura
  recurrente — es un `on_snapshot`, facturado distinto), más lo que pide la caché incremental del
  backend: como mucho **una consulta cada `CACHE_TTL_SECONDS` (10 s por defecto) mientras haya
  algún usuario activo**, compartida entre todos los usuarios conectados simultáneamente (no una
  consulta por usuario), más ~500 lecturas por arranque de instancia (ventana inicial de 500
  eventos, `repo.WINDOW`).

Con `E = 500` eventos/día, el costo estimado es de **centavos de dólar por mes**.

## 6. Data lake

Por evento: imagen original ≈ 150-250 KB (1280×720, calidad 90) + miniatura ≈ 30 KB + JSON del
sidecar ≈ 2 KB. Con 500 eventos/día: ≈ 100-140 MB/día ≈ **3-4 GB/mes nuevos**. Con la retención de
60 días (mitad del tiempo en Standard, mitad en Nearline), en régimen estable hay
≈ 6-8 GB de imágenes acumuladas → **≈ US$0.10-0.15/mes**. La subida desde la Pi (ingress a GCS) no
se cobra. BigQuery: centavos de dólar por mes (los datos son chicos y están particionados por
fecha, así que el MERGE diario escanea poco).

## 7. Riesgos residuales de costo (ya mitigados, no eliminados)

- **Un usuario autorizado olvida una pestaña de "En vivo" abierta.** Acotado por el cierre
  automático de inactividad (15 min) y por pestaña oculta (30 s).
- **Un `DEVICE_TOKEN` filtrado** permitiría a alguien abrir conexiones de uplink falsas. Acotado
  por `ALLOWED_DEVICES` (lista cerrada de dispositivos válidos), `max-instances=1` (techo físico
  de cómputo sin importar cuántas conexiones se abran) y la rotación documentada en
  `docs/SECURITY.md`.
- **Alerta de presupuesto por pronóstico** como red de seguridad adicional, pendiente de que el
  usuario la configure (`infra/08_cleanup_billing.sh` recuerda el paso; no se automatiza porque la
  automatización de esto ya fue bloqueada dos veces por el modo seguro de la sesión de trabajo).

## 8. Qué queda pendiente de verificar (Fase 10, no hecho en este documento)

- **Ningún número de esta página es un costo real medido.** Son estimaciones basadas en fórmulas y
  en los precios de referencia de la sección 1.
- Al cerrar una sesión de pruebas con streaming real: detener procesos, dejar el dispositivo en
  `stopped`, y **revisar Billing → Reports al día siguiente** (la facturación tarda hasta 24 h en
  reflejarse) — un "$0" visto el mismo día de las pruebas no es evidencia de nada.
- Verificar los precios de la sección 1 contra las páginas de precios vigentes de GCP antes de
  tomar cualquier decisión de negocio sobre ellos.
- Una vez verificado el costo real de un ciclo completo (24 h con uso típico de "En vivo"),
  actualizar esta página con la cifra medida y la fecha de la medición.
