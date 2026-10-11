import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { formatInt } from "@/design/format";
import { describe, expect, it } from "vitest";
import {
  apiError,
  baseHandlers,
  eventsPage,
  http,
  json,
  renderScreen,
  server,
  summary,
  installMockServer,
} from "@/test/c2a-utils";
import { pointsDelta, relativeDelta } from "./kpis";
import OverviewPage from ".";

installMockServer();

function setup(over: { summaryHandler?: Parameters<typeof http.get>[1]; events?: unknown } = {}) {
  server.use(
    ...baseHandlers("viewer"),
    http.get("*/api/devices/:id/summary", over.summaryHandler ?? (() => json(summary()))),
    http.get("*/api/devices/:id/events", () => json((over.events ?? eventsPage(6)) as never)),
  );
  return renderScreen(<OverviewPage />);
}

describe("OverviewPage", () => {
  it("muestra estado del dispositivo, KPIs con deltas, gráficos y últimos eventos", async () => {
    setup();
    expect(await screen.findByText("Detectando")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Ver en vivo →" })).toHaveAttribute("href", "/live");

    expect(await screen.findByText(formatInt(1284))).toBeInTheDocument();
    expect(screen.getByText("+12%")).toBeInTheDocument(); // (1284-1146)/1146
    expect(screen.getByText("6 h 12 min")).toBeInTheDocument();
    expect(screen.getByText("84%")).toBeInTheDocument();
    expect(screen.getByText("−2 pts")).toBeInTheDocument();
    expect(screen.getByText("99.8%")).toBeInTheDocument();
    expect(screen.getByText("hora de Lima")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Actividad" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Por clase de EPP" })).toBeInTheDocument();

    const list = await screen.findByRole("list", { name: "Últimos eventos" });
    const imgs = within(list).getAllByRole("img");
    expect(imgs).toHaveLength(6);
    expect(imgs[0]).toHaveAttribute(
      "alt",
      expect.stringMatching(/^Captura del evento: Guantes de látex, .+, confianza 93 %$/),
    );
    expect(imgs[0]).toHaveAttribute("loading", "lazy");
  });

  it("sin periodo previo muestra un guion, nunca +∞ %", async () => {
    setup({
      summaryHandler: () =>
        json(
          summary({
            kpis: {
              events: { value: 10, previous: 0 },
              epp_seconds: { value: 90, previous: null },
              avg_confidence: { value: 0.8, previous: null },
              capture_coverage: { value: 1, previous: null },
              top_class: { count: 10, nombre_clase: "hairnet" },
            },
          }),
        ),
    });
    await screen.findByText("10");
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(4);
    expect(screen.queryByText(/∞/)).toBeNull();
  });

  it("cambiar de periodo vuelve a pedir el resumen", async () => {
    const ranges: string[] = [];
    setup({
      summaryHandler: ({ request }) => {
        ranges.push(new URL(request.url).searchParams.get("range") ?? "");
        return json(summary());
      },
    });
    await screen.findByText(formatInt(1284));
    await userEvent.click(screen.getByRole("button", { name: "24 h" }));
    await waitFor(() => expect(ranges).toContain("24h"));
    expect(screen.getByRole("button", { name: "24 h" })).toHaveAttribute("aria-pressed", "true");
  });

  it("cobertura incompleta enlaza a los eventos sin captura", async () => {
    setup({
      summaryHandler: () =>
        json(
          summary({
            sample: { events: 500, complete: false },
            kpis: {
              ...summary().kpis,
              capture_coverage: { value: 0.99, previous: 1 },
            },
          }),
        ),
    });
    const link = await screen.findByRole("link", { name: /5 sin captura → ver/ });
    expect(link).toHaveAttribute("href", expect.stringContaining("captura=falta"));
    expect(screen.getByText(/se calculan sobre los últimos 500 eventos/)).toBeInTheDocument();
  });

  it("vacío: explica qué significa y qué hacer", async () => {
    setup({
      summaryHandler: () =>
        json(
          summary({
            kpis: {
              events: { value: 0, previous: 0 },
              epp_seconds: { value: 0, previous: 0 },
              avg_confidence: { value: null, previous: null },
              capture_coverage: { value: null, previous: null },
              top_class: { count: 0 },
            },
            series: [{ bucket: "2026-10-10T05:00:00Z", total: 0, by_class: {} }],
            by_class: [],
          }),
        ),
      events: { items: [], next_cursor: null },
    });
    expect(await screen.findByText("Sin eventos en este periodo")).toBeInTheDocument();
    expect(await screen.findByText("Todavía no hay eventos")).toBeInTheDocument();
  });

  it("error del resumen: mensaje claro y Reintentar", async () => {
    let calls = 0;
    setup({
      summaryHandler: () => {
        calls += 1;
        return calls === 1
          ? apiError(500, "error", "No se pudo calcular el resumen.")
          : json(summary());
      },
    });
    expect(await screen.findByText("No se pudo calcular el resumen.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText(formatInt(1284))).toBeInTheDocument();
  });

  it("sin permiso (403) no se presenta como error genérico", async () => {
    setup({ summaryHandler: () => apiError(403, "sin_permiso", "prohibido") });
    expect(await screen.findByRole("heading", { name: "Sin permiso" })).toBeInTheDocument();
  });

  it("capturas expiradas muestran la política de retención, no un error", async () => {
    const page = eventsPage(1);
    page.items[0] = { ...page.items[0]!, capture_status: "expired", thumb_url: null };
    setup({ events: page });
    expect(
      await screen.findByText("Captura eliminada por política de retención (60 días)"),
    ).toBeInTheDocument();
  });
});

describe("deltas de KPI", () => {
  it("más es bueno, menos es malo, y sin cambio es neutro", () => {
    expect(relativeDelta(120, 100, "7d")).toMatchObject({ direction: "up", tone: "good" });
    expect(relativeDelta(80, 100, "7d")).toMatchObject({ direction: "down", tone: "bad" });
    expect(pointsDelta(0.8, 0.8, "24h")).toMatchObject({ direction: null, tone: "neutral" });
    expect(relativeDelta(5, 0, "30d")).toMatchObject({ text: "—", direction: null });
  });
});
