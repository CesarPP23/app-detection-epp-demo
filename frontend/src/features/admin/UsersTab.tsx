import { useState, type FormEvent } from "react";
import { ApiError } from "@/api/client";
import { useUserMutations, useUsers } from "@/api/queries";
import type { Role, UserOut } from "@/api/types";
import { useAuth } from "@/auth/AuthProvider";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { QueryErrorState } from "@/components/QueryErrorState";
import { Skeleton } from "@/components/Skeleton";
import { useToast } from "@/components/Toast";
import { formatDateTime } from "@/design/format";

export const ROLE_LABEL: Record<Role, string> = {
  viewer: "Observador",
  operator: "Operador",
  admin: "Administrador",
};
const ROLES: Role[] = ["viewer", "operator", "admin"];

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/** Email válido y en minúsculas (la clave de dashboard_users). Devuelve null si no es válido. */
export function normalizeEmail(raw: string): string | null {
  const email = raw.trim().toLowerCase();
  return EMAIL_RE.test(email) ? email : null;
}

const field =
  "min-h-11 rounded-control border border-line bg-surface-2 px-3 text-sm text-ink placeholder:text-ink-3";

function AddUser() {
  const { create } = useUserMutations();
  const { toast } = useToast();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [error, setError] = useState<string | null>(null);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const clean = normalizeEmail(email);
    if (!clean) {
      setError("Escribe un correo válido, por ejemplo nombre@empresa.com.");
      return;
    }
    setError(null);
    create.mutate(
      { email: clean, role },
      {
        onSuccess: () => {
          setEmail("");
          toast(`Se dio acceso a ${clean}`, "success");
        },
        onError: (err) =>
          setError(err instanceof ApiError ? err.message : "No se pudo agregar al usuario."),
      },
    );
  };

  return (
    <form
      onSubmit={submit}
      noValidate
      className="flex flex-col gap-3 rounded-card border border-line bg-surface-1 p-4 sm:flex-row sm:items-end"
    >
      <label className="flex flex-1 flex-col gap-1 text-sm text-ink-2">
        Correo de Google
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="nombre@empresa.com"
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? "error-alta" : undefined}
          className={field}
        />
      </label>
      <label className="flex flex-col gap-1 text-sm text-ink-2">
        Rol
        <select value={role} onChange={(e) => setRole(e.target.value as Role)} className={field}>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
      </label>
      <button
        type="submit"
        disabled={create.isPending}
        className="min-h-11 rounded-control px-4 text-sm font-medium text-white disabled:opacity-60"
        style={{ background: "var(--accent)" }}
      >
        Agregar usuario
      </button>
      {error && (
        <p id="error-alta" role="alert" className="text-sm text-critical sm:basis-full">
          {error}
        </p>
      )}
    </form>
  );
}

export function UsersTab() {
  const users = useUsers();
  const { user: me } = useAuth();
  const { patch } = useUserMutations();
  const { toast } = useToast();
  const [target, setTarget] = useState<UserOut | null>(null);

  const fail = (e: unknown) =>
    toast(e instanceof ApiError ? e.message : "No se pudo guardar el cambio.", "error");
  const setRole = (u: UserOut, role: Role) =>
    patch.mutate({ email: u.email, role }, { onError: fail });
  const setActive = (u: UserOut, active: boolean) =>
    patch.mutate(
      { email: u.email, active },
      {
        onSuccess: () => {
          setTarget(null);
          toast(
            active ? `${u.email} puede entrar de nuevo` : `${u.email} ya no tiene acceso`,
            "success",
          );
        },
        onError: (e) => {
          setTarget(null);
          fail(e);
        },
      },
    );

  let list;
  if (users.isPending)
    list = (
      <div aria-busy="true" className="flex flex-col gap-2">
        <Skeleton className="h-11" label="Cargando usuarios" />
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-11" />
        ))}
      </div>
    );
  else if (users.isError)
    list = <QueryErrorState error={users.error} onRetry={() => void users.refetch()} />;
  else if (users.data.length === 0)
    list = (
      <EmptyState
        title="Todavía no hay usuarios"
        message="Agrega el correo de Google de quien deba entrar al panel. Sin acceso autorizado, nadie puede ver los datos."
      />
    );
  else
    list = (
      <div className="overflow-x-auto rounded-card border border-line">
        <table className="w-full text-sm">
          <caption className="sr-only">Usuarios con acceso al panel</caption>
          <thead className="bg-surface-2 text-left text-ink-2">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">
                Correo
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Rol
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Estado
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Alta
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                <span className="sr-only">Acciones</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {users.data.map((u) => {
              const self = me?.email === u.email;
              return (
                <tr key={u.email} className="border-t border-line">
                  <th scope="row" className="px-3 py-2 text-left font-normal text-ink">
                    {u.email}
                    {self && <span className="ml-2 text-xs text-ink-2">(tú)</span>}
                  </th>
                  <td className="px-3 py-2">
                    <select
                      aria-label={`Rol de ${u.email}`}
                      value={u.role}
                      disabled={self || patch.isPending}
                      title={self ? "No puedes cambiar tu propio rol" : undefined}
                      onChange={(e) => setRole(u, e.target.value as Role)}
                      className={field}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r}>
                          {ROLE_LABEL[r]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-3 py-2 text-ink">{u.active ? "Activo" : "Desactivado"}</td>
                  <td className="px-3 py-2 text-ink-2">
                    {u.created_at ? formatDateTime(u.created_at) : "—"}
                  </td>
                  <td className="px-3 py-2 text-right">
                    {u.active ? (
                      <button
                        type="button"
                        disabled={self}
                        title={self ? "No puedes desactivarte a ti mismo" : undefined}
                        onClick={() => setTarget(u)}
                        className="min-h-11 rounded-control border border-line px-3 text-sm text-ink hover:bg-surface-2 disabled:opacity-50"
                      >
                        Desactivar
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setActive(u, true)}
                        className="min-h-11 rounded-control border border-line px-3 text-sm text-ink hover:bg-surface-2"
                      >
                        Reactivar
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );

  return (
    <div className="flex flex-col gap-4">
      <AddUser />
      {list}
      <ConfirmDialog
        open={target !== null}
        onOpenChange={(o) => !o && setTarget(null)}
        title="Desactivar usuario"
        description={
          <>
            {target?.email} dejará de poder entrar al panel en menos de un minuto. El sistema no
            permite quedarse sin administradores.
          </>
        }
        confirmLabel="Desactivar"
        destructive
        busy={patch.isPending}
        onConfirm={() => target && setActive(target, false)}
      />
    </div>
  );
}
