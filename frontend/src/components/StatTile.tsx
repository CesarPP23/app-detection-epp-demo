import { ArrowDown, ArrowUp } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Skeleton } from "./Skeleton";
import { Sparkline } from "./Sparkline";

export interface Delta {
  /** "+12 %", "−2 pts" o "—" cuando no hay periodo previo. */
  text: string;
  direction: "up" | "down" | null;
  /** Bueno o malo para el negocio (no es lo mismo que subir o bajar). */
  tone: "good" | "bad" | "neutral";
  /** Comparación en texto, p. ej. "vs 7 d ant." */
  versus?: string;
}

interface Props {
  label: string;
  value: ReactNode;
  delta?: Delta;
  sparkline?: number[];
  sparklineLabel?: string;
  hint?: ReactNode;
  /** Convierte el tile en enlace (cada número lleva a su evidencia). */
  to?: string;
  loading?: boolean;
  className?: string;
}

const TONE: Record<Delta["tone"], string> = {
  good: "text-delta-good",
  bad: "text-critical-text",
  neutral: "text-ink-2",
};

/** KPI como stat tile (no un gráfico de una barra). Valores con cifras proporcionales. */
export function StatTile({
  label,
  value,
  delta,
  sparkline,
  sparklineLabel,
  hint,
  to,
  loading,
  className = "",
}: Props) {
  const body = (
    <>
      <p className="text-sm text-ink-2">{label}</p>
      {loading ? (
        <Skeleton className="mt-2 h-10 w-24" />
      ) : (
        <p className="mt-1 text-[32px] font-semibold leading-tight text-ink">{value}</p>
      )}
      {delta && !loading && (
        <p className={`mt-1 flex items-center gap-1 text-sm ${TONE[delta.tone]}`}>
          {delta.direction === "up" && <ArrowUp aria-hidden className="size-4" />}
          {delta.direction === "down" && <ArrowDown aria-hidden className="size-4" />}
          <span>{delta.text}</span>
          {delta.versus && <span className="text-ink-3">{delta.versus}</span>}
        </p>
      )}
      {sparkline && sparkline.length > 1 && !loading && (
        <div className="mt-2">
          <Sparkline values={sparkline} label={sparklineLabel ?? `Tendencia de ${label}`} />
        </div>
      )}
      {hint && !loading && <p className="mt-2 text-sm text-ink-2">{hint}</p>}
    </>
  );
  const cls = `block rounded-card border border-line bg-surface-1 p-4 ${className}`;
  return to ? (
    <Link to={to} className={`${cls} hover:bg-surface-2`}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}
