import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  baseHandlers,
  DEVICE,
  makeEvent,
  renderRoute,
  server,
  setupMswServer,
} from "@/test/harness";
import LivePage from "./index";

setupMswServer();

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  binaryType = "blob";
  onopen: (() => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  close = vi.fn();
  constructor(public url: string) {
    FakeWebSocket.instances.push(this);
  }
}

const routes = [{ path: "/live", element: <LivePage /> }];

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});
afterEach(() => {
  vi.unstubAllGlobals();
});

function handlers(role: "viewer" | "operator" | "admin") {
  return [
    ...baseHandlers(role),
    http.get("*/api/devices/:id/events", () =>
      HttpResponse.json({ items: [makeEvent(1)], next_cursor: null }),
    ),
  ];
}

describe("En vivo", () => {
  it("abre el WebSocket del dispositivo y lo cierra al salir de la pantalla", async () => {
    server.use(...handlers("viewer"));
    const { unmount } = renderRoute(routes, "/live");
    await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
    const ws = FakeWebSocket.instances[0]!;
    expect(ws.url).toContain(`/api/devices/${DEVICE.id}/live`);
    expect(ws.url).not.toContain("token");
    expect(ws.close).not.toHaveBeenCalled();
    unmount();
    expect(ws.close).toHaveBeenCalled(); // esto es lo que apaga el streaming de la Pi
  });

  it("un viewer no ve el botón de pausar", async () => {
    server.use(...handlers("viewer"));
    renderRoute(routes, "/live");
    await screen.findByRole("heading", { name: "En vivo" });
    await screen.findByText("Detecciones recientes");
    expect(screen.queryByRole("button", { name: /Pausar detección/ })).not.toBeInTheDocument();
  });

  it("un operador pausa con confirmación y se muestra el aviso", async () => {
    let body: unknown;
    server.use(
      ...handlers("operator"),
      http.put("*/api/devices/:id/desired-state", async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...DEVICE, desired_state: "stopped", state: "paused" });
      }),
    );
    renderRoute(routes, "/live");
    await userEvent.click(await screen.findByRole("button", { name: /Pausar detección/ }));
    expect(await screen.findByText(/dejará de registrar eventos/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Pausar" }));
    await waitFor(() => expect(body).toEqual({ state: "stopped" }));
    expect(await screen.findByText(/Detección pausada por ana@example.com/)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Reanudar detección/ })).toBeInTheDocument();
  });

  it("muestra la salud del dispositivo y las detecciones recientes", async () => {
    server.use(...handlers("viewer"));
    renderRoute(routes, "/live");
    expect(await screen.findByText(/4\.8 fps/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Cofia|hairnet/ })).toBeInTheDocument();
  });

  it("con la pestaña oculta más de 30 s cierra el video", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      server.use(...handlers("viewer"));
      renderRoute(routes, "/live");
      await waitFor(() => expect(FakeWebSocket.instances).toHaveLength(1));
      const ws = FakeWebSocket.instances[0]!;
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
      act(() => {
        document.dispatchEvent(new Event("visibilitychange"));
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(31_000);
      });
      expect(ws.close).toHaveBeenCalled();
    } finally {
      Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
      vi.useRealTimers();
    }
  });

  it("si falla la consulta del dispositivo ofrece reintentar", async () => {
    server.use(
      http.get("*/api/auth/me", () =>
        HttpResponse.json({ email: "a@b.c", name: "A", picture: null, role: "viewer" }),
      ),
      http.get("*/api/devices", () =>
        HttpResponse.json({ error: { code: "x", message: "f" } }, { status: 500 }),
      ),
    );
    renderRoute(routes, "/live");
    expect(
      await screen.findByRole("button", { name: "Reintentar" }, { timeout: 8000 }),
    ).toBeInTheDocument();
  }, 15000);
});
