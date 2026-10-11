import { useState } from "react";
import { Link } from "react-router-dom";
import { useEvents, useSummary } from "@/api/queries";
import type { DeviceStatus, SummaryRange } from "@/api/types";
import { ActivityChart } from "@/components/ActivityChart";
import { ClassBreakdown } from "@/components/ClassBreakdown";
import { useDevice } from "@/components/DeviceContext";
import { EmptyState } from "@/components/EmptyState";
import { QueryErrorState } from "@/components/QueryErrorState";
import { Skeleton } from "@/components/Skeleton";
import { StatTile } from "@/components/StatTile";
import { StatusPill } from "@/components/StatusPill";
import { classLabel } from "@/design/classColors";
import {
  formatDateTime,
  formatDuration,
  formatInt,
  formatPercent,
  formatRelative,
  formatTemp,
} from "@/design/format";
import { pointsDelta, relativeDelta } from "./kpis";
import { RecentEvents } from "./RecentEvents";

const RANGES: { value: SummaryRange; label: string }[] = [
  { value: "24h", label: "24 h" },
  { value: "7d", label: "7 días" },
  { value: "30d", label: "30 días" },
];

function DeviceCard({ device }: { device: DeviceStatus }) {
  const h = device.health;
  const parts = [
    device.last_seen ? `última señal ${formatRelative(device.last_seen)}` : "sin señal registrada",
    h.inference_fps != null ? `${h.inference_fps.toFixed(1)} fps` : null,
    h.cpu_temp_c != null ? formatTemp(h.cpu_temp_c) : null,
  ].filter(Boolean);
  return (
    <section
      aria-label="Estado del dispositivo"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-card border border-line bg-surface-1 p-4"
    >
      <span aria-live="polite">
        <StatusPill state={device.state} />
      </span>
      <span className="text-sm text-ink">{device.name}</span>
      <span className="text-sm text-ink-2">· {parts.join(" · ")}</span>
      <Link to="/live" className="ml-auto min-h-11 content-center text-sm font-medium text-accent">
        Ver en vivo →
      </Link>
    </section>
  );
}

function RangeSelector({
  value,
  onChange,
}: {
  value: SummaryRange;
  onChange: (r: SummaryRange) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2">
      <div
        role="group"
        aria-label="Periodo"
        className="flex rounded-control border border-line p-0.5 text-sm"
      >
        {RANGES.map((r) => (
          <button
            key={r.value}
            type="button"
            aria-pressed={value === r.value}
            onClick={() => onChange(r.value)}
            className={`min-h-9 rounded-[4px] px-4 ${value === r.value ? "bg-surface-2 font-medium text-ink" : "text-ink-2"}`}
          >
            {r.label}
          </button>
        ))}
      </div>
      <p className="text-sm text-ink-3">hora de Lima</p>
    </div>
  );
}

function SummarySkeleton() {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <StatTile key={i} label="Cargando…" value="" loading />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
        <Skeleton className="h-80" label="Cargando la actividad" />
        <Skeleton className="h-80" />
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const dev = useDevice();
  const [range, setRange] = useState<SummaryRange>("7d");
  const summary = useSummary(dev.deviceId, range);
  const recent = useEvents(dev.deviceId, { limit: 6 });

  let deviceBlock;
  if (dev.isLoading)
    deviceBlock = <Skeleton className="h-[74px]" label="Cargando el dispositivo" />;
  else if (dev.isError)
    deviceBlock = (
      <QueryErrorState
        error={null}
        onRetry={dev.refetch}
        title="No se pudo cargar el dispositivo"
      />
    );
  else if (!dev.device)
    deviceBlock = (
      <EmptyState
        title="Aún no hay dispositivos"
        message="Cuando el administrador registre un dispositivo, aparecerá aquí con su estado."
      />
    );
  else deviceBlock = <DeviceCard device={dev.device} />;

  const s = summary.data;
  const k = s?.kpis;
  const uncovered =
    s && k?.capture_coverage.value != null && s.sample.events > 0
      ? Math.round(s.sample.events * (1 - k.capture_coverage.value))
      : 0;

  let body;
  if (!dev.deviceId) body = null;
  else if (summary.isError && !s)
    body = <QueryErrorState error={summary.error} onRetry={() => void summary.refetch()} />;
  else if (!s) body = <SummarySkeleton />;
  else if (k?.events.value === 0 && s.series.every((b) => b.total === 0))
    body = (
      <EmptyState
        title="Sin eventos en este periodo"
        message="El dispositivo no confirmó detecciones en este periodo. Prueba con un periodo más largo o revisa que esté detectando."
        action={
          <Link to="/device" className="text-sm font-medium text-accent">
            Ver el estado del dispositivo
          </Link>
        }
      />
    );
  else
    body = (
      <>
        {(s.partial || !s.sample.complete) && (
          <p className="rounded-control border border-line bg-surface-2 p-3 text-sm text-ink-2">
            {s.partial
              ? "Datos parciales: el periodo no está cubierto del todo por la información disponible. "
              : ""}
            {!s.sample.complete
              ? `La confianza media y la cobertura de capturas se calculan sobre los últimos ${formatInt(s.sample.events)} eventos.`
              : ""}
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatTile
            label="Eventos"
            value={k?.events.value != null ? formatInt(k.events.value) : "—"}
            delta={relativeDelta(k?.events.value, k?.events.previous, range)}
            sparkline={s.series.map((b) => b.total)}
            sparklineLabel={`Eventos por ${range === "24h" ? "hora" : "día"} en el periodo`}
            to={`/events?rango=${range}`}
          />
          <StatTile
            label="Tiempo con EPP"
            value={k?.epp_seconds.value != null ? formatDuration(k.epp_seconds.value) : "—"}
            delta={relativeDelta(k?.epp_seconds.value, k?.epp_seconds.previous, range)}
            to={`/events?rango=${range}`}
          />
          <StatTile
            label="Confianza media"
            value={k?.avg_confidence.value != null ? formatPercent(k.avg_confidence.value) : "—"}
            delta={pointsDelta(k?.avg_confidence.value, k?.avg_confidence.previous, range)}
            hint={
              k?.top_class.nombre_clase
                ? `Clase más frecuente: ${classLabel(k.top_class.nombre_clase)}`
                : undefined
            }
          />
          <StatTile
            label="Cobertura de capturas"
            value={
              k?.capture_coverage.value != null ? formatPercent(k.capture_coverage.value, 1) : "—"
            }
            delta={pointsDelta(k?.capture_coverage.value, k?.capture_coverage.previous, range)}
            hint={
              uncovered > 0 ? (
                <Link to={`/events?captura=falta&rango=${range}`} className="text-accent">
                  {formatInt(uncovered)} sin captura → ver
                </Link>
              ) : undefined
            }
          />
        </div>
        <div className="grid gap-4 lg:grid-cols-[2fr_1fr]">
          <ActivityChart series={s.series} range={range} />
          <ClassBreakdown
            items={s.by_class}
            hrefFor={(c) => `/events?clase=${encodeURIComponent(c)}&rango=${range}`}
          />
        </div>
      </>
    );

  const items = recent.data?.pages[0]?.items ?? [];
  let recentBlock;
  if (!dev.deviceId) recentBlock = null;
  else if (recent.isError)
    recentBlock = <QueryErrorState error={recent.error} onRetry={() => void recent.refetch()} />;
  else if (recent.isPending)
    recentBlock = (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="aspect-video" />
        ))}
      </div>
    );
  else if (items.length === 0)
    recentBlock = (
      <EmptyState
        title="Todavía no hay eventos"
        message="Los eventos aparecerán aquí cuando el dispositivo confirme una detección."
      />
    );
  else recentBlock = <RecentEvents items={items} />;

  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-ink">Resumen</h1>
      {deviceBlock}
      {dev.deviceId && <RangeSelector value={range} onChange={setRange} />}
      {body}
      {dev.deviceId && (
        <section aria-labelledby="ultimos" className="flex flex-col gap-3">
          <div className="flex items-center justify-between">
            <h2 id="ultimos" className="text-xl font-semibold text-ink">
              Últimos eventos
            </h2>
            <Link to="/events" className="min-h-11 content-center text-sm font-medium text-accent">
              Ver todos →
            </Link>
          </div>
          {recentBlock}
          {items[0] && (
            <p className="text-xs text-ink-3">
              Último evento: {formatDateTime(items[0].end_ts)} (hora de Lima)
            </p>
          )}
        </section>
      )}
    </div>
  );
}
