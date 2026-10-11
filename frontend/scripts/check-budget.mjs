// Falla si el JS inicial (el que referencia index.html) supera el presupuesto de 3.5.
import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BUDGET_KB = 180;
const html = readFileSync("dist/index.html", "utf8");
const files = [...html.matchAll(/src="(\/assets\/[^"]+\.js)"/g)].map((m) => m[1]);
let total = 0;
for (const f of files) total += gzipSync(readFileSync(`dist${f}`), { level: 9 }).length;
const kb = total / 1024;
console.log(`JS inicial: ${kb.toFixed(1)} KB gzip (presupuesto ${BUDGET_KB} KB)`);
if (kb > BUDGET_KB) process.exit(1);
