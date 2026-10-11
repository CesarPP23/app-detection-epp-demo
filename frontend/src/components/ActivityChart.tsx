import { useMemo, useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Summary, SummaryRange } from "@/api/types";
import { classColor, classLabel } from "@/design/classColors";
import { formatBucket, formatInt } from "@/design/format";
import { ChartFrame } from "./ChartFrame";
import { ClassChip } from "./ClassChip";
import { DataTable } from "./DataTable";

interface Props {
  series: Summary["series"];
  range: SummaryRange;
}

type Mode = "total" | "class";
const FIXED_ORDER = ["hairnet", "lab_coat", "face_mask", "latex_gloves"];

/** Orden estable de clases: primero las conocidas en su orden de slot, luego el resto alfabético. */
export function orderClasses(names: Iterable<string>): string[] {
  return [...new Set(names)].sort((a, b) => {
    const ia = FIXED_ORDER.indexOf(a);
    const ib = FIXED_ORDER.indexOf(b);
    if (ia >= 0 || ib >= 0) return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    return a.localeCompare(b);
  });
}

interface TipProps {
  active?: boolean;
  label?: string;
  payload?: { dataKey?: string | number; value?: number; color?: string }[];
  mode: Mode;
}

function Tip({ active, label, payload, mode }: TipProps) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-control border border-line bg-surface-1 px-3 py-2 text-sm shadow-lg">
      <p className="font-medium text-ink">{label}</p>
      {payload.map((p) => (
        <p key={String(p.dataKey)} className="tabular flex items-center gap-2 text-ink">
          {mode === "class" && (
            <span aria-hidden className="size-2.5 rounded-sm" style={{ background: p.color }} />
          )}
          <span className="text-ink-2">
            {mode === "class" ? classLabel(String(p.dataKey)) : "Eventos"}
          </span>
          {formatInt(p.value ?? 0)}
        </p>
      ))}
    </div>
  );
}

/** Eventos por hora (24 h) o por día. Total = columnas en un solo tono (--accent). Por clase =
 * columnas apiladas con los colores fijos de clase, 2 px de separación y leyenda. Un solo eje Y,
 * grilla tenue, tooltip por columna, vista de tabla. */
export function ActivityChart({ series, range }: Props) {
  const [mode, setMode] = useState<Mode>("total");
  const classes = useMemo(
    () => orderClasses(series.flatMap((b) => Object.keys(b.by_class))),
    [series],
  );
  const data = useMemo(
    () =>
      series.map((b) => ({
        label: formatBucket(b.bucket, range),
        total: b.total,
        ...b.by_class,
      })),
    [series, range],
  );

  const toggle = (
    <div
      role="group"
      aria-label="Desglose"
      className="flex rounded-control border border-line p-0.5 text-sm"
    >
      {(["total", "class"] as const).map((m) => (
        <button
          key={m}
          type="button"
          aria-pressed={mode === m}
          onClick={() => setMode(m)}
          className={`min-h-8 rounded-[4px] px-3 ${mode === m ? "bg-surface-2 font-medium text-ink" : "text-ink-2"}`}
        >
          {m === "total" ? "Total" : "Por clase"}
        </button>
      ))}
    </div>
  );

  const chart = (
    <>
      <div className="h-64 w-full">
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <BarChart
            data={data}
            margin={{ top: 8, right: 4, bottom: 0, left: 0 }}
            barCategoryGap="20%"
          >
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={{ stroke: "var(--axis)" }}
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              interval="preserveStartEnd"
              minTickGap={16}
            />
            <YAxis
              width={36}
              allowDecimals={false}
              tickLine={false}
              axisLine={false}
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
            />
            <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<Tip mode={mode} />} />
            {mode === "total" ? (
              <Bar
                dataKey="total"
                fill="var(--accent)"
                radius={[4, 4, 0, 0]}
                isAnimationActive={false}
              />
            ) : (
              classes.map((c, i) => (
                <Bar
                  key={c}
                  dataKey={c}
                  stackId="clases"
                  fill={classColor(c)}
                  stroke="var(--surface-1)"
                  strokeWidth={2}
                  radius={i === classes.length - 1 ? [4, 4, 0, 0] : 0}
                  isAnimationActive={false}
                />
              ))
            )}
          </BarChart>
        </ResponsiveContainer>
      </div>
      {mode === "class" && (
        <ul aria-label="Leyenda" className="mt-3 flex flex-wrap gap-2">
          {classes.map((c) => (
            <li key={c}>
              <ClassChip name={c} />
            </li>
          ))}
        </ul>
      )}
    </>
  );

  const table = (
    <DataTable
      caption="Eventos por periodo"
      columns={["Periodo", "Total", ...classes.map(classLabel)]}
      rows={series.map((b) => [
        formatBucket(b.bucket, range),
        formatInt(b.total),
        ...classes.map((c) => formatInt(b.by_class[c] ?? 0)),
      ])}
    />
  );

  return (
    <ChartFrame
      title="Actividad"
      description={
        range === "24h" ? "Eventos por hora, hora de Lima." : "Eventos por día, hora de Lima."
      }
      actions={toggle}
      chart={chart}
      table={table}
    />
  );
}
