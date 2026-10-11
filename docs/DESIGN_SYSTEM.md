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

## 6. Pendiente de completar en la fase de pantallas

- Fichas de las 7 pantallas con capturas en tema claro y oscuro (3.10).
- Revisión heurística de Nielsen y resultados de la prueba de usabilidad (5 participantes, SUS ≥ 75) (3.13).
- Resultados de Lighthouse (móvil) y de axe por pantalla.
