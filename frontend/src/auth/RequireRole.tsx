import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import type { Role } from "@/api/types";
import { ErrorState } from "@/components/ErrorState";
import { Skeleton } from "@/components/Skeleton";
import { hasRole, useAuth } from "./AuthProvider";

/** Sin sesión → /login conservando la ruta (?next=). Sin rol suficiente → pantalla "Sin permiso". */
export function RequireRole({ role = "viewer", children }: { role?: Role; children: ReactNode }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  // padding (no margin): el margen colapsa con <body> y lo desplazaría 24 px (CLS).
  if (loading)
    return (
      <div className="p-6">
        <Skeleton className="h-40" label="Verificando sesión" />
      </div>
    );
  if (!user) {
    const next = encodeURIComponent(loc.pathname + loc.search);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  if (!hasRole(user.role, role)) {
    return (
      <ErrorState
        title="Sin permiso"
        message="Tu cuenta no tiene acceso a esta sección. Pide acceso al administrador."
      />
    );
  }
  return <>{children}</>;
}
