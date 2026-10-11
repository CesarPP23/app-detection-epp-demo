import { useCallback, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import { ApiError } from "@/api/client";
import { useAuthConfig } from "@/api/queries";
import { useAuth } from "@/auth/AuthProvider";
import { GoogleButton } from "@/auth/GoogleButton";
import { Skeleton } from "@/components/Skeleton";

/** Solo rutas internas: evita redirecciones abiertas a través de ?next=. */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}

/** Email del ID token de Google, solo para mostrarlo en el aviso (la verificación es del backend). */
export function emailFromCredential(credential: string): string | null {
  try {
    const part = credential.split(".")[1];
    if (!part) return null;
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/");
    const payload = JSON.parse(
      atob(b64.padEnd(b64.length + ((4 - (b64.length % 4)) % 4), "=")),
    ) as {
      email?: unknown;
    };
    return typeof payload.email === "string" ? payload.email : null;
  } catch {
    return null;
  }
}

/** Pantalla 1: inicio de sesión. Si la cuenta no está autorizada no se revela nada más. */
export default function LoginPage() {
  const { user, loading, loginWithCredential } = useAuth();
  const config = useAuthConfig();
  const [params] = useSearchParams();
  const [denied, setDenied] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onCredential = useCallback(
    (credential: string) => {
      setDenied(null);
      setFailure(null);
      setBusy(true);
      loginWithCredential(credential)
        .catch((e: unknown) => {
          if (e instanceof ApiError && e.forbidden) {
            setDenied(emailFromCredential(credential) ?? "tu cuenta de Google");
          } else {
            setFailure(
              e instanceof ApiError ? e.message : "No se pudo iniciar sesión. Intenta de nuevo.",
            );
          }
        })
        .finally(() => setBusy(false));
    },
    [loginWithCredential],
  );

  if (user) return <Navigate to={safeNext(params.get("next"))} replace />;

  return (
    <main className="flex min-h-dvh items-center justify-center bg-page px-4 py-8">
      <section
        aria-labelledby="titulo"
        className="w-full max-w-sm rounded-panel border border-line bg-surface-1 p-8 text-center"
      >
        <h1 id="titulo" className="text-2xl font-semibold text-ink">
          EPP Monitor
        </h1>
        <p className="mt-2 text-sm text-ink-2">Monitoreo de equipos de protección personal</p>

        <div className="mt-8" aria-busy={loading || config.isPending || busy}>
          {loading || config.isPending ? (
            <Skeleton className="mx-auto h-10 w-[280px]" label="Preparando el inicio de sesión" />
          ) : config.isError || !config.data?.client_id ? (
            <p role="alert" className="text-sm text-critical">
              El inicio de sesión no está disponible por ahora. Intenta de nuevo más tarde.
            </p>
          ) : (
            <GoogleButton clientId={config.data.client_id} onCredential={onCredential} />
          )}
        </div>

        {denied && (
          <p role="alert" className="mt-4 text-sm text-critical">
            Tu cuenta ({denied}) no tiene acceso. Pide acceso al administrador.
          </p>
        )}
        {failure && (
          <p role="alert" className="mt-4 text-sm text-critical">
            {failure}
          </p>
        )}

        <p className="mt-8 text-xs text-ink-3">
          Al continuar aceptas que el sistema registre tu actividad de acceso. Las imágenes de
          trabajadores se tratan según el{" "}
          <Link to="/privacidad" className="text-accent underline">
            aviso de privacidad
          </Link>
          .
        </p>
      </section>
    </main>
  );
}
