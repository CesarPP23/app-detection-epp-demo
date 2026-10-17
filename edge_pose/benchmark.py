"""Benchmark de inferencia en el dispositivo (Raspberry Pi 5 o laptop).

Mide latencia/FPS de cada etapa y formato de modelo, junto con CPU, RAM y temperatura.
Genera data/benchmark_<host>.csv y lo imprime como tabla (para el capítulo de Resultados).

Uso:
    .venv/bin/python benchmark.py                       # frames sintéticos + imágenes del dataset si existen
    .venv/bin/python benchmark.py --video ../06\ .../IMG_9704.mp4 --n 100
"""
from __future__ import annotations

import argparse
import csv
import platform
import socket
import statistics
import time
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent


def cpu_temp():
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
    except Exception:
        return None


def load_frames(video: str | None, n: int) -> list[np.ndarray]:
    frames = []
    if video:
        cap = cv2.VideoCapture(video)
        while len(frames) < n:
            ok, f = cap.read()
            if not ok:
                break
            frames.append(cv2.resize(f, (640, 480)))
        cap.release()
    if not frames:
        rng = np.random.default_rng(0)
        frames = [rng.integers(0, 255, (480, 640, 3), dtype=np.uint8) for _ in range(n)]
    return frames


def bench_model(path: str, imgsz: int, frames: list[np.ndarray], classes=None) -> dict:
    import psutil
    from ultralytics import YOLO
    m = YOLO(path, task="pose" if "pose" in Path(path).name else "detect")
    for f in frames[:3]:
        m.predict(f, imgsz=imgsz, verbose=False, classes=classes, device="cpu")
    proc = psutil.Process()
    proc.cpu_percent(None)
    lat = []
    t_all = time.perf_counter()
    for f in frames:
        t = time.perf_counter()
        m.predict(f, imgsz=imgsz, verbose=False, classes=classes, device="cpu")
        lat.append((time.perf_counter() - t) * 1000)
    wall = time.perf_counter() - t_all
    return {
        "modelo": Path(path).name, "imgsz": imgsz, "n": len(frames),
        "lat_media_ms": round(statistics.mean(lat), 1), "lat_p50_ms": round(statistics.median(lat), 1),
        "lat_p95_ms": round(sorted(lat)[int(0.95 * len(lat)) - 1], 1), "fps": round(len(frames) / wall, 2),
        "cpu_proc_%": round(proc.cpu_percent(None), 1), "ram_mb": round(proc.memory_info().rss / 1e6, 0),
        "temp_c": cpu_temp(),
    }


def bench_motion(frames: list[np.ndarray]) -> dict:
    from epp_edge.motion import MotionDetector
    md = MotionDetector()
    lat = []
    for f in frames:
        t = time.perf_counter(); md.detect(f); lat.append((time.perf_counter() - t) * 1000)
    return {"modelo": "MOG2 (movimiento)", "imgsz": 160, "n": len(frames), "lat_media_ms": round(statistics.mean(lat), 2),
            "lat_p50_ms": round(statistics.median(lat), 2), "lat_p95_ms": round(sorted(lat)[int(0.95 * len(lat)) - 1], 2),
            "fps": round(1000 / statistics.mean(lat), 1), "cpu_proc_%": None, "ram_mb": None, "temp_c": cpu_temp()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=None)
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--all-formats", action="store_true", help="incluye .pt y .onnx del modelo EPP si existen")
    args = ap.parse_args()

    frames = load_frames(args.video, args.n)
    rows = [bench_motion(frames)]
    models = [("models/pose_yolov8n_320_ncnn_model", 320, None), ("models/epp_yolov8n_640_ncnn_model", 640, None)]
    if (BASE / "models/person_yolov8n_320_ncnn_model").exists():
        models.insert(1, ("models/person_yolov8n_320_ncnn_model", 320, [0]))
    if (BASE / "models/epp_yolo11n_640_ncnn_model").exists():
        models.append(("models/epp_yolo11n_640_ncnn_model", 640, None))
    for extra in (480, 416, 320):
        p = BASE / f"models/epp_yolov8n_{extra}_ncnn_model"
        if p.exists():
            models.append((str(p), extra, None))
    if args.all_formats:
        for name, sz in (("models/epp_yolov8n.pt", 640), ("models/epp_yolov8n_640.onnx", 640)):
            if (BASE / name).exists():
                models.append((name, sz, None))
    for path, sz, cls in models:
        if not (BASE / path).exists():
            print("omitido (no existe):", path); continue
        print("benchmark", path, "…")
        rows.append(bench_model(str(BASE / path), sz, frames, cls))

    host = socket.gethostname()
    out = BASE / "data" / f"benchmark_{host}.csv"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(f"\nHost: {host} | {platform.machine()} | {platform.platform()}")
    print(f"{'modelo':38s} {'imgsz':>5s} {'lat_ms':>7s} {'p95':>7s} {'fps':>6s} {'cpu%':>6s} {'ram':>6s} {'temp':>5s}")
    for r in rows:
        print(f"{r['modelo']:38s} {r['imgsz']:5d} {r['lat_media_ms']:7} {r['lat_p95_ms']:7} {r['fps']:6} "
              f"{str(r['cpu_proc_%']):>6s} {str(r['ram_mb']):>6s} {str(r['temp_c']):>5s}")
    print("CSV:", out)


if __name__ == "__main__":
    main()
