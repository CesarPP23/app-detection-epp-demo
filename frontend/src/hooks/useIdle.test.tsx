import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { IDLE_TIMEOUT_MS, useIdle } from "./useIdle";

describe("useIdle", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("usa 15 minutos por defecto", () => {
    expect(IDLE_TIMEOUT_MS).toBe(15 * 60 * 1000);
  });

  it("pasa a inactivo tras el tiempo sin interacción", () => {
    const { result } = renderHook(() => useIdle(1000));
    expect(result.current.idle).toBe(false);
    act(() => void vi.advanceTimersByTime(1001));
    expect(result.current.idle).toBe(true);
  });

  it("la actividad reinicia el contador", () => {
    const { result } = renderHook(() => useIdle(1000));
    act(() => void vi.advanceTimersByTime(800));
    act(() => void window.dispatchEvent(new Event("keydown")));
    act(() => void vi.advanceTimersByTime(800));
    expect(result.current.idle).toBe(false);
    act(() => void vi.advanceTimersByTime(300));
    expect(result.current.idle).toBe(true);
  });

  it("en pausa solo se reanuda con resume(), no con un movimiento accidental", () => {
    const { result } = renderHook(() => useIdle(1000));
    act(() => void vi.advanceTimersByTime(1001));
    act(() => void window.dispatchEvent(new Event("pointermove")));
    expect(result.current.idle).toBe(true);
    act(() => result.current.resume());
    expect(result.current.idle).toBe(false);
  });

  it("limpia los listeners al desmontar", () => {
    const spy = vi.spyOn(window, "removeEventListener");
    const { unmount } = renderHook(() => useIdle(1000));
    unmount();
    expect(spy).toHaveBeenCalledWith("keydown", expect.any(Function));
  });
});
