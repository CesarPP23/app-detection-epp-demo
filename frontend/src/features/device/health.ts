import type { DeviceStatus } from "@/api/types";
import { formatRelative } from "@/design/format";

export type Tone = "good" | "warning" | "serious" | "critical" | "neutral";

/** Temperatura de la CPU: < 70 °C bien, 70-80 atención, > 80 crítico (3.10). */
export function tempTone(c: number | null | undefined): Tone {
  if (c == null) return "neutral";
  if (c < 70) return "good";
  if (c <= 80) return "warning";
  return "critical";
}

export function tempLabel(tone: Tone): string {
  return {
    good: "Normal",
    warning: "Atención",
    critical: "Crítica",
    serious: "Alta",
    neutral: "Sin dato",
  }[tone];
}

/** Outbox: 0 al día; pendiente moderado = atención; creciendo = serio. */
export function outboxTone(n: number | null | undefined): Tone {
  if (n == null) return "neutral";
  if (n === 0) return "good";
  return n <= 50 ? "warning" : "serious";
}

export function outboxLabel(n: number | null | undefined): string {
  if (n == null) return "Sin dato";
  if (n === 0) return "Al día";
  return n <= 50 ? "Subiendo eventos" : "Acumulando";
}

/** Mensajes de diagnóstico accionables según el estado del dispositivo. */
export function diagnostics(d: DeviceStatus, now: Date = new Date()): string[] {
  const out: string[] = [];
  const h = d.health;
  if (d.state === "offline") {
    const since = d.last_seen ? formatRelative(d.last_seen, now).replace(/^hace /, "") : null;
    out.push(
      since
        ? `Sin señal desde hace ${since}. Revisa la alimentación y la red de la Pi (ver runbook).`
        : "El dispositivo nunca ha enviado señal. Revisa que esté encendido, conectado a la red y con el servicio instalado (ver runbook).",
    );
  }
  if (d.state === "camera_error" || d.camera_ok === false) {
    out.push(
      "La cámara no responde. Revisa el cable o el puerto USB y reinicia el servicio (ver runbook).",
    );
  }
  if (tempTone(h.cpu_temp_c) === "critical") {
    out.push(
      "La CPU supera los 80 °C. Revisa el disipador y la ventilación antes de que se reduzca el rendimiento.",
    );
  } else if (tempTone(h.cpu_temp_c) === "warning") {
    out.push("La CPU está entre 70 y 80 °C. Conviene revisar la ventilación.");
  }
  if (outboxTone(h.outbox_pending) === "serious") {
    out.push(
      `Hay ${h.outbox_pending} eventos esperando subirse. Revisa la conexión a internet de la Pi: los eventos no se pierden y se enviarán al volver la red.`,
    );
  }
  if (d.state === "paused") {
    out.push(
      "La detección está pausada: la cámara no registra eventos hasta que alguien la reanude.",
    );
  }
  return out;
}
