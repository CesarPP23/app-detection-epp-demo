import { formatInTimeZone, fromZonedTime } from "date-fns-tz";
import type { EventSummary, EventsQuery } from "@/api/types";
import { SITE_TZ } from "@/design/format";

export type Preset = "hoy" | "24h" | "7d" | "30d" | "custom";
export type View = "galeria" | "tabla";
export type Density = "comoda" | "compacta";
export type SortKey = "hora" | "conf";

export interface EventFilters {
  preset: Preset;
  /** Fechas locales (YYYY-MM-DD, hora de Lima) cuando preset = custom. */
  from: string;
  to: string;
  clases: string[];
  /** 0-100. */
  minConf: number;
  simulados: boolean;
  /** Solo eventos cuya captura no está "ok" (enlace desde Resumen). */
  sinCaptura: boolean;
  view: View;
  density: Density;
  sort: SortKey;
  dir: "asc" | "desc";
}

export const DEFAULT_FILTERS: EventFilters = {
  preset: "7d",
  from: "",
  to: "",
  clases: [],
  minConf: 0,
  simulados: false,
  sinCaptura: false,
  view: "galeria",
  density: "comoda",
  sort: "hora",
  dir: "desc",
};

const PRESETS: Preset[] = ["hoy", "24h", "7d", "30d", "custom"];

function pick<T extends string>(v: string | null, allowed: readonly T[], fallback: T): T {
  return v && (allowed as readonly string[]).includes(v) ? (v as T) : fallback;
}

/** Los filtros viven en la URL para poder compartir un enlace (3.10). */
export function parseFilters(sp: URLSearchParams): EventFilters {
  const conf = Number(sp.get("conf"));
  return {
    preset: pick(sp.get("rango"), PRESETS, DEFAULT_FILTERS.preset),
    from: sp.get("from") ?? "",
    to: sp.get("to") ?? "",
    clases: (sp.get("clase") ?? "").split(",").filter(Boolean),
    minConf: Number.isFinite(conf) ? Math.min(100, Math.max(0, Math.round(conf))) : 0,
    simulados: sp.get("sim") === "1",
    sinCaptura: sp.get("captura") === "falta",
    view: pick(sp.get("vista"), ["galeria", "tabla"] as const, DEFAULT_FILTERS.view),
    density: pick(sp.get("dens"), ["comoda", "compacta"] as const, DEFAULT_FILTERS.density),
    sort: pick(sp.get("orden"), ["hora", "conf"] as const, DEFAULT_FILTERS.sort),
    dir: pick(sp.get("dir"), ["asc", "desc"] as const, DEFAULT_FILTERS.dir),
  };
}

/** Solo escribe lo que difiere del valor por defecto: URLs cortas y legibles. */
export function serializeFilters(f: EventFilters, base?: URLSearchParams): URLSearchParams {
  const sp = new URLSearchParams(base);
  const set = (k: string, v: string | null) => (v ? sp.set(k, v) : sp.delete(k));
  const d = DEFAULT_FILTERS;
  set("rango", f.preset !== d.preset ? f.preset : null);
  set("from", f.preset === "custom" && f.from ? f.from : null);
  set("to", f.preset === "custom" && f.to ? f.to : null);
  set("clase", f.clases.length ? f.clases.join(",") : null);
  set("conf", f.minConf > 0 ? String(f.minConf) : null);
  set("sim", f.simulados ? "1" : null);
  set("captura", f.sinCaptura ? "falta" : null);
  set("vista", f.view !== d.view ? f.view : null);
  set("dens", f.density !== d.density ? f.density : null);
  set("orden", f.sort !== d.sort ? f.sort : null);
  set("dir", f.dir !== d.dir ? f.dir : null);
  return sp;
}

const HOUR = 3_600_000;

/** Inicio del día (00:00 de Lima) del instante dado. */
export function startOfLimaDay(now: Date): Date {
  return fromZonedTime(`${formatInTimeZone(now, SITE_TZ, "yyyy-MM-dd")}T00:00:00`, SITE_TZ);
}

/** Rango [from, to] en ISO UTC. `now` se redondea al minuto para no cambiar la clave de caché. */
export function resolveRange(
  f: Pick<EventFilters, "preset" | "from" | "to">,
  nowMs: number,
): { from?: string; to?: string } {
  const now = new Date(Math.floor(nowMs / 60_000) * 60_000);
  switch (f.preset) {
    case "hoy":
      return { from: startOfLimaDay(now).toISOString() };
    case "24h":
      return { from: new Date(now.getTime() - 24 * HOUR).toISOString() };
    case "7d":
      return { from: new Date(now.getTime() - 7 * 24 * HOUR).toISOString() };
    case "30d":
      return { from: new Date(now.getTime() - 30 * 24 * HOUR).toISOString() };
    case "custom": {
      const out: { from?: string; to?: string } = {};
      if (f.from) out.from = fromZonedTime(`${f.from}T00:00:00`, SITE_TZ).toISOString();
      if (f.to) out.to = fromZonedTime(`${f.to}T23:59:59.999`, SITE_TZ).toISOString();
      return out;
    }
  }
}

/** Consulta al servidor. El backend filtra por UNA clase; con varias se filtra en el cliente. */
export function toQuery(
  f: EventFilters,
  range: { from?: string; to?: string },
): Omit<EventsQuery, "cursor"> {
  const q: Omit<EventsQuery, "cursor"> = { limit: 24, ...range };
  if (f.clases.length === 1 && f.clases[0]) q.clase = f.clases[0];
  if (f.minConf > 0) q.min_conf = f.minConf / 100;
  if (f.simulados) q.include_simulated = true;
  return q;
}

/** Filtros que el servidor no resuelve: varias clases y "sin captura". */
export function clientFilter(items: EventSummary[], f: EventFilters): EventSummary[] {
  return items.filter(
    (e) =>
      (f.clases.length < 2 || f.clases.includes(e.nombre_clase)) &&
      (!f.sinCaptura || e.capture_status !== "ok"),
  );
}

export function sortItems(items: EventSummary[], f: EventFilters): EventSummary[] {
  const sign = f.dir === "asc" ? 1 : -1;
  const key = (e: EventSummary) =>
    f.sort === "conf" ? e.confianza_promedio : new Date(e.end_ts).getTime();
  return [...items].sort((a, b) => sign * (key(a) - key(b)));
}

export const hasActiveFilters = (f: EventFilters) =>
  f.clases.length > 0 || f.minConf > 0 || f.simulados || f.sinCaptura || f.preset !== "7d";
