/** Cliente WebSocket del video en vivo (protocolo 1.2/3.6).
 * Mensaje binario = uint64 big-endian con captured_at (ms Unix) + JPEG (empieza con FF D8).
 * Mensaje de texto = JSON {type: "status", state: "waiting" | "live" | "ended"}. */

export const HEADER_BYTES = 8;
export const BACKOFF_MS = [1000, 2000, 4000, 8000] as const;
export const MAX_RECONNECTS = 5;
export const FROZEN_AFTER_MS = 2000;

export interface ParsedFrame {
  capturedAt: number;
  jpeg: ArrayBuffer;
}

/** Devuelve null si el mensaje no cumple el protocolo (cabecera corta o sin FF D8). */
export function parseFrame(buf: ArrayBuffer): ParsedFrame | null {
  if (buf.byteLength <= HEADER_BYTES + 2) return null;
  const view = new DataView(buf);
  if (view.getUint8(HEADER_BYTES) !== 0xff || view.getUint8(HEADER_BYTES + 1) !== 0xd8) return null;
  return { capturedAt: Number(view.getBigUint64(0)), jpeg: buf.slice(HEADER_BYTES) };
}

export type StreamState = "waiting" | "live" | "ended";
export type ConnState =
  "connecting" | "open" | "reconnecting" | "closed" | "denied" | "busy" | "failed";

export interface LiveMetrics {
  fps: number;
  latencyMs: number | null;
}

/** Ventana móvil para fps y latencia aproximada (depende de la sincronización de relojes). */
export class MetricsWindow {
  private samples: { receivedAt: number; capturedAt: number }[] = [];

  constructor(private readonly windowMs = 3000) {}

  push(capturedAt: number, receivedAt: number): void {
    this.samples.push({ receivedAt, capturedAt });
    const min = receivedAt - this.windowMs;
    while (this.samples.length && (this.samples[0]?.receivedAt ?? 0) < min) this.samples.shift();
  }

  snapshot(): LiveMetrics {
    const n = this.samples.length;
    if (n === 0) return { fps: 0, latencyMs: null };
    const first = this.samples[0];
    const last = this.samples[n - 1];
    if (!first || !last) return { fps: 0, latencyMs: null };
    const span = (last.receivedAt - first.receivedAt) / 1000;
    const fps = n > 1 && span > 0 ? (n - 1) / span : 0;
    return { fps, latencyMs: Math.max(0, last.receivedAt - last.capturedAt) };
  }

  reset(): void {
    this.samples = [];
  }
}

export interface LiveHandlers {
  onFrame: (frame: ParsedFrame) => void;
  onStatus: (state: StreamState) => void;
  onConn: (state: ConnState) => void;
}

export interface LiveOptions {
  deviceId: string;
  /** Inyectable para pruebas. */
  createSocket?: (url: string) => WebSocket;
  baseUrl?: string;
}

function defaultUrl(deviceId: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/api/devices/${encodeURIComponent(deviceId)}/live`;
}

export class LiveConnection {
  private ws: WebSocket | null = null;
  private attempts = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private disposed = false;
  private handlers: LiveHandlers;
  private opts: LiveOptions;

  constructor(opts: LiveOptions, handlers: LiveHandlers) {
    this.opts = opts;
    this.handlers = handlers;
  }

  connect(): void {
    if (this.disposed) return;
    clearTimeout(this.timer);
    this.handlers.onConn(this.attempts === 0 ? "connecting" : "reconnecting");
    const url = this.opts.baseUrl ?? defaultUrl(this.opts.deviceId);
    const ws = this.opts.createSocket ? this.opts.createSocket(url) : new WebSocket(url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      this.attempts = 0;
      this.handlers.onConn("open");
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (typeof ev.data === "string") {
        try {
          const msg = JSON.parse(ev.data) as { type?: string; state?: StreamState };
          if (msg.type === "status" && msg.state) this.handlers.onStatus(msg.state);
        } catch {
          /* mensaje de texto ajeno al protocolo */
        }
        return;
      }
      const frame = parseFrame(ev.data as ArrayBuffer);
      if (frame) this.handlers.onFrame(frame);
    };
    ws.onclose = (ev: CloseEvent) => {
      if (this.disposed || this.ws !== ws) return;
      if (ev.code === 1008) return this.handlers.onConn("denied");
      if (ev.code === 1013) return this.handlers.onConn("busy");
      this.scheduleReconnect();
    };
    ws.onerror = () => {
      /* el cierre posterior dispara la reconexión */
    };
  }

  private scheduleReconnect(): void {
    if (this.attempts >= MAX_RECONNECTS) return this.handlers.onConn("failed");
    const delay = BACKOFF_MS[Math.min(this.attempts, BACKOFF_MS.length - 1)] ?? 8000;
    this.attempts += 1;
    this.handlers.onConn("reconnecting");
    this.timer = setTimeout(() => this.connect(), delay);
  }

  /** Botón "Reintentar" tras `failed`. */
  retry(): void {
    this.attempts = 0;
    this.close(false);
    this.connect();
  }

  private close(final: boolean): void {
    clearTimeout(this.timer);
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onclose = null;
      ws.onmessage = null;
      ws.close();
    }
    if (final) this.disposed = true;
  }

  /** Cierra el socket: lo que apaga el streaming de la Pi y el costo (3.6). */
  dispose(): void {
    this.close(true);
    this.handlers.onConn("closed");
  }
}
