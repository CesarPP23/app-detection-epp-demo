import { expect, test } from "@playwright/test";
import { EVENT_ID, mockApi } from "./mock";

const apiCalls = (calls: string[]) => calls.filter((c) => !c.includes("/api/auth/"));

test.describe("sesión", () => {
  test("ventana limpia: sin cookie solo se ve el login", async ({ page }) => {
    await mockApi(page, { role: null });
    await page.goto("/");
    await expect(page).toHaveURL(/\/login\?next=%2F$/);
    await expect(page.getByRole("heading", { name: "EPP Monitor" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Continuar con Google" })).toBeVisible();
    // Nada del dashboard.
    await expect(page.getByRole("navigation")).toHaveCount(0);
  });

  test("cuenta no autorizada: mensaje de acceso denegado sin más detalle", async ({ page }) => {
    await mockApi(page, { role: null, loginStatus: 403 });
    await page.goto("/login");
    await page.getByRole("button", { name: "Continuar con Google" }).click();
    await expect(page.getByRole("alert")).toContainText("Tu cuenta (externo@example.com) no tiene acceso");
    await expect(page.getByRole("alert")).toContainText("Pide acceso al administrador");
    await expect(page).toHaveURL(/\/login/);
  });

  test("cuenta autorizada: entra al Resumen respetando ?next=", async ({ page }) => {
    // Sin sesión en /me; POST /auth/google acepta la credencial y concede la sesión.
    await mockApi(page, { role: null });
    await page.goto("/login?next=%2Fdevice");
    await page.getByRole("button", { name: "Continuar con Google" }).click();
    await expect(page).toHaveURL(/\/device$/);
  });
});

const NAV_DEST = [
  { label: "Resumen", url: /\/$/, heading: "Resumen" },
  { label: "En vivo", url: /\/live$/, heading: "En vivo" },
  { label: "Eventos", url: /\/events/, heading: "Eventos" },
  { label: "Dispositivo", url: /\/device$/, heading: "Dispositivo" },
];

for (const role of ["viewer", "admin"] as const) {
  test(`recorrido de pantallas con rol ${role}`, async ({ page }) => {
    await mockApi(page, { role });
    await page.goto("/");
    const nav = page.getByRole("navigation", { name: /principal/i }).first();
    for (const d of NAV_DEST) {
      await page.getByRole("link", { name: d.label, exact: true }).first().click();
      await expect(page).toHaveURL(d.url);
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    }
    void nav;
    const admin = page.getByRole("link", { name: "Administración", exact: true });
    if (role === "admin") {
      await admin.first().click();
      await expect(page).toHaveURL(/\/admin$/);
      await expect(page.getByRole("heading", { name: "Administración" })).toBeVisible();
      await expect(page.getByText("admin@viz-ia.com").first()).toBeVisible();
    } else {
      await expect(admin).toHaveCount(0);
      await page.goto("/admin");
      await expect(page.getByText("Sin permiso")).toBeVisible();
    }
  });
}

test("viewer no ve el botón de pausar detección; operator sí", async ({ page }) => {
  await mockApi(page, { role: "viewer" });
  await page.goto("/live");
  await expect(page.getByRole("heading", { name: "En vivo", level: 1 })).toBeVisible();
  await expect(page.getByRole("button", { name: /Pausar detección/ })).toHaveCount(0);

  const p2 = await page.context().newPage();
  await mockApi(p2, { role: "operator" });
  await p2.goto("/live");
  await expect(p2.getByRole("button", { name: /Pausar detección/ })).toBeVisible();
});

test("evento → detalle con cajas, y /events/<id> abre el mismo detalle directamente", async ({ page }) => {
  await mockApi(page);
  await page.goto("/events");
  await page.getByRole("link", { name: /Abrir evento/ }).first().click().catch(async () => {
    await page.getByRole("button", { name: /Abrir evento/ }).first().click();
  });
  await expect(page).toHaveURL(new RegExp(`/events/${EVENT_ID}`));
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator("svg[viewBox=\"0 0 1 1\"] rect")).toHaveCount(2);
  await dialog.getByRole("switch", { name: "Mostrar cajas" }).click();
  await expect(dialog.locator("svg[viewBox=\"0 0 1 1\"] rect")).toHaveCount(0);

  // Enlace directo en una pestaña nueva.
  const direct = await page.context().newPage();
  await mockApi(direct);
  await direct.goto(`/events/${EVENT_ID}`);
  await expect(direct.getByRole("dialog")).toBeVisible();
  await expect(direct.getByRole("dialog").locator("svg[viewBox=\"0 0 1 1\"] rect")).toHaveCount(2);
});

test("los filtros viven en la URL y llegan al servidor", async ({ page }) => {
  const m = await mockApi(page);
  await page.goto("/events?clase=hairnet&conf=50&sim=1&vista=tabla");
  await expect(page.getByRole("heading", { name: "Eventos", level: 1 })).toBeVisible();
  await expect.poll(() => m.eventQueries.some((q) => q.includes("clase=hairnet"))).toBe(true);
  const q = m.eventQueries.find((x) => x.includes("clase=hairnet")) ?? "";
  expect(q).toContain("min_conf=0.5");
  expect(q).toContain("include_simulated=true");
  await expect(page.getByRole("table")).toBeVisible();

  // Cambiar un filtro desde la UI actualiza la URL.
  await page.getByLabel("Incluir simulados").uncheck();
  await expect(page).not.toHaveURL(/sim=1/);
  await expect(page).toHaveURL(/clase=hairnet/);
});

test("pestaña oculta: no hay requests nuevas a la API", async ({ page }) => {
  await page.clock.install();
  const m = await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Resumen", level: 1 })).toBeVisible();
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor("02:00");
  const before = apiCalls(m.calls).length;
  await page.clock.runFor("05:00");
  expect(apiCalls(m.calls).length).toBe(before);
});

test("15 min sin interacción: capa de pausa, WebSocket cerrado y cero requests", async ({ page }) => {
  await page.clock.install();
  const m = await mockApi(page);
  await page.goto("/live");
  await expect(page.getByRole("heading", { name: "En vivo", level: 1 })).toBeVisible();
  await expect.poll(m.openSockets).toBe(1);

  await page.clock.runFor("15:05");
  const overlay = page.getByRole("alertdialog");
  await expect(overlay).toContainText("Sesión en pausa para ahorrar recursos");
  await expect.poll(m.openSockets).toBe(0);

  const before = apiCalls(m.calls).length;
  await page.clock.runFor("05:00");
  expect(apiCalls(m.calls).length).toBe(before);

  await overlay.getByRole("button", { name: "Reanudar" }).click();
  await expect(overlay).toHaveCount(0);
});

test("salir de En vivo cierra el WebSocket", async ({ page }) => {
  const m = await mockApi(page);
  await page.goto("/live");
  await expect.poll(m.openSockets).toBe(1);
  await page.getByRole("link", { name: "Resumen", exact: true }).first().click();
  await expect.poll(m.openSockets).toBe(0);
});

test("barra lateral: ítem activo resaltado y ícono junto al texto", async ({ page }) => {
  await mockApi(page);
  await page.goto("/events");
  const link = page.getByRole("navigation", { name: "Principal" }).getByRole("link", { name: "Eventos" });
  await expect(link).toHaveAttribute("aria-current", "page");
  const [icon, text] = await Promise.all([
    link.locator("svg").boundingBox(),
    link.locator("span").boundingBox(),
  ]);
  expect(icon && text && Math.abs(icon.y + icon.height / 2 - (text.y + text.height / 2)) < 8).toBe(true);
});

test("control: con la pestaña visible sí hay refrescos periódicos", async ({ page }) => {
  await page.clock.install();
  const m = await mockApi(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Resumen", level: 1 })).toBeVisible();
  const before = apiCalls(m.calls).length;
  await page.clock.runFor("01:00");
  expect(apiCalls(m.calls).length).toBeGreaterThan(before);
});
