import { useCallback, useEffect, useRef, useState } from "react";

export const IDLE_TIMEOUT_MS = 15 * 60 * 1000;
const EVENTS = ["pointerdown", "pointermove", "keydown", "touchstart", "scroll", "wheel"] as const;

/** Inactividad global (3.9): sin mouse, teclado, toque ni scroll durante `timeoutMs`. */
export function useIdle(timeoutMs: number = IDLE_TIMEOUT_MS): {
  idle: boolean;
  resume: () => void;
} {
  const [idle, setIdle] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const idleRef = useRef(false);

  const arm = useCallback(() => {
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      idleRef.current = true;
      setIdle(true);
    }, timeoutMs);
  }, [timeoutMs]);

  const resume = useCallback(() => {
    idleRef.current = false;
    setIdle(false);
    arm();
  }, [arm]);

  useEffect(() => {
    arm();
    const onActivity = () => {
      // Una vez en pausa solo se reanuda con el botón de la capa, no con un movimiento accidental.
      if (!idleRef.current) arm();
    };
    for (const e of EVENTS) window.addEventListener(e, onActivity, { passive: true });
    return () => {
      clearTimeout(timer.current);
      for (const e of EVENTS) window.removeEventListener(e, onActivity);
    };
  }, [arm]);

  return { idle, resume };
}
