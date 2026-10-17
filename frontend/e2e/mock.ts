import type { Page, Route } from "@playwright/test";

export type Role = "viewer" | "operator" | "admin";

const DEVICE = "raspberry-pi-01";
const ids = [0, 1, 2, 3].map((i) => `0192f6c4-8a1e-7c3b-9f2d-4b8e1a2c3d4${i}`);
export const EVENT_ID = ids[0] as string;

const iso = (minAgo: number) => new Date(Date.now() - minAgo * 60_000).toISOString();

const device = (over: Record<string, unknown> = {}) => ({
  id: DEVICE,
  name: DEVICE,
  state: "detecting",
  desired_state: "running",
  streaming: false,
  camera_ok: true,
  last_seen: iso(2),
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

const clases = ["latex_gloves", "hairnet", "face_mask", "lab_coat"];
const eventItem = (i: number) => ({
  event_id: ids[i % ids.length],
  nombre_clase: clases[i % clases.length],
  clase_id: i % clases.length,
  confianza_promedio: 0.8,
  confianza_max: 0.93 - i * 0.05,
  frame_count: 30,
  start_ts: iso(10 + i * 20),
  end_ts: iso(9 + i * 20),
  duration_seconds: 9 + i,
  is_simulated: false,
  capture_status: "ok",
  thumb_url: `/api/events/${ids[i % ids.length]}/capture?kind=thumb`,
});

const summary = (range: string) => ({
  range,
  generated_at: new Date().toISOString(),
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
  series: [0, 1, 2, 3, 4, 5, 6].map((d) => ({
    bucket: new Date(Date.now() - (6 - d) * 86_400_000).toISOString(),
    total: 10 + d * 3,
    by_class: { latex_gloves: 4 + d, hairnet: 3, face_mask: 2 + d, lab_coat: 1 },
  })),
  by_class: [
    { nombre_clase: "latex_gloves", count: 600 },
    { nombre_clase: "hairnet", count: 400 },
    { nombre_clase: "face_mask", count: 200 },
    { nombre_clase: "lab_coat", count: 84 },
  ],
});

const detail = (id: string) => ({
  ...eventItem(Math.max(0, ids.indexOf(id))),
  event_id: id,
  device_id: DEVICE,
  schema_version: 2,
  event_type: "deteccion_confirmada",
  closed_reason: "gap",
  capture: {
    status: "ok",
    width: 1280,
    height: 720,
    bytes: 184233,
    captured_at: iso(9.5),
    confidence_at_capture: 0.93,
    frame_seq: 81234,
  },
  detections_at_capture: [
    { clase_id: 0, nombre_clase: "latex_gloves", confianza: 0.93, bbox_xyxy_norm: [0.41, 0.52, 0.55, 0.71] },
    { clase_id: 1, nombre_clase: "hairnet", confianza: 0.77, bbox_xyxy_norm: [0.38, 0.05, 0.52, 0.18] },
  ],
  edge: { edge_version: "2.0.0", model_version: "a1b2c3d4e5f6", camera_source: "picamera2" },
  thumb_url: `/api/events/${id}/capture?kind=thumb`,
  original_url: `/api/events/${id}/capture?kind=original`,
  lake_uri: "gs://epp-detection-v1-lake/raw/images/x.jpg",
});

const CAPTURE_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#9aa4b2"/><rect x="40" y="40" width="1200" height="640" fill="#c3cad4"/></svg>';

const GIS_STUB = `
window.google = { accounts: { id: {
  initialize: function (c) { window.__gisCb = c.callback; },
  renderButton: function (el) {
    var b = document.createElement('button');
    b.type = 'button'; b.textContent = 'Continuar con Google';
    b.setAttribute('data-testid', 'gis-button');
    b.onclick = function () {
      var p = btoa(JSON.stringify({ email: 'externo@example.com' }));
      window.__gisCb({ credential: 'h.' + p + '.s' });
    };
    el.appendChild(b);
  }
} } };`;

export interface MockOptions {
  /** null = sin sesión (401). */
  role?: Role | null;
  deviceOver?: Record<string, unknown>;
  /** Respuesta de POST /api/auth/google. */
  loginStatus?: number;
  eventsPageSize?: number;
}

export interface Mock {
  /** Requests a /api/ registradas (método + ruta con query). */
  calls: string[];
  /** Cuántos WebSockets /live hay abiertos ahora mismo. */
  openSockets: () => number;
  /** Query string de cada GET /events recibido (para probar filtros). */
  eventQueries: string[];
}

const json = (route: Route, body: unknown, status = 200) =>
  route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });

/** Intercepta toda la API, el script de Google y el WebSocket de video. */
export async function mockApi(page: Page, opts: MockOptions = {}): Promise<Mock> {
  let role: Role | null = opts.role === undefined ? "viewer" : opts.role;
  const calls: string[] = [];
  const eventQueries: string[] = [];
  let sockets = 0;

  await page.route("https://accounts.google.com/gsi/client", (r) =>
    r.fulfill({ status: 200, contentType: "text/javascript", body: GIS_STUB }),
  );

  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    calls.push(`${method} ${path}${url.search}`);

    if (path === "/api/auth/config")
      return json(route, { client_id: "test-client.apps.googleusercontent.com" });
    if (path === "/api/auth/google") {
      if ((opts.loginStatus ?? 200) !== 200)
        return json(
          route,
          { error: { code: "sin_acceso", message: "Cuenta no autorizada." } },
          opts.loginStatus,
        );
      role = role ?? "viewer"; // el login concede la sesión
      return json(route, { email: "ana@viz-ia.com", name: "Ana", picture: null, role });
    }
    if (path === "/api/auth/me") {
      if (!role)
        return json(route, { error: { code: "no_autenticado", message: "Inicia sesión." } }, 401);
      return json(route, { email: "ana@viz-ia.com", name: "Ana", picture: null, role });
    }
    if (!role)
      return json(route, { error: { code: "no_autenticado", message: "Inicia sesión." } }, 401);

    if (path === "/api/devices") return json(route, [device(opts.deviceOver)]);
    if (/^\/api\/devices\/[^/]+\/summary$/.test(path))
      return json(route, summary(url.searchParams.get("range") ?? "7d"));
    if (/^\/api\/devices\/[^/]+\/events$/.test(path)) {
      eventQueries.push(url.search);
      const n = opts.eventsPageSize ?? 4;
      return json(route, {
        items: Array.from({ length: n }, (_, i) => eventItem(i)),
        next_cursor: null,
      });
    }
    if (/^\/api\/devices\/[^/]+\/desired-state$/.test(path))
      return json(route, device({ desired_state: "stopped", state: "paused" }));
    if (/^\/api\/events\/[^/]+\/capture$/.test(path))
      return route.fulfill({ status: 200, contentType: "image/svg+xml", body: CAPTURE_SVG });
    const ev = path.match(/^\/api\/events\/([^/]+)$/);
    if (ev) return json(route, detail(ev[1] as string));
    if (path === "/api/admin/users")
      return json(route, [
        {
          email: "admin@viz-ia.com",
          role: "admin",
          active: true,
          created_at: iso(5000),
          created_by: "bootstrap",
        },
        {
          email: "ana@viz-ia.com",
          role: "viewer",
          active: true,
          created_at: iso(900),
          created_by: "admin@viz-ia.com",
        },
      ]);
    if (path === "/api/admin/audit")
      return json(route, {
        items: [
          { id: "1", action: "live_start", email: "ana@viz-ia.com", target: DEVICE, detail: null, ts: iso(30) },
          { id: "2", action: "login_denied", email: "externo@example.com", target: null, detail: null, ts: iso(90) },
        ],
        next_cursor: null,
      });
    return json(route, { error: { code: "no_encontrado", message: "No existe." } }, 404);
  });

  await page.routeWebSocket(/\/api\/devices\/[^/]+\/live/, (ws) => {
    sockets += 1;
    ws.send(JSON.stringify({ type: "status", state: "waiting" }));
    ws.onClose(() => {
      sockets -= 1;
    });
  });

  return { calls, openSockets: () => sockets, eventQueries };
}

export const SCREENS = [
  { name: "login", path: "/login", auth: false },
  { name: "resumen", path: "/", auth: true },
  { name: "en-vivo", path: "/live", auth: true },
  { name: "eventos", path: "/events", auth: true },
  { name: "detalle-evento", path: `/events/${EVENT_ID}`, auth: true },
  { name: "dispositivo", path: "/device", auth: true },
  { name: "administracion", path: "/admin", auth: true },
] as const;

export async function setTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((t) => {
    try {
      localStorage.setItem("epp-theme", t);
    } catch {
      /* sin storage */
    }
  }, theme);
}
