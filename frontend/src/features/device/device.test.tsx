import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  apiError,
  baseHandlers,
  device,
  http,
  json,
  me,
  renderScreen,
  server,
  installMockServer,
  trainingSession,
  trainingSessionsPage,
} from "@/test/c2a-utils";
import { diagnostics, outboxTone, tempTone } from "./health";
import DevicePage from ".";

installMockServer();

describe("DevicePage", () => {
  it("muestra el estado, las tarjetas de salud y las versiones", async () => {
    server.use(...baseHandlers("viewer"));
    renderScreen(<DevicePage />);
    expect(await screen.findByText("Detectando")).toBeInTheDocument();
    expect(screen.getByText("4.8 fps")).toBeInTheDocument();
    expect(screen.getByText("52.0 °C")).toBeInTheDocument();
    expect(screen.getByText("Normal")).toBeInTheDocument(); // temperatura < 70
    expect(screen.getByText("Al día")).toBeInTheDocument(); // outbox 0
    expect(screen.getByText("v2.0.0")).toBeInTheDocument();
    expect(screen.getByText(/Modelo a1b2c3d4e5f6/)).toBeInTheDocument();
    expect(screen.queryByText("Qué revisar")).toBeNull();
  });

  it("viewer no ve el botón de pausar", async () => {
    server.use(...baseHandlers("viewer"));
    renderScreen(<DevicePage />);
    expect(await screen.findByText(/pausar o reanudar la detección/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pausar detección" })).toBeNull();
  });

  it("operador pausa con confirmación y se avisa con un toast", async () => {
    let body: unknown = null;
    server.use(
      ...baseHandlers("operator"),
      http.put("*/api/devices/:id/desired-state", async ({ request }) => {
        body = await request.json();
        return json(device({ state: "paused", desired_state: "stopped" }));
      }),
    );
    renderScreen(<DevicePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Pausar detección" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/La cámara dejará de registrar eventos/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Pausar" }));
    await waitFor(() => expect(body).toEqual({ state: "stopped" }));
    expect(await screen.findByText("Detección pausada por ana@viz-ia.com")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Reanudar detección" })).toBeInTheDocument();
  });

  it("cancelar el diálogo no llama a la API", async () => {
    let called = false;
    server.use(
      ...baseHandlers("admin"),
      http.put("*/api/devices/:id/desired-state", () => {
        called = true;
        return json(device());
      }),
    );
    renderScreen(<DevicePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Pausar detección" }));
    await userEvent.click(await screen.findByRole("button", { name: "Cancelar" }));
    expect(called).toBe(false);
  });

  it("error al pausar muestra el mensaje del servidor", async () => {
    server.use(
      ...baseHandlers("operator"),
      http.put("*/api/devices/:id/desired-state", () =>
        apiError(500, "error", "No se pudo guardar el cambio."),
      ),
    );
    renderScreen(<DevicePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Pausar detección" }));
    await userEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: "Pausar" }),
    );
    expect(await screen.findByText("No se pudo guardar el cambio.")).toBeInTheDocument();
  });

  it("sin conexión: diagnóstico accionable y estado crítico", async () => {
    server.use(
      ...baseHandlers(
        "viewer",
        device({
          state: "offline",
          last_seen: new Date(Date.now() - 25 * 60_000).toISOString(),
        }),
      ),
    );
    renderScreen(<DevicePage />);
    expect(await screen.findByText("Sin conexión")).toBeInTheDocument();
    expect(
      screen.getByText(/Sin señal desde hace 25 minutos\. Revisa la alimentación/),
    ).toBeInTheDocument();
  });

  it("temperatura alta y cámara caída aparecen con texto, no solo color", async () => {
    server.use(
      ...baseHandlers(
        "viewer",
        device({
          state: "camera_error",
          camera_ok: false,
          health: { ...device().health, cpu_temp_c: 85, outbox_pending: 80 },
        }),
      ),
    );
    renderScreen(<DevicePage />);
    expect(await screen.findByText("Crítica")).toBeInTheDocument();
    expect(screen.getByText("Acumulando")).toBeInTheDocument();
    expect(screen.getByText("No disponible")).toBeInTheDocument();
    expect(screen.getByText(/La cámara no responde/)).toBeInTheDocument();
    expect(screen.getByText(/La CPU supera los 80 °C/)).toBeInTheDocument();
  });

  it("sin dispositivos: estado vacío con explicación", async () => {
    server.use(
      http.get("*/api/auth/me", () => json(me("viewer"))),
      http.get("*/api/devices", () => json([])),
    );
    renderScreen(<DevicePage />);
    expect(await screen.findByText("Aún no hay dispositivos")).toBeInTheDocument();
  });

  it("error al cargar: mensaje y Reintentar", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/auth/me", () => json(me("viewer"))),
      http.get("*/api/devices", () => {
        calls += 1;
        return calls === 1 ? apiError(500, "error", "x") : json([device()]);
      }),
    );
    renderScreen(<DevicePage />);
    expect(await screen.findByText("No se pudo cargar el dispositivo")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("Detectando")).toBeInTheDocument();
  });
});

describe("Captura para reentrenamiento", () => {
  it("viewer no ve el botón de iniciar ni el link de descarga", async () => {
    server.use(
      http.get("*/api/devices/:id/training-captures", () =>
        json(trainingSessionsPage([trainingSession()])),
      ),
      ...baseHandlers("viewer"),
    );
    renderScreen(<DevicePage />);
    expect(
      await screen.findByText(/iniciar o detener la captura/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Descargar/ })).toBeNull();
  });

  it("operador inicia una captura con confirmación y se avisa con un toast", async () => {
    let called = false;
    server.use(
      ...baseHandlers("operator"),
      http.post("*/api/devices/:id/training-captures", () => {
        called = true;
        return json({ session_id: "raspberry-pi-01-123", started_at: "2026-10-16T14:00:00Z" });
      }),
    );
    renderScreen(<DevicePage />);
    await userEvent.click(
      await screen.findByRole("button", { name: /Capturar fotos para reentrenamiento/ }),
    );
    const dialog = await screen.findByRole("dialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Iniciar" }));
    await waitFor(() => expect(called).toBe(true));
    expect(
      await screen.findByText("Captura iniciada: la cámara empieza a subir fotos para reentrenamiento"),
    ).toBeInTheDocument();
  });

  it("con una sesión activa muestra «Detener captura» en vez de «Iniciar»", async () => {
    server.use(
      http.get("*/api/devices/:id/training-captures", () =>
        json(trainingSessionsPage([trainingSession({ status: "capturing", ended_at: null })])),
      ),
      ...baseHandlers("operator"),
    );
    renderScreen(<DevicePage />);
    expect(await screen.findByRole("button", { name: /Detener captura/ })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Capturar fotos para reentrenamiento/ }),
    ).toBeNull();
  });

  it("admin ve el link de descarga de una sesión completa", async () => {
    server.use(
      http.get("*/api/devices/:id/training-captures", () =>
        json(trainingSessionsPage([trainingSession()])),
      ),
      ...baseHandlers("admin"),
    );
    renderScreen(<DevicePage />);
    const link = await screen.findByRole("link", { name: /Descargar/ });
    expect(link).toHaveAttribute(
      "href",
      "/api/devices/raspberry-pi-01/training-captures/raspberry-pi-01-1760000000000/download",
    );
  });

  it("operador no ve el link de descarga de esa misma sesión", async () => {
    server.use(
      http.get("*/api/devices/:id/training-captures", () =>
        json(trainingSessionsPage([trainingSession()])),
      ),
      ...baseHandlers("operator"),
    );
    renderScreen(<DevicePage />);
    expect(await screen.findByText(/600 fotos/)).toBeInTheDocument(); // la fila cargó
    expect(screen.queryByRole("link", { name: /Descargar/ })).toBeNull();
  });
});

describe("umbrales de salud", () => {
  it("temperatura: < 70 bien, 70-80 atención, > 80 crítico", () => {
    expect(tempTone(69.9)).toBe("good");
    expect(tempTone(70)).toBe("warning");
    expect(tempTone(80)).toBe("warning");
    expect(tempTone(80.1)).toBe("critical");
    expect(tempTone(null)).toBe("neutral");
  });

  it("outbox: 0 bien, hasta 50 atención, más serio", () => {
    expect(outboxTone(0)).toBe("good");
    expect(outboxTone(3)).toBe("warning");
    expect(outboxTone(51)).toBe("serious");
  });

  it("una cámara sana no genera diagnósticos", () => {
    expect(diagnostics(device())).toEqual([]);
  });
});
