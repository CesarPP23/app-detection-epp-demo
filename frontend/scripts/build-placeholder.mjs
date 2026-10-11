// Placeholder de la Fase 1: la SPA real (Vite + React + TypeScript) llega en la Fase 3.
import { mkdirSync, writeFileSync } from "node:fs";

mkdirSync("dist/assets", { recursive: true });
writeFileSync(
  "dist/index.html",
  '<!doctype html><html lang="es"><head><meta charset="utf-8"><title>EPP Monitor</title></head><body><p>EPP Monitor</p></body></html>\n',
);
