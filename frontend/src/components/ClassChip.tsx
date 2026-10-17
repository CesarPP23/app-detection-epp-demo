import { classColor, classLabel } from "@/design/classColors";

/** Chip de clase: texto en tinta normal y un punto del color de la clase (el texto nunca va en
 * el color de la serie). */
export function ClassChip({ name, className = "" }: { name: string; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 py-0.5 text-xs font-medium text-ink ${className}`}
    >
      <span
        aria-hidden
        className="size-2.5 rounded-full"
        style={{ background: classColor(name) }}
      />
      {classLabel(name)}
    </span>
  );
}
