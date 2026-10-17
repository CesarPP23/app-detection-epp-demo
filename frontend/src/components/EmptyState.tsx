import type { LucideIcon } from "lucide-react";
import { Inbox } from "lucide-react";
import type { ReactNode } from "react";

interface Props {
  title: string;
  /** Qué significa y qué hacer. */
  message: string;
  icon?: LucideIcon;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, message, icon: Icon = Inbox, action, className = "" }: Props) {
  return (
    <div
      className={`flex flex-col items-center gap-3 rounded-card border border-dashed border-line bg-surface-1 p-8 text-center ${className}`}
    >
      <Icon aria-hidden className="size-8 text-ink-3" />
      <h2 className="text-base font-semibold text-ink">{title}</h2>
      <p className="max-w-md text-sm text-ink-2">{message}</p>
      {action}
    </div>
  );
}
