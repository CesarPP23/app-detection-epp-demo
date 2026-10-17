import {
  keepPreviousData,
  QueryClient,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useRefetchInterval } from "@/hooks/ActivityProvider";
import { api, get } from "./client";
import type {
  AuditPage,
  DeviceStatus,
  EventDetail,
  EventPage,
  EventsQuery,
  Me,
  Role,
  Summary,
  SummaryRange,
  TrainingCaptureStarted,
  TrainingSessionPage,
  UserOut,
} from "./types";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 10_000,
        refetchOnWindowFocus: true,
        refetchIntervalInBackground: false,
        retry: (count, err) => {
          const status = (err as { status?: number }).status ?? 0;
          return status !== 401 && status !== 403 && status !== 404 && count < 2;
        },
      },
    },
  });
}

/** Intervalos de 3.5. Todos se apagan con inactividad o pestaña oculta (useRefetchInterval). */
export const INTERVALS = {
  summary: 30_000,
  events: 30_000,
  device: 10_000,
  deviceLive: 5_000,
  trainingCapture: 5_000,
} as const;

export const keys = {
  me: ["me"] as const,
  devices: ["devices"] as const,
  summary: (id: string, range: SummaryRange) => ["summary", id, range] as const,
  events: (id: string, q: EventsQuery) => ["events", id, q] as const,
  event: (eventId: string) => ["event", eventId] as const,
  users: ["admin", "users"] as const,
  audit: (q: { action?: string; email?: string }) => ["admin", "audit", q] as const,
  trainingSessions: (deviceId: string) => ["training-sessions", deviceId] as const,
};

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: ({ signal }) => get<Me>("/api/auth/me", undefined, signal),
    staleTime: 5 * 60_000,
    retry: false,
  });
}

export function useDevices(live = false) {
  const interval = useRefetchInterval(live ? INTERVALS.deviceLive : INTERVALS.device);
  return useQuery({
    queryKey: keys.devices,
    queryFn: ({ signal }) => get<DeviceStatus[]>("/api/devices", undefined, signal),
    refetchInterval: interval,
  });
}

export function useSummary(deviceId: string, range: SummaryRange) {
  const interval = useRefetchInterval(INTERVALS.summary);
  return useQuery({
    queryKey: keys.summary(deviceId, range),
    queryFn: ({ signal }) => get<Summary>(`/api/devices/${deviceId}/summary`, { range }, signal),
    refetchInterval: interval,
    placeholderData: keepPreviousData,
    enabled: !!deviceId,
  });
}

/** Lista paginada por cursor (no páginas numeradas: el cursor es lo correcto con Firestore). */
export function useEvents(
  deviceId: string,
  filters: Omit<EventsQuery, "cursor"> = {},
  refresh = true,
) {
  const interval = useRefetchInterval(INTERVALS.events);
  return useInfiniteQuery({
    queryKey: keys.events(deviceId, filters),
    queryFn: ({ pageParam, signal }) =>
      get<EventPage>(
        `/api/devices/${deviceId}/events`,
        { ...filters, cursor: pageParam || undefined },
        signal,
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchInterval: refresh ? interval : false,
    enabled: !!deviceId,
  });
}

export function useEvent(eventId: string | undefined) {
  return useQuery({
    queryKey: keys.event(eventId ?? ""),
    queryFn: ({ signal }) => get<EventDetail>(`/api/events/${eventId}`, undefined, signal),
    enabled: !!eventId,
    staleTime: 60_000,
  });
}

export function useSetDesiredState(deviceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (state: "running" | "stopped") =>
      api<DeviceStatus>("PUT", `/api/devices/${deviceId}/desired-state`, { body: { state } }),
    onSuccess: (device) => {
      qc.setQueryData<DeviceStatus[]>(keys.devices, (old) =>
        old ? old.map((d) => (d.id === device.id ? device : d)) : [device],
      );
    },
  });
}

export function useUsers() {
  return useQuery({
    queryKey: keys.users,
    queryFn: ({ signal }) => get<UserOut[]>("/api/admin/users", undefined, signal),
  });
}

export function useUserMutations() {
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: keys.users });
  return {
    create: useMutation({
      mutationFn: (u: { email: string; role: Role }) =>
        api<UserOut>("POST", "/api/admin/users", { body: u }),
      onSuccess: refresh,
    }),
    patch: useMutation({
      mutationFn: (v: { email: string; role?: Role; active?: boolean }) => {
        const { email, ...body } = v;
        return api<UserOut>("PATCH", `/api/admin/users/${encodeURIComponent(email)}`, { body });
      },
      onSuccess: refresh,
    }),
    remove: useMutation({
      mutationFn: (email: string) =>
        api<undefined>("DELETE", `/api/admin/users/${encodeURIComponent(email)}`),
      onSuccess: refresh,
    }),
  };
}

export function useAudit(filter: { action?: string; email?: string }) {
  return useInfiniteQuery({
    queryKey: keys.audit(filter),
    queryFn: ({ pageParam, signal }) =>
      get<AuditPage>("/api/admin/audit", { ...filter, cursor: pageParam || undefined }, signal),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/** Sesiones de captura de fotos para reentrenamiento (no el reentrenamiento en sí, eso es en dev). */
export function useTrainingSessions(deviceId: string) {
  const fast = useRefetchInterval(INTERVALS.trainingCapture);
  return useInfiniteQuery({
    queryKey: keys.trainingSessions(deviceId),
    queryFn: ({ pageParam, signal }) =>
      get<TrainingSessionPage>(
        `/api/devices/${deviceId}/training-captures`,
        { cursor: pageParam || undefined },
        signal,
      ),
    initialPageParam: "",
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    // sondeo rápido solo mientras haya una sesión en curso; si no, el usuario la inicia a mano.
    refetchInterval: (query) => {
      const capturing = query.state.data?.pages[0]?.items.some((s) => s.status === "capturing");
      return capturing ? fast : false;
    },
    enabled: !!deviceId,
  });
}

export function useStartTrainingCapture(deviceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<TrainingCaptureStarted>("POST", `/api/devices/${deviceId}/training-captures`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.trainingSessions(deviceId) }),
  });
}

export function useStopTrainingCapture(deviceId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sessionId: string) =>
      api<undefined>("POST", `/api/devices/${deviceId}/training-captures/${sessionId}/stop`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: keys.trainingSessions(deviceId) }),
  });
}

/** Client ID de Google (no es secreto). Lo expone el backend; en dev puede venir de VITE_GOOGLE_CLIENT_ID. */
export function useAuthConfig() {
  return useQuery({
    queryKey: ["auth-config"] as const,
    queryFn: async ({ signal }) => {
      const fromEnv = import.meta.env["VITE_GOOGLE_CLIENT_ID"] as string | undefined;
      if (fromEnv) return { client_id: fromEnv };
      return get<{ client_id: string }>("/api/auth/config", undefined, signal);
    },
    staleTime: Infinity,
    retry: false,
  });
}
