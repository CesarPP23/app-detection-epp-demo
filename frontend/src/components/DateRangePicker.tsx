import type { Preset } from "@/features/events/filters";

interface Value {
  preset: Preset;
  from: string;
  to: string;
}

const PRESETS: { id: Preset; label: string }[] = [
  { id: "hoy", label: "Hoy" },
  { id: "24h", label: "24 h" },
  { id: "7d", label: "7 días" },
  { id: "30d", label: "30 días" },
  { id: "custom", label: "Personalizado" },
];

/** Presets + rango personalizado (fechas en hora de Lima). */
export function DateRangePicker({
  value,
  onChange,
}: {
  value: Value;
  onChange: (next: Value) => void;
}) {
  const field =
    "min-h-11 rounded-control border border-line bg-surface-2 px-2 text-sm text-ink md:min-h-9";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div role="radiogroup" aria-label="Rango de fechas" className="flex flex-wrap gap-1">
        {PRESETS.map((p) => {
          const on = value.preset === p.id;
          return (
            <button
              key={p.id}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => onChange({ ...value, preset: p.id })}
              className={`min-h-11 rounded-control border px-3 text-sm md:min-h-9 ${
                on
                  ? "border-accent bg-accent font-medium text-white"
                  : "border-line bg-surface-2 text-ink hover:bg-surface-1"
              }`}
            >
              {p.label}
            </button>
          );
        })}
      </div>
      {value.preset === "custom" && (
        <div className="flex flex-wrap items-center gap-2 text-sm text-ink-2">
          <label className="flex items-center gap-1.5">
            Desde
            <input
              type="date"
              className={field}
              value={value.from}
              max={value.to || undefined}
              onChange={(e) => onChange({ ...value, from: e.target.value })}
            />
          </label>
          <label className="flex items-center gap-1.5">
            Hasta
            <input
              type="date"
              className={field}
              value={value.to}
              min={value.from || undefined}
              onChange={(e) => onChange({ ...value, to: e.target.value })}
            />
          </label>
        </div>
      )}
    </div>
  );
}
