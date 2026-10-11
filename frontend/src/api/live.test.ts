import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  BACKOFF_MS,
  LiveConnection,
  MAX_RECONNECTS,
  MetricsWindow,
  parseFrame,
  type ConnState,
} from "./live";

function frame(capturedAt: number, jpeg: number[] = [0xff, 0xd8, 1, 2, 3]): ArrayBuffer {
  const buf = new ArrayBuffer(8 + jpeg.length);
  new DataView(buf).setBigUint64(0, BigInt(capturedAt));
  new Uint8Array(buf, 8).set(jpeg);
  return buf;
}

describe("parseFrame (protocolo 1.2)", () => {
  it("lee captured_at big-endian y separa el JPEG", () => {
    const f = parseFrame(frame(1_760_105_000_123));
    expect(f?.capturedAt).toBe(1_760_105_000_123);
    expect(new Uint8Array(f!.jpeg)[0]).toBe(0xff);
    expect(new Uint8Array(f!.jpeg)[1]).toBe(0xd8);
    expect(f!.jpeg.byteLength).toBe(5);
  });

  it("rechaza mensajes cortos o sin FF D8", () => {
    expect(parseFrame(new ArrayBuffer(8))).toBeNull();
    expect(parseFrame(new ArrayBuffer(10))).toBeNull();
    expect(parseFrame(frame(1, [0x00, 0x01, 0x02]))).toBeNull();
  });
});

describe("MetricsWindow", () => {
  it("calcula fps y latencia aproximada", () => {
    const m = new MetricsWindow(3000);
    for (let i = 0; i < 9; i++) m.push(1000 + i * 125 - 400, 1000 + i * 125);
    const s = m.snapshot();
    expect(s.fps).toBeCloseTo(8, 0);
    expect(s.latencyMs).toBe(400);
  });

  it("descarta muestras fuera de la ventana y no da latencias negativas", () => {
    const m = new MetricsWindow(1000);
    m.push(0, 0);
    m.push(5000, 4000); // reloj adelantado
    const s = m.snapshot();
    expect(s.latencyMs).toBe(0);
    expect(s.fps).toBe(0); // solo queda una muestra
  });

  it("sin datos: 0 fps y latencia nula", () => {
    expect(new MetricsWindow().snapshot()).toEqual({ fps: 0, latencyMs: null });
  });
});

class FakeSocket {
  static all: FakeSocket[] = [];
  binaryType = "";
  onopen: (() => void) | null = null;
  onclose: ((e: { code: number }) => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(public url: string) {
    FakeSocket.all.push(this);
  }
  close() {
    this.closed = true;
  }
}

describe("LiveConnection", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSocket.all = [];
  });
  afterEach(() => vi.useRealTimers());

  function make() {
    const states: ConnState[] = [];
    const frames: number[] = [];
    const statuses: string[] = [];
    const c = new LiveConnection(
      {
        deviceId: "pi",
        baseUrl: "ws://x/live",
        createSocket: (u) => new FakeSocket(u) as unknown as WebSocket,
      },
      {
        onFrame: (f) => frames.push(f.capturedAt),
        onStatus: (s) => statuses.push(s),
        onConn: (s) => states.push(s),
      },
    );
    return { c, states, frames, statuses };
  }
  const last = () => FakeSocket.all[FakeSocket.all.length - 1]!;

  it("entrega frames binarios y estados de texto; descarta basura", () => {
    const { c, frames, statuses } = make();
    c.connect();
    last().onopen?.();
    last().onmessage?.({ data: JSON.stringify({ type: "status", state: "live" }) });
    last().onmessage?.({ data: "no es json" });
    last().onmessage?.({ data: frame(42) });
    last().onmessage?.({ data: new ArrayBuffer(3) });
    expect(statuses).toEqual(["live"]);
    expect(frames).toEqual([42]);
  });

  it("reconecta con backoff 1-2-4-8 s y falla tras 5 intentos", () => {
    const { c, states } = make();
    c.connect();
    for (let i = 0; i < MAX_RECONNECTS; i++) {
      last().onclose?.({ code: 1006 });
      vi.advanceTimersByTime(BACKOFF_MS[Math.min(i, BACKOFF_MS.length - 1)]! + 1);
    }
    expect(FakeSocket.all.length).toBe(MAX_RECONNECTS + 1);
    last().onclose?.({ code: 1006 });
    expect(states[states.length - 1]).toBe("failed");
  });

  it("no reconecta ante 1008 (sin permiso) ni 1013 (saturado)", () => {
    const a = make();
    a.c.connect();
    last().onclose?.({ code: 1008 });
    expect(a.states.at(-1)).toBe("denied");
    const b = make();
    b.c.connect();
    last().onclose?.({ code: 1013 });
    expect(b.states.at(-1)).toBe("busy");
    vi.advanceTimersByTime(20_000);
    expect(FakeSocket.all.length).toBe(2);
  });

  it("dispose() cierra el socket y cancela reconexiones", () => {
    const { c, states } = make();
    c.connect();
    const ws = last();
    c.dispose();
    expect(ws.closed).toBe(true);
    expect(states.at(-1)).toBe("closed");
    vi.advanceTimersByTime(20_000);
    expect(FakeSocket.all.length).toBe(1);
  });
});
