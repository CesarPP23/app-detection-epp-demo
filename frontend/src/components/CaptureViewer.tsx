import { ImageOff } from "lucide-react";
import { useState } from "react";
import type { CaptureStatus, DetectionBox } from "@/api/types";
import { classColor, classLabel } from "@/design/classColors";
import { formatPercent, formatTime } from "@/design/format";

interface Props {
  src: string | null | undefined;
  status: CaptureStatus;
  /** Clase del evento: se enfatiza con su color y trazo de 2 px; las demás en gris y 1 px. */
  eventClass: string;
  detections: DetectionBox[];
  eventTime: string;
  confidence: number;
  showBoxes?: boolean;
  /** 16:9 por defecto: evita saltos de layout (CLS). */
  aspect?: string;
  className?: string;
}

const REASON: Record<CaptureStatus, string> = {
  ok: "",
  expired: "Captura eliminada por política de retención (60 días).",
  missing: "Este evento no tiene captura.",
  dropped_disk_full: "La captura se descartó porque el disco del dispositivo estaba lleno.",
  missing_legacy: "Evento anterior al registro de capturas.",
};

export function captureReason(status: CaptureStatus): string {
  return REASON[status];
}

/** Imagen con las cajas superpuestas en SVG (coordenadas normalizadas → viewBox 0 0 1 1). */
export function CaptureViewer({
  src,
  status,
  eventClass,
  detections,
  eventTime,
  confidence,
  showBoxes = true,
  aspect = "16 / 9",
  className = "",
}: Props) {
  const [failed, setFailed] = useState(false);
  const unavailable = status !== "ok" || !src || failed;
  const alt = `Captura del evento: ${classLabel(eventClass)}, ${formatTime(eventTime)}, confianza ${Math.round(confidence * 100)} %`;

  return (
    <div
      className={`relative w-full overflow-hidden rounded-video border border-line bg-surface-2 ${className}`}
      style={{ aspectRatio: aspect }}
    >
      {unavailable ? (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center">
          <ImageOff aria-hidden className="size-8 text-ink-3" />
          <p className="text-sm text-ink-2">
            {failed && status === "ok" ? "No se pudo cargar la imagen." : REASON[status]}
          </p>
        </div>
      ) : (
        <>
          <img
            src={src}
            alt={alt}
            decoding="async"
            className="absolute inset-0 size-full object-contain"
            onError={() => setFailed(true)}
          />
          {showBoxes && (
            <svg
              aria-hidden
              viewBox="0 0 1 1"
              preserveAspectRatio="none"
              className="pointer-events-none absolute inset-0 size-full"
            >
              {detections.map((d, i) => {
                const [x1, y1, x2, y2] = d.bbox_xyxy_norm;
                if (x1 === undefined || y1 === undefined || x2 === undefined || y2 === undefined)
                  return null;
                const mine = d.nombre_clase === eventClass;
                return (
                  <rect
                    key={i}
                    x={x1}
                    y={y1}
                    width={x2 - x1}
                    height={y2 - y1}
                    fill="none"
                    stroke={mine ? classColor(eventClass) : "var(--text-muted)"}
                    strokeWidth={mine ? 2 : 1}
                    vectorEffect="non-scaling-stroke"
                  />
                );
              })}
            </svg>
          )}
          {showBoxes &&
            detections.map((d, i) => {
              const [x1, y1] = d.bbox_xyxy_norm;
              if (x1 === undefined || y1 === undefined) return null;
              // Cajas pegadas al borde superior: la etiqueta va dentro, no recortada fuera de la imagen.
              const inside = y1 < 0.08;
              return (
                <span
                  key={i}
                  className="pointer-events-none absolute max-w-[60%] truncate rounded-sm px-1 text-xs text-ink"
                  style={{
                    left: `${x1 * 100}%`,
                    top: `${y1 * 100}%`,
                    transform: inside ? "none" : "translateY(-100%)",
                    background: "color-mix(in srgb, var(--surface-1) 85%, transparent)",
                  }}
                >
                  {classLabel(d.nombre_clase)} {formatPercent(d.confianza)}
                </span>
              );
            })}
        </>
      )}
    </div>
  );
}
