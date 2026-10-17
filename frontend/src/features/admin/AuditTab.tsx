import { useState, type FormEvent } from "react";
import { useAudit } from "@/api/queries";
import { EmptyState } from "@/components/EmptyState";
import { QueryErrorState } from "@/components/QueryErrorState";
import { Skeleton } from "@/components/Skeleton";
import { formatDateTime } from "@/design/format";

const field =
  "min-h-11 rounded-control border border-line bg-surface-2 px-3 text-sm text-ink placeholder:text-ink-3";

/** Nombres legibles de las acciones que registra el backend; las desconocidas se muestran tal cual. */
export const ACTION_LABEL: Record<string, string> = {
  login: "Inicio de sesión",
  login_denied: "Acceso denegado",
  live_start: "Inicio de video en vivo",
  live_end: "Fin de video en vivo",
  desired_state: "Cambio de estado de detección",
  capture_original: "Descarga de captura original",
  user_create: "Alta de usuario",
  user_update: "Cambio de usuario",
  user_delete: "Baja de usuario",
};

export function AuditTab() {
  const [draft, setDraft] = useState({ action: "", email: "" });
  const [applied, setApplied] = useState<{ action?: string; email?: string }>({});
  const audit = useAudit(applied);

  const apply = (e: FormEvent) => {
    e.preventDefault();
    const action = draft.action.trim();
    const email = draft.email.trim().toLowerCase();
    setApplied({ ...(action ? { action } : {}), ...(email ? { email } : {}) });
  };

  const rows = audit.data?.pages.flatMap((p) => p.items) ?? [];

  let list;
  if (audit.isPending)
    list = (
      <div aria-busy="true" className="flex flex-col gap-2">
        <Skeleton className="h-11" label="Cargando auditoría" />
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-11" />
        ))}
      </div>
    );
  else if (audit.isError)
    list = <QueryErrorState error={audit.error} onRetry={() => void audit.refetch()} />;
  else if (rows.length === 0)
    list = (
      <EmptyState
        title="Sin registros"
        message="No hay acciones registradas con estos filtros. Prueba quitando algún filtro."
      />
    );
  else
    list = (
      <>
        <div className="relative overflow-x-auto rounded-card border border-line">
          <table className="tabular w-full text-sm">
            <caption className="sr-only">Registro de auditoría</caption>
            <thead className="bg-surface-2 text-left text-ink-2">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">
                  Fecha (hora de Lima)
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Usuario
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Acción
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Objetivo
                </th>
                <th scope="col" className="px-3 py-2 font-medium">
                  Detalle
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-line">
                  <th scope="row" className="px-3 py-2 text-left font-normal text-ink">
                    {formatDateTime(r.ts)}
                  </th>
                  <td className="px-3 py-2 text-ink">{r.email}</td>
                  <td className="px-3 py-2 text-ink" title={r.action}>
                    {ACTION_LABEL[r.action] ?? r.action}
                  </td>
                  <td className="px-3 py-2 text-ink-2">{r.target ?? "—"}</td>
                  <td className="px-3 py-2 text-ink-2">{r.detail ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {audit.hasNextPage && (
          <button
            type="button"
            disabled={audit.isFetchingNextPage}
            onClick={() => void audit.fetchNextPage()}
            className="min-h-11 self-center rounded-control border border-line bg-surface-2 px-4 text-sm font-medium text-ink"
          >
            {audit.isFetchingNextPage ? "Cargando…" : "Cargar más"}
          </button>
        )}
      </>
    );

  return (
    <div className="flex flex-col gap-4">
      <form
        onSubmit={apply}
        className="flex flex-col gap-3 rounded-card border border-line bg-surface-1 p-4 sm:flex-row sm:items-end"
      >
        <label className="flex flex-col gap-1 text-sm text-ink-2">
          Acción
          <input
            list="acciones"
            value={draft.action}
            onChange={(e) => setDraft({ ...draft, action: e.target.value })}
            placeholder="Todas"
            className={field}
          />
        </label>
        <datalist id="acciones">
          {Object.entries(ACTION_LABEL).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </datalist>
        <label className="flex flex-1 flex-col gap-1 text-sm text-ink-2">
          Usuario
          <input
            type="email"
            value={draft.email}
            onChange={(e) => setDraft({ ...draft, email: e.target.value })}
            placeholder="Todos"
            className={field}
          />
        </label>
        <button
          type="submit"
          className="min-h-11 rounded-control border border-line bg-surface-2 px-4 text-sm font-medium text-ink"
        >
          Filtrar
        </button>
      </form>
      {list}
    </div>
  );
}
