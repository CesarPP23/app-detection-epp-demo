import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

export const SHORTCUTS: { keys: string; desc: string }[] = [
  { keys: "g r", desc: "Ir a Resumen" },
  { keys: "g v", desc: "Ir a En vivo" },
  { keys: "g e", desc: "Ir a Eventos" },
  { keys: "f", desc: "Pantalla completa en En vivo" },
  { keys: "/", desc: "Buscar evento por ID" },
  { keys: "?", desc: "Mostrar esta ayuda" },
];

export const FOCUS_SEARCH_EVENT = "epp:focus-search";

function typing(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  return (
    !!el &&
    (el.tagName === "INPUT" ||
      el.tagName === "TEXTAREA" ||
      el.tagName === "SELECT" ||
      el.isContentEditable)
  );
}

/** Atajos globales: `g r`, `g v`, `g e`, `/` y `?`. (`f` lo maneja LivePlayer.) */
export function useShortcuts(onHelp: () => void): void {
  const nav = useNavigate();
  const armed = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const gPressed = useRef(false);

  useEffect(() => {
    const disarm = () => {
      gPressed.current = false;
      clearTimeout(armed.current);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target)) return;
      if (gPressed.current) {
        const dest = { r: "/", v: "/live", e: "/events" }[e.key];
        disarm();
        if (dest) {
          e.preventDefault();
          nav(dest);
        }
        return;
      }
      if (e.key === "g") {
        gPressed.current = true;
        armed.current = setTimeout(disarm, 1000);
      } else if (e.key === "/") {
        e.preventDefault();
        nav("/events");
        setTimeout(() => window.dispatchEvent(new Event(FOCUS_SEARCH_EVENT)), 50);
      } else if (e.key === "?") {
        e.preventDefault();
        onHelp();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      disarm();
    };
  }, [nav, onHelp]);
}
