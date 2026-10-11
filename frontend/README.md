# EPP Monitor — frontend

**Para quién es:** quien desarrolle, pruebe o compile la SPA. **Última verificación:** 2026-10-10.

React 19 + TypeScript estricto + Vite. Habla únicamente con la API de `backend/` (REST + WebSocket,
mismo origen: sin CORS). Reglas de diseño en [`docs/DESIGN_SYSTEM.md`](../docs/DESIGN_SYSTEM.md).

## Requisitos

- Node 24 (`.nvmrc`) y pnpm 10.18.3 (`corepack enable`; la versión queda fijada en `packageManager`).
- Dependencias con versión exacta (`.npmrc` → `save-exact`) y `pnpm-lock.yaml` versionado.

## Comandos

| Comando | Qué hace |
|---|---|
| `pnpm install --frozen-lockfile` | Instala (es lo que hace el Dockerfile) |
| `pnpm dev` | Vite en `http://localhost:5173` con proxy de `/api` y `/ws` a `127.0.0.1:8080` |
| `pnpm build` | `tsc --noEmit` + `vite build` → `dist/` |
| `pnpm check:budget` | Falla si el JS inicial supera 180 KB gzip |
| `pnpm lint` / `pnpm typecheck` | ESLint (typescript-eslint, jsx-a11y, react-hooks) / TypeScript |
| `pnpm test` | Vitest + Testing Library (+ MSW) |
| `pnpm test:e2e` | Playwright (móvil 360, tablet 768, escritorio 1440) contra `vite preview` |
| `pnpm gen:api` | Regenera `src/api/schema.d.ts` desde `openapi.json` |

### Desarrollo local completo

```bash
# terminal 1: backend (sin Google; usuario dev@localhost con rol admin)
AUTH_MODE=dev DEVICE_TOKEN=... SESSION_SECRET=... ALLOWED_DEVICES=raspberry-pi-01 \
  uvicorn epp_api.main:app --reload --port 8080      # desde backend/
# terminal 2
pnpm dev
```

### Regenerar los tipos de la API

```bash
python backend/scripts/dump_openapi.py frontend/openapi.json
pnpm gen:api            # actualiza src/api/schema.d.ts
```

`openapi.json` y `schema.d.ts` se versionan. La CI verifica que no queden cambios sin regenerar: un
cambio de contrato rompe el build en vez de fallar en silencio en producción.

## Estructura

```
src/
  api/        schema.d.ts (generado) · types.ts · client.ts (fetch, CSRF, 401/403) · queries.ts · live.ts (WS de video)
  auth/       AuthProvider · GoogleButton (GIS) · RequireRole
  design/     tokens.css · classColors.ts · format.ts (es-PE, Lima) · theme.ts
  hooks/      useIdle · usePageVisible · ActivityProvider · useShortcuts
  components/ sistema de componentes (ver DESIGN_SYSTEM.md §4)
  features/   una carpeta por pantalla (overview, live, events, device, admin, login, privacy)
  routes.tsx  rutas lazy por pantalla · App.tsx · main.tsx
public/       theme-init.js (tema antes del primer render; archivo por el CSP)
scripts/      check-budget.mjs
```

## Convenciones

- **Consultas:** solo vía los hooks de `api/queries.ts`; nunca `fetch` en componentes. Los intervalos
  usan `useRefetchInterval` para apagarse con inactividad o pestaña oculta.
- **Componentes:** gráficos solo con `ActivityChart` / `ClassBreakdown` (imponen las reglas de
  dataviz y traen tabla alternativa). No se configura Recharts en las páginas.
- **Colores:** solo tokens (`bg-surface-1`, `text-ink-2`, `var(--accent)`…) y `classColor()` para las
  clases. Nada de hex en componentes.
- **Textos:** español neutro (es-PE), sin jerga técnica. Formatos desde `design/format.ts`.
- **Almacenamiento:** `localStorage` solo para preferencias, siempre con `try/catch` (`readPref` / `writePref`).
- **Seguridad:** sin tokens en query strings ni en el navegador; la sesión es una cookie `HttpOnly`.
  Toda mutación envía `X-Requested-With: epp-web` (lo hace `api/client.ts`).

## Cómo agregar una pantalla

1. Crea `src/features/<nombre>/index.tsx` con `export default function …Page()`.
2. Regístrala en `src/routes.tsx` con `lazy(() => import("./features/<nombre>"))` dentro de
   `RequireRole` con el rol mínimo.
3. Agrega el destino a `components/nav.ts` si va en la navegación.
4. Cubre los 4 estados: carga (`Skeleton`), vacío (`EmptyState`), error (`ErrorState`) y sin permiso.
5. Añade una prueba de componente y un caso e2e con axe en ambos temas.
