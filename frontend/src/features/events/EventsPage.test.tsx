import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import {
  baseHandlers,
  makeDetail,
  makeEvent,
  renderRoute,
  server,
  setupMswServer,
} from "@/test/harness";
import EventsPage from "./index";

setupMswServer();

const routes = [
  { path: "/events", element: <EventsPage /> },
  { path: "/events/:eventId", element: <EventsPage /> },
];

describe("Eventos", () => {
  it("los filtros de la URL llegan al servidor", async () => {
    const seen: URL[] = [];
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", ({ request }) => {
        seen.push(new URL(request.url));
        return HttpResponse.json({ items: [makeEvent(1)], next_cursor: null });
      }),
    );
    renderRoute(routes, "/events?clase=hairnet&conf=60&sim=1&rango=24h");
    await screen.findByRole("link", { name: /Abrir evento/ });
    const q = seen[0]!.searchParams;
    expect(q.get("clase")).toBe("hairnet");
    expect(q.get("min_conf")).toBe("0.6");
    expect(q.get("include_simulated")).toBe("true");
    expect(q.get("from")).toBeTruthy();
  });

  it("cambiar un filtro actualiza la URL", async () => {
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", () =>
        HttpResponse.json({ items: [makeEvent(1)], next_cursor: null }),
      ),
    );
    const { router } = renderRoute(routes, "/events");
    await screen.findByRole("link", { name: /Abrir evento/ });
    await userEvent.click(screen.getByRole("radio", { name: "30 días" }));
    await userEvent.click(screen.getByRole("button", { name: /Tabla/ }));
    await waitFor(() => {
      const sp = new URLSearchParams(router.state.location.search);
      expect(sp.get("rango")).toBe("30d");
      expect(sp.get("vista")).toBe("tabla");
    });
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("«Cargar más» pide la página siguiente con el cursor y agrega las tarjetas", async () => {
    const cursors: (string | null)[] = [];
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", ({ request }) => {
        const cursor = new URL(request.url).searchParams.get("cursor");
        cursors.push(cursor);
        return cursor
          ? HttpResponse.json({ items: [makeEvent(3), makeEvent(4)], next_cursor: null })
          : HttpResponse.json({ items: [makeEvent(1), makeEvent(2)], next_cursor: "CUR1" });
      }),
    );
    renderRoute(routes, "/events");
    expect(await screen.findAllByRole("link", { name: /Abrir evento/ })).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await waitFor(() =>
      expect(screen.getAllByRole("link", { name: /Abrir evento/ })).toHaveLength(4),
    );
    expect(cursors).toEqual([null, "CUR1"]);
    expect(screen.queryByRole("button", { name: "Cargar más" })).not.toBeInTheDocument();
  });

  it("sin eventos muestra el estado vacío", async () => {
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
    );
    renderRoute(routes, "/events");
    expect(await screen.findByText("Aún no hay eventos")).toBeInTheDocument();
  });

  it("un error del servidor ofrece reintentar", async () => {
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", () =>
        HttpResponse.json({ error: { code: "x", message: "falla" } }, { status: 500 }),
      ),
    );
    renderRoute(routes, "/events");
    expect(
      await screen.findByRole("button", { name: "Reintentar" }, { timeout: 8000 }),
    ).toBeInTheDocument();
  }, 15000);

  it("una captura vencida muestra el aviso de retención de 60 días", async () => {
    const ev = makeEvent(1, { capture_status: "expired", thumb_url: null });
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", () =>
        HttpResponse.json({ items: [ev], next_cursor: null }),
      ),
      http.get("*/api/events/:id", () => HttpResponse.json(makeDetail(ev))),
    );
    renderRoute(routes, `/events/${ev.event_id}`);
    const panel = await screen.findByRole("dialog");
    expect(
      await within(panel).findByText(
        /Captura eliminada por política de retención \(60 días\)\. Los datos/,
      ),
    ).toBeInTheDocument();
    expect(
      within(panel).queryByRole("link", { name: /Descargar original/ }),
    ).not.toBeInTheDocument();
  });

  it("el detalle navega al evento siguiente de la lista", async () => {
    const a = makeEvent(1);
    const b = makeEvent(2);
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", () =>
        HttpResponse.json({ items: [a, b], next_cursor: null }),
      ),
      http.get("*/api/events/:id", ({ params }) =>
        HttpResponse.json(makeDetail(params["id"] === a.event_id ? a : b)),
      ),
    );
    const { router } = renderRoute(routes, `/events/${a.event_id}`);
    const panel = await screen.findByRole("dialog");
    await within(panel).findByText(a.event_id);
    await waitFor(() =>
      expect(within(panel).getByRole("button", { name: /siguiente/ })).toBeEnabled(),
    );
    await userEvent.click(within(panel).getByRole("button", { name: /siguiente/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe(`/events/${b.event_id}`));
  });

  it("la captura se amplía a pantalla completa con sus etiquetas y las flechas no cambian de evento", async () => {
    const a = makeEvent(1, { nombre_clase: "episodio_no_conforme" });
    const b = makeEvent(2);
    const detail = makeDetail(a, {
      detections_at_capture: [
        {
          clase_id: 0,
          nombre_clase: "Cofia",
          confianza: 0.91,
          bbox_xyxy_norm: [0.1, 0.2, 0.3, 0.4],
        },
      ],
    });
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", () =>
        HttpResponse.json({ items: [a, b], next_cursor: null }),
      ),
      http.get("*/api/events/:id", () => HttpResponse.json(detail)),
    );
    const { router } = renderRoute(routes, `/events/${a.event_id}`);
    const panel = await screen.findByRole("dialog");
    await userEvent.click(await within(panel).findByRole("button", { name: "Ampliar imagen" }));
    const viewer = await screen.findByRole("dialog", { name: "Captura del evento" });
    expect(within(viewer).getByText(/Cofia 91/)).toBeInTheDocument();
    await userEvent.click(within(viewer).getByRole("button", { name: /Acercar/ }));
    expect(within(viewer).getByText("125 %")).toBeInTheDocument();
    await userEvent.keyboard("{ArrowRight}");
    expect(router.state.location.pathname).toBe(`/events/${a.event_id}`);
    await userEvent.click(within(viewer).getByRole("button", { name: "Cerrar visor" }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Captura del evento" })).not.toBeInTheDocument(),
    );
  });

  it("evento inexistente: mensaje claro y no un error genérico", async () => {
    server.use(
      ...baseHandlers(),
      http.get("*/api/devices/:id/events", () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
      http.get("*/api/events/:id", () =>
        HttpResponse.json(
          { error: { code: "no_encontrado", message: "No existe" } },
          { status: 404 },
        ),
      ),
    );
    renderRoute(routes, "/events/no-existe");
    expect(await screen.findByText("Evento no encontrado")).toBeInTheDocument();
  });
});
