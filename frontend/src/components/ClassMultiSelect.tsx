import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, X } from "lucide-react";
import { CLASS_LABELS, classLabel } from "@/design/classColors";
import { ClassChip } from "./ClassChip";

interface Props {
  value: string[];
  onChange: (next: string[]) => void;
  /** Clases extra vistas en los datos (además de las cuatro conocidas). */
  extra?: string[];
}

/** Multiselección de clases con chips de color (texto en tinta normal + punto de la clase). */
export function ClassMultiSelect({ value, onChange, extra = [] }: Props) {
  const all = Array.from(new Set([...Object.keys(CLASS_LABELS), ...extra, ...value]));
  const toggle = (name: string) =>
    onChange(value.includes(name) ? value.filter((v) => v !== name) : [...value, name]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Menu.Root>
        <Menu.Trigger
          className="inline-flex min-h-11 items-center gap-2 rounded-control border border-line bg-surface-2 px-3 text-sm text-ink md:min-h-9"
          aria-label="Filtrar por clase de EPP"
        >
          Clase
          <ChevronDown aria-hidden className="size-4" />
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content
            align="start"
            sideOffset={4}
            className="z-50 min-w-48 rounded-card border border-line bg-surface-1 p-1 shadow-lg"
          >
            {all.map((name) => (
              <Menu.CheckboxItem
                key={name}
                checked={value.includes(name)}
                onCheckedChange={() => toggle(name)}
                onSelect={(e) => e.preventDefault()}
                className="flex min-h-9 cursor-pointer items-center gap-2 rounded-control px-2 text-sm text-ink outline-none data-[highlighted]:bg-surface-2"
              >
                <span className="flex size-4 items-center justify-center">
                  <Menu.ItemIndicator>
                    <Check aria-hidden className="size-4" />
                  </Menu.ItemIndicator>
                </span>
                <ClassChip name={name} />
              </Menu.CheckboxItem>
            ))}
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
      {value.map((name) => (
        <button
          key={name}
          type="button"
          onClick={() => toggle(name)}
          aria-label={`Quitar filtro ${classLabel(name)}`}
          className="inline-flex items-center gap-1 rounded-full"
        >
          <ClassChip name={name} />
          <X aria-hidden className="size-3.5 text-ink-2" />
        </button>
      ))}
    </div>
  );
}
