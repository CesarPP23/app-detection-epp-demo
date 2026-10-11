import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { Summary } from "@/api/types";
import { ActivityChart, orderClasses } from "./ActivityChart";
import { ClassBreakdown } from "./ClassBreakdown";
import { ConfidenceMeter } from "./ConfidenceMeter";
import { StatTile } from "./StatTile";
import { StatusPill } from "./StatusPill";
import { Sparkline } from "./Sparkline";

const series: Summary["series"] = [
  { bucket: "2026-10-10T14:00:00Z", total: 5, by_class: { latex_gloves: 3, hairnet: 2 } },
  { bucket: "2026-10-10T15:00:00Z", total: 7, by_class: { face_mask: 7 } },
];

describe("ActivityChart", () => {
  it("tiene vista de tabla con todos los datos y vuelve al gráfico", () => {
    render(<ActivityChart series={series} range="24h" />);
    expect(screen.getByRole("heading", { name: "Actividad" })).toBeInTheDocument();
    const toggle = screen.getByRole("button", { name: "Ver como tabla" });
    fireEvent.click(toggle);
    const table = screen.getByRole("table", { name: "Eventos por periodo" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(3);
    expect(within(table).getByText("09:00")).toBeInTheDocument();
    expect(within(rows[2]!).getAllByText("7", { selector: "td" })).toHaveLength(2); // total y mascarilla
    expect(
      within(table).getByRole("columnheader", { name: "Guantes de látex" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Ver gráfico" }));
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("'Por clase' muestra leyenda con las clases presentes", () => {
    render(<ActivityChart series={series} range="24h" />);
    fireEvent.click(screen.getByRole("button", { name: "Por clase" }));
    const legend = screen.getByRole("list", { name: "Leyenda" });
    expect(within(legend).getAllByRole("listitem")).toHaveLength(3);
  });

  it("ordena las clases por slot fijo, no por frecuencia", () => {
    expect(orderClasses(["latex_gloves", "zzz", "hairnet", "face_mask"])).toEqual([
      "hairnet",
      "face_mask",
      "latex_gloves",
      "zzz",
    ]);
  });
});

describe("ClassBreakdown", () => {
  const items = [
    { nombre_clase: "hairnet", count: 10 },
    { nombre_clase: "latex_gloves", count: 30 },
  ];

  it("ordena de mayor a menor y expone etiquetas accesibles", () => {
    render(
      <MemoryRouter>
        <ClassBreakdown items={items} hrefFor={(c) => `/events?clase=${c}`} />
      </MemoryRouter>,
    );
    const buttons = screen.getAllByRole("button", { name: /eventos \(/ });
    expect(buttons[0]).toHaveAccessibleName(/Guantes de látex: 30 eventos \(75\s?%\)/);
  });

  it("tiene vista de tabla", () => {
    render(
      <MemoryRouter>
        <ClassBreakdown items={items} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Ver como tabla" }));
    const table = screen.getByRole("table", { name: "Eventos por clase de EPP" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
  });

  it("sin datos explica el vacío", () => {
    render(
      <MemoryRouter>
        <ClassBreakdown items={[]} />
      </MemoryRouter>,
    );
    expect(screen.getByText("Sin eventos en este periodo.")).toBeInTheDocument();
  });
});

describe("piezas de estado", () => {
  it("StatusPill siempre lleva texto además de color", () => {
    render(<StatusPill state="camera_error" />);
    expect(screen.getByText("Cámara no disponible")).toBeInTheDocument();
  });

  it("ConfidenceMeter limita el rango y expone el valor", () => {
    render(<ConfidenceMeter value={1.7} />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "100");
  });

  it("StatTile muestra el delta con texto y sin periodo previo un guion", () => {
    render(
      <StatTile
        label="Eventos"
        value="1 284"
        delta={{ text: "—", direction: null, tone: "neutral" }}
      />,
    );
    expect(screen.getByText("—")).toBeInTheDocument();
  });

  it("Sparkline necesita al menos 2 puntos", () => {
    const { container } = render(<Sparkline values={[1]} label="x" />);
    expect(container.querySelector("svg")).toBeNull();
  });
});
