import * as Tabs from "@radix-ui/react-tabs";
import { hasRole, useAuth } from "@/auth/AuthProvider";
import { ErrorState } from "@/components/ErrorState";
import { AuditTab } from "./AuditTab";
import { UsersTab } from "./UsersTab";

const trigger =
  "min-h-11 border-b-2 border-transparent px-4 text-sm text-ink-2 data-[state=active]:border-[var(--accent)] data-[state=active]:font-medium data-[state=active]:text-ink";

/** Pantalla 7: solo administradores. Si la ruta se abre sin ese rol (p. ej. rol revocado en
 * caliente) se muestra "Sin permiso", no un error genérico. */
export default function AdminPage() {
  const { user } = useAuth();
  if (!hasRole(user?.role, "admin")) {
    return (
      <ErrorState
        title="Sin permiso"
        message="Tu cuenta no tiene acceso a esta sección. Pide acceso al administrador."
      />
    );
  }
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold text-ink">Administración</h1>
      <Tabs.Root defaultValue="usuarios">
        <Tabs.List aria-label="Secciones de administración" className="flex border-b border-line">
          <Tabs.Trigger value="usuarios" className={trigger}>
            Usuarios
          </Tabs.Trigger>
          <Tabs.Trigger value="auditoria" className={trigger}>
            Auditoría
          </Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="usuarios" className="pt-4">
          <UsersTab />
        </Tabs.Content>
        <Tabs.Content value="auditoria" className="pt-4">
          <AuditTab />
        </Tabs.Content>
      </Tabs.Root>
    </div>
  );
}
