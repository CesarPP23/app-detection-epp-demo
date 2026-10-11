import { formatDistanceStrict } from "date-fns";
import { es } from "date-fns/locale";
import { formatInTimeZone } from "date-fns-tz";

export const SITE_TZ = "America/Lima";
const LOCALE = "es-PE";

const nf = new Intl.NumberFormat(LOCALE);
const nf1 = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1, minimumFractionDigits: 1 });

export function formatInt(n: number): string {
  return nf.format(Math.round(n));
}

export function formatPercent(fraction: number, digits = 0): string {
  return new Intl.NumberFormat(LOCALE, {
    style: "percent",
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(fraction);
}

type Maybe = number | null | undefined;

/** Variación relativa ("+12 %"). Sin periodo previo devuelve "—", nunca "+∞ %". */
export function formatDeltaRelative(value: Maybe, previous: Maybe): string {
  if (value == null || previous == null || previous === 0) return "—";
  const d = (value - previous) / previous;
  return `${d >= 0 ? "+" : "−"}${formatPercent(Math.abs(d))}`;
}

/** Variación en puntos porcentuales ("−2 pts") para magnitudes que ya son fracciones 0-1. */
export function formatDeltaPoints(value: Maybe, previous: Maybe): string {
  if (value == null || previous == null) return "—";
  const d = Math.round((value - previous) * 100);
  return `${d >= 0 ? "+" : "−"}${Math.abs(d)} pts`;
}

/** 9 → "9 s", 75 → "1 min 15 s", 22320 → "6 h 12 min". */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h} h ${m} min`;
  return `${m} min ${s % 60} s`;
}

export function formatDateTime(iso: string): string {
  return formatInTimeZone(new Date(iso), SITE_TZ, "d MMM yyyy, HH:mm:ss", { locale: es });
}

export function formatTime(iso: string): string {
  return formatInTimeZone(new Date(iso), SITE_TZ, "HH:mm:ss", { locale: es });
}

export function formatRelative(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (Math.abs(now.getTime() - d.getTime()) < 5_000) return "ahora";
  return `hace ${formatDistanceStrict(d, now, { locale: es })}`;
}

export function formatTemp(c: number): string {
  return `${nf1.format(c)} °C`;
}

export function formatLatency(ms: number): string {
  return `≈ ${formatInt(ms)} ms`;
}

export function shortId(id: string): string {
  return id.length > 13 ? `${id.slice(0, 8)}…${id.slice(-4)}` : id;
}

/** Etiqueta de un bucket de la serie: hora (24 h) o día (7 d / 30 d), hora de Lima. */
export function formatBucket(iso: string, range: "24h" | "7d" | "30d"): string {
  return range === "24h"
    ? formatInTimeZone(new Date(iso), SITE_TZ, "HH:mm", { locale: es })
    : formatInTimeZone(new Date(iso), SITE_TZ, "d MMM", { locale: es });
}
