import * as Menu from "@radix-ui/react-dropdown-menu";
import { LogOut, Moon, PanelLeft, Sun } from "lucide-react";
import { useAuth } from "@/auth/AuthProvider";
import { useTheme } from "@/design/theme";
import { useDevice } from "./DeviceContext";
import { StatusPill } from "./StatusPill";

const ROLE_LABEL = { viewer: "Observador", operator: "Operador", admin: "Administrador" } as const;

/** Barra superior de 56 px: selector de dispositivo, píldora de estado global (aria-live),
 * selector de tema y menú de usuario. */
export function TopBar({ onToggleSidebar }: { onToggleSidebar: () => void }) {
  const { user, logout } = useAuth();
  const { devices, device, setDeviceId } = useDevice();
  const { theme, toggle } = useTheme();
  const btn =
    "inline-flex size-10 items-center justify-center rounded-control text-ink-2 hover:bg-surface-2";

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-line bg-surface-1 px-3 md:px-4">
      <button
        type="button"
        onClick={onToggleSidebar}
        aria-label="Mostrar u ocultar el menú lateral"
        className={`${btn} hidden md:inline-flex`}
      >
        <PanelLeft aria-hidden className="size-5" />
      </button>
      <span className="text-base font-semibold text-ink">EPP Monitor</span>

      {devices.length > 0 && (
        <label className="ml-2 hidden items-center gap-2 text-sm text-ink-2 sm:flex">
          <span className="sr-only">Dispositivo</span>
          <select
            value={device?.id ?? ""}
            onChange={(e) => setDeviceId(e.target.value)}
            className="min-h-9 rounded-control border border-line bg-surface-2 px-2 text-sm text-ink"
          >
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="ml-auto flex items-center gap-1">
        <div aria-live="polite" aria-atomic="true">
          <StatusPill state={device?.state} />
        </div>
        <button
          type="button"
          onClick={toggle}
          className={btn}
          aria-label={theme === "dark" ? "Cambiar a tema claro" : "Cambiar a tema oscuro"}
        >
          {theme === "dark" ? (
            <Sun aria-hidden className="size-5" />
          ) : (
            <Moon aria-hidden className="size-5" />
          )}
        </button>
        <Menu.Root>
          <Menu.Trigger
            aria-label="Menú de usuario"
            className="inline-flex size-10 items-center justify-center overflow-hidden rounded-full border border-line bg-surface-2 text-sm font-semibold text-ink"
          >
            {user?.picture ? (
              <img
                src={user.picture}
                alt=""
                referrerPolicy="no-referrer"
                className="size-full object-cover"
              />
            ) : (
              (user?.email[0] ?? "?").toUpperCase()
            )}
          </Menu.Trigger>
          <Menu.Portal>
            <Menu.Content
              align="end"
              sideOffset={8}
              className="z-50 min-w-56 rounded-card border border-line bg-surface-1 p-1 shadow-lg"
            >
              <div className="px-3 py-2">
                <p className="truncate text-sm font-medium text-ink">{user?.name ?? user?.email}</p>
                <p className="truncate text-xs text-ink-2">{user?.email}</p>
                {user && <p className="text-xs text-ink-3">{ROLE_LABEL[user.role]}</p>}
              </div>
              <Menu.Separator className="my-1 h-px bg-line" />
              <Menu.Item
                onSelect={() => void logout()}
                className="flex min-h-11 items-center gap-2 rounded-control px-3 text-sm text-ink outline-none data-[highlighted]:bg-surface-2"
              >
                <LogOut aria-hidden className="size-4" /> Cerrar sesión
              </Menu.Item>
            </Menu.Content>
          </Menu.Portal>
        </Menu.Root>
      </div>
    </header>
  );
}
