import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { useDevices } from "@/api/queries";
import type { DeviceStatus } from "@/api/types";

interface Ctx {
  devices: DeviceStatus[];
  /** Dispositivo seleccionado (el primero por defecto). */
  device: DeviceStatus | undefined;
  deviceId: string;
  setDeviceId: (id: string) => void;
  isLoading: boolean;
  isError: boolean;
  refetch: () => void;
}

const DeviceCtx = createContext<Ctx | null>(null);

/** Selector de dispositivo (preparado para varios; hoy uno). Se monta con el usuario ya autenticado. */
export function DeviceProvider({
  children,
  live = false,
}: {
  children: ReactNode;
  live?: boolean;
}) {
  const q = useDevices(live);
  const [picked, setPicked] = useState<string>("");
  const devices = q.data ?? [];
  const device = devices.find((d) => d.id === picked) ?? devices[0];
  const value = useMemo<Ctx>(
    () => ({
      devices,
      device,
      deviceId: device?.id ?? "",
      setDeviceId: setPicked,
      isLoading: q.isPending,
      isError: q.isError,
      refetch: () => void q.refetch(),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [q.data, q.isPending, q.isError, picked],
  );
  return <DeviceCtx.Provider value={value}>{children}</DeviceCtx.Provider>;
}

export function useDevice(): Ctx {
  const v = useContext(DeviceCtx);
  if (!v) throw new Error("useDevice fuera de DeviceProvider");
  return v;
}
