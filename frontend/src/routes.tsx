import { lazy, Suspense } from "react";
import { createBrowserRouter, Outlet } from "react-router-dom";
import { RequireRole } from "@/auth/RequireRole";
import { AppShell } from "@/components/AppShell";
import { Skeleton } from "@/components/Skeleton";

// Code splitting por ruta: Recharts solo viaja con las pantallas que lo usan.
const Overview = lazy(() => import("./features/overview"));
const Live = lazy(() => import("./features/live"));
const Events = lazy(() => import("./features/events"));
const Device = lazy(() => import("./features/device"));
const Admin = lazy(() => import("./features/admin"));
const Login = lazy(() => import("./features/login"));
const Privacy = lazy(() => import("./features/privacy"));

function Page() {
  return (
    <Suspense fallback={<Skeleton className="h-64 w-full" label="Cargando pantalla" />}>
      <Outlet />
    </Suspense>
  );
}

export const routes = [
  {
    element: <Page />,
    children: [
      { path: "/login", element: <Login /> },
      { path: "/privacidad", element: <Privacy /> },
      {
        element: (
          <RequireRole role="viewer">
            <AppShell />
          </RequireRole>
        ),
        children: [
          { path: "/", element: <Overview /> },
          { path: "/live", element: <Live /> },
          { path: "/events", element: <Events /> },
          { path: "/events/:eventId", element: <Events /> },
          { path: "/device", element: <Device /> },
          {
            path: "/admin",
            element: (
              <RequireRole role="admin">
                <Admin />
              </RequireRole>
            ),
          },
        ],
      },
      { path: "*", element: <Overview /> },
    ],
  },
];

export const createRouter = () => createBrowserRouter(routes);
