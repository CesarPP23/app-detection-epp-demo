import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { mockApi, SCREENS, setTheme } from "./mock";

const VIEWPORTS = [
  { w: 360, h: 740 },
  { w: 768, h: 1024 },
  { w: 1440, h: 900 },
] as const;

// Capturas para docs/DESIGN_SYSTEM.md: escritorio en ambos temas y móvil en claro.
const OUT = resolve(import.meta.dirname, "../../docs/img");
mkdirSync(OUT, { recursive: true });
const shouldSave = (w: number, theme: string) => w === 1440 || (w === 360 && theme === "light");

async function settle(page: Page) {
  await page.waitForLoadState("networkidle");
  await expect(page.locator("h1, [role=dialog]").first()).toBeVisible();
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
  await page.waitForTimeout(800);
}

/** Elementos con texto visible cuya caja se sale del viewport por la derecha (fuera de contenedores con scroll propio). */
function overflowingNodes(page: Page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const scrolls = (el: Element | null): boolean => {
      for (let n = el; n && n !== document.body; n = n.parentElement) {
        const ox = getComputedStyle(n).overflowX;
        if (ox === "auto" || ox === "scroll" || ox === "hidden" || ox === "clip") return true;
      }
      return false;
    };
    const bad: string[] = [];
    for (const el of Array.from(document.body.querySelectorAll("*"))) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.right > vw + 1 && !scrolls(el)) {
        bad.push(`${el.tagName.toLowerCase()}.${String((el as HTMLElement).className).slice(0, 60)} right=${Math.round(r.right)}`);
      }
    }
    return bad.slice(0, 5);
  });
}

for (const vp of VIEWPORTS) {
  for (const theme of ["light", "dark"] as const) {
    for (const s of SCREENS) {
      test(`visual: ${s.name} ${vp.w}px ${theme}: sin scroll horizontal ni desbordes`, async ({ page }) => {
        await page.setViewportSize({ width: vp.w, height: vp.h });
        await mockApi(page, { role: s.auth ? "admin" : null });
        await setTheme(page, theme);
        await page.goto(s.path);
        await settle(page);

        const dims = await page.evaluate(() => ({
          scroll: document.documentElement.scrollWidth,
          client: document.documentElement.clientWidth,
        }));
        expect(dims.scroll, `scrollWidth ${dims.scroll} > clientWidth ${dims.client}`).toBeLessThanOrEqual(
          dims.client,
        );
        expect(await overflowingNodes(page)).toEqual([]);

        if (shouldSave(vp.w, theme)) {
          await page.screenshot({
            path: `${OUT}/${s.name}-${vp.w}-${theme === "light" ? "claro" : "oscuro"}.png`,
            fullPage: true,
            animations: "disabled",
          });
        }
      });
    }
  }
}
