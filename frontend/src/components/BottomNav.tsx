import * as Menu from "@radix-ui/react-dropdown-menu";
import { Cpu, LogOut, MoreHorizontal, ShieldCheck } from "lucide-react";
import { NavLink, useNavigate } from "react-router-dom";
import { hasRole, useAuth } from "@/auth/AuthProvider";
import { NAV } from "./nav";

const MAIN = NAV.slice(0, 3);

/** Navegación inferior en móvil (< 768 px): Resumen, En vivo, Eventos y Más. Objetivos ≥ 44 px. */
export function BottomNav() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const item = "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-xs";
  return (
    <nav
      aria-label="Principal"
      className="fixed inset-x-0 bottom-0 z-40 flex border-t border-line bg-surface-1 pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {MAIN.map(({ to, label, icon: Icon, end }) => (
        <NavLink
          key={to}
          to={to}
          end={end}
          className={({ isActive }) =>
            `${item} ${isActive ? "font-semibold text-ink" : "text-ink-2"}`
          }
        >
          <Icon aria-hidden className="size-5" />
          {label}
        </NavLink>
      ))}
      <Menu.Root>
        <Menu.Trigger className={`${item} text-ink-2`} aria-label="Más opciones">
          <MoreHorizontal aria-hidden className="size-5" />
          Más
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Content
            side="top"
            align="end"
            sideOffset={8}
            className="z-50 min-w-48 rounded-card border border-line bg-surface-1 p-1 shadow-lg"
          >
            <Menu.Item
              onSelect={() => nav("/device")}
              className="flex min-h-11 items-center gap-2 rounded-control px-3 text-sm text-ink outline-none data-[highlighted]:bg-surface-2"
            >
              <Cpu aria-hidden className="size-4" /> Dispositivo
            </Menu.Item>
            {hasRole(user?.role, "admin") && (
              <Menu.Item
                onSelect={() => nav("/admin")}
                className="flex min-h-11 items-center gap-2 rounded-control px-3 text-sm text-ink outline-none data-[highlighted]:bg-surface-2"
              >
                <ShieldCheck aria-hidden className="size-4" /> Administración
              </Menu.Item>
            )}
            <Menu.Item
              onSelect={() => void logout()}
              className="flex min-h-11 items-center gap-2 rounded-control px-3 text-sm text-ink outline-none data-[highlighted]:bg-surface-2"
            >
              <LogOut aria-hidden className="size-4" /> Cerrar sesión
            </Menu.Item>
          </Menu.Content>
        </Menu.Portal>
      </Menu.Root>
    </nav>
  );
}
