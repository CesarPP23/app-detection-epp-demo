import { AlertTriangle, ArrowDown, ArrowUp, CheckCircle2 } from "lucide-react";
import { Link } from "react-router-dom";
import type { EventSummary } from "@/api/types";
import { formatDateTime, formatDuration, formatPercent, shortId } from "@/design/format";
import type { Density, SortKey } from "@/features/events/filters";
import { captureReason } from "./CaptureViewer";
import { ClassChip } from "./ClassChip";
import { CopyButton } from "./CopyButton";

interface Props {
  events: EventSummary[];
  sort: SortKey;
  dir: "asc" | "desc";
  density: Density;
  onSort: (key: SortKey) => void;
  /** Destino de la fila (conserva la query string de los filtros). */
  hrefFor: (eventId: string) => string;
}

function SortHeader({
  label,
  active,
  dir,
  onClick,
}: {
  label: string;
  active: boolean;
  dir: "asc" | "desc";
  onClick: () => void;
}) {
  const Icon = dir === "asc" ? ArrowUp : ArrowDown;
  return (
    <th
      scope="col"
      aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"}
      className="px-3 py-2 text-left font-medium"
    >
      <button
        type="button"
        onClick={onClick}
        className="inline-flex items-center gap-1 hover:text-ink"
      >
        {label}
        {active && <Icon aria-hidden className="size-3.5" />}
      </button>
    </th>
  );
}

/** Vista de tabla del explorador: hora absoluta, clase, duración, confianza, captura e ID. */
export function EventTable({ events, sort, dir, density, onSort, hrefFor }: Props) {
  const pad = density === "compacta" ? "py-1" : "py-3";
  return (
    <div className="relative overflow-x-auto rounded-card border border-line bg-surface-1">
      <table className="tabular w-full min-w-[760px] text-sm">
        <caption className="sr-only">Eventos registrados</caption>
        <thead className="bg-surface-2 text-ink-2">
          <tr>
            <SortHeader
              label="Hora"
              active={sort === "hora"}
              dir={dir}
              onClick={() => onSort("hora")}
            />
            <th scope="col" className="px-3 py-2 text-left font-medium">
              Clase
            </th>
            <th scope="col" className="px-3 py-2 text-left font-medium">
              Duración
            </th>
            <SortHeader
              label="Confianza prom./máx."
              active={sort === "conf"}
              dir={dir}
              onClick={() => onSort("conf")}
            />
            <th scope="col" className="px-3 py-2 text-left font-medium">
              Frames
            </th>
            <th scope="col" className="px-3 py-2 text-left font-medium">
              Captura
            </th>
            <th scope="col" className="px-3 py-2 text-left font-medium">
              ID
            </th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <tr key={e.event_id} className="border-t border-line hover:bg-surface-2">
              <td className={`px-3 ${pad}`}>
                <Link
                  to={hrefFor(e.event_id)}
                  className="text-ink underline-offset-2 hover:underline"
                >
                  {formatDateTime(e.end_ts)}
                </Link>
                {e.is_simulated && (
                  <span className="ml-2 rounded-full border border-line px-2 py-0.5 text-xs">
                    Simulado
                  </span>
                )}
              </td>
              <td className={`px-3 ${pad}`}>
                <ClassChip name={e.nombre_clase} />
              </td>
              <td className={`px-3 ${pad}`}>{formatDuration(e.duration_seconds)}</td>
              <td className={`px-3 ${pad}`}>
                {formatPercent(e.confianza_promedio)} / {formatPercent(e.confianza_max)}
              </td>
              <td className={`px-3 ${pad}`}>{e.frame_count ?? "—"}</td>
              <td className={`px-3 ${pad}`}>
                {e.capture_status === "ok" ? (
                  <span className="inline-flex items-center gap-1 text-ink">
                    <CheckCircle2
                      aria-hidden
                      className="size-4"
                      style={{ color: "var(--status-good)" }}
                    />
                    <span className="sr-only">Con captura</span>
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-ink-2">
                    <AlertTriangle
                      aria-hidden
                      className="size-4"
                      style={{ color: "var(--status-serious)" }}
                    />
                    {captureReason(e.capture_status)}
                  </span>
                )}
              </td>
              <td className={`px-3 ${pad}`}>
                <span
                  className="inline-flex items-center gap-1 font-mono text-xs"
                  title={e.event_id}
                >
                  {shortId(e.event_id)}
                  <CopyButton text={e.event_id} label="Copiar ID del evento" />
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
