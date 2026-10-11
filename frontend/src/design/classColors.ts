/** Mapeo FIJO clase → slot categórico (identidad, no magnitud). Nunca por ranking ni por orden de
 * aparición. Los valores de las 4 clases conocidas viven en tokens.css (--class-*) para respetar
 * el tema elegido. */
const FIXED = ["hairnet", "lab_coat", "face_mask", "latex_gloves"] as const;
export const EXTRA_SLOTS = 4; // slots 5-8; después "Otras" en gris

export const CLASS_LABELS: Record<string, string> = {
  hairnet: "Cofia",
  lab_coat: "Mandil",
  face_mask: "Mascarilla",
  latex_gloves: "Guantes de látex",
};

export const OTHER_COLOR = "var(--class-other)";

// Respaldo para clases nuevas (slots 5-8), asignados por orden de registro.
const EXTRA_COLORS = ["#7a5bd6", "#d6558a", "#4a9ba8", "#8a8a3a"];
const dynamic = new Map<string, string>();

export function classColor(name: string): string {
  if ((FIXED as readonly string[]).includes(name)) return `var(--class-${name})`;
  const known = dynamic.get(name);
  if (known) return known;
  if (dynamic.size < EXTRA_SLOTS) {
    const c = EXTRA_COLORS[dynamic.size] ?? OTHER_COLOR;
    dynamic.set(name, c);
    return c;
  }
  return OTHER_COLOR;
}

export function classLabel(name: string): string {
  return CLASS_LABELS[name] ?? name.replace(/_/g, " ");
}

export function isFixedClass(name: string): boolean {
  return (FIXED as readonly string[]).includes(name);
}

/** Solo para pruebas. */
export function resetDynamicClasses(): void {
  dynamic.clear();
}
