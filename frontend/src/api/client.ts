import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get forbidden(): boolean {
    return this.status === 403;
  }
}

/** Rutas donde un 401 es parte del flujo normal y no debe redirigir. */
const NO_REDIRECT = ["/api/auth/me", "/api/auth/google"];

let redirecting = false;

function goToLogin(): void {
  if (redirecting) return;
  redirecting = true;
  const here = window.location.pathname + window.location.search;
  const next = here.startsWith("/login") ? "" : `?next=${encodeURIComponent(here)}`;
  window.location.assign(`/login${next}`);
}

type Query = Record<string, string | number | boolean | null | undefined>;

export function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `${path}?${s}` : path;
}

async function parseError(res: Response): Promise<ApiError> {
  let code = "error";
  let message = "No se pudo completar la operación.";
  try {
    const body = (await res.json()) as Partial<ApiErrorBody>;
    if (body.error) {
      code = body.error.code;
      message = body.error.message;
    }
  } catch {
    /* cuerpo no JSON */
  }
  return new ApiError(res.status, code, message);
}

export async function api<T>(
  method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE",
  path: string,
  options: { body?: unknown; query?: Query; signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-Requested-With"] = "epp-web"; // CSRF (3.1)
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(withQuery(path, options.query), {
      method,
      headers,
      credentials: "same-origin",
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: options.signal ?? null,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") throw e;
    throw new ApiError(0, "sin_conexion", "Sin conexión con el servidor.");
  }

  if (res.status === 401 && !NO_REDIRECT.includes(path)) {
    goToLogin();
    throw new ApiError(401, "no_autenticado", "Tu sesión expiró. Inicia sesión de nuevo.");
  }
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const get = <T>(path: string, query?: Query, signal?: AbortSignal) =>
  api<T>("GET", path, { ...(query ? { query } : {}), ...(signal ? { signal } : {}) });
