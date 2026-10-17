import { ApiError } from "@/api/client";
import { ErrorState } from "./ErrorState";

interface Props {
  error: unknown;
  onRetry?: () => void;
  title?: string;
  className?: string;
}

/** Error de una consulta: un 403 es "Sin permiso", no un error genérico (3.5). */
export function QueryErrorState({ error, onRetry, title, className }: Props) {
  if (error instanceof ApiError && error.forbidden) {
    return (
      <ErrorState
        title="Sin permiso"
        message="Tu cuenta no tiene acceso a esta sección. Pide acceso al administrador."
        {...(className ? { className } : {})}
      />
    );
  }
  const message =
    error instanceof ApiError ? error.message : "Ocurrió un problema al cargar los datos.";
  return (
    <ErrorState
      {...(title ? { title } : {})}
      message={message}
      {...(onRetry ? { onRetry } : {})}
      {...(className ? { className } : {})}
    />
  );
}
