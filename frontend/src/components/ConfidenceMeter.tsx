import { formatPercent } from "@/design/format";

interface Props {
  /** Fracción 0-1. */
  value: number;
  label?: string;
  className?: string;
  showValue?: boolean;
}

/** Barra-medidor de magnitud en un solo tono (--accent). */
export function ConfidenceMeter({
  value,
  label = "Confianza",
  className = "",
  showValue = true,
}: Props) {
  const v = Math.min(1, Math.max(0, value));
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(v * 100)}
        aria-valuetext={formatPercent(v)}
        className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2"
      >
        <div className="h-full rounded-full bg-accent" style={{ width: `${v * 100}%` }} />
      </div>
      {showValue && (
        <span className="tabular w-10 text-right text-xs text-ink-2">{formatPercent(v)}</span>
      )}
    </div>
  );
}
