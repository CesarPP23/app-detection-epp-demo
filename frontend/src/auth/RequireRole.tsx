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
  if (loading) return <Skeleton className="m-6 h-40" label="Verificando sesión" />;
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
