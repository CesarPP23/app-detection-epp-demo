import { Cpu, Images, LayoutDashboard, Radio, ShieldCheck, type LucideIcon } from "lucide-react";
import type { Role } from "@/api/types";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  minRole?: Role;
  end?: boolean;
}

export const NAV: NavItem[] = [
  { to: "/", label: "Resumen", icon: LayoutDashboard, end: true },
  { to: "/live", label: "En vivo", icon: Radio },
  { to: "/events", label: "Eventos", icon: Images },
  { to: "/device", label: "Dispositivo", icon: Cpu },
  { to: "/admin", label: "Administración", icon: ShieldCheck, minRole: "admin" },
];
