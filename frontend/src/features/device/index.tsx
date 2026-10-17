import {
  AlertCircle,
  AlertTriangle,
  Camera,
  CheckCircle2,
  Download,
  Info,
  Square,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { ApiError } from "@/api/client";
import {
  useSetDesiredState,
  useStartTrainingCapture,
  useStopTrainingCapture,
  useTrainingSessions,
} from "@/api/queries";
import type { DeviceStatus, TrainingSessionSummary } from "@/api/types";
import { hasRole, useAuth } from "@/auth/AuthProvider";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useDevice } from "@/components/DeviceContext";
import { EmptyState } from "@/components/EmptyState";
import { QueryErrorState } from "@/components/QueryErrorState";
import { Skeleton } from "@/components/Skeleton";
import { StatusPill } from "@/components/StatusPill";
import { useToast } from "@/components/Toast";
import {
  formatDateTime,
  formatDuration,
  formatInt,
  formatLatency,
  formatRelative,
  formatTemp,
} from "@/design/format";
import { diagnostics, outboxLabel, outboxTone, tempLabel, tempTone, type Tone } from "./health";

const TONE_STYLE: Record<Tone, { icon: LucideIcon; color: string }> = {
  good: { icon: CheckCircle2, color: "var(--status-good)" },
  warning: { icon: AlertTriangle, color: "var(--status-warning)" },
  serious: { icon: AlertTriangle, color: "var(--status-serious)" },
  critical: { icon: AlertCircle, color: "var(--status-critical)" },
  neutral: { icon: Info, color: "var(--text-muted)" },
};

/** Tarjeta de salud: estado siempre con ícono + texto + color, nunca solo color. */
function HealthCard({
  title,
  value,
  tone = "neutral",
  status,
  children,
}: {
  title: string;
  value: ReactNode;
  tone?: Tone;
  status?: string;
  children?: ReactNode;
}) {
  const { icon: Icon, color } = TONE_STYLE[tone];
  return (
    <div className="rounded-card border border-line bg-surface-1 p-4">
      <h3 className="text-sm text-ink-2">{title}</h3>
      <p className="mt-1 text-xl font-semibold text-ink">{value}</p>
      {status && (
        <p className="mt-1 flex items-center gap-1.5 text-sm text-ink">
          <Icon aria-hidden className="size-4" style={{ color }} />
          {status}
        </p>
      )}
      {children && <div className="mt-1 text-sm text-ink-2">{children}</div>}
    </div>
  );
}

const dash = "—";

function Cards({ d }: { d: DeviceStatus }) {
  const h = d.health;
  const temp = tempTone(h.cpu_temp_c);
  const outbox = outboxTone(h.outbox_pending);
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
      <HealthCard
        title="Inferencia"
        value={h.inference_fps != null ? `${h.inference_fps.toFixed(1)} fps` : dash}
      >
        {h.inference_ms_p50 != null && h.inference_ms_p95 != null
          ? `Mediana ${formatLatency(h.inference_ms_p50)} · p95 ${formatLatency(h.inference_ms_p95)}`
          : "Sin mediciones todavía"}
      </HealthCard>
      <HealthCard
        title="Temperatura de la CPU"
        value={h.cpu_temp_c != null ? formatTemp(h.cpu_temp_c) : dash}
        tone={temp}
        status={tempLabel(temp)}
      />
      <HealthCard
        title="Eventos pendientes de subir"
        value={h.outbox_pending != null ? formatInt(h.outbox_pending) : dash}
        tone={outbox}
        status={outboxLabel(h.outbox_pending)}
      />
      <HealthCard
        title="Última señal"
        value={d.last_seen ? formatRelative(d.last_seen) : "Sin señal"}
      >
        {d.last_seen ? formatDateTime(d.last_seen) : "El dispositivo aún no ha reportado."}
      </HealthCard>
      <HealthCard
        title="Cámara"
        value={d.camera_ok == null ? dash : d.camera_ok ? "Disponible" : "No disponible"}
        tone={d.camera_ok == null ? "neutral" : d.camera_ok ? "good" : "critical"}
        status={d.camera_ok == null ? "Sin dato" : d.camera_ok ? "Funcionando" : "Revisar"}
      >
        {h.camera_source ? `Fuente: ${h.camera_source}` : null}
      </HealthCard>
      <HealthCard title="Versiones" value={h.edge_version ? `v${h.edge_version}` : dash}>
        Modelo {h.model_version ?? dash}
      </HealthCard>
    </div>
  );
}

function Controls({ d }: { d: DeviceStatus }) {
  const { user } = useAuth();
  const { toast } = useToast();
  const mutation = useSetDesiredState(d.id);
  const [open, setOpen] = useState(false);

  if (!hasRole(user?.role, "operator")) {
    return (
      <p className="text-sm text-ink-2">
        Solo operadores y administradores pueden pausar o reanudar la detección.
      </p>
    );
  }
  const pausing = d.desired_state === "running";
  const confirm = () =>
    mutation.mutate(pausing ? "stopped" : "running", {
      onSuccess: () => {
        setOpen(false);
        toast(
          pausing
            ? `Detección pausada por ${user?.email ?? "ti"}`
            : `Detección reanudada por ${user?.email ?? "ti"}`,
          "success",
        );
      },
      onError: (e) => {
        setOpen(false);
        toast(
          e instanceof ApiError ? e.message : "No se pudo cambiar el estado de la detección.",
          "error",
        );
      },
    });

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="min-h-11 rounded-control border border-line bg-surface-2 px-4 text-sm font-medium text-ink hover:bg-surface-1"
      >
        {pausing ? "Pausar detección" : "Reanudar detección"}
      </button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title={pausing ? "Pausar la detección" : "Reanudar la detección"}
        description={
          pausing
            ? "La cámara dejará de registrar eventos hasta que alguien la reanude. ¿Continuar?"
            : "La cámara volverá a registrar eventos. ¿Continuar?"
        }
        confirmLabel={pausing ? "Pausar" : "Reanudar"}
        busy={mutation.isPending}
        onConfirm={confirm}
      />
    </>
  );
}

const CLOSED_REASON_LABEL: Record<string, string> = {
  stopped_by_operator: "Detenida",
  max_duration: "Se cortó sola tras 1 hora",
};

function TrainingCaptureControl({
  deviceId,
  active,
}: {
  deviceId: string;
  active: TrainingSessionSummary | undefined;
}) {
  const { user } = useAuth();
  const { toast } = useToast();
  const start = useStartTrainingCapture(deviceId);
  const stop = useStopTrainingCapture(deviceId);
  const [open, setOpen] = useState(false);

  if (!hasRole(user?.role, "operator")) {
    return (
      <p className="text-sm text-ink-2">
        Solo operadores y administradores pueden iniciar o detener la captura.
      </p>
    );
  }

  if (active) {
    const confirm = () =>
      stop.mutate(active.session_id, {
        onSuccess: () => {
          setOpen(false);
          toast("Captura detenida", "success");
        },
        onError: (e) => {
          setOpen(false);
          toast(e instanceof ApiError ? e.message : "No se pudo detener la captura.", "error");
        },
      });
    return (
      <>
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line bg-surface-2 px-4 text-sm font-medium text-ink hover:bg-surface-1"
        >
          <Square aria-hidden className="size-4" /> Detener captura
        </button>
        <ConfirmDialog
          open={open}
          onOpenChange={setOpen}
          title="Detener la captura"
          description="Se deja de subir fotos nuevas de esta sesión. Las que ya se subieron quedan disponibles para descargar."
          confirmLabel="Detener"
          busy={stop.isPending}
          onConfirm={confirm}
        />
      </>
    );
  }

  const confirm = () =>
    start.mutate(undefined, {
      onSuccess: () => {
        setOpen(false);
        toast("Captura iniciada: la cámara empieza a subir fotos para reentrenamiento", "success");
      },
      onError: (e) => {
        setOpen(false);
        toast(e instanceof ApiError ? e.message : "No se pudo iniciar la captura.", "error");
      },
    });
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line bg-surface-2 px-4 text-sm font-medium text-ink hover:bg-surface-1"
      >
        <Camera aria-hidden className="size-4" /> Capturar fotos para reentrenamiento
      </button>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        title="Iniciar captura para reentrenamiento"
        description="La cámara empieza a subir fotos sueltas (sin anotar) a la nube, hasta que la detengas o pase 1 hora. No afecta la detección en curso."
        confirmLabel="Iniciar"
        busy={start.isPending}
        onConfirm={confirm}
      />
    </>
  );
}

function TrainingSessionRow({ s, deviceId }: { s: TrainingSessionSummary; deviceId: string }) {
  const { user } = useAuth();
  const canDownload = hasRole(user?.role, "admin") && s.status === "completed" && s.frame_count > 0;
  const ended = s.ended_at ? new Date(s.ended_at) : new Date();
  const seconds = Math.max(0, Math.round((ended.getTime() - new Date(s.started_at).getTime()) / 1000));
  return (
    <tr className="border-t border-line">
      <th scope="row" className="px-3 py-2 text-left font-normal text-ink">
        {formatDateTime(s.started_at)}
      </th>
      <td className="px-3 py-2 text-ink-2">
        {s.status === "capturing" ? "Capturando…" : (CLOSED_REASON_LABEL[s.closed_reason ?? ""] ?? "Completa")}
      </td>
      <td className="px-3 py-2 text-ink">{formatDuration(seconds)}</td>
      <td className="px-3 py-2 text-ink">{formatInt(s.frame_count)} fotos</td>
      <td className="px-3 py-2 text-right">
        {canDownload && (
          <a
            href={`/api/devices/${deviceId}/training-captures/${s.session_id}/download`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line bg-surface-2 px-3 text-sm text-ink md:min-h-9"
          >
            <Download aria-hidden className="size-4" /> Descargar
          </a>
        )}
      </td>
    </tr>
  );
}

function TrainingCaptureSection({ deviceId }: { deviceId: string }) {
  const sessions = useTrainingSessions(deviceId);
  const items = sessions.data?.pages.flatMap((p) => p.items) ?? [];
  const active = items.find((s) => s.status === "capturing");

  return (
    <section
      aria-labelledby="training-capture"
      className="rounded-card border border-line bg-surface-1 p-4"
    >
      <div className="flex flex-wrap items-center gap-3">
        <h2 id="training-capture" className="text-base font-semibold text-ink">
          Captura para reentrenamiento
        </h2>
        <span className="ml-auto">
          <TrainingCaptureControl deviceId={deviceId} active={active} />
        </span>
      </div>
      {sessions.isPending ? (
        <Skeleton className="mt-3 h-11" label="Cargando sesiones de captura" />
      ) : sessions.isError ? (
        <QueryErrorState error={null} onRetry={() => void sessions.refetch()} />
      ) : items.length === 0 ? (
        <p className="mt-3 text-sm text-ink-2">Todavía no se capturó ninguna sesión.</p>
      ) : (
        <>
          <div className="relative mt-3 overflow-x-auto rounded-card border border-line">
            <table className="tabular w-full text-sm">
              <caption className="sr-only">Sesiones de captura para reentrenamiento</caption>
              <thead className="bg-surface-2 text-left text-ink-2">
                <tr>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Inicio
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Estado
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Duración
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium">
                    Fotos
                  </th>
                  <th scope="col" className="px-3 py-2 font-medium" />
                </tr>
              </thead>
              <tbody>
                {items.map((s) => (
                  <TrainingSessionRow key={s.session_id} s={s} deviceId={deviceId} />
                ))}
              </tbody>
            </table>
          </div>
          {sessions.hasNextPage && (
            <button
              type="button"
              disabled={sessions.isFetchingNextPage}
              onClick={() => void sessions.fetchNextPage()}
              className="mt-3 min-h-11 rounded-control border border-line bg-surface-2 px-4 text-sm font-medium text-ink"
            >
              {sessions.isFetchingNextPage ? "Cargando…" : "Cargar más"}
            </button>
          )}
        </>
      )}
    </section>
  );
}

export default function DevicePage() {
  const dev = useDevice();
  const d = dev.device;

  let body;
  if (dev.isLoading)
    body = (
      <div aria-busy="true" className="flex flex-col gap-4">
        <Skeleton className="h-16" label="Cargando el dispositivo" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      </div>
    );
  else if (dev.isError)
    body = (
      <QueryErrorState
        error={null}
        onRetry={dev.refetch}
        title="No se pudo cargar el dispositivo"
      />
    );
  else if (!d)
    body = (
      <EmptyState
        title="Aún no hay dispositivos"
        message="Cuando el administrador registre un dispositivo, aparecerá aquí con su estado."
      />
    );
  else {
    const notes = diagnostics(d);
    body = (
      <>
        <section
          aria-label="Estado"
          className="flex flex-wrap items-center gap-3 rounded-card border border-line bg-surface-1 p-4"
        >
          <span aria-live="polite">
            <StatusPill state={d.state} />
          </span>
          <span className="text-sm text-ink">{d.name}</span>
          <span className="ml-auto">
            <Controls d={d} />
          </span>
        </section>
        {notes.length > 0 && (
          <section
            aria-labelledby="diag"
            className="rounded-card border border-line bg-surface-1 p-4"
          >
            <h2 id="diag" className="text-base font-semibold text-ink">
              Qué revisar
            </h2>
            <ul className="mt-2 flex flex-col gap-2">
              {notes.map((n) => (
                <li key={n} className="flex gap-2 text-sm text-ink">
                  <Info aria-hidden className="mt-0.5 size-4 shrink-0 text-ink-2" />
                  {n}
                </li>
              ))}
            </ul>
          </section>
        )}
        <Cards d={d} />
        <TrainingCaptureSection deviceId={d.id} />
      </>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-ink">Dispositivo</h1>
      {body}
    </div>
  );
}
