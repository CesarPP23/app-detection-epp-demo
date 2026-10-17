import { formatDuration, formatTime } from "@/design/format";

interface Props {
  startTs: string;
  endTs: string;
  /** Momento de la captura; marcador sobre la barra. */
  capturedAt?: string | null;
}

/** Barra de la sesión (inicio → fin) con un marcador en el momento de la captura. */
export function SessionTimeline({ startTs, endTs, capturedAt }: Props) {
  const start = new Date(startTs).getTime();
  const end = new Date(endTs).getTime();
  const span = Math.max(1, end - start);
  const cap = capturedAt ? new Date(capturedAt).getTime() : null;
  const pos = cap === null ? null : Math.min(1, Math.max(0, (cap - start) / span));
  const duration = (end - start) / 1000;
  return (
    <div>
      <div
        role="img"
        aria-label={`Sesión de ${formatDuration(duration)}, de ${formatTime(startTs)} a ${formatTime(endTs)}${
          capturedAt ? `; captura a las ${formatTime(capturedAt)}` : ""
        }`}
        className="relative mt-2 h-2 rounded-full bg-accent"
      >
        {pos !== null && (
          <span
            aria-hidden
            className="absolute top-1/2 size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 bg-surface-1"
            style={{ left: `${pos * 100}%`, borderColor: "var(--text-primary)" }}
          />
        )}
      </div>
      <div className="tabular mt-2 flex justify-between text-xs text-ink-2">
        <span>{formatTime(startTs)}</span>
        <span>{formatDuration(duration)}</span>
        <span>{formatTime(endTs)}</span>
      </div>
    </div>
  );
}
