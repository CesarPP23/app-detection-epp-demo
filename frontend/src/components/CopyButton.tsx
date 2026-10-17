import { Check, Copy } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface Props {
  text: string;
  label?: string;
  className?: string;
}

export function CopyButton({ text, label = "Copiar", className = "" }: Props) {
  const [done, setDone] = useState(false);
  const t = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(t.current), []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      clearTimeout(t.current);
      t.current = setTimeout(() => setDone(false), 1800);
    } catch {
      /* portapapeles no disponible */
    }
  }

  return (
    <button
      type="button"
      onClick={copy}
      aria-label={done ? "Copiado" : label}
      title={done ? "Copiado" : label}
      className={`inline-flex size-8 items-center justify-center rounded-control text-ink-2 hover:bg-surface-2 ${className}`}
    >
      {done ? (
        <Check aria-hidden className="size-4 text-good" />
      ) : (
        <Copy aria-hidden className="size-4" />
      )}
      <span role="status" className="sr-only">
        {done ? "Copiado" : ""}
      </span>
    </button>
  );
}
