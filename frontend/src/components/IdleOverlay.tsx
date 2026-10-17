import { PauseCircle } from "lucide-react";
import { useEffect, useRef } from "react";
import { useActivity } from "@/hooks/ActivityProvider";

/** Capa de pausa tras 15 min sin interacción (3.9): no hay requests ni video mientras esté visible. */
export function IdleOverlay() {
  const { idle, resume } = useActivity();
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (idle) btn.current?.focus();
  }, [idle]);
  if (!idle) return null;
  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="idle-title"
      aria-describedby="idle-desc"
      className="fixed inset-0 z-[60] flex items-center justify-center p-4"
      style={{ background: "var(--overlay)" }}
    >
      <div className="max-w-sm rounded-card border border-line bg-surface-1 p-6 text-center shadow-xl">
        <PauseCircle aria-hidden className="mx-auto size-10 text-warning" />
        <h2 id="idle-title" className="mt-3 text-xl font-semibold text-ink">
          Sesión en pausa para ahorrar recursos
        </h2>
        <p id="idle-desc" className="mt-2 text-sm text-ink-2">
          Los datos se actualizarán al volver.
        </p>
        <button
          ref={btn}
          type="button"
          onClick={resume}
          className="mt-5 min-h-11 rounded-control bg-accent-solid px-5 text-sm font-medium text-white"
        >
          Reanudar
        </button>
      </div>
    </div>
  );
}
