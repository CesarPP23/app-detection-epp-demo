"""
Entrena los modelos de detección de EPP con transfer learning desde pesos COCO.

Modelos usados en la tesis:
  - yolov8n (nano)  -> desplegado en Raspberry Pi 5
  - yolov8s (small) -> referencia de precisión con más parámetros
  - yolo11n         -> alternativa más reciente (menos FLOPs, mejor recall de guante)

Uso:
    python train.py                                   # entrena yolov8n y yolov8s con data/epp_v3
    python train.py --models yolov8n yolo11n --data data/epp_v4/data.yaml --suffix _epp_v4
    python train.py --epochs 50 --batch 8

Salidas en runs/<modelo>/ : weights/best.pt, results.csv, curvas, matriz de confusión.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "epp_v3" / "data.yaml"
RUNS = ROOT / "runs"


def train_one(model_name: str, epochs: int, batch: int, imgsz: int, patience: int, data: Path = None, run_name: str = None,
              seed: int = 0, project: Path = None) -> dict:
    data = data or DATA
    project = project or RUNS
    run_name = run_name or model_name
    model = YOLO(f"{model_name}.pt")  # descarga pesos COCO si no existen
    t0 = time.time()
    results = model.train(
        data=str(data),
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        patience=patience,
        seed=seed,
        deterministic=True,
        project=str(project),
        name=run_name,
        exist_ok=True,
        pretrained=True,
        optimizer="auto",
        cos_lr=True,
        close_mosaic=10,
        # Augmentación estándar de Ultralytics (mosaic, HSV, flip, scale) —
        # complementa las aug_* del dataset y mejora robustez a iluminación.
        hsv_h=0.015, hsv_s=0.7, hsv_v=0.4, fliplr=0.5, scale=0.5,
        plots=True,
        verbose=True,
    )
    elapsed = time.time() - t0
    best = project / run_name / "weights" / "best.pt"
    info = {
        "model": model_name, "run": run_name, "data": str(data), "seed": seed,
        "epochs_requested": epochs,
        "train_time_min": round(elapsed / 60, 1),
        "best_weights": str(best),
        "val_mAP50": float(results.box.map50),
        "val_mAP50_95": float(results.box.map),
        "val_precision": float(results.box.mp),
        "val_recall": float(results.box.mr),
    }
    (project / run_name / "train_info.json").write_text(json.dumps(info, indent=2))
    print(json.dumps(info, indent=2))
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", default=["yolov8n", "yolov8s"])
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--patience", type=int, default=20)
    ap.add_argument("--data", default=str(DATA), help="data.yaml del dataset preparado")
    ap.add_argument("--suffix", default="", help="sufijo del nombre del run (p. ej. _v4)")
    args = ap.parse_args()

    data = Path(args.data)
    assert data.exists(), f"No existe {data}. Ejecuta primero prepare_dataset.py"
    RUNS.mkdir(exist_ok=True)
    summary = [train_one(m, args.epochs, args.batch, args.imgsz, args.patience, data, m + args.suffix) for m in args.models]
    (RUNS / "train_summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
