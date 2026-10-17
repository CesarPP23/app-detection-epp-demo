import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { AuditPage, UserOut } from "@/api/types";
import {
  apiError,
  baseHandlers,
  http,
  installMockServer,
  json,
  renderScreen,
  server,
  user,
} from "@/test/c2a-utils";
import { normalizeEmail } from "./UsersTab";
import AdminPage from ".";

installMockServer();

const ME = "admin@viz-ia.com";
const USERS: UserOut[] = [
  user({ email: ME, role: "admin" }),
  user({ email: "ana@viz-ia.com", role: "viewer" }),
  user({ email: "baja@viz-ia.com", role: "viewer", active: false }),
];

function setup(role: "viewer" | "operator" | "admin" = "admin", users: UserOut[] = USERS) {
  server.use(
    http.get("*/api/auth/me", () => json({ email: ME, name: "A", picture: null, role })),
    ...baseHandlers(role),
    http.get("*/api/admin/users", () => json(users)),
  );
}

describe("AdminPage - permiso", () => {
  it("un operador ve 'Sin permiso' y no se pide la lista de usuarios", async () => {
    let asked = false;
    server.use(
      ...baseHandlers("operator"),
      http.get("*/api/admin/users", () => {
        asked = true;
        return json([]);
      }),
    );
    renderScreen(<AdminPage />);
    expect(await screen.findByRole("heading", { name: "Sin permiso" })).toBeInTheDocument();
    expect(asked).toBe(false);
  });
});

describe("AdminPage - usuarios", () => {
  it("lista usuarios con rol, estado y alta; el propio no se puede degradar ni desactivar", async () => {
    setup();
    renderScreen(<AdminPage />);
    const table = await screen.findByRole("table", { name: "Usuarios con acceso al panel" });
    expect(within(table).getByText("ana@viz-ia.com")).toBeInTheDocument();
    expect(within(table).getByText("Desactivado")).toBeInTheDocument();
    expect(within(table).getByRole("combobox", { name: `Rol de ${ME}` })).toBeDisabled();
    const ownRow = within(table).getByText(ME).closest("tr")!;
    expect(within(ownRow).getByRole("button", { name: "Desactivar" })).toBeDisabled();
  });

  it("valida el correo antes de enviar", async () => {
    let posted = false;
    setup();
    server.use(
      http.post("*/api/admin/users", () => {
        posted = true;
        return json(user());
      }),
    );
    renderScreen(<AdminPage />);
    await userEvent.type(await screen.findByLabelText("Correo de Google"), "no-es-un-correo");
    await userEvent.click(screen.getByRole("button", { name: "Agregar usuario" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Escribe un correo válido");
    expect(posted).toBe(false);
  });

  it("agrega un usuario con el correo en minúsculas y el rol elegido", async () => {
    let body: unknown;
    setup();
    server.use(
      http.post("*/api/admin/users", async ({ request }) => {
        body = await request.json();
        return json(user({ email: "nuevo@viz-ia.com", role: "operator" }), 201);
      }),
    );
    renderScreen(<AdminPage />);
    await userEvent.type(await screen.findByLabelText("Correo de Google"), " Nuevo@Viz-IA.com ");
    await userEvent.selectOptions(screen.getByLabelText("Rol"), "operator");
    await userEvent.click(screen.getByRole("button", { name: "Agregar usuario" }));
    await waitFor(() => expect(body).toEqual({ email: "nuevo@viz-ia.com", role: "operator" }));
    expect(await screen.findByText("Se dio acceso a nuevo@viz-ia.com")).toBeInTheDocument();
  });

  it("muestra el error del servidor al agregar", async () => {
    setup();
    server.use(
      http.post("*/api/admin/users", () => apiError(409, "ya_existe", "Ese usuario ya existe.")),
    );
    renderScreen(<AdminPage />);
    await userEvent.type(await screen.findByLabelText("Correo de Google"), "ana@viz-ia.com");
    await userEvent.click(screen.getByRole("button", { name: "Agregar usuario" }));
    expect(await screen.findByText("Ese usuario ya existe.")).toBeInTheDocument();
  });

  it("desactivar pide confirmación y envía active=false", async () => {
    let body: unknown;
    setup();
    server.use(
      http.patch("*/api/admin/users/:email", async ({ request }) => {
        body = await request.json();
        return json(user({ email: "ana@viz-ia.com", active: false }));
      }),
    );
    renderScreen(<AdminPage />);
    const row = (await screen.findByText("ana@viz-ia.com")).closest("tr")!;
    await userEvent.click(within(row).getByRole("button", { name: "Desactivar" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/dejará de poder entrar/)).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole("button", { name: "Desactivar" }));
    await waitFor(() => expect(body).toEqual({ active: false }));
  });

  it("cambiar el rol de otro usuario envía el nuevo rol; un rechazo del servidor se avisa", async () => {
    setup();
    server.use(
      http.patch("*/api/admin/users/:email", () =>
        apiError(409, "ultimo_admin", "No se puede quitar al último administrador."),
      ),
    );
    renderScreen(<AdminPage />);
    await userEvent.selectOptions(await screen.findByLabelText("Rol de ana@viz-ia.com"), "admin");
    expect(
      await screen.findByText("No se puede quitar al último administrador."),
    ).toBeInTheDocument();
  });

  it("error al cargar: mensaje y Reintentar", async () => {
    let calls = 0;
    server.use(
      ...baseHandlers("admin"),
      http.get("*/api/auth/me", () => json({ email: ME, name: "A", picture: null, role: "admin" })),
      http.get("*/api/admin/users", () => {
        calls += 1;
        return calls === 1
          ? apiError(500, "error", "No se pudo leer a los usuarios.")
          : json(USERS);
      }),
    );
    renderScreen(<AdminPage />);
    expect(await screen.findByText("No se pudo leer a los usuarios.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("ana@viz-ia.com")).toBeInTheDocument();
  });

  it("sin usuarios: estado vacío con explicación", async () => {
    setup("admin", []);
    renderScreen(<AdminPage />);
    expect(await screen.findByText("Todavía no hay usuarios")).toBeInTheDocument();
  });
});

describe("AdminPage - auditoría", () => {
  const page = (n: number, next: string | null): AuditPage => ({
    items: Array.from({ length: n }, (_, i) => ({
      id: `a${next ?? "fin"}${i}`,
      ts: "2026-10-10T14:03:11Z",
      email: "ana@viz-ia.com",
      action: i % 2 ? "live_start" : "login_denied",
      target: "raspberry-pi-01",
      detail: null,
    })),
    next_cursor: next,
  });

  it("filtra por acción y usuario, y pagina con 'Cargar más'", async () => {
    const queries: string[] = [];
    setup();
    server.use(
      http.get("*/api/admin/audit", ({ request }) => {
        const u = new URL(request.url);
        queries.push(u.search);
        return json(u.searchParams.get("cursor") ? page(1, null) : page(2, "c1"));
      }),
    );
    renderScreen(<AdminPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Auditoría" }));
    const table = await screen.findByRole("table", { name: "Registro de auditoría" });
    expect(within(table).getByText("Acceso denegado")).toBeInTheDocument();
    expect(within(table).getByText("Inicio de video en vivo")).toBeInTheDocument();
    expect(within(table).getAllByRole("row")).toHaveLength(3);

    await userEvent.click(screen.getByRole("button", { name: "Cargar más" }));
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(4));
    expect(screen.queryByRole("button", { name: "Cargar más" })).toBeNull();

    await userEvent.type(screen.getByLabelText("Acción"), "live_start");
    await userEvent.type(screen.getByLabelText("Usuario"), "Ana@viz-ia.com");
    await userEvent.click(screen.getByRole("button", { name: "Filtrar" }));
    await waitFor(() => expect(queries.at(-1)).toContain("action=live_start"));
    expect(queries.at(-1)).toContain("email=ana%40viz-ia.com");
  });

  it("sin registros: estado vacío", async () => {
    setup();
    server.use(http.get("*/api/admin/audit", () => json({ items: [], next_cursor: null })));
    renderScreen(<AdminPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Auditoría" }));
    expect(await screen.findByText("Sin registros")).toBeInTheDocument();
  });

  it("error: mensaje y Reintentar", async () => {
    let calls = 0;
    setup();
    server.use(
      http.get("*/api/admin/audit", () => {
        calls += 1;
        return calls === 1
          ? apiError(500, "error", "No se pudo leer la auditoría.")
          : json(page(1, null));
      }),
    );
    renderScreen(<AdminPage />);
    await userEvent.click(await screen.findByRole("tab", { name: "Auditoría" }));
    expect(await screen.findByText("No se pudo leer la auditoría.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("table", { name: "Registro de auditoría" })).toBeInTheDocument();
  });
});

describe("normalizeEmail", () => {
  it("acepta correos válidos en minúsculas y rechaza el resto", () => {
    expect(normalizeEmail("  A@B.com ")).toBe("a@b.com");
    expect(normalizeEmail("sin-arroba")).toBeNull();
    expect(normalizeEmail("a@b")).toBeNull();
  });
});
