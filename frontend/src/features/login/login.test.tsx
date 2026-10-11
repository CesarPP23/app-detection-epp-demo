import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { apiError, http, json, me, renderScreen, server, installMockServer } from "@/test/c2a-utils";
import LoginPage, { emailFromCredential, safeNext } from ".";

vi.mock("@/auth/GoogleButton", () => ({
  GoogleButton: ({ onCredential }: { onCredential: (c: string) => void }) => (
    <button
      type="button"
      onClick={() => onCredential(`h.${btoa(JSON.stringify({ email: "x@y.com" }))}.s`)}
    >
      Continuar con Google
    </button>
  ),
}));

installMockServer();

function anonymous() {
  server.use(
    http.get("*/api/auth/me", () => apiError(401, "no_autenticado", "Sin sesión")),
    http.get("*/api/auth/config", () => json({ client_id: "cid.apps.googleusercontent.com" })),
    http.get("*/api/devices", () => json([])),
  );
}

describe("LoginPage", () => {
  it("muestra el nombre en texto, el propósito y el aviso de privacidad", async () => {
    anonymous();
    renderScreen(<LoginPage />, { route: "/login", path: "/login" });
    expect(screen.getByRole("heading", { name: "EPP Monitor" })).toBeInTheDocument();
    expect(screen.getByText("Monitoreo de equipos de protección personal")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Continuar con Google" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "aviso de privacidad" })).toHaveAttribute(
      "href",
      "/privacidad",
    );
    expect(screen.queryByRole("img")).toBeNull(); // sin logo (U8)
  });

  it("cuenta no autorizada: mensaje de acceso denegado sin revelar nada más", async () => {
    anonymous();
    server.use(
      http.post("*/api/auth/google", () =>
        apiError(403, "sin_acceso", "detalle interno que no debe mostrarse"),
      ),
    );
    renderScreen(<LoginPage />, { route: "/login", path: "/login" });
    await userEvent.click(await screen.findByRole("button", { name: "Continuar con Google" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Tu cuenta (x@y.com) no tiene acceso. Pide acceso al administrador.",
    );
    expect(alert).not.toHaveTextContent("detalle interno");
  });

  it("error de red: mensaje claro con el texto del servidor", async () => {
    anonymous();
    server.use(
      http.post("*/api/auth/google", () => apiError(500, "error", "Algo falló. Intenta de nuevo.")),
    );
    renderScreen(<LoginPage />, { route: "/login", path: "/login" });
    await userEvent.click(await screen.findByRole("button", { name: "Continuar con Google" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Algo falló. Intenta de nuevo.");
  });

  it("sin client ID disponible: el inicio de sesión no está disponible", async () => {
    server.use(
      http.get("*/api/auth/me", () => apiError(401, "no_autenticado", "Sin sesión")),
      http.get("*/api/auth/config", () => apiError(404, "no_encontrado", "No existe")),
      http.get("*/api/devices", () => json([])),
    );
    renderScreen(<LoginPage />, { route: "/login", path: "/login" });
    expect(await screen.findByRole("alert")).toHaveTextContent("no está disponible");
  });

  it("con sesión activa redirige a la ruta pedida", async () => {
    server.use(
      http.get("*/api/auth/me", () => json(me("viewer"))),
      http.get("*/api/auth/config", () => json({ client_id: "x" })),
      http.get("*/api/devices", () => json([])),
    );
    renderScreen(<LoginPage />, { route: "/login?next=%2Fevents", path: "/login" });
    await waitFor(() => expect(screen.getByText("otra ruta")).toBeInTheDocument());
  });
});

describe("helpers de login", () => {
  it("safeNext solo acepta rutas internas", () => {
    expect(safeNext("/events?x=1")).toBe("/events?x=1");
    expect(safeNext("//evil.com")).toBe("/");
    expect(safeNext("https://evil.com")).toBe("/");
    expect(safeNext(null)).toBe("/");
  });

  it("emailFromCredential tolera tokens inválidos", () => {
    expect(emailFromCredential("basura")).toBeNull();
    expect(emailFromCredential(`h.${btoa(JSON.stringify({ email: "a@b.c" }))}.s`)).toBe("a@b.c");
  });
});
