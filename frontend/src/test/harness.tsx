import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { afterAll, afterEach, beforeAll } from "vitest";
import { createQueryClient } from "@/api/queries";
import type { DeviceStatus, EventDetail, EventSummary, Me, Role } from "@/api/types";
import { AuthProvider } from "@/auth/AuthProvider";
import { DeviceProvider } from "@/components/DeviceContext";
import { ToastProvider } from "@/components/Toast";
import { ActivityProvider } from "@/hooks/ActivityProvider";

export const server = setupServer();

/** Node no resuelve URLs relativas en fetch: se les antepone un origen de pruebas. */
function patchRelativeFetch(): void {
  const original = globalThis.fetch;
  globalThis.fetch = ((input: RequestInfo | URL, init?: RequestInit) =>
    original(
      typeof input === "string" && input.startsWith("/") ? `http://localhost${input}` : input,
      init,
    )) as typeof fetch;
}

class NoopResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/** Llamar una vez por archivo de prueba. */
export function setupMswServer(): void {
  beforeAll(() => {
    patchRelativeFetch();
    (globalThis as { ResizeObserver?: unknown }).ResizeObserver = NoopResizeObserver;
    server.listen({ onUnhandledFrame: "error" });
  });
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
}

export const DEVICE: DeviceStatus = {
  id: "raspberry-pi-01",
  name: "raspberry-pi-01",
  desired_state: "running",
  state: "detecting",
  streaming: false,
  camera_ok: true,
  last_seen: new Date().toISOString(),
  health: {
    inference_fps: 4.8,
    inference_ms_p50: 180,
    cpu_temp_c: 52,
    outbox_pending: 0,
    model_version: "a1b2c3d4e5f6",
    edge_version: "2.0.0",
  },
};

export function makeEvent(i: number, over: Partial<EventSummary> = {}): EventSummary {
  const end = new Date(Date.UTC(2026, 9, 10, 14, 0, 0) - i * 60_000).toISOString();
  return {
    event_id: `0192f6c4-0000-7000-8000-${String(i).padStart(12, "0")}`,
    nombre_clase: "hairnet",
    clase_id: 0,
    confianza_promedio: 0.8,
    confianza_max: 0.9,
    duration_seconds: 9,
    frame_count: 30,
    start_ts: end,
    end_ts: end,
    is_simulated: false,
    capture_status: "ok",
    thumb_url: `/api/events/e${i}/capture?kind=thumb`,
    ...over,
  };
}

export function makeDetail(ev: EventSummary, over: Partial<EventDetail> = {}): EventDetail {
  return {
    ...ev,
    capture: { status: ev.capture_status, captured_at: ev.end_ts },
    detections_at_capture: [
      {
        clase_id: 0,
        nombre_clase: ev.nombre_clase,
        confianza: 0.9,
        bbox_xyxy_norm: [0.1, 0.1, 0.4, 0.5],
      },
    ],
    device_id: DEVICE.id,
    edge: { edge_version: "2.0.0", model_version: "a1b2c3d4e5f6" },
    closed_reason: "gap",
    original_url:
      ev.capture_status === "ok" ? `/api/events/${ev.event_id}/capture?kind=original` : null,
    ...over,
  } as EventDetail;
}

export function baseHandlers(role: Role = "viewer") {
  const me: Me = { email: "ana@example.com", name: "Ana", picture: null, role } as Me;
  return [
    http.get("*/api/auth/me", () => HttpResponse.json(me)),
    http.get("*/api/devices", () => HttpResponse.json([DEVICE])),
  ];
}

/** Monta una pantalla dentro de los proveedores reales y un router en memoria. */
export function renderRoute(routes: { path: string; element: React.ReactNode }[], initial: string) {
  const router = createMemoryRouter(
    [
      {
        element: (
          <DeviceProvider>
            <Outlet />
          </DeviceProvider>
        ),
        children: routes,
      },
    ],
    { initialEntries: [initial] },
  );
  const client = createQueryClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <ToastProvider>
          <ActivityProvider>
            <RouterProvider router={router} />
          </ActivityProvider>
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>,
  );
  return { router, client, ...utils };
}
