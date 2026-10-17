"""Benchmark de latencia/FPS del modelo de EPP (Fase 7 del plan de remediación).

Mide `{pt, ncnn} × {320, 416, 640}` (10 inferencias de warm-up + 100 de medición cada
combinación, por defecto) y aplica la regla de selección del plan: la combinación más
rápida con `mAP50-95` ≤ `--max-map-drop` puntos por debajo de `--baseline` (default
`pt@640`) y `p95 < 0.7 / --inference-fps`. Sin `--data` (data.yaml de validación) no hay
`mAP50-95`: se documenta `ncnn@416` como supuesto, tal como indica el plan.

Uso:
    python -m tools.benchmark
    python -m tools.benchmark --formats pt,ncnn --sizes 320,416,640 --warmup 10 --iters 100
    python -m tools.benchmark --data data/epp_v3/data.yaml   # si existe un dataset de validación
    python -m tools.benchmark --json-out benchmark_result.json

Sin `--video`, genera un clip sintético de ~6 s (figuras simples sobre una textura de fondo
pseudoaleatoria determinista, sin contenido real) en `media/bench_synthetic.mp4` SOLO para
tener una fuente de frames consistente entre combinaciones; sirve para medir latencia/FPS,
no precisión (no hay `mAP50-95` real sin `--data`). `media/*.mp4` está fuera de git.

Las combinaciones `ncnn@<imgsz>` se exportan automáticamente (vía `tools.export_model`) si
no existen aún en `--export-root` (default: `<weights>/../exports/`); usa
`--no-auto-export` para desactivarlo y exportar a mano de antemano.

Si una combinación falla (p. ej. falta `ultralytics`/`torch`, o Smart App Control bloquea
sus DLL en Windows — ver `docs/MODEL.md`), se reporta como fila de error y el benchmark
continúa con las demás combinaciones en vez de abortar.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

DEFAULT_SIZES = (320, 416, 640)
DEFAULT_FORMATS = ("pt", "ncnn")


# ---------------------------------------------------------------------------------------------
# Clip sintético (solo para latencia/FPS, no representa escenas reales)
# ---------------------------------------------------------------------------------------------


def generate_synthetic_clip(
    path: Path, width: int = 1280, height: int = 720, fps: int = 20, seconds: float = 6.0, seed: int = 0
) -> Path:
    """Genera un clip sintético corto (textura de fondo + figuras simples en movimiento).

    Uso exclusivo: alimentar el modelo con frames de tamaño/variación consistente entre
    combinaciones para medir latencia/FPS. No es una escena real ni sirve para medir
    `mAP50-95`; ver la limitación documentada en `docs/MODEL.md`.

    La textura de fondo se genera con aritmética modular determinista (en vez de
    `numpy.random`) porque en esta máquina de desarrollo Smart App Control bloquea la
    extensión compilada `numpy.random._common` (el mismo tipo de bloqueo que afecta a
    `torch`, ver `docs/MODEL.md`); así el script funciona igual en cualquier entorno.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(
            f"No se pudo abrir VideoWriter para {path} (¿falta el backend de video de OpenCV?)"
        )
    n_frames = max(1, int(fps * seconds))
    yy, xx = np.meshgrid(np.arange(height), np.arange(width), indexing="ij")
    base_texture = ((xx * 13 + yy * 7 + seed * 101) % 37).astype(np.uint8)
    try:
        for i in range(n_frames):
            shade = ((base_texture.astype(np.int32) + i * 3) % 40).astype(np.uint8)
            frame = np.stack([shade, shade, shade], axis=-1)
            cx = int(width * (0.5 + 0.3 * np.sin(i / 10)))
            cy = int(height * (0.5 + 0.3 * np.cos(i / 13)))
            cv2.rectangle(frame, (cx - 80, cy - 120), (cx + 80, cy + 120), (60, 140, 60), -1)
            cv2.circle(frame, (cx, cy - 150), 40, (180, 180, 60), -1)
            writer.write(frame)
    finally:
        writer.release()
    return path


def iter_frames(video_path: Path, count: int):
    """Produce `count` frames leyendo `video_path` en loop (como `FileSource` en capture.py)."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"No se pudo abrir {video_path}")
    try:
        produced = 0
        while produced < count:
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            yield frame
            produced += 1
    finally:
        cap.release()


# ---------------------------------------------------------------------------------------------
# Medición pura (testeable sin ultralytics/torch/ncnn)
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LatencyStats:
    p50_ms: float
    p95_ms: float
    fps: float


def latency_stats(latencies_ms: list[float]) -> LatencyStats:
    if not latencies_ms:
        return LatencyStats(0.0, 0.0, 0.0)
    arr = np.asarray(latencies_ms, dtype=float)
    mean_s = float(arr.mean()) / 1000.0
    return LatencyStats(
        p50_ms=float(np.percentile(arr, 50)),
        p95_ms=float(np.percentile(arr, 95)),
        fps=(1.0 / mean_s if mean_s > 0 else 0.0),
    )


@dataclass
class BenchResult:
    format: str  # "pt" | "ncnn"
    imgsz: int
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    fps: float = 0.0
    map5095: float | None = None
    error: str | None = None

    @property
    def label(self) -> str:
        return f"{self.format}@{self.imgsz}"


class Runner(Protocol):
    def predict(self, frame: np.ndarray) -> None: ...


def run_timed(runner: Runner, frames: list[np.ndarray], warmup: int, iters: int) -> list[float]:
    """Corre `warmup` inferencias sin medir y luego `iters` midiendo cada una (ms)."""
    if len(frames) < warmup + iters:
        raise ValueError(f"Se necesitan {warmup + iters} frames y solo hay {len(frames)}")
    for f in frames[:warmup]:
        runner.predict(f)
    out: list[float] = []
    for f in frames[warmup : warmup + iters]:
        t0 = time.perf_counter()
        runner.predict(f)
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def render_table(results: list[BenchResult]) -> str:
    header = f"{'combo':<10} {'p50_ms':>8} {'p95_ms':>8} {'fps':>7} {'mAP50-95':>9}"
    lines = [header, "-" * len(header)]
    for r in results:
        if r.error:
            lines.append(f"{r.label:<10} ERROR: {r.error}")
            continue
        m = f"{r.map5095:.3f}" if r.map5095 is not None else "n/d"
        lines.append(f"{r.label:<10} {r.p50_ms:8.1f} {r.p95_ms:8.1f} {r.fps:7.2f} {m:>9}")
    return "\n".join(lines)


def select_combo(
    results: list[BenchResult],
    *,
    baseline: str = "pt@640",
    max_map_drop: float = 2.0,
    inference_fps: float = 5.0,
    default_assumption: str = "ncnn@416",
) -> tuple[str, str]:
    """Regla de selección del plan (Fase 7): la combinación más rápida con `mAP50-95` a lo
    sumo `max_map_drop` puntos porcentuales por debajo de `baseline` y
    `p95 < 0.7 / inference_fps`. `max_map_drop` está en puntos porcentuales (p. ej. `2.0`
    significa 2 puntos) aunque `BenchResult.map5095` sea una fracción 0-1 (lo que devuelve
    `metrics.box.map` de Ultralytics); por eso la diferencia se escala ×100 antes de comparar.
    Sin `mAP50-95` disponible para todas las combinaciones (sin dataset de validación),
    se documenta `default_assumption` como supuesto, tal como indica el plan.
    """
    ok = [r for r in results if r.error is None]
    base = next((r for r in ok if r.label == baseline), None)
    have_map = base is not None and base.map5095 is not None and all(r.map5095 is not None for r in ok)
    if not have_map:
        return (
            default_assumption,
            "sin dataset de validación local (mAP50-95 no disponible): se aplica el supuesto "
            "por defecto indicado en el plan, no una medición",
        )

    threshold_p95_s = 0.7 / inference_fps
    candidates = [
        r
        for r in ok
        if (base.map5095 - r.map5095) * 100.0 <= max_map_drop  # type: ignore[operator]
        and (r.p95_ms / 1000.0) < threshold_p95_s
    ]
    if not candidates:
        return (
            default_assumption,
            f"ninguna combinación cumple mAP50-95 ≤ {max_map_drop} pts bajo {baseline} y "
            f"p95 < {threshold_p95_s:.3f}s: se aplica el supuesto por defecto del plan",
        )
    best = min(candidates, key=lambda r: r.p95_ms)
    return (
        best.label,
        f"más rápida (menor p95) que cumple mAP50-95 ≤ {max_map_drop} pts bajo {baseline} "
        f"y p95 < 0.7/INFERENCE_FPS ({threshold_p95_s:.3f}s)",
    )


def cpu_temperature_celsius() -> tuple[float | None, str]:
    """Best-effort: en Windows, `psutil.sensors_temperatures` no está implementado."""
    try:
        import psutil

        temps = psutil.sensors_temperatures()
    except ImportError:
        return None, "no disponible: falta psutil"
    except AttributeError:
        return None, "no disponible en este sistema operativo (normal en Windows; sí funciona en la Pi)"
    if not temps:
        return None, "no disponible: el sistema no expone sensores de temperatura a psutil"
    for key in ("cpu_thermal", "coretemp", "cpu-thermal"):
        if temps.get(key):
            return temps[key][0].current, "ok"
    first = next(iter(temps.values()), None)
    return (first[0].current if first else None), "ok (sensor genérico, no identificado como CPU)"


# ---------------------------------------------------------------------------------------------
# Runners reales (ultralytics; mismo backend que epp_edge.inference.load_yolo)
# ---------------------------------------------------------------------------------------------


class UltralyticsRunner:
    def __init__(self, model_path: Path, imgsz: int, conf: float = 0.5, iou: float = 0.45) -> None:
        from ultralytics import YOLO  # import tardío: puede fallar (torch ausente/bloqueado)

        self._model = YOLO(str(model_path), task="detect")
        self._imgsz = imgsz
        self._conf = conf
        self._iou = iou

    def predict(self, frame: np.ndarray) -> None:
        self._model.predict(frame, imgsz=self._imgsz, conf=self._conf, iou=self._iou, verbose=False)


def ensure_ncnn_model(weights: Path, export_root: Path, imgsz: int, auto_export: bool) -> Path:
    """Devuelve el directorio `*_ncnn_model` para `imgsz`, exportándolo si falta.

    Cada tamaño necesita su propio directorio de exportación (Ultralytics nombra el
    resultado solo por el stem del checkpoint, no por imgsz), así que se exporta en una
    subcarpeta `ncnn_<imgsz>/` con una copia del checkpoint.
    """
    stage = export_root / f"ncnn_{imgsz}"
    model_dir = stage / f"{weights.stem}_ncnn_model"
    if model_dir.exists():
        return model_dir
    if not auto_export:
        raise FileNotFoundError(f"No existe {model_dir}; corre tools.export_model o quita --no-auto-export")

    stage.mkdir(parents=True, exist_ok=True)
    staged_weights = stage / weights.name
    if not staged_weights.exists():
        shutil.copyfile(weights, staged_weights)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import export_model  # módulo hermano: tools/export_model.py

    out = export_model.export(staged_weights, "ncnn", imgsz)
    return Path(out)


def build_runner(fmt: str, weights: Path, imgsz: int, export_root: Path, auto_export: bool) -> Runner:
    if fmt == "pt":
        return UltralyticsRunner(weights, imgsz)
    if fmt == "ncnn":
        model_dir = ensure_ncnn_model(weights, export_root, imgsz, auto_export)
        return UltralyticsRunner(model_dir, imgsz)
    raise ValueError(f"Formato desconocido: {fmt}")


def measure_map5095(model_path: Path, data_yaml: Path, imgsz: int) -> float | None:
    from ultralytics import YOLO

    model = YOLO(str(model_path), task="detect")
    metrics = model.val(data=str(data_yaml), imgsz=imgsz, verbose=False)
    return float(metrics.box.map)  # mAP50-95


# ---------------------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", type=Path, default=Path("torch/best.pt"))
    ap.add_argument("--formats", default=",".join(DEFAULT_FORMATS), help="lista separada por comas: pt,ncnn")
    ap.add_argument(
        "--sizes", default=",".join(str(s) for s in DEFAULT_SIZES), help="lista separada por comas"
    )
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--iters", type=int, default=100)
    ap.add_argument("--video", type=Path, default=Path("media/bench_synthetic.mp4"))
    ap.add_argument(
        "--regenerate-video", action="store_true", help="regenera el clip sintético aunque ya exista"
    )
    ap.add_argument("--export-root", type=Path, default=None, help="default: <carpeta de --weights>/exports")
    ap.add_argument(
        "--no-auto-export", action="store_true", help="no exportar a ncnn automáticamente si falta"
    )
    ap.add_argument(
        "--data", type=Path, default=None, help="data.yaml de validación para mAP50-95 (opcional)"
    )
    ap.add_argument("--inference-fps", type=float, default=5.0, help="para el umbral p95 < 0.7/INFERENCE_FPS")
    ap.add_argument(
        "--max-map-drop", type=float, default=2.0, help="puntos porcentuales de mAP50-95 tolerados"
    )
    ap.add_argument("--baseline", default="pt@640")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args(argv)

    formats = [f.strip() for f in args.formats.split(",") if f.strip()]
    sizes = [int(s.strip()) for s in args.sizes.split(",") if s.strip()]
    export_root = args.export_root or (args.weights.parent / "exports")

    if not args.weights.exists():
        print(f"No existe {args.weights}", file=sys.stderr)
        return 2

    if not args.video.exists() or args.regenerate_video:
        print(
            f"Generando clip sintético en {args.video} "
            "(solo para latencia/FPS, no representa escenas reales)…"
        )
        generate_synthetic_clip(args.video)

    needed = args.warmup + args.iters
    try:
        frames = list(iter_frames(args.video, needed))
    except Exception as e:  # noqa: BLE001
        print(f"No se pudieron leer frames de {args.video}: {e}", file=sys.stderr)
        return 1

    temp_c, temp_note = cpu_temperature_celsius()
    print(f"Temperatura de CPU: {temp_c if temp_c is not None else 'n/d'} ({temp_note})")
    if args.data is None:
        print("mAP50-95: no disponible, sin dataset de validación local (--data no especificado)")

    results: list[BenchResult] = []
    for fmt in formats:
        for size in sizes:
            label = f"{fmt}@{size}"
            print(f"Midiendo {label} ({args.warmup} warm-up + {args.iters} medición)…")
            try:
                runner = build_runner(fmt, args.weights, size, export_root, not args.no_auto_export)
                model_path_for_map = (
                    args.weights if fmt == "pt" else ensure_ncnn_model(args.weights, export_root, size, False)
                )
                lat_ms = run_timed(runner, frames, args.warmup, args.iters)
                stats = latency_stats(lat_ms)
                map5095 = measure_map5095(model_path_for_map, args.data, size) if args.data else None
                results.append(BenchResult(fmt, size, stats.p50_ms, stats.p95_ms, stats.fps, map5095))
            except Exception as e:  # noqa: BLE001 - múltiples causas posibles (torch, pnnx, ncnn, dataset…)
                print(f"  ERROR en {label}: {e}", file=sys.stderr)
                results.append(BenchResult(fmt, size, error=str(e)))

    print()
    print(render_table(results))
    chosen, reason = select_combo(
        results,
        baseline=args.baseline,
        max_map_drop=args.max_map_drop,
        inference_fps=args.inference_fps,
    )
    print(f"\nCombinación elegida: {chosen}\nMotivo: {reason}")

    if args.json_out:
        payload = {
            "weights": str(args.weights),
            "video": str(args.video),
            "warmup": args.warmup,
            "iters": args.iters,
            "cpu_temperature_c": temp_c,
            "cpu_temperature_note": temp_note,
            "results": [asdict(r) for r in results],
            "chosen": chosen,
            "chosen_reason": reason,
        }
        args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nResultado guardado en {args.json_out}")

    return 1 if all(r.error for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
