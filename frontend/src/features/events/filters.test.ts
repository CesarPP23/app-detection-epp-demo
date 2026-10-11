import { describe, expect, it } from "vitest";
import { makeEvent } from "@/test/harness";
import {
  clientFilter,
  DEFAULT_FILTERS,
  hasActiveFilters,
  parseFilters,
  resolveRange,
  serializeFilters,
  sortItems,
  toQuery,
} from "./filters";

describe("filtros en la URL", () => {
  it("sin parámetros devuelve los valores por defecto y una URL vacía", () => {
    const f = parseFilters(new URLSearchParams());
    expect(f).toEqual(DEFAULT_FILTERS);
    expect(serializeFilters(f).toString()).toBe("");
    expect(hasActiveFilters(f)).toBe(false);
  });

  it("ida y vuelta conserva clase, confianza, simulados y rango", () => {
    const f = {
      ...DEFAULT_FILTERS,
      preset: "custom" as const,
      from: "2026-10-01",
      to: "2026-10-09",
      clases: ["hairnet", "lab_coat"],
      minConf: 60,
      simulados: true,
      view: "tabla" as const,
    };
    const back = parseFilters(new URLSearchParams(serializeFilters(f).toString()));
    expect(back).toEqual(f);
  });

  it("ignora valores inválidos de la URL", () => {
    const f = parseFilters(new URLSearchParams("rango=ayer&conf=999&vista=x&dir=up"));
    expect(f.preset).toBe("7d");
    expect(f.minConf).toBe(100);
    expect(f.view).toBe("galeria");
    expect(f.dir).toBe("desc");
  });
});

describe("rango y consulta", () => {
  it("«hoy» empieza a las 00:00 de Lima (05:00 UTC)", () => {
    const now = Date.UTC(2026, 9, 10, 3, 0, 0); // 22:00 del 9 en Lima
    expect(resolveRange({ preset: "hoy", from: "", to: "" }, now).from).toBe(
      "2026-10-09T05:00:00.000Z",
    );
  });

  it("personalizado usa días completos de Lima", () => {
    const r = resolveRange({ preset: "custom", from: "2026-10-01", to: "2026-10-02" }, 0);
    expect(r.from).toBe("2026-10-01T05:00:00.000Z");
    expect(r.to).toBe("2026-10-03T04:59:59.999Z");
  });

  it("una clase va al servidor; varias se filtran en el cliente", () => {
    const one = toQuery({ ...DEFAULT_FILTERS, clases: ["hairnet"], minConf: 60 }, {});
    expect(one.clase).toBe("hairnet");
    expect(one.min_conf).toBe(0.6);
    const two = toQuery({ ...DEFAULT_FILTERS, clases: ["hairnet", "lab_coat"] }, {});
    expect(two.clase).toBeUndefined();
    const items = [
      makeEvent(1),
      makeEvent(2, { nombre_clase: "lab_coat" }),
      makeEvent(3, { nombre_clase: "face_mask" }),
    ];
    expect(
      clientFilter(items, { ...DEFAULT_FILTERS, clases: ["hairnet", "lab_coat"] }),
    ).toHaveLength(2);
  });

  it("«solo sin captura» y orden por confianza", () => {
    const items = [
      makeEvent(1, { confianza_promedio: 0.5 }),
      makeEvent(2, { capture_status: "expired", confianza_promedio: 0.9 }),
    ];
    expect(clientFilter(items, { ...DEFAULT_FILTERS, sinCaptura: true })).toHaveLength(1);
    const sorted = sortItems(items, { ...DEFAULT_FILTERS, sort: "conf", dir: "desc" });
    expect(sorted[0]?.confianza_promedio).toBe(0.9);
  });
});
