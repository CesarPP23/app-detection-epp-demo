import { useNavigate } from "react-router-dom";
import { formatInt, formatPercent } from "@/design/format";
import { classLabel } from "@/design/classColors";
import { ChartFrame } from "./ChartFrame";
import { DataTable } from "./DataTable";

export interface ClassCountItem {
  nombre_clase: string;
  count: number;
}

interface Props {
  items: ClassCountItem[];
  /** A dónde lleva el clic en una barra (cada número lleva a su evidencia). */
  hrefFor?: (clase: string) => string;
  loading?: boolean;
}

/** Barras horizontales en UN solo tono (magnitud), ordenadas desc., valor al final de la barra.
 * Marca fina, extremo redondeado de 4 px anclado a la base, una sola escala. */
export function ClassBreakdown({ items, hrefFor }: Props) {
  const nav = useNavigate();
  const sorted = [...items].sort((a, b) => b.count - a.count);
  const total = sorted.reduce((s, i) => s + i.count, 0);
  const max = sorted[0]?.count || 1;

  const chart =
    sorted.length === 0 ? (
      <p className="py-8 text-center text-sm text-ink-2">Sin eventos en este periodo.</p>
    ) : (
      <ul className="flex flex-col gap-3">
        {sorted.map((it) => {
          const pct = total ? it.count / total : 0;
          const label = `${classLabel(it.nombre_clase)}: ${formatInt(it.count)} eventos (${formatPercent(pct)})`;
          const go = hrefFor ? () => nav(hrefFor(it.nombre_clase)) : undefined;
          return (
            <li key={it.nombre_clase}>
              <button
                type="button"
                onClick={go}
                disabled={!go}
                title={label}
                aria-label={label}
                className="group grid w-full min-h-11 grid-cols-[110px_1fr_auto] items-center gap-3 rounded-control text-left enabled:hover:bg-surface-2"
              >
                <span className="truncate text-sm text-ink">{classLabel(it.nombre_clase)}</span>
                <span className="h-3 overflow-hidden rounded-r-[4px] bg-transparent">
                  <span
                    className="block h-full rounded-r-[4px] bg-accent"
                    style={{ width: `${Math.max(2, (it.count / max) * 100)}%` }}
                  />
                </span>
                <span className="tabular text-sm text-ink">{formatInt(it.count)}</span>
              </button>
            </li>
          );
        })}
      </ul>
    );

  return (
    <ChartFrame
      title="Por clase de EPP"
      description="Eventos del periodo por clase, de mayor a menor."
      chart={chart}
      table={
        <DataTable
          caption="Eventos por clase de EPP"
          columns={["Clase", "Eventos", "Porcentaje"]}
          rows={sorted.map((i) => [
            classLabel(i.nombre_clase),
            formatInt(i.count),
            formatPercent(total ? i.count / total : 0),
          ])}
        />
      }
    />
  );
}
