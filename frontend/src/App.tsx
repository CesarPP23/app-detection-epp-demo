import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { RouterProvider } from "react-router-dom";
import { createQueryClient } from "@/api/queries";
import { AuthProvider } from "@/auth/AuthProvider";
import { ToastProvider } from "@/components/Toast";
import { ActivityProvider } from "@/hooks/ActivityProvider";
import { createRouter } from "./routes";

export default function App() {
  const [client] = useState(createQueryClient);
  const [router] = useState(createRouter);
  return (
    <QueryClientProvider client={client}>
      <ActivityProvider>
        <AuthProvider>
          <ToastProvider>
            <RouterProvider router={router} />
          </ToastProvider>
        </AuthProvider>
      </ActivityProvider>
    </QueryClientProvider>
  );
}
