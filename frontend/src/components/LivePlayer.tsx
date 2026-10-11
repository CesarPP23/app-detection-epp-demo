import { Maximize2, Minimize2, Radio, RefreshCw, VideoOff } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  FROZEN_AFTER_MS,
  LiveConnection,
  MetricsWindow,
  type ConnState,
  type LiveMetrics,
  type ParsedFrame,
  type StreamState,
} from "@/api/live";
import { useActivity } from "@/hooks/ActivityProvider";
import { formatLatency } from "@/design/format";

/** Con la pestaña oculta más de 30 s se cierra el video (3.6). */
export const HIDDEN_CLOSE_MS = 30_000;

export type PlayerView =
  "connecting" | "waiting" | "live" | "frozen" | "ended" | "denied" | "busy" | "failed" | "paused";

export const VIEW_TEXT: Record<PlayerView, { title: string; hint?: string }> = {
  connecting: { title: "Conectando…" },
  waiting: { title: "Esperando al dispositivo", hint: "Puede tardar unos segundos." },
  live: { title: "En vivo" },
  frozen: { title: "Señal congelada", hint: "No llegan imágenes del dispositivo." },
  ended: { title: "Transmisión finalizada" },
  denied: { title: "Sin permiso para ver el video" },
  busy: { title: "Hay demasiados espectadores", hint: "Intenta de nuevo en unos minutos." },
  failed: { title: "No se pudo conectar", hint: "Revisa tu conexión e inténtalo de nuevo." },
  paused: { title: "Video en pausa", hint: "Se cerró por inactividad o por ocultar la pestaña." },
};

export function resolveView(
  conn: ConnState,
  stream: StreamState | null,
  frozen: boolean,
  hasFrame: boolean,
): PlayerView {
  if (conn === "denied") return "denied";
  if (conn === "busy") return "busy";
  if (conn === "failed") return "failed";
  if (conn === "closed") return "paused";
  if (conn === "connecting" || conn === "reconnecting") return hasFrame ? "frozen" : "connecting";
  if (stream === "ended") return "ended";
  if (stream === "waiting" && !hasFrame) return "waiting";
  if (hasFrame) return frozen ? "frozen" : "live";
  return "waiting";
}

interface Props {
  deviceId: string;
  /** Barra de acciones junto a las métricas (p. ej. "Pausar detección"). */
  actions?: ReactNode;
  onMetrics?: (m: LiveMetrics) => void;
  className?: string;
}

export function LivePlayer({ deviceId, actions, onMetrics, className = "" }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const connRef = useRef<LiveConnection | null>(null);
  const metricsRef = useRef(new MetricsWindow());
  const lastFrameAt = useRef(0);
  const decoding = useRef(false);
  const pending = useRef<ParsedFrame | null>(null);
  const [conn, setConn] = useState<ConnState>("connecting");
  const [stream, setStream] = useState<StreamState | null>(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [frozen, setFrozen] = useState(false);
  const [metrics, setMetrics] = useState<LiveMetrics>({ fps: 0, latencyMs: null });
  const [full, setFull] = useState(false);
  const { idle, visible } = useActivity();
  const [hiddenTooLong, setHiddenTooLong] = useState(false);

  // Oculta > 30 s → cerrar.
  useEffect(() => {
    if (visible) {
      setHiddenTooLong(false);
      return;
    }
    const t = setTimeout(() => setHiddenTooLong(true), HIDDEN_CLOSE_MS);
    return () => clearTimeout(t);
  }, [visible]);

  const shouldRun = !idle && !hiddenTooLong;

  const draw = useCallback(async (frame: ParsedFrame) => {
    // Si llega un frame mientras se decodifica otro, se descarta el viejo: se dibuja el más reciente.
    if (decoding.current) {
      pending.current = frame;
      return;
    }
    decoding.current = true;
    let next: ParsedFrame | null = frame;
    while (next) {
      pending.current = null;
      try {
        const bmp = await createImageBitmap(new Blob([next.jpeg], { type: "image/jpeg" }));
        const c = canvasRef.current;
        if (c) {
          if (c.width !== bmp.width || c.height !== bmp.height) {
            c.width = bmp.width;
            c.height = bmp.height;
          }
          c.getContext("2d")?.drawImage(bmp, 0, 0, c.width, c.height);
        }
        bmp.close();
        const now = Date.now();
        lastFrameAt.current = now;
        metricsRef.current.push(next.capturedAt, now);
        setHasFrame(true);
        setFrozen(false);
      } catch {
        /* JPEG corrupto: se ignora */
      }
      next = pending.current;
    }
    decoding.current = false;
  }, []);

  useEffect(() => {
    if (!shouldRun) {
      setConn("closed");
      return;
    }
    metricsRef.current.reset();
    setHasFrame(false);
    setStream(null);
    const live = new LiveConnection(
      { deviceId },
      { onFrame: draw, onStatus: setStream, onConn: setConn },
    );
    connRef.current = live;
    live.connect();
    return () => {
      live.dispose(); // salir de la ruta cierra el WebSocket → la Pi corta
      connRef.current = null;
    };
  }, [deviceId, shouldRun, draw]);

  // Señal congelada (> 2 s sin frames) y métricas cada 500 ms.
  useEffect(() => {
    const t = setInterval(() => {
      if (hasFrame && Date.now() - lastFrameAt.current > FROZEN_AFTER_MS) setFrozen(true);
      const m = metricsRef.current.snapshot();
      setMetrics(m);
      onMetrics?.(m);
    }, 500);
    return () => clearInterval(t);
  }, [hasFrame, onMetrics]);

  const toggleFull = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await boxRef.current?.requestFullscreen();
    } catch {
      /* pantalla completa no disponible */
    }
  }, []);

  useEffect(() => {
    const onFs = () => setFull(!!document.fullscreenElement);
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.isContentEditable))
        return;
      if (e.key === "f" && !e.ctrlKey && !e.metaKey && !e.altKey) void toggleFull();
    };
    document.addEventListener("fullscreenchange", onFs);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("fullscreenchange", onFs);
      window.removeEventListener("keydown", onKey);
    };
  }, [toggleFull]);

  const view = resolveView(conn, stream, frozen, hasFrame);
  const text = VIEW_TEXT[view];
  const showOverlay = view !== "live";
  const canRetry = view === "failed" || view === "ended" || view === "paused";

  return (
    <div className={className}>
      <div className="mb-2 flex flex-wrap items-center gap-3 text-sm">
        <span
          className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-1 px-3 py-1 font-medium text-ink"
          data-view={view}
        >
          <Radio
            aria-hidden
            className={`size-4 text-accent ${view === "live" ? "live-dot" : ""}`}
          />
          {view === "live" ? "EN VIVO" : text.title}
        </span>
        {view === "live" && (
          <span className="tabular text-ink-2">
            {metrics.latencyMs !== null && formatLatency(metrics.latencyMs)} ·{" "}
            {Math.round(metrics.fps)} fps
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {actions}
          <button
            type="button"
            onClick={toggleFull}
            aria-label={full ? "Salir de pantalla completa" : "Pantalla completa (f)"}
            className="inline-flex size-9 items-center justify-center rounded-control border border-line text-ink hover:bg-surface-2"
          >
            {full ? (
              <Minimize2 aria-hidden className="size-4" />
            ) : (
              <Maximize2 aria-hidden className="size-4" />
            )}
          </button>
        </span>
      </div>

      <div
        ref={boxRef}
        className="relative w-full overflow-hidden rounded-video bg-black"
        style={{ aspectRatio: "16 / 9" }}
      >
        <canvas
          ref={canvasRef}
          role="img"
          aria-label="Video en vivo de la cámara"
          width={640}
          height={360}
          className={`absolute inset-0 size-full object-contain transition-opacity ${view === "frozen" ? "opacity-40" : ""}`}
        />
        {showOverlay && (
          <div
            role="status"
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-4 text-center text-white"
          >
            <VideoOff aria-hidden className="size-8 opacity-80" />
            <p className="text-base font-semibold">{text.title}</p>
            {text.hint && <p className="text-sm opacity-80">{text.hint}</p>}
            {canRetry && (
              <button
                type="button"
                onClick={() =>
                  view === "paused" ? setHiddenTooLong(false) : connRef.current?.retry()
                }
                className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-control bg-white/15 px-4 text-sm font-medium hover:bg-white/25"
              >
                <RefreshCw aria-hidden className="size-4" />
                Reintentar
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
