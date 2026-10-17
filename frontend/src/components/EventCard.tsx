import { ImageOff } from "lucide-react";
import { Link } from "react-router-dom";
import type { EventSummary } from "@/api/types";
import { classLabel } from "@/design/classColors";
import { formatDateTime, formatDuration, formatRelative } from "@/design/format";
import { captureReason } from "./CaptureViewer";
import { ClassChip } from "./ClassChip";
import { ConfidenceMeter } from "./ConfidenceMeter";

interface Props {
  event: EventSummary;
  /** Destino al abrir (conserva la query string de los filtros). */
  to: string;
  className?: string;
}

/** Tarjeta de galería: miniatura 16:9 (lazy, aspecto fijo → sin CLS), clase, confianza y hora. */
export function EventCard({ event: e, to, className = "" }: Props) {
  const hasThumb = e.capture_status === "ok" && !!e.thumb_url;
  return (
    <Link
      to={to}
      aria-label={`Abrir evento: ${classLabel(e.nombre_clase)}, ${formatDateTime(e.end_ts)}`}
      className={`group block overflow-hidden rounded-card border border-line bg-surface-1 hover:border-accent ${className}`}
    >
      <div className="relative bg-surface-2" style={{ aspectRatio: "16 / 9" }}>
        {hasThumb ? (
          <img
            src={e.thumb_url ?? undefined}
            alt={`Captura del evento: ${classLabel(e.nombre_clase)}, ${formatDateTime(e.end_ts)}, confianza ${Math.round(e.confianza_max * 100)} %`}
            loading="lazy"
            decoding="async"
            className="absolute inset-0 size-full object-cover"
          />
        ) : (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-1 p-2 text-center text-xs text-ink-2">
            <ImageOff aria-hidden className="size-6 text-ink-3" />
            {captureReason(e.capture_status)}
          </div>
        )}
        {e.is_simulated && (
          <span className="absolute left-2 top-2 rounded-full border border-line bg-surface-1 px-2 py-0.5 text-xs font-medium text-ink">
            Simulado
          </span>
        )}
      </div>
      <div className="space-y-2 p-3">
        <div className="flex items-center justify-between gap-2">
          <ClassChip name={e.nombre_clase} />
          <span className="tabular text-xs text-ink-2">{formatDuration(e.duration_seconds)}</span>
        </div>
        <ConfidenceMeter value={e.confianza_promedio} label="Confianza promedio" />
        <p className="text-xs text-ink-2" title={formatDateTime(e.end_ts)}>
          {formatRelative(e.end_ts)}
        </p>
      </div>
    </Link>
  );
}
