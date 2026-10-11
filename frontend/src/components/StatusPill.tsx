import {
  CameraOff,
  CheckCircle2,
  PauseCircle,
  Radio,
  WifiOff,
  type LucideIcon,
} from "lucide-react";
import type { DeviceState } from "@/api/types";

const MAP: Record<DeviceState, { label: string; icon: LucideIcon; color: string }> = {
  detecting: { label: "Detectando", icon: CheckCircle2, color: "var(--status-good)" },
  live: { label: "En vivo", icon: Radio, color: "var(--accent)" }, // acento: el rojo es "crítico"
  paused: { label: "Pausado", icon: PauseCircle, color: "var(--status-warning)" },
  offline: { label: "Sin conexión", icon: WifiOff, color: "var(--status-critical)" },
  camera_error: { label: "Cámara no disponible", icon: CameraOff, color: "var(--status-serious)" },
};

export function stateLabel(state: DeviceState): string {
  return MAP[state].label;
}

/** Estado SIEMPRE con ícono + texto + color, nunca solo color (3.8). */
export function StatusPill({
  state,
  className = "",
}: {
  state: DeviceState | undefined;
  className?: string;
}) {
  if (!state) {
    return (
      <span
        className={`inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1 text-sm text-ink-3 ${className}`}
      >
        Estado desconocido
      </span>
    );
  }
  const { label, icon: Icon, color } = MAP[state];
  return (
    <span
      data-state={state}
      className={`inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-1 px-3 py-1 text-sm font-medium text-ink ${className}`}
    >
      <Icon
        aria-hidden
        className={`size-4 ${state === "live" ? "live-dot" : ""}`}
        style={{ color }}
      />
      {label}
    </span>
  );
}
