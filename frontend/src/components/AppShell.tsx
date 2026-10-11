import * as Dialog from "@radix-ui/react-dialog";
import { useCallback, useState } from "react";
import { Outlet } from "react-router-dom";
import { readPref, writePref } from "@/design/theme";
import { SHORTCUTS, useShortcuts } from "@/hooks/useShortcuts";
import { BottomNav } from "./BottomNav";
import { DeviceProvider } from "./DeviceContext";
import { IdleOverlay } from "./IdleOverlay";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

function ShortcutsHelp({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50" style={{ background: "var(--overlay)" }} />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-32px)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-card border border-line bg-surface-1 p-6 shadow-xl">
          <Dialog.Title className="text-xl font-semibold text-ink">Atajos de teclado</Dialog.Title>
          <Dialog.Description className="sr-only">Lista de atajos disponibles.</Dialog.Description>
          <dl className="mt-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            {SHORTCUTS.map((s) => (
              <div key={s.keys} className="contents">
                <dt>
                  <kbd className="rounded-control border border-line bg-surface-2 px-2 py-0.5 font-mono text-xs text-ink">
                    {s.keys}
                  </kbd>
                </dt>
                <dd className="text-ink-2">{s.desc}</dd>
              </div>
            ))}
          </dl>
          <Dialog.Close className="mt-5 min-h-11 w-full rounded-control border border-line bg-surface-2 text-sm font-medium text-ink">
            Cerrar
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

/** Layout con sesión iniciada: barra superior, lateral (escritorio/tablet) o inferior (móvil). */
export function AppShell() {
  const [collapsed, setCollapsed] = useState(() => {
    const saved = readPref("epp-sidebar");
    if (saved) return saved === "collapsed";
    return typeof window !== "undefined" && window.innerWidth < 1024; // tablet: colapsada por defecto
  });
  const [help, setHelp] = useState(false);
  const openHelp = useCallback(() => setHelp(true), []);
  useShortcuts(openHelp);

  const toggle = () =>
    setCollapsed((c) => {
      writePref("epp-sidebar", c ? "expanded" : "collapsed");
      return !c;
    });

  return (
    <DeviceProvider>
      <a href="#contenido" className="skip-link">
        Saltar al contenido
      </a>
      <TopBar onToggleSidebar={toggle} />
      <div className="flex">
        <Sidebar collapsed={collapsed} />
        <main
          id="contenido"
          tabIndex={-1}
          className="mx-auto w-full min-w-0 max-w-[1440px] px-4 pb-24 pt-6 md:px-6 md:pb-8"
        >
          <Outlet />
        </main>
      </div>
      <BottomNav />
      <IdleOverlay />
      <ShortcutsHelp open={help} onOpenChange={setHelp} />
    </DeviceProvider>
  );
}
