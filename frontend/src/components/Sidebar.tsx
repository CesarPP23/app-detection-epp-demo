import * as Tooltip from "@radix-ui/react-tooltip";
import { NavLink, useMatch } from "react-router-dom";
import { hasRole, useAuth } from "@/auth/AuthProvider";
import { NAV, type NavItem } from "./nav";

/** Barra lateral de 232 px, colapsable a 64 px (solo íconos con tooltip). */
export function Sidebar({ collapsed }: { collapsed: boolean }) {
  const { user } = useAuth();
  const items = NAV.filter((i) => !i.minRole || hasRole(user?.role, i.minRole));
  return (
    <Tooltip.Provider delayDuration={200}>
      <nav
        aria-label="Principal"
        className={`sticky top-14 hidden h-[calc(100dvh-56px)] shrink-0 flex-col gap-1 border-r border-line bg-surface-1 p-2 md:flex ${collapsed ? "w-16" : "w-[232px]"}`}
      >
        {items.map((item) => (
          <SidebarItem key={item.to} item={item} collapsed={collapsed} />
        ))}
      </nav>
    </Tooltip.Provider>
  );
}

/** La clase va como cadena (no como función de NavLink): Radix Slot no preserva un className-función. */
function SidebarItem({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const { to, label, icon: Icon, end } = item;
  const active = useMatch({ path: to, end: end ?? false }) !== null;
  const cls = `flex min-h-11 items-center gap-3 rounded-control px-3 text-sm ${
    active ? "bg-surface-2 font-semibold text-ink" : "text-ink-2 hover:bg-surface-2"
  } ${collapsed ? "justify-center px-0" : ""}`;
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <NavLink
          to={to}
          end={end}
          aria-label={collapsed ? label : undefined}
          aria-current={active ? "page" : undefined}
          className={cls}
        >
          <Icon aria-hidden className="size-5 shrink-0" />
          {!collapsed && <span>{label}</span>}
        </NavLink>
      </Tooltip.Trigger>
      {collapsed && (
        <Tooltip.Portal>
          <Tooltip.Content
            side="right"
            sideOffset={8}
            className="z-50 rounded-control border border-line bg-surface-1 px-2 py-1 text-sm text-ink shadow-lg"
          >
            {label}
          </Tooltip.Content>
        </Tooltip.Portal>
      )}
    </Tooltip.Root>
  );
}
