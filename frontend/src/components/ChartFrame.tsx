import { useId, useState, type ReactNode } from "react";

interface Props {
  title: string;
  /** Descripción breve (aria-describedby). */
  description: string;
  /** Controles extra a la derecha del título (p. ej. conmutador Total / Por clase). */
  actions?: ReactNode;
  chart: ReactNode;
  table: ReactNode;
  className?: string;
}

/** Marco común de los gráficos: título, descripción, "Ver como tabla" y superficie. Las páginas
 * no configuran Recharts: usan los componentes que envuelven este marco (3.11). */
export function ChartFrame({ title, description, actions, chart, table, className = "" }: Props) {
  const [asTable, setAsTable] = useState(false);
  const id = useId();
  return (
    <figure
      className={`rounded-card border border-line bg-surface-1 p-4 ${className}`}
      aria-labelledby={`${id}-t`}
      aria-describedby={`${id}-d`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <figcaption>
          <h3 id={`${id}-t`} className="text-base font-semibold text-ink">
            {title}
          </h3>
          <p id={`${id}-d`} className="text-sm text-ink-2">
            {description}
          </p>
        </figcaption>
        <div className="flex items-center gap-2">
          {actions}
          <button
            type="button"
            aria-pressed={asTable}
            onClick={() => setAsTable((v) => !v)}
            className="min-h-9 rounded-control border border-line px-3 text-sm text-ink hover:bg-surface-2"
          >
            {asTable ? "Ver gráfico" : "Ver como tabla"}
          </button>
        </div>
      </div>
      <div className="mt-3">{asTable ? table : chart}</div>
    </figure>
  );
}
