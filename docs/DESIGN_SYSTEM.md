# Sistema de diseño — EPP Monitor

**Para quién es:** quien implemente o mantenga el frontend (`frontend/`) o revise su diseño.
**Última verificación:** 2026-10-10 (cimientos y componentes; las fichas de pantalla y las capturas las completa la fase de pantallas).

El frontend es una SPA en React + TypeScript (Vite). Este documento fija las reglas visuales y de
interacción que los componentes de `frontend/src/components/` **imponen**: las páginas no
configuran Recharts ni inventan colores fuera de los tokens.

---

## 1. Principios

1. **Estado de un vistazo.** En menos de 3 s cualquiera sabe si el dispositivo está detectando, en
   vivo, pausado o caído. La píldora de estado vive en la barra superior de todas las pantallas.
2. **Evidencia primero.** Cada número lleva a los eventos que lo producen y cada evento lleva a su
   captura. Nada de cifras sin trazabilidad.
3. **Calma por defecto.** Neutros para la estructura, un acento azul para la interacción y la
   magnitud, y colores de estado **solo** para el estado (nunca decorativos).
4. **Honestidad de los datos.** Periodos explícitos ("últimos 7 días", "hora de Lima"), eventos
   simulados ocultos por defecto y marcados cuando se muestran, latencia siempre aproximada ("≈").
5. **Accesible y en español.** WCAG 2.2 AA, teclado completo, es-PE.

## 2. Tokens

Definidos en `frontend/src/design/tokens.css` como variables CSS y expuestos a Tailwind v4 con
`@theme inline` (clases `bg-surface-1`, `text-ink-2`, `border-line`, `text-critical`, …).

**El tema por defecto es claro (U8)** y no sigue la preferencia del sistema operativo. El oscuro se
aplica solo con `[data-theme="dark"]` cuando el usuario lo elige en la barra superior. La elección se
guarda en `localStorage` (con `try/catch`) y `public/theme-init.js` la aplica antes del primer render
para evitar el destello de tema. Es un archivo y no un script en línea porque el CSP del backend
solo permite `script-src 'self'`.

| Token | Claro | Oscuro | Uso |
|---|---|---|---|
| `--page` | `#f9f9f7` | `#0d0d0d` | fondo de la app |
| `--surface-1` | `#fcfcfb` | `#1a1a19` | tarjetas, paneles, gráficos |
| `--surface-2` | `#f0efec` | `#232322` | hover, filas alternas, inputs |
| `--border` | `rgba(11,11,11,.10)` | `rgba(255,255,255,.10)` | bordes de 1 px |
| `--text-primary` | `#0b0b0b` | `#ffffff` | texto principal y valores |
| `--text-secondary` | `#52514e` | `#c3c2b7` | etiquetas, descripciones |
| `--text-muted` | `#898781` | `#898781` | ejes, metadatos, placeholders |
| `--grid` | `#e1e0d9` | `#2c2c2a` | líneas de grilla |
| `--axis` | `#c3c2b7` | `#383835` | línea base |
| `--accent` | `#2a78d6` | `#3987e5` | botones primarios, enlaces, foco, magnitud |
| `--status-good` | `#0ca30c` | `#0ca30c` | Detectando / OK |
| `--status-warning` | `#fab219` | `#fab219` | Pausado / atención |
| `--status-serious` | `#ec835a` | `#ec835a` | Cámara con problemas / outbox creciendo |
| `--status-critical` | `#d03b3b` | `#d03b3b` | Sin conexión / error |
| `--delta-good-text` | `#006300` | `#0ca30c` | texto de variación positiva |
| `--text-muted-aa` | `#6b6a65` | `#a3a19b` | texto atenuado (metadatos, ayudas): `text-ink-3` |
| `--accent-text` | `#1f6bc4` | `#6aa5f0` | enlaces y texto en color de acento: `text-accent-text` |
| `--accent-solid` | `#1f6bc4` | `#2570cc` | fondo de botones y chips activos con texto blanco: `bg-accent-solid` |
| `--critical-text` | `#c23030` | `#f27878` | mensajes y variaciones negativas en texto: `text-critical-text` |

**Variantes AA para texto (hallazgo del cierre de la Fase 3).** Los valores de la tabla de 3.8 se
conservan para lo que no es texto (barras, íconos, bordes, foco, series), pero como *texto* no llegan
a 4,5:1: `--text-muted` `#898781` da 3,4:1 sobre `--page`; `--accent` `#2a78d6` da 4,3:1 sobre
`--surface-1` y 4,42:1 con texto blanco encima; `--status-critical` `#d03b3b` da 3,6:1 sobre
`--surface-1` oscuro. axe lo detectó en el Resumen, el login y Administración. Se añadieron las
cuatro variantes de arriba, todas >= 4,5:1 sobre `--page`, `--surface-1` y `--surface-2` en su tema.
Regla: **texto -> variante `-text`/`-aa`; relleno y trazo -> token base.**

- **Tipografía:** `system-ui, -apple-system, "Segoe UI", Roboto, sans-serif` (sin fuentes web).
  Escala 12 / 14 / 16 / 20 / 24 / 32 / 48 px; pesos 400/500/600. `tabular-nums` (`.tabular`) solo en
  tablas y ejes; los KPI usan cifras proporcionales.
- **Espaciado:** grilla de 4 px. **Radios:** 6 (controles), 10 (tarjetas), 14 (paneles de video).
- **Elevación:** solo bordes de 1 px; sombra únicamente en diálogos y menús.
- **Movimiento:** 150–200 ms `ease-out`; nada animado en bucle salvo el punto "en vivo"; todo se
  desactiva con `prefers-reduced-motion`.
- **Estados siempre con ícono + texto + color**: `CheckCircle2` Detectando, `Radio` En vivo (acento,
  no rojo: el rojo es "crítico"), `PauseCircle` Pausado, `WifiOff` Sin conexión, `CameraOff` Cámara
  no disponible.

### Colores por clase de EPP

Identidad, no magnitud: mapeo **fijo por nombre de clase** (`design/classColors.ts`), nunca por
ranking ni por orden de aparición. Una clase nueva toma los slots 5–8 por orden de registro y, pasado
el slot 8, "Otras" en gris. Un filtro que cambia la cantidad de series no repinta a las demás.

| Clase | Slot | Claro | Oscuro |
|---|---|---|---|
| `hairnet` | 1 azul | `#2a78d6` | `#3987e5` |
| `lab_coat` | 2 naranja | `#eb6834` | `#d95926` |
| `face_mask` | 3 aqua | `#1baf7a` | `#199e70` |
| `latex_gloves` | 4 amarillo | `#eda100` | `#c98500` |

Las chips de clase (`ClassChip`) usan texto en tinta normal y un punto del color de la clase: **el
texto nunca va en el color de la serie**.

#### Validación de la paleta (skill `dataviz`, `validate_palette.js`)

```
Palette (light, surface #fcfcfb, categorical): 4 slots
  [PASS] Lightness band         all 4 inside L 0.43–0.77
  [PASS] Chroma floor           all 4 >= 0.1
  [PASS] CVD separation         worst adjacent #eda100↔#1baf7a ΔE 9.1 (protan) · tritan 27.0
  [PASS] Normal-vision floor    worst adjacent #eda100↔#1baf7a ΔE 22.9 (normal)
  [WARN] Contrast vs surface    below 3:1 — relief required (visible labels or table view): [["#1baf7a",2.74],["#eda100",2.11]]
  → ALL CHECKS PASS

Palette (dark, surface #1a1a19, categorical): 4 slots
  [PASS] Lightness band         all 4 inside L 0.48–0.67
  [PASS] Chroma floor           all 4 >= 0.1
  [PASS] CVD separation         worst adjacent #c98500↔#199e70 ΔE 8.4 (protan) · tritan 24.4
  [PASS] Normal-vision floor    worst adjacent #c98500↔#199e70 ΔE 19.8 (normal)
  [PASS] Contrast vs surface    all 4 >= 3:1
  → ALL CHECKS PASS
```

**Obligaciones que deja el WARN en tema claro:** `face_mask` (aqua) y `latex_gloves` (amarillo)
quedan por debajo de 3:1 contra la superficie, así que **siempre** van con etiqueta visible o vista
de tabla (leyenda con chips en "Por clase", etiquetas directas en barras, `Ver como tabla` en todo
gráfico). Con CVD en 8–9 ΔE la separación es legal solo con codificación secundaria: leyenda, 2 px de
separación entre segmentos y etiquetas.

## 3. Layout y navegación

- **Escritorio (≥ 1024 px):** barra lateral de 232 px colapsable a 64 px (solo íconos con tooltip):
  *Resumen*, *En vivo*, *Eventos*, *Dispositivo* y, para `admin`, *Administración*. Barra superior de
  56 px: selector de dispositivo, **píldora de estado global** (`aria-live="polite"`), selector de
  tema y menú de usuario. Contenido máximo de 1440 px, márgenes de 24 px.
- **Tablet (768–1023 px):** barra lateral colapsada por defecto.
- **Móvil (< 768 px):** navegación inferior (Resumen, En vivo, Eventos, Más), márgenes de 16 px, sin
  scroll horizontal, objetivos táctiles ≥ 44 px.
- **Inactividad (`hooks/useIdle.ts`):** 15 min sin mouse, teclado, toque ni scroll → todos los
  intervalos de TanStack Query se detienen, el WebSocket de video se cierra y `IdleOverlay` muestra
  "Sesión en pausa para ahorrar recursos · Los datos se actualizarán al volver · [Reanudar]". En
  pausa solo el botón reanuda (un movimiento accidental no). Con la pestaña oculta no se refresca y
  el video se cierra a los 30 s. **Esto es lo que mantiene el costo en cero con pestañas olvidadas.**
- **Atajos** (ayuda con `?`): `g r` Resumen, `g v` En vivo, `g e` Eventos, `f` pantalla completa en
  En vivo, `/` buscar evento por ID. Se ignoran mientras se escribe en un campo.

## 4. Componentes (`frontend/src/components/`)

| Componente | Propósito y reglas |
|---|---|
| `AppShell`, `Sidebar`, `TopBar`, `BottomNav` | Layout 3.9, "Saltar al contenido", atajos, ayuda de atajos |
| `StatusPill` | Estado con ícono + texto + color. `live` usa el acento (no el rojo) |
| `StatTile` | KPI (valor, delta con flecha + texto, sparkline opcional). Delta "—" si no hay periodo previo |
| `Sparkline` | Línea de 2 px, un tono, sin ejes |
| `ActivityChart` | Columnas en un tono (`--accent`) o apiladas por clase; extremos de 4 px; 2 px de separación; un solo eje Y; grilla tenue; tooltip por columna; leyenda en "Por clase"; tabla alternativa |
| `ClassBreakdown` | Barras horizontales en un tono, ordenadas desc., valor al final; clic lleva a Eventos filtrados; tabla alternativa |
| `ChartFrame`, `DataTable` | Marco común de gráficos (título, `aria-describedby`, "Ver como tabla") y su tabla |
| `ClassChip` | Punto del color de clase + texto en tinta normal |
| `ConfidenceMeter` | `role="meter"`, un solo tono |
| `EventCard`/`EventTable`/`EventDetailPanel`/`FilterBar` | Específicos de pantalla: los implementa la fase de pantallas sobre estas piezas |
| `CaptureViewer` | Imagen 16:9 (sin CLS) + cajas SVG normalizadas (clase del evento con su color y 2 px; el resto en gris y 1 px); estados `expired`/`missing`/error de carga con mensaje |
| `SessionTimeline` | Barra inicio→fin con marcador en el instante de la captura |
| `LivePlayer` | Canvas + WebSocket binario; estados conectando → esperando al dispositivo → en vivo → señal congelada (> 2 s) → transmisión finalizada (+ denegado, saturado, fallido, en pausa); métricas "≈ ms · fps"; pantalla completa (`f`); descarta frames viejos; se cierra al salir de la ruta, con la pestaña oculta > 30 s o inactividad |
| `EmptyState`, `ErrorState`, `Skeleton` | Vacío (qué significa y qué hacer), error ("Reintentar", sin jerga), esqueleto con la forma final |
| `ConfirmDialog` | Radix Dialog: foco atrapado, Esc, ARIA |
| `ToastProvider`/`useToast` | Región `aria-live="polite"` |
| `CopyButton` | Anuncia "Copiado" |
| `IdleOverlay` | Capa de pausa por inactividad |
| `RequireRole` (`auth/`) | Sin sesión → `/login?next=`; rol insuficiente → "Sin permiso" |

Reglas de datos del cliente: `staleTime` 10 s, `refetchOnWindowFocus`, sin refresco en segundo plano;
intervalos resumen/eventos 30 s, dispositivo 10 s (5 s en "En vivo"), **todos apagados con
inactividad o pestaña oculta** (`useRefetchInterval`). Un `401` lleva a `/login?next=`; un `403`
muestra "Sin permiso". Sin `localStorage` para datos ni tokens, solo preferencias (tema, densidad,
barra lateral).

Presupuesto de rendimiento: JS inicial < 180 KB gzip (`pnpm check:budget`; medido: 143,6 KB), code
splitting por ruta (Recharts solo viaja con las pantallas que tienen gráficos), LCP < 2,5 s en 4G,
CLS < 0,05 (imágenes con `aspect-ratio` fijo).

## 5. Accesibilidad (WCAG 2.2 AA)

- Contraste de texto ≥ 4,5:1 (≥ 3:1 en texto grande y controles); se verifica con axe en ambos temas
  (`@axe-core/playwright`, en CI).
- Foco visible de 2 px con `--accent` y desplazamiento de 2 px; orden de foco lógico; "Saltar al contenido".
- Todo funciona con teclado (Radix resuelve diálogos, menús y tooltips).
- `alt` de capturas: "Captura del evento: {clase}, {hora}, confianza {x} %".
- Cambios de estado del dispositivo anunciados con `aria-live="polite"`.
- Cada gráfico tiene título, descripción breve (`aria-describedby`) y vista de tabla.
- Español neutro (es-PE), segunda persona, sin jerga técnica ("Sin señal del dispositivo", no
  "WebSocket closed 1006"). Números con `Intl.NumberFormat("es-PE")`; fechas en `America/Lima`.

## 6. Fichas de pantalla (3.10)

Capturas generadas por `frontend/e2e/visual.spec.ts` con datos de ejemplo (la API se intercepta en el
navegador, sin backend real); se regeneran con `pnpm test:e2e`. Escritorio (1440 px) en ambos temas y
móvil (360 px) en claro; 768 px se verifica sin guardarse.

| Pantalla (ruta) | Rol mínimo | Claro 1440 | Oscuro 1440 | Móvil 360 |
|---|---|---|---|---|
| Inicio de sesión (`/login`) | — | ![](img/login-1440-claro.png) | ![](img/login-1440-oscuro.png) | ![](img/login-360-claro.png) |
| Resumen (`/`) | viewer | ![](img/resumen-1440-claro.png) | ![](img/resumen-1440-oscuro.png) | ![](img/resumen-360-claro.png) |
| En vivo (`/live`) | viewer (pausar: operator) | ![](img/en-vivo-1440-claro.png) | ![](img/en-vivo-1440-oscuro.png) | ![](img/en-vivo-360-claro.png) |
| Eventos (`/events`) | viewer | ![](img/eventos-1440-claro.png) | ![](img/eventos-1440-oscuro.png) | ![](img/eventos-360-claro.png) |
| Detalle de evento (`/events/:id`) | viewer | ![](img/detalle-evento-1440-claro.png) | ![](img/detalle-evento-1440-oscuro.png) | ![](img/detalle-evento-360-claro.png) |
| Dispositivo (`/device`) | viewer (pausar: operator) | ![](img/dispositivo-1440-claro.png) | ![](img/dispositivo-1440-oscuro.png) | ![](img/dispositivo-360-claro.png) |
| Administración (`/admin`) | admin | ![](img/administracion-1440-claro.png) | ![](img/administracion-1440-oscuro.png) | ![](img/administracion-360-claro.png) |

Cada pantalla define estados de carga (esqueletos con la forma final), vacío, error con "Reintentar" y
sin permiso; están cubiertos por las pruebas de Vitest de cada pantalla.

## 7. Verificación automatizada

Fecha: 2026-10-10 · Chromium (Playwright 1.64) contra `vite preview` con la API interceptada.

| Verificación | Cómo | Resultado |
|---|---|---|
| Flujos e2e (13) | `e2e/flujos.spec.ts`: login en ventana limpia, cuenta no autorizada, login con `?next=`, recorrido de las 7 pantallas con `viewer` y `admin`, `viewer` sin botón de pausa, evento → detalle con cajas, `/events/<id>` directo, filtros en la URL, pestaña oculta (con control positivo), 15 min de inactividad (reloj simulado), salida de En vivo, barra lateral | ✓ 13/13 |
| Accesibilidad (14) | `e2e/accesibilidad.spec.ts`: axe (`wcag2a/aa`, `wcag21a/aa`, `wcag22aa`) en las 7 pantallas × 2 temas | ✓ 0 violaciones serias o críticas |
| Visual (42) | `e2e/visual.spec.ts`: 7 pantallas × 3 anchos (360/768/1440) × 2 temas; `scrollWidth <= clientWidth` y ningún elemento fuera del viewport salvo dentro de contenedores con scroll propio | ✓ 42/42 |
| Presupuesto de JS | `pnpm check:budget` | ✓ 141,1 KB gzip (límite 180) |
| Lighthouse móvil sobre `/` | Lighthouse 12.8.2, preset móvil (Slow 4G y CPU ×4 simulados), 3 corridas | Performance 90–91 · Accesibilidad 98 · Mejores prácticas 100 · CLS 0,00 · TBT 20 ms · LCP 3,1–3,2 s · FCP 2,4 s |

Notas honestas sobre Lighthouse:

- Se midió con la API interceptada y un servidor local, no contra Cloud Run: **el LCP real depende de
  la red y del arranque en frío de la instancia** y debe repetirse en la Fase 10 con la URL de
  producción.
- El preset móvil de Lighthouse (Slow 4G) es más severo que el "4G" de la meta de LCP < 2,5 s; con
  LCP = 3,1 s el rendimiento queda justo en 90, sin margen. El elemento LCP es el valor del primer KPI
  (depende de la respuesta de `/api/devices/.../summary`).
- La primera medición dio CLS 0,058 (> 0,05): el esqueleto "Verificando sesión" usaba `m-6` y su margen
  colapsaba con `<body>`, que se desplazaba 24 px al aparecer y al desaparecer. Se cambió por `p-6`
  (en `RequireRole`) y el CLS bajó a 0,00.

### Defectos hallados y corregidos durante la verificación

1. **Contraste insuficiente como texto** en tokens de la tabla 3.8 (ver sección 2): se añadieron
   `--text-muted-aa`, `--accent-text`, `--accent-solid` y `--critical-text`.
2. **Desborde horizontal en Administración a 360 px** (428 px de ancho): el `span.sr-only` de la columna
   "Acciones" es `position:absolute` y, sin ancestro posicionado, ensanchaba la página aunque la tabla
   tuviera su propio scroll. Los contenedores `overflow-x-auto` de tablas ahora son `relative`.
3. **Barra lateral sin estilos**: `Tooltip.Trigger asChild` (Radix `Slot`) no preserva un `className`
   que sea función (el de `NavLink`), así que el ítem activo no se resaltaba y el ícono quedaba sobre
   el texto. `SidebarItem` calcula la clase como cadena con `useMatch` y marca `aria-current="page"`.
4. **Variación de puntos mal redondeada**: la cobertura de capturas pasó de 100 % a 99,8 % y se
   mostraba "+0 pts" con flecha roja; ahora sale "−0.2 pts" (un decimal; el signo sale de la
   diferencia real, no del redondeo).
5. **Etiqueta de caja recortada**: en capturas con una detección pegada al borde superior, la
   etiqueta (clase y confianza) quedaba fuera de la imagen; ahora se coloca dentro de la caja.

## 8. Prueba de usabilidad y revisión heurística (3.13) — PENDIENTE (requiere personas)

> Esta sección es una plantilla. **No contiene resultados**: se completa cuando se hagan las
> sesiones con usuarios reales (no se simulan).

**Revisión heurística de Nielsen** (hacer antes de la prueba; corregir los hallazgos y registrarlos):

| # | Heurística | Hallazgo | Severidad (0–4) | Estado |
|---|---|---|---|---|
| 1 | Visibilidad del estado del sistema | | | |
| 2 | Coincidencia con el mundo real | | | |
| 3 | Control y libertad del usuario | | | |
| 4 | Consistencia y estándares | | | |
| 5 | Prevención de errores | | | |
| 6 | Reconocer antes que recordar | | | |
| 7 | Flexibilidad y eficiencia | | | |
| 8 | Diseño estético y minimalista | | | |
| 9 | Ayuda a reconocer y recuperarse de errores | | | |
| 10 | Ayuda y documentación | | | |

**Prueba de usabilidad**: 5 participantes, 5 tareas cronometradas, sin ayuda. Metas: ≥ 80 % de éxito
y tarea 1 en < 5 s.

| Tarea | P1 | P2 | P3 | P4 | P5 | Éxito | Tiempo mediano |
|---|---|---|---|---|---|---|---|
| 1. ¿El dispositivo está funcionando ahora? | | | | | | | |
| 2. ¿Cuántos eventos de guantes hubo ayer? | | | | | | | |
| 3. Muéstrame la captura del último evento de mascarilla | | | | | | | |
| 4. Abre el video en vivo | | | | | | | |
| 5. Comparte el enlace de un evento | | | | | | | |

**Cuestionario SUS** (meta ≥ 75): puntuación por participante P1 __ · P2 __ · P3 __ · P4 __ · P5 __ ·
promedio __.

**Observaciones y cambios derivados**: (pendiente).
