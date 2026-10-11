import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
}

/** Diálogo accesible (foco atrapado, Esc, ARIA vía Radix). */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel = "Continuar",
  cancelLabel = "Cancelar",
  destructive = false,
  busy = false,
  onConfirm,
}: Props) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50" style={{ background: "var(--overlay)" }} />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-32px)] max-w-md -translate-x-1/2 -translate-y-1/2 rounded-card border border-line bg-surface-1 p-6 shadow-xl">
          <Dialog.Title className="text-xl font-semibold text-ink">{title}</Dialog.Title>
          <Dialog.Description className="mt-2 text-sm text-ink-2">{description}</Dialog.Description>
          <div className="mt-6 flex justify-end gap-2">
            <Dialog.Close className="min-h-11 rounded-control border border-line bg-surface-2 px-4 text-sm font-medium text-ink">
              {cancelLabel}
            </Dialog.Close>
            <button
              type="button"
              disabled={busy}
              onClick={onConfirm}
              className="min-h-11 rounded-control px-4 text-sm font-medium text-white disabled:opacity-60"
              style={{ background: destructive ? "var(--status-critical)" : "var(--accent)" }}
            >
              {busy ? "Procesando…" : confirmLabel}
            </button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
