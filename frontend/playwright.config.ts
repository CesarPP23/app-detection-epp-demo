import { defineConfig } from "@playwright/test";

// e2e contra `vite preview` con la API simulada por MSW (sin backend real).
export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  fullyParallel: true,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: "http://127.0.0.1:4173", trace: "retain-on-failure" },
  webServer: {
    command: "pnpm build && pnpm preview --host 127.0.0.1 --port 4173",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: true,
  },
  projects: [
    { name: "movil", use: { viewport: { width: 360, height: 740 } } },
    { name: "tablet", use: { viewport: { width: 768, height: 1024 } } },
    { name: "escritorio", use: { viewport: { width: 1440, height: 900 } } },
  ],
});
