import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

type Tone = "info" | "success" | "error";
interface ToastItem {
  id: number;
  message: string;
  tone: Tone;
}
interface ToastApi {
  toast: (message: string, tone?: Tone) => void;
}

const Ctx = createContext<ToastApi>({ toast: () => {} });

/** Región aria-live="polite": los lectores de pantalla anuncian cada aviso. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const toast = useCallback((message: string, tone: Tone = "info") => {
    const id = ++seq.current;
    setItems((cur) => [...cur.slice(-2), { id, message, tone }]);
    setTimeout(() => setItems((cur) => cur.filter((t) => t.id !== id)), 5000);
  }, []);
  const api = useMemo(() => ({ toast }), [toast]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed inset-x-4 bottom-20 z-50 flex flex-col items-center gap-2 md:bottom-6 md:items-end"
      >
        {items.map((t) => (
          <div
            key={t.id}
            className="pointer-events-auto max-w-sm rounded-card border border-line bg-surface-1 px-4 py-3 text-sm text-ink shadow-lg"
            style={{
              borderLeft: `4px solid ${t.tone === "error" ? "var(--status-critical)" : t.tone === "success" ? "var(--status-good)" : "var(--accent)"}`,
            }}
          >
            {t.message}
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastApi {
  return useContext(Ctx);
}
