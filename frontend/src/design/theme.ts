import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
const KEY = "epp-theme";
const listeners = new Set<() => void>();

function current(): Theme {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

/** Claro por defecto (U8); el oscuro solo si el usuario lo eligió. */
export function applyTheme(theme: Theme): void {
  if (theme === "dark") document.documentElement.setAttribute("data-theme", "dark");
  else document.documentElement.removeAttribute("data-theme");
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* sin localStorage: el tema vale solo para esta sesión */
  }
  listeners.forEach((l) => l());
}

export function useTheme(): { theme: Theme; setTheme: (t: Theme) => void; toggle: () => void } {
  const theme = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    current,
    () => "light" as Theme,
  );
  const setTheme = useCallback((t: Theme) => applyTheme(t), []);
  const toggle = useCallback(() => applyTheme(current() === "dark" ? "light" : "dark"), []);
  return { theme, setTheme, toggle };
}

/** Preferencias de UI (no datos): siempre con try/catch (3.5). */
export function readPref(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
export function writePref(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* ignorar */
  }
}
