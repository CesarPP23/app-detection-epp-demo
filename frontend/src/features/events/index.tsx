import { useCallback, useMemo } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useEvents } from "@/api/queries";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { EventCard } from "@/components/EventCard";
import { EventDetailPanel } from "@/components/EventDetailPanel";
import { EventTable } from "@/components/EventTable";
import { FilterBar } from "@/components/FilterBar";
import { useDevice } from "@/components/DeviceContext";
import { Skeleton } from "@/components/Skeleton";
import {
  clientFilter,
  hasActiveFilters,
  parseFilters,
  resolveRange,
  serializeFilters,
  sortItems,
  toQuery,
  type EventFilters,
  type SortKey,
} from "./filters";

function ListSkeleton({ view }: { view: EventFilters["view"] }) {
  return view === "galeria" ? (
    <div
      className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
      aria-busy="true"
      aria-label="Cargando eventos"
    >
      {Array.from({ length: 8 }, (_, i) => (
        <Skeleton key={i} className="h-56 w-full" />
      ))}
    </div>
  ) : (
    <Skeleton className="h-72 w-full" label="Cargando eventos" />
  );
}

export default function EventsPage() {
  const { deviceId, isLoading: deviceLoading } = useDevice();
  const { eventId } = useParams();
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const filters = useMemo(() => parseFilters(sp), [sp]);

  // Los presets relativos se resuelven una vez por cambio de filtros (clave de caché estable).
  const spKey = sp.toString();
  const query = useMemo(
    () => toQuery(filters, resolveRange(filters, Date.now())),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [spKey],
  );
  const events = useEvents(deviceId, query);

  const apply = useCallback(
    (next: EventFilters) => setSp(serializeFilters(next, sp), { replace: true }),
    [sp, setSp],
  );

  const all = useMemo(() => events.data?.pages.flatMap((p) => p.items) ?? [], [events.data]);
  const items = useMemo(() => sortItems(clientFilter(all, filters), filters), [all, filters]);
  const extraClasses = useMemo(() => [...new Set(all.map((e) => e.nombre_clase))], [all]);

  const search = sp.toString() ? `?${sp.toString()}` : "";
  const hrefFor = useCallback((id: string) => `/events/${id}${search}`, [search]);
  const closeDetail = useCallback(() => nav(`/events${search}`), [nav, search]);
  const goTo = useCallback((id: string) => nav(hrefFor(id), { replace: true }), [nav, hrefFor]);

  const idx = eventId ? items.findIndex((e) => e.event_id === eventId) : -1;
  const prevId = idx > 0 ? items[idx - 1]?.event_id : undefined;
  const nextId = idx >= 0 ? items[idx + 1]?.event_id : undefined;

  const onSort = (key: SortKey) =>
    apply({
      ...filters,
      sort: key,
      dir: filters.sort === key ? (filters.dir === "desc" ? "asc" : "desc") : "desc",
    });

  const forbidden = events.error instanceof ApiError && events.error.status === 403;

  let body;
  if (deviceLoading || events.isPending) {
    body = <ListSkeleton view={filters.view} />;
  } else if (forbidden) {
    body = (
      <ErrorState
        title="Sin permiso"
        message="Tu cuenta no puede ver los eventos. Pide acceso al administrador."
      />
    );
  } else if (events.isError) {
    body = (
      <ErrorState
        message="No pudimos traer los eventos. Revisa tu conexión e inténtalo de nuevo."
        onRetry={() => void events.refetch()}
      />
    );
  } else if (items.length === 0) {
    body = (
      <EmptyState
        title={hasActiveFilters(filters) ? "Ningún evento coincide" : "Aún no hay eventos"}
        message={
          hasActiveFilters(filters)
            ? "Prueba con otro rango de fechas o quita algún filtro."
            : "Cuando el dispositivo confirme una detección, aparecerá aquí con su captura."
        }
        action={
          events.hasNextPage ? (
            <button
              type="button"
              onClick={() => void events.fetchNextPage()}
              className="min-h-11 rounded-control border border-line bg-surface-2 px-4 text-sm text-ink"
            >
              Buscar en eventos más antiguos
            </button>
          ) : undefined
        }
      />
    );
  } else if (filters.view === "galeria") {
    body = (
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        {items.map((e) => (
          <li key={e.event_id}>
            <EventCard event={e} to={hrefFor(e.event_id)} />
          </li>
        ))}
      </ul>
    );
  } else {
    body = (
      <EventTable
        events={items}
        sort={filters.sort}
        dir={filters.dir}
        density={filters.density}
        onSort={onSort}
        hrefFor={hrefFor}
      />
    );
  }

  return (
    <section aria-labelledby="titulo" className="space-y-4">
      <div>
        <h1 id="titulo" className="text-2xl font-semibold text-ink">
          Eventos
        </h1>
        <p className="mt-1 text-sm text-ink-2">
          Detecciones confirmadas con su captura · hora de Lima
        </p>
      </div>

      <FilterBar
        filters={filters}
        onChange={apply}
        extraClasses={extraClasses}
        onSearchId={(id) => nav(hrefFor(id))}
        onReset={hasActiveFilters(filters) ? () => setSp({}, { replace: true }) : undefined}
      />

      <div aria-live="polite">{body}</div>

      {events.hasNextPage && items.length > 0 && (
        <div className="flex justify-center">
          <button
            type="button"
            onClick={() => void events.fetchNextPage()}
            disabled={events.isFetchingNextPage}
            className="min-h-11 rounded-control border border-line bg-surface-2 px-6 text-sm font-medium text-ink disabled:opacity-60"
          >
            {events.isFetchingNextPage ? "Cargando…" : "Cargar más"}
          </button>
        </div>
      )}

      {eventId && (
        <EventDetailPanel
          eventId={eventId}
          onClose={closeDetail}
          prevId={prevId}
          nextId={nextId}
          onNavigate={goTo}
        />
      )}
    </section>
  );
}
