import * as Dialog from "@radix-ui/react-dialog";
import { ChevronLeft, ChevronRight, Download, Link2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError } from "@/api/client";
import { useEvent } from "@/api/queries";
import type { EventDetail } from "@/api/types";
import { hasRole, useAuth } from "@/auth/AuthProvider";
import { formatDateTime, formatDuration, formatInt, formatPercent } from "@/design/format";
import { CaptureViewer } from "./CaptureViewer";
import { ClassChip } from "./ClassChip";
import { ConfidenceMeter } from "./ConfidenceMeter";
import { CopyButton } from "./CopyButton";
import { EmptyState } from "./EmptyState";
import { ErrorState } from "./ErrorState";
import { SessionTimeline } from "./SessionTimeline";
import { Skeleton } from "./Skeleton";
import { useToast } from "./Toast";

const CLOSED_REASON: Record<string, string> = {
  gap: "Fin por ausencia de detecciones",
  shutdown: "Apagado del dispositivo",
  stopped: "Detección pausada",
  camera_lost: "Se perdió la cámara",
};

interface Props {
  eventId: string;
  onClose: () => void;
  /** Navegación ← / → entre los eventos de la lista actual. */
  prevId?: string | undefined;
  nextId?: string | undefined;
  onNavigate: (eventId: string) => void;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-2 border-t border-line py-2 text-sm first:border-t-0">
      <dt className="text-ink-2">{label}</dt>
      <dd className="min-w-0 break-words text-ink">{children}</dd>
    </div>
  );
}

function Body({ ev }: { ev: EventDetail }) {
  const { user } = useAuth();
  const { toast } = useToast();
  const [boxes, setBoxes] = useState(true);
  const link = `${window.location.origin}/events/${ev.event_id}`;
  const expired = ev.capture_status === "expired";
  const canDownload = ev.capture_status === "ok" && !!ev.original_url;

  return (
    <div className="space-y-4">
      <CaptureViewer
        src={ev.original_url}
        status={ev.capture_status}
        eventClass={ev.nombre_clase}
        detections={ev.detections_at_capture}
        eventTime={ev.end_ts}
        confidence={ev.confianza_max}
        showBoxes={boxes}
      />
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex min-h-11 items-center gap-2 text-sm text-ink md:min-h-9">
          <input
            type="checkbox"
            role="switch"
            className="size-4 accent-[var(--accent)]"
            checked={boxes}
            onChange={(e) => setBoxes(e.target.checked)}
            disabled={ev.capture_status !== "ok"}
          />
          Mostrar cajas
        </label>
        <span className="ml-auto flex gap-2">
          {canDownload && (
            <a
              href={ev.original_url ?? undefined}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line bg-surface-2 px-3 text-sm text-ink md:min-h-9"
            >
              <Download aria-hidden className="size-4" /> Descargar original
            </a>
          )}
          <button
            type="button"
            onClick={() =>
              void navigator.clipboard
                ?.writeText(link)
                .then(() => toast("Enlace copiado", "success"))
                .catch(() => toast("No se pudo copiar el enlace", "error"))
            }
            className="inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line bg-surface-2 px-3 text-sm text-ink md:min-h-9"
          >
            <Link2 aria-hidden className="size-4" /> Copiar enlace al evento
          </button>
        </span>
      </div>
      {expired && (
        <p className="text-sm text-ink-2">
          Captura eliminada por política de retención (60 días). Los datos del evento se conservan.
        </p>
      )}

      <div>
        <h3 className="text-base font-semibold text-ink">Sesión</h3>
        <SessionTimeline
          startTs={ev.start_ts}
          endTs={ev.end_ts}
          capturedAt={ev.capture.captured_at ?? null}
        />
      </div>

      <dl>
        <Row label="Clase">
          <ClassChip name={ev.nombre_clase} />
        </Row>
        <Row label="Confianza prom.">
          <ConfidenceMeter value={ev.confianza_promedio} label="Confianza promedio" />
        </Row>
        <Row label="Confianza máx.">
          <ConfidenceMeter value={ev.confianza_max} label="Confianza máxima" />
        </Row>
        <Row label="Inicio">{formatDateTime(ev.start_ts)}</Row>
        <Row label="Fin">{formatDateTime(ev.end_ts)}</Row>
        <Row label="Duración">{formatDuration(ev.duration_seconds)}</Row>
        <Row label="Frames">{ev.frame_count != null ? formatInt(ev.frame_count) : "—"}</Row>
        <Row label="Motivo de cierre">
          {ev.closed_reason ? (CLOSED_REASON[ev.closed_reason] ?? ev.closed_reason) : "—"}
        </Row>
        <Row label="Dispositivo">{ev.device_id}</Row>
        <Row label="Modelo">{ev.edge.model_version ?? "—"}</Row>
        <Row label="Software">{ev.edge.edge_version ?? "—"}</Row>
        {ev.is_simulated && <Row label="Origen">Simulado</Row>}
        <Row label="ID del evento">
          <span className="inline-flex items-center gap-1 font-mono text-xs">
            {ev.event_id}
            <CopyButton text={ev.event_id} label="Copiar ID del evento" />
          </span>
        </Row>
        {hasRole(user?.role, "admin") && ev.lake_uri && (
          <Row label="Objeto del lake">
            <span className="inline-flex items-center gap-1 font-mono text-xs">
              {ev.lake_uri}
              <CopyButton text={ev.lake_uri} label="Copiar ruta del lake" />
            </span>
          </Row>
        )}
        {ev.capture.confidence_at_capture != null && (
          <Row label="Confianza en la captura">
            {formatPercent(ev.capture.confidence_at_capture)}
          </Row>
        )}
      </dl>
    </div>
  );
}

/** Detalle de un evento: panel lateral de 560 px en escritorio y pantalla completa en móvil.
 * Ruta enlazable `/events/:eventId`. */
export function EventDetailPanel({ eventId, onClose, prevId, nextId, onNavigate }: Props) {
  const q = useEvent(eventId);

  // ← / → entre eventos de la lista actual (no interfiere con campos de texto).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
      if (el?.getAttribute("role") === "slider") return;
      if (e.key === "ArrowLeft" && prevId) onNavigate(prevId);
      if (e.key === "ArrowRight" && nextId) onNavigate(nextId);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [prevId, nextId, onNavigate]);

  const err = q.error instanceof ApiError ? q.error : null;
  const nav =
    "inline-flex size-11 items-center justify-center rounded-control border border-line text-ink hover:bg-surface-2 disabled:opacity-40 md:size-9";

  return (
    <Dialog.Root open onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40" style={{ background: "var(--overlay)" }} />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-line bg-surface-1 shadow-xl md:w-[560px]"
        >
          <div className="flex items-center gap-2 border-b border-line px-4 py-3">
            <Dialog.Title className="text-xl font-semibold text-ink">
              Detalle del evento
            </Dialog.Title>
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                className={nav}
                disabled={!prevId}
                onClick={() => prevId && onNavigate(prevId)}
                aria-label="Evento anterior (←)"
              >
                <ChevronLeft aria-hidden className="size-4" />
              </button>
              <button
                type="button"
                className={nav}
                disabled={!nextId}
                onClick={() => nextId && onNavigate(nextId)}
                aria-label="Evento siguiente (→)"
              >
                <ChevronRight aria-hidden className="size-4" />
              </button>
              <Dialog.Close className={nav} aria-label="Cerrar detalle">
                <X aria-hidden className="size-4" />
              </Dialog.Close>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {q.isPending ? (
              <div className="space-y-4" aria-busy="true">
                <Skeleton className="w-full" label="Cargando evento" />
                <Skeleton className="h-40 w-full" />
              </div>
            ) : err?.status === 404 ? (
              <EmptyState
                title="Evento no encontrado"
                message="El enlace no corresponde a ningún evento. Puede que el ID esté incompleto."
              />
            ) : err?.status === 403 ? (
              <ErrorState
                title="Sin permiso"
                message="Tu cuenta no puede ver este evento. Pide acceso al administrador."
              />
            ) : q.isError ? (
              <ErrorState message="No se pudo cargar el evento." onRetry={() => void q.refetch()} />
            ) : (
              <Body ev={q.data} />
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
