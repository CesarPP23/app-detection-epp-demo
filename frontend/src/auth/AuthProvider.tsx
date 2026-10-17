import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "@/api/client";
import { keys, useMe } from "@/api/queries";
import type { Me, Role } from "@/api/types";

export const ROLE_RANK: Record<Role, number> = { viewer: 1, operator: 2, admin: 3 };

export function hasRole(role: Role | undefined, min: Role): boolean {
  return !!role && ROLE_RANK[role] >= ROLE_RANK[min];
}

interface AuthState {
  user: Me | null;
  loading: boolean;
  loginError: string | null;
  loginWithCredential: (credential: string) => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const me = useMe();

  const loginWithCredential = useCallback(
    async (credential: string) => {
      const user = await api<Me>("POST", "/api/auth/google", { body: { credential } });
      qc.setQueryData(keys.me, user);
    },
    [qc],
  );

  const logout = useCallback(async () => {
    try {
      await api<undefined>("POST", "/api/auth/logout");
    } finally {
      qc.clear();
      window.location.assign("/login");
    }
  }, [qc]);

  const value = useMemo<AuthState>(
    () => ({
      user: me.data ?? null,
      loading: me.isPending,
      loginError: me.error instanceof ApiError && me.error.status !== 401 ? me.error.message : null,
      loginWithCredential,
      logout,
    }),
    [me.data, me.isPending, me.error, loginWithCredential, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth fuera de AuthProvider");
  return v;
}
