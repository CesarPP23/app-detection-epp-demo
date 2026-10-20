import { ImageOff, Maximize2 } from "lucide-react";
import { useState } from "react";
import type { CaptureStatus, DetectionBox } from "@/api/types";
import { classColor, classLabel } from "@/design/classColors";
import { formatPercent, formatTime } from "@/design/format";

interface Props {
  src: string | null | undefined;
  status: CaptureStatus;
  /** Clase del evento: se enfatiza con su color y trazo grueso; las demás en gris y trazo fino.
   * Si ninguna detección es de esa clase (episodios del nodo con pose), se enfatizan todas. */
  eventClass: string;
  detections: DetectionBox[];
  eventTime: string;
  confidence: number;
  showBoxes?: boolean;
  /** Relación de aspecto inicial; al cargar se usa la real de la imagen para que las cajas
   * (coordenadas normalizadas) coincidan con lo que se ve. 16:9 por defecto: evita CLS. */
  aspect?: string;
  /** Al hacer clic en la imagen o en «Ampliar» (abre el visor a pantalla completa). */
  onExpand?: () => void;
  /** Factor para las etiquetas: el visor lo usa para contrarrestar el zoom. */
  labelScale?: number;
  /** Etiquetas más grandes (visor a pantalla completa). */
  large?: boolean;
  className?: string;
  style?: React.CSSProperties;
  onAspect?: (ratio: number) => void;
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
  onExpand,
  labelScale = 1,
  large = false,
  className = "",
  style,
  onAspect,
}: Props) {
  const [failed, setFailed] = useState(false);
  const [ratio, setRatio] = useState<number | null>(null);
  const unavailable = status !== "ok" || !src || failed;
  const alt = `Captura del evento: ${classLabel(eventClass)}, ${formatTime(eventTime)}, confianza ${Math.round(confidence * 100)} %`;
  const anyMine = detections.some((d) => d.nombre_clase === eventClass);
  const isMine = (d: DetectionBox) => !anyMine || d.nombre_clase === eventClass;

  return (
    <div
      className={`relative w-full overflow-hidden rounded-video border border-line bg-surface-2 ${className}`}
      style={{ aspectRatio: ratio ? String(ratio) : aspect, ...style }}
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
            draggable={false}
            className="absolute inset-0 size-full object-contain"
            onLoad={(e) => {
              const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
              if (w > 0 && h > 0) {
                setRatio(w / h);
                onAspect?.(w / h);
              }
            }}
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
                const mine = isMine(d);
                return (
                  <rect
                    key={i}
                    x={x1}
                    y={y1}
                    width={x2 - x1}
                    height={y2 - y1}
                    fill="none"
                    stroke={mine ? classColor(d.nombre_clase) : "var(--text-muted)"}
                    strokeWidth={mine ? (large ? 3 : 2.5) : 1.5}
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
              const mine = isMine(d);
              // Cajas pegadas al borde superior: la etiqueta va dentro, no recortada fuera de la imagen.
              const inside = y1 < (large ? 0.05 : 0.1);
              return (
                <span
                  key={i}
                  className={`pointer-events-none absolute max-w-[70%] truncate rounded-sm px-1.5 font-semibold leading-snug shadow-sm ${large ? "text-sm" : "text-xs"}`}
                  style={{
                    left: `${x1 * 100}%`,
                    top: `${y1 * 100}%`,
                    transform: `${inside ? "" : "translateY(-100%) "}scale(${labelScale})`,
                    transformOrigin: inside ? "top left" : "bottom left",
                    // Fondo sólido del color de la clase: legible sobre cualquier imagen.
                    background: mine ? classColor(d.nombre_clase) : "var(--surface-1)",
                    color: mine ? "#0b0b0b" : "var(--text-primary)",
                  }}
                >
                  {classLabel(d.nombre_clase)} {formatPercent(d.confianza)}
                </span>
              );
            })}
          {onExpand && (
            // Toda la imagen es el botón; el icono de la esquina solo lo hace evidente.
            <button
              type="button"
              onClick={onExpand}
              aria-label="Ampliar imagen"
              title="Ampliar imagen"
              className="group absolute inset-0 cursor-zoom-in focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--accent)]"
            >
              <span className="absolute right-2 bottom-2 inline-flex size-11 items-center justify-center rounded-control border border-line bg-surface-1/90 text-ink shadow-sm group-hover:bg-surface-2 md:size-9">
                <Maximize2 aria-hidden className="size-4" />
              </span>
            </button>
          )}
        </>
      )}
    </div>
  );
}
