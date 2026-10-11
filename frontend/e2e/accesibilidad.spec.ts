import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi, SCREENS, setTheme } from "./mock";

const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator("h1, [role=dialog]").first()).toBeVisible();
  // Esqueletos fuera y animaciones de gráficos terminadas.
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
  await page.waitForTimeout(700);
}

for (const theme of ["light", "dark"] as const) {
  for (const s of SCREENS) {
    test(`axe: ${s.name} (${theme}) sin violaciones serias ni críticas`, async ({ page }) => {
      await mockApi(page, { role: s.auth ? "admin" : null });
      await setTheme(page, theme);
      await page.goto(s.path);
      await settle(page);
      if (theme === "dark") await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
      const results = await new AxeBuilder({ page }).withTags(TAGS).analyze();
      const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      const report = bad.map((v) => ({
        id: v.id,
        impact: v.impact,
        help: v.help,
        nodes: v.nodes.slice(0, 4).map((n) => ({ target: n.target, summary: n.failureSummary })),
      }));
      expect(report, JSON.stringify(report, null, 2)).toEqual([]);
    });
  }
}
