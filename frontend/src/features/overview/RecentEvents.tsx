import { ImageOff } from "lucide-react";
import { Link } from "react-router-dom";
import type { EventSummary } from "@/api/types";
import { classLabel } from "@/design/classColors";
import { formatDateTime, formatDuration, formatPercent, formatRelative } from "@/design/format";

function captureAlt(e: EventSummary): string {
  return `Captura del evento: ${classLabel(e.nombre_clase)}, ${formatDateTime(e.end_ts)}, confianza ${Math.round(e.confianza_max * 100)} %`;
}

function Missing({ e }: { e: EventSummary }) {
  const text =
    e.capture_status === "expired"
      ? "Captura eliminada por política de retención (60 días)"
      : "Sin captura";
  return (
    <div className="flex aspect-video flex-col items-center justify-center gap-1 bg-surface-2 p-2 text-center text-xs text-ink-2">
      <ImageOff aria-hidden className="size-5 text-ink-3" />
      {text}
    </div>
  );
}

/** Tarjeta mínima de la pantalla Resumen (la galería completa vive en Eventos). */
function RecentCard({ e }: { e: EventSummary }) {
  return (
    <li className="w-56 shrink-0 snap-start lg:w-auto">
      <Link
        to={`/events/${e.event_id}`}
        className="block overflow-hidden rounded-card border border-line bg-surface-1 hover:bg-surface-2"
      >
        {e.capture_status === "ok" && e.thumb_url ? (
          <img
            src={e.thumb_url}
            alt={captureAlt(e)}
            loading="lazy"
            decoding="async"
            className="aspect-video w-full object-cover"
          />
        ) : (
          <Missing e={e} />
        )}
        <div className="p-3 text-sm">
          <p className="truncate font-medium text-ink">
            {classLabel(e.nombre_clase)}
            {e.is_simulated && <span className="ml-2 text-xs text-ink-2">Simulado</span>}
          </p>
          <p className="text-ink-2">
            {formatPercent(e.confianza_max)} · {formatDuration(e.duration_seconds)}
          </p>
          <p className="text-ink-3" title={formatDateTime(e.end_ts)}>
            {formatRelative(e.end_ts)}
          </p>
        </div>
      </Link>
    </li>
  );
}

/** Seis tarjetas; en móvil se desplazan en horizontal dentro de su contenedor. */
export function RecentEvents({ items }: { items: EventSummary[] }) {
  return (
    <ul
      aria-label="Últimos eventos"
      className="flex snap-x gap-3 overflow-x-auto pb-2 lg:grid lg:grid-cols-6 lg:overflow-visible"
    >
      {items.slice(0, 6).map((e) => (
        <RecentCard key={e.event_id} e={e} />
      ))}
    </ul>
  );
}
