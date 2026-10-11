import { describe, expect, it } from "vitest";
import {
  formatBucket,
  formatDeltaPoints,
  formatDeltaRelative,
  formatDuration,
  formatInt,
  formatLatency,
  formatPercent,
  formatRelative,
  formatTime,
  shortId,
} from "./format";

describe("format (es-PE, hora de Lima)", () => {
  it("formatea duraciones", () => {
    expect(formatDuration(9.18)).toBe("9 s");
    expect(formatDuration(75)).toBe("1 min 15 s");
    expect(formatDuration(22320)).toBe("6 h 12 min");
    expect(formatDuration(-3)).toBe("0 s");
  });

  it("no inventa +∞ % sin periodo previo", () => {
    expect(formatDeltaRelative(10, 0)).toBe("—");
    expect(formatDeltaRelative(10, null)).toBe("—");
    expect(formatDeltaRelative(null, 5)).toBe("—");
    expect(formatDeltaPoints(0.8, null)).toBe("—");
  });

  it("calcula variaciones con signo", () => {
    expect(formatDeltaRelative(112, 100)).toMatch(/^\+12\s?%$/);
    expect(formatDeltaRelative(90, 100)).toMatch(/^−10\s?%$/);
    expect(formatDeltaPoints(0.82, 0.84)).toBe("−2 pts");
    expect(formatDeltaPoints(0.9, 0.84)).toBe("+6 pts");
  });

  it("formatea números y porcentajes con Intl", () => {
    expect(formatInt(1284.4)).toMatch(/^1\D?284$/);
    expect(formatPercent(0.998, 1)).toMatch(/99[.,]8\s?%/);
    expect(formatLatency(420)).toBe("≈ 420 ms");
  });

  it("usa la hora de Lima (UTC−5)", () => {
    expect(formatTime("2026-10-10T14:03:11.204Z")).toBe("09:03:11");
    expect(formatBucket("2026-10-10T14:00:00Z", "24h")).toBe("09:00");
    expect(formatBucket("2026-10-10T14:00:00Z", "7d")).toMatch(/10/);
  });

  it("describe tiempo relativo", () => {
    const now = new Date("2026-10-10T14:03:11Z");
    expect(formatRelative("2026-10-10T14:03:09Z", now)).toBe("ahora");
    expect(formatRelative("2026-10-10T14:00:11Z", now)).toMatch(/^hace 3 minutos?$/);
  });

  it("acorta ids", () => {
    expect(shortId("0192f6c4-8a1e-7c3b-9f2d-4b8e1a2c3d4e")).toBe("0192f6c4…3d4e");
    expect(shortId("abc")).toBe("abc");
  });
});
