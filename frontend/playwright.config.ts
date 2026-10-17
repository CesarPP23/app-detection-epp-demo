import { defineConfig } from "@playwright/test";

// e2e contra `vite preview` con la API interceptada en el navegador (sin backend real).
// Los viewports de 360 / 768 / 1440 px se recorren dentro de los specs visuales.
export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  fullyParallel: true,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
    locale: "es-PE",
    timezoneId: "America/Lima",
  },
  webServer: {
    command: "npm run build && npx vite preview --host 127.0.0.1 --port 4173 --strictPort",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: true,
    timeout: 180_000,
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
