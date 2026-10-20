import * as Dialog from "@radix-ui/react-dialog";
import { RotateCcw, X, ZoomIn, ZoomOut } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CaptureStatus, DetectionBox } from "@/api/types";
import { CaptureViewer } from "./CaptureViewer";

interface Props {
  open: boolean;
  onClose: () => void;
  src: string | null | undefined;
  status: CaptureStatus;
  eventClass: string;
  detections: DetectionBox[];
  eventTime: string;
  confidence: number;
  showBoxes: boolean;
  onShowBoxes: (v: boolean) => void;
}

const MIN = 1;
const MAX = 6;
const STEP = 1.25;

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/** Visor a pantalla completa de la captura, con las cajas y etiquetas superpuestas.
 * Zoom con rueda, botones, + / − / 0 o doble clic; arrastrar para desplazar. */
export function CaptureLightbox({ open, onClose, showBoxes, onShowBoxes, ...cap }: Props) {
  const stage = useRef<HTMLDivElement | null>(null);
  // El Portal de Radix monta el contenido después del primer render: hace falta saber cuándo
  // existe el nodo para registrar la rueda.
  const [stageEl, setStageEl] = useState<HTMLDivElement | null>(null);
  const [ratio, setRatio] = useState(16 / 9);
  const [view, setView] = useState({ z: 1, x: 0, y: 0 });
  const z = view.z;
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);

  // Mantiene la imagen cubriendo el área visible: no se puede desplazar más allá del borde.
  const bound = useCallback((x: number, y: number, zoom: number) => {
    const el = stage.current?.firstElementChild as HTMLElement | null | undefined;
    if (!el) return { x: 0, y: 0 };
    const mx = ((zoom - 1) * el.offsetWidth) / 2;
    const my = ((zoom - 1) * el.offsetHeight) / 2;
    return { x: clamp(x, -mx, mx), y: clamp(y, -my, my) };
  }, []);

  /** Nuevo zoom hacia un punto (px relativos al centro del área), con el desplazamiento acotado. */
  const zoomed = useCallback(
    (v: { z: number; x: number; y: number }, next: number, px: number, py: number) => {
      const nz = clamp(next, MIN, MAX);
      return { z: nz, ...bound(px - ((px - v.x) * nz) / v.z, py - ((py - v.y) * nz) / v.z, nz) };
    },
    [bound],
  );

  /** Zoom hacia un punto; sin punto, hacia el centro. */
  const zoomTo = useCallback(
    (next: number, px = 0, py = 0) => {
      setView((v) => zoomed(v, next, px, py));
    },
    [zoomed],
  );

  const reset = () => setView({ z: 1, x: 0, y: 0 });

  useEffect(() => {
    if (!open) reset();
  }, [open]);

  // La rueda necesita un listener no pasivo para poder evitar el scroll de la página.
  useEffect(() => {
    const el = stageEl;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const px = e.clientX - r.left - r.width / 2;
      const py = e.clientY - r.top - r.height / 2;
      setView((v) => zoomed(v, v.z * (e.deltaY < 0 ? STEP : 1 / STEP), px, py));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [stageEl, zoomed]);

  const btn =
    "inline-flex size-11 items-center justify-center rounded-control border border-white/20 text-white hover:bg-white/10 disabled:opacity-40 md:size-9";

  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/90" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed inset-0 z-[70] flex flex-col bg-neutral-950"
          onKeyDown={(e) => {
            // Las flechas no deben cambiar de evento en el panel de detalle de abajo.
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") e.preventDefault();
            if (e.key === "+" || e.key === "=") zoomTo(z * STEP);
            if (e.key === "-") zoomTo(z / STEP);
            if (e.key === "0") reset();
          }}
        >
          <div className="flex flex-wrap items-center gap-2 px-4 py-3 text-white">
            <Dialog.Title className="text-base font-semibold">Captura del evento</Dialog.Title>
            <label className="ml-4 flex min-h-11 items-center gap-2 text-sm md:min-h-9">
              <input
                type="checkbox"
                role="switch"
                className="size-4 accent-[var(--accent)]"
                checked={showBoxes}
                onChange={(e) => onShowBoxes(e.target.checked)}
              />
              Mostrar cajas
            </label>
            <div className="ml-auto flex items-center gap-2">
              <button
                type="button"
                className={btn}
                onClick={() => zoomTo(z / STEP)}
                disabled={z <= MIN}
                aria-label="Alejar (−)"
              >
                <ZoomOut aria-hidden className="size-4" />
              </button>
              <span className="w-12 text-center text-sm tabular-nums" aria-live="polite">
                {Math.round(z * 100)} %
              </span>
              <button
                type="button"
                className={btn}
                onClick={() => zoomTo(z * STEP)}
                disabled={z >= MAX}
                aria-label="Acercar (+)"
              >
                <ZoomIn aria-hidden className="size-4" />
              </button>
              <button
                type="button"
                className={btn}
                onClick={reset}
                disabled={z === 1}
                aria-label="Restablecer zoom (0)"
              >
                <RotateCcw aria-hidden className="size-4" />
              </button>
              <Dialog.Close className={btn} aria-label="Cerrar visor">
                <X aria-hidden className="size-4" />
              </Dialog.Close>
            </div>
          </div>
          <div
            ref={(el) => {
              stage.current = el;
              setStageEl(el);
            }}
            className={`flex min-h-0 flex-1 touch-none items-center justify-center overflow-hidden p-4 select-none ${
              z > 1 ? "cursor-grab active:cursor-grabbing" : "cursor-zoom-in"
            }`}
            onDoubleClick={(e) => {
              if (z > 1) return reset();
              const r = e.currentTarget.getBoundingClientRect();
              zoomTo(2.5, e.clientX - r.left - r.width / 2, e.clientY - r.top - r.height / 2);
            }}
            onPointerDown={(e) => {
              if (z <= 1) return;
              e.currentTarget.setPointerCapture(e.pointerId);
              drag.current = { x: e.clientX, y: e.clientY, tx: view.x, ty: view.y };
            }}
            onPointerMove={(e) => {
              const d = drag.current;
              if (!d) return;
              setView({ z, ...bound(d.tx + e.clientX - d.x, d.ty + e.clientY - d.y, z) });
            }}
            onPointerUp={() => (drag.current = null)}
            onPointerCancel={() => (drag.current = null)}
          >
            <div
              style={{
                width: `min(100%, calc((100dvh - 7rem) * ${ratio}))`,
                transform: `translate(${view.x}px, ${view.y}px) scale(${z})`,
                transition: drag.current ? "none" : "transform 120ms ease-out",
              }}
            >
              <CaptureViewer
                {...cap}
                showBoxes={showBoxes}
                large
                labelScale={1 / z}
                onAspect={setRatio}
                className="border-white/10"
              />
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
