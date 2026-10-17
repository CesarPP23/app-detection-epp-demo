import { createContext, useContext, useMemo, type ReactNode } from "react";
import { useIdle } from "./useIdle";
import { usePageVisible } from "./usePageVisible";

interface Activity {
  idle: boolean;
  visible: boolean;
  /** true solo si el usuario está presente: pestaña visible y sin inactividad. */
  active: boolean;
  resume: () => void;
}

const Ctx = createContext<Activity>({ idle: false, visible: true, active: true, resume: () => {} });

export function ActivityProvider({
  children,
  timeoutMs,
}: {
  children: ReactNode;
  timeoutMs?: number;
}) {
  const { idle, resume } = useIdle(timeoutMs);
  const visible = usePageVisible();
  const value = useMemo(
    () => ({ idle, visible, active: !idle && visible, resume }),
    [idle, visible, resume],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useActivity(): Activity {
  return useContext(Ctx);
}

/** Intervalo de refresco que se apaga con el usuario inactivo o la pestaña oculta. */
export function useRefetchInterval(ms: number): number | false {
  const { active } = useActivity();
  return active ? ms : false;
}
