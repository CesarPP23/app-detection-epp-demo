/** Esqueleto con la forma final (nunca spinners de página completa). */
export function Skeleton({ className = "", label }: { className?: string; label?: string }) {
  return (
    <div
      className={`skeleton ${className}`}
      role={label ? "status" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}
