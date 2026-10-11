import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { http, HttpResponse, type JsonBodyType } from "msw";
import { setupServer } from "msw/node";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterAll, afterEach, beforeAll } from "vitest";
import type { DeviceStatus, EventPage, Me, Role, Summary, UserOut } from "@/api/types";
import { AuthProvider } from "@/auth/AuthProvider";
import { DeviceProvider } from "@/components/DeviceContext";
import { ToastProvider } from "@/components/Toast";
import { ActivityProvider } from "@/hooks/ActivityProvider";

// En jsdom el fetch de Node no resuelve rutas relativas: se completan contra localhost.
const realFetch = globalThis.fetch;
globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
  typeof input === "string" && input.startsWith("/")
    ? realFetch(new URL(input, "http://localhost").href, init)
    : realFetch(input, init)) as typeof fetch;

export const server = setupServer();

/** Registra el ciclo de vida del servidor MSW en el archivo de test que lo importa. */
export function installMockServer(): void {
  beforeAll(() => server.listen({ onUnhandledFrame: "error" }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
}

export const me = (role: Role = "viewer", email = "ana@viz-ia.com"): Me => ({
  email,
  name: "Ana",
  picture: null,
  role,
});

export const device = (over: Partial<DeviceStatus> = {}): DeviceStatus => ({
  id: "raspberry-pi-01",
  name: "raspberry-pi-01",
  state: "detecting",
  desired_state: "running",
  streaming: false,
  camera_ok: true,
  last_seen: new Date(Date.now() - 2 * 60_000).toISOString(),
  health: {
    inference_fps: 4.8,
    inference_ms_p50: 180,
    inference_ms_p95: 240,
    cpu_temp_c: 52,
    outbox_pending: 0,
    edge_version: "2.0.0",
    model_version: "a1b2c3d4e5f6",
    camera_source: "picamera2",
  },
  ...over,
});

export const summary = (over: Partial<Summary> = {}): Summary => ({
  range: "7d",
  generated_at: "2026-10-10T15:00:00Z",
  timezone: "America/Lima",
  partial: false,
  sample: { events: 500, complete: true },
  kpis: {
    events: { value: 1284, previous: 1146 },
    epp_seconds: { value: 22320, previous: 21250 },
    avg_confidence: { value: 0.84, previous: 0.86 },
    capture_coverage: { value: 0.998, previous: 1 },
    top_class: { count: 600, nombre_clase: "latex_gloves", previous_nombre_clase: "hairnet" },
  },
  series: [
    { bucket: "2026-10-09T05:00:00Z", total: 5, by_class: { latex_gloves: 3, hairnet: 2 } },
    { bucket: "2026-10-10T05:00:00Z", total: 7, by_class: { face_mask: 7 } },
  ],
  by_class: [
    { nombre_clase: "latex_gloves", count: 600 },
    { nombre_clase: "hairnet", count: 400 },
  ],
  ...over,
});

export const eventsPage = (n = 2): EventPage => ({
  items: Array.from({ length: n }, (_, i) => ({
    event_id: `0192f6c4-8a1e-7c3b-9f2d-4b8e1a2c3d4${i}`,
    nombre_clase: "latex_gloves",
    clase_id: 3,
    confianza_promedio: 0.8,
    confianza_max: 0.93,
    frame_count: 30,
    start_ts: "2026-10-10T14:03:11Z",
    end_ts: "2026-10-10T14:03:20Z",
    duration_seconds: 9,
    is_simulated: false,
    capture_status: "ok" as const,
    thumb_url: `/api/events/e${i}/capture?kind=thumb`,
  })),
  next_cursor: null,
});

export const user = (over: Partial<UserOut> = {}): UserOut => ({
  email: "ana@viz-ia.com",
  role: "viewer",
  active: true,
  created_at: "2026-10-01T10:00:00Z",
  created_by: "admin@viz-ia.com",
  ...over,
});

export const json = (body: JsonBodyType, status = 200) => HttpResponse.json(body, { status });
export const apiError = (status: number, code: string, message: string) =>
  HttpResponse.json({ error: { code, message } }, { status });

/** Handlers base de una sesión autenticada con un dispositivo. */
export function baseHandlers(role: Role = "viewer", dev: DeviceStatus = device()) {
  return [
    http.get("*/api/auth/me", () => json(me(role))),
    http.get("*/api/devices", () => json([dev])),
  ];
}

interface Options {
  route?: string;
  path?: string;
}

/** Monta la pantalla con sesión, dispositivo, consultas (sin reintentos) y enrutador. */
export function renderScreen(ui: ReactElement, { route = "/", path = "/" }: Options = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[route]}>
        <ActivityProvider>
          <AuthProvider>
            <ToastProvider>
              <DeviceProvider>
                <Routes>
                  <Route path={path} element={ui} />
                  <Route path="*" element={<p>otra ruta</p>} />
                </Routes>
              </DeviceProvider>
            </ToastProvider>
          </AuthProvider>
        </ActivityProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

export { http };
