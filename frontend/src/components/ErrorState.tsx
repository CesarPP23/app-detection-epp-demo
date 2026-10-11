import { AlertTriangle } from "lucide-react";

interface Props {
  title?: string;
  message: string;
  onRetry?: () => void;
  className?: string;
}

/** Mensaje claro + "Reintentar". Sin jerga técnica (3.12). */
export function ErrorState({
  title = "No se pudo cargar",
  message,
  onRetry,
  className = "",
}: Props) {
  return (
    <div
      role="alert"
      className={`flex flex-col items-center gap-3 rounded-card border border-line bg-surface-1 p-8 text-center ${className}`}
    >
      <AlertTriangle aria-hidden className="size-8 text-serious" />
      <h2 className="text-xl font-semibold text-ink">{title}</h2>
      <p className="max-w-md text-sm text-ink-2">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="min-h-11 rounded-control border border-line bg-surface-2 px-4 text-sm font-medium text-ink hover:bg-surface-1"
        >
          Reintentar
        </button>
      )}
    </div>
  );
}
