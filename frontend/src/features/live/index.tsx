import { PauseCircle, PlayCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useDevices, useEvents, useSetDesiredState } from "@/api/queries";
import type { DeviceStatus } from "@/api/types";
import { hasRole, useAuth } from "@/auth/AuthProvider";
import { ClassChip } from "@/components/ClassChip";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useDevice } from "@/components/DeviceContext";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { LivePlayer } from "@/components/LivePlayer";
import { Skeleton } from "@/components/Skeleton";
import { StatusPill } from "@/components/StatusPill";
import { useToast } from "@/components/Toast";
import { formatInt, formatLatency, formatRelative, formatTemp } from "@/design/format";

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3 py-1 text-sm">
      <dt className="text-ink-2">{label}</dt>
      <dd className="tabular text-right text-ink">{value}</dd>
    </div>
  );
}

function HealthPanel({ d }: { d: DeviceStatus }) {
  const h = d.health;
  const fps = h.inference_fps != null ? `${h.inference_fps.toFixed(1)} fps` : "—";
  const p50 = h.inference_ms_p50 != null ? formatLatency(h.inference_ms_p50) : "—";
  return (
    <section aria-labelledby="salud" className="rounded-card border border-line bg-surface-1 p-4">
      <h2 id="salud" className="mb-2 text-base font-semibold text-ink">
        Estado del dispositivo
      </h2>
      <div className="mb-2">
        <StatusPill state={d.state} />
      </div>
      <dl>
        <Metric label="Inferencia" value={`${fps} · p50 ${p50}`} />
        <Metric label="Temperatura" value={h.cpu_temp_c != null ? formatTemp(h.cpu_temp_c) : "—"} />
        <Metric
          label="Pendientes de subir"
          value={h.outbox_pending != null ? formatInt(h.outbox_pending) : "—"}
        />
        <Metric
          label="Modelo"
          value={`${h.model_version ?? "—"}${h.edge_version ? ` · v${h.edge_version}` : ""}`}
        />
        <Metric label="Última señal" value={d.last_seen ? formatRelative(d.last_seen) : "—"} />
      </dl>
    </section>
  );
}

function RecentDetections({ deviceId }: { deviceId: string }) {
  const q = useEvents(deviceId, { limit: 5 });
  const items = q.data?.pages[0]?.items.slice(0, 5) ?? [];
  return (
    <section
      aria-labelledby="recientes"
      className="rounded-card border border-line bg-surface-1 p-4"
    >
      <h2 id="recientes" className="mb-2 text-base font-semibold text-ink">
        Detecciones recientes
      </h2>
      {q.isPending ? (
        <Skeleton className="h-24 w-full" label="Cargando detecciones" />
      ) : q.isError ? (
        <ErrorState
          title="Sin datos"
          message="No se pudieron traer las detecciones."
          onRetry={() => void q.refetch()}
        />
      ) : items.length === 0 ? (
        <p className="text-sm text-ink-2">Todavía no hay detecciones.</p>
      ) : (
        <ul className="space-y-2">
          {items.map((e) => (
            <li key={e.event_id}>
              <Link
                to={`/events/${e.event_id}`}
                className="flex items-center gap-3 rounded-control p-1 hover:bg-surface-2"
              >
                {e.capture_status === "ok" && e.thumb_url ? (
                  <img
                    src={e.thumb_url}
                    alt=""
                    loading="lazy"
                    decoding="async"
                    className="h-9 w-16 shrink-0 rounded-control object-cover"
                    style={{ aspectRatio: "16 / 9" }}
                  />
                ) : (
                  <span
                    aria-hidden
                    className="h-9 w-16 shrink-0 rounded-control bg-surface-2"
                    style={{ aspectRatio: "16 / 9" }}
                  />
                )}
                <ClassChip name={e.nombre_clase} />
                <span className="ml-auto text-xs text-ink-2">{formatRelative(e.end_ts)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function LivePage() {
  const { deviceId, isLoading } = useDevice();
  // En "En vivo" el estado del dispositivo se refresca cada 5 s (3.5). Comparte la clave con el shell.
  const devices = useDevices(true);
  const device = devices.data?.find((d) => d.id === deviceId);
  const { user } = useAuth();
  const { toast } = useToast();
  const setState = useSetDesiredState(deviceId);
  const [confirm, setConfirm] = useState(false);
  const canControl = hasRole(user?.role, "operator");

  if (isLoading || devices.isPending) {
    return <Skeleton className="h-96 w-full" label="Cargando" />;
  }
  if (devices.isError) {
    const forbidden = devices.error instanceof ApiError && devices.error.status === 403;
    return (
      <ErrorState
        title={forbidden ? "Sin permiso" : "No se pudo cargar"}
        message={
          forbidden
            ? "Tu cuenta no puede ver el video. Pide acceso al administrador."
            : "No pudimos consultar el dispositivo. Inténtalo de nuevo."
        }
        onRetry={forbidden ? undefined : () => void devices.refetch()}
      />
    );
  }
  if (!device) {
    return (
      <EmptyState
        title="Sin dispositivos"
        message="No hay ningún dispositivo registrado para mostrar en vivo."
      />
    );
  }

  const running = device.desired_state === "running";

  const change = (state: "running" | "stopped") =>
    setState.mutate(state, {
      onSuccess: () => {
        setConfirm(false);
        toast(
          state === "stopped"
            ? `Detección pausada por ${user?.email ?? "un operador"}`
            : "Detección reanudada",
          "success",
        );
      },
      onError: (e) => {
        setConfirm(false);
        toast(e instanceof Error ? e.message : "No se pudo cambiar el estado", "error");
      },
    });

  const action = canControl ? (
    running ? (
      <button
        type="button"
        onClick={() => setConfirm(true)}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-control border border-line bg-surface-2 px-3 text-sm font-medium text-ink md:min-h-9"
      >
        <PauseCircle aria-hidden className="size-4" /> Pausar detección
      </button>
    ) : (
      <button
        type="button"
        disabled={setState.isPending}
        onClick={() => change("running")}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-control bg-accent-solid px-3 text-sm font-medium text-white disabled:opacity-60 md:min-h-9"
      >
        <PlayCircle aria-hidden className="size-4" /> Reanudar detección
      </button>
    )
  ) : null;

  return (
    <section aria-labelledby="titulo" className="space-y-4">
      <h1 id="titulo" className="text-2xl font-semibold text-ink">
        En vivo
      </h1>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <LivePlayer deviceId={device.id} actions={action} />
        <div className="space-y-4">
          <HealthPanel d={device} />
          <RecentDetections deviceId={device.id} />
        </div>
      </div>
      <ConfirmDialog
        open={confirm}
        onOpenChange={setConfirm}
        title="Pausar la detección"
        description="La cámara dejará de registrar eventos hasta que alguien la reanude. ¿Continuar?"
        confirmLabel="Pausar"
        busy={setState.isPending}
        onConfirm={() => change("stopped")}
      />
    </section>
  );
}
