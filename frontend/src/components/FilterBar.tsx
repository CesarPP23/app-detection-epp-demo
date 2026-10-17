import * as Slider from "@radix-ui/react-slider";
import { LayoutGrid, Search, Table2 } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { FOCUS_SEARCH_EVENT } from "@/hooks/useShortcuts";
import type { EventFilters } from "@/features/events/filters";
import { ClassMultiSelect } from "./ClassMultiSelect";
import { DateRangePicker } from "./DateRangePicker";

interface Props {
  filters: EventFilters;
  onChange: (next: EventFilters) => void;
  extraClasses?: string[];
  /** Búsqueda por event_id: abre el detalle directamente. */
  onSearchId: (id: string) => void;
  onReset?: () => void;
}

/** Filtros del explorador de eventos. Un solo renglón en escritorio; se pliega en móvil. */
export function FilterBar({ filters, onChange, extraClasses, onSearchId, onReset }: Props) {
  const search = useRef<HTMLInputElement>(null);
  const [id, setId] = useState("");
  const [conf, setConf] = useState(filters.minConf);

  useEffect(() => setConf(filters.minConf), [filters.minConf]);
  useEffect(() => {
    const focus = () => search.current?.focus();
    window.addEventListener(FOCUS_SEARCH_EVENT, focus);
    return () => window.removeEventListener(FOCUS_SEARCH_EVENT, focus);
  }, []);

  const set = (patch: Partial<EventFilters>) => onChange({ ...filters, ...patch });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const v = id.trim();
    if (v) onSearchId(v);
  };
  const seg = (on: boolean) =>
    `inline-flex min-h-11 items-center gap-1.5 px-3 text-sm md:min-h-9 ${
      on ? "bg-accent-solid font-medium text-white" : "bg-surface-2 text-ink hover:bg-surface-1"
    }`;

  return (
    <div className="space-y-3 rounded-card border border-line bg-surface-1 p-3 md:p-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <DateRangePicker
          value={{ preset: filters.preset, from: filters.from, to: filters.to }}
          onChange={(v) => set(v)}
        />
        <ClassMultiSelect
          value={filters.clases}
          onChange={(clases) => set({ clases })}
          extra={extraClasses ?? []}
        />
        <div className="flex items-center gap-2 text-sm text-ink-2">
          <span id="conf-label">Confianza mínima</span>
          <Slider.Root
            className="relative flex h-6 w-32 touch-none select-none items-center"
            min={0}
            max={100}
            step={5}
            value={[conf]}
            onValueChange={([v]) => setConf(v ?? 0)}
            onValueCommit={([v]) => set({ minConf: v ?? 0 })}
          >
            <Slider.Track className="relative h-1.5 grow rounded-full bg-surface-2">
              <Slider.Range className="absolute h-full rounded-full bg-accent" />
            </Slider.Track>
            <Slider.Thumb
              aria-labelledby="conf-label"
              className="block size-5 rounded-full border-2 border-accent bg-surface-1 outline-none focus-visible:ring-2"
            />
          </Slider.Root>
          <span className="tabular w-10 text-ink">{conf} %</span>
        </div>
        <label className="flex min-h-11 items-center gap-2 text-sm text-ink md:min-h-9">
          <input
            type="checkbox"
            className="size-4 accent-[var(--accent)]"
            checked={filters.simulados}
            onChange={(e) => set({ simulados: e.target.checked })}
          />
          Incluir simulados
        </label>
        {filters.sinCaptura && (
          <button
            type="button"
            onClick={() => set({ sinCaptura: false })}
            className="min-h-9 rounded-full border border-line bg-surface-2 px-3 text-sm text-ink"
          >
            Solo sin captura ✕
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <form onSubmit={submit} role="search" className="flex items-center gap-2">
          <label className="relative">
            <span className="sr-only">Buscar evento por ID</span>
            <Search
              aria-hidden
              className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-ink-3"
            />
            <input
              ref={search}
              type="search"
              value={id}
              onChange={(e) => setId(e.target.value)}
              placeholder="Buscar por ID  ( / )"
              className="min-h-11 w-64 max-w-full rounded-control border border-line bg-surface-2 pl-8 pr-2 text-sm text-ink md:min-h-9"
            />
          </label>
          <button
            type="submit"
            className="min-h-11 rounded-control border border-line bg-surface-2 px-3 text-sm text-ink md:min-h-9"
          >
            Abrir
          </button>
        </form>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          {filters.view === "tabla" && (
            <label className="flex items-center gap-1.5 text-sm text-ink-2">
              Densidad
              <select
                value={filters.density}
                onChange={(e) => set({ density: e.target.value as EventFilters["density"] })}
                className="min-h-11 rounded-control border border-line bg-surface-2 px-2 text-sm text-ink md:min-h-9"
              >
                <option value="comoda">Cómoda</option>
                <option value="compacta">Compacta</option>
              </select>
            </label>
          )}
          {onReset && (
            <button
              type="button"
              onClick={onReset}
              className="min-h-11 rounded-control px-3 text-sm text-ink-2 hover:bg-surface-2 md:min-h-9"
            >
              Limpiar filtros
            </button>
          )}
          <div
            role="group"
            aria-label="Vista"
            className="flex overflow-hidden rounded-control border border-line"
          >
            <button
              type="button"
              aria-pressed={filters.view === "galeria"}
              onClick={() => set({ view: "galeria" })}
              className={seg(filters.view === "galeria")}
            >
              <LayoutGrid aria-hidden className="size-4" /> Galería
            </button>
            <button
              type="button"
              aria-pressed={filters.view === "tabla"}
              onClick={() => set({ view: "tabla" })}
              className={seg(filters.view === "tabla")}
            >
              <Table2 aria-hidden className="size-4" /> Tabla
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
