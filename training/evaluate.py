"""
Evalúa los modelos entrenados sobre el conjunto de TEST (nunca visto en entrenamiento)
y genera las tablas que van al capítulo de Resultados de la tesis.

Para cada modelo en runs/<modelo>/weights/best.pt:
  - mAP50, mAP50-95, precisión, recall, F1 (global y por clase)
  - matriz de confusión (PNG) y curvas PR/F1
  - velocidad de inferencia en GPU (ms/imagen)

También evalúa los modelos exportados (NCNN/ONNX) si existen, para demostrar que la
exportación no degrada la precisión.

Uso:
    python evaluate.py                # evalúa todos los runs
    python evaluate.py --split valid
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "epp_v3" / "data.yaml"
RUNS = ROOT / "runs"
OUT = RUNS / "eval"
CLASSES = ["Cofia", "Guante", "Mascarilla"]


def evaluate(weights: Path | str, name: str, split: str, imgsz: int, device) -> dict:
    model = YOLO(str(weights))
    m = model.val(
        data=str(DATA),
        split=split,
        imgsz=imgsz,
        batch=16,
        conf=0.001,
        iou=0.6,
        device=device,
        project=str(OUT),
        name=name,
        exist_ok=True,
        plots=True,
        verbose=False,
    )
    row = {
        "modelo": name,
        "split": split,
        "imgsz": imgsz,
        "mAP50": round(float(m.box.map50), 4),
        "mAP50-95": round(float(m.box.map), 4),
        "precision": round(float(m.box.mp), 4),
        "recall": round(float(m.box.mr), 4),
        "F1": round(float(2 * m.box.mp * m.box.mr / max(m.box.mp + m.box.mr, 1e-9)), 4),
        "inferencia_ms": round(float(m.speed["inference"]), 2),
    }
    per_class = []
    for i, c in enumerate(CLASSES):
        p, r, ap50, ap = m.box.class_result(i)
        per_class.append({
            "modelo": name, "clase": c,
            "precision": round(float(p), 4), "recall": round(float(r), 4),
            "AP50": round(float(ap50), 4), "AP50-95": round(float(ap), 4),
            "F1": round(float(2 * p * r / max(p + r, 1e-9)), 4),
        })
    return {"global": row, "per_class": per_class}


def main() -> None:
    global DATA, OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--device", default=0)
    ap.add_argument("--data", default=str(DATA), help="data.yaml del dataset (test)")
    ap.add_argument("--runs", default="yolo*", help="patrón de runs a evaluar (p. ej. yolov8n_v4)")
    ap.add_argument("--no-export", action="store_true", help="no evaluar los modelos exportados")
    ap.add_argument("--out", default="", help="subcarpeta de salida en runs/ (por defecto eval)")
    args = ap.parse_args()
    DATA = Path(args.data)
    if args.out:
        OUT = RUNS / args.out

    OUT.mkdir(parents=True, exist_ok=True)
    candidates: list[tuple[Path, str]] = []
    for run in sorted(RUNS.glob(args.runs)):
        best = run / "weights" / "best.pt"
        if best.exists():
            candidates.append((best, run.name))
    # modelos exportados (mismo nombre + sufijo)
    export_dir = ROOT / "export"
    if export_dir.exists() and not args.no_export:
        for p in sorted(export_dir.iterdir()):
            if (p.name.endswith("_ncnn_model") or p.suffix == ".onnx") and not p.name.startswith(("person", "pose")):
                candidates.append((p, p.name.replace("_model", "")))

    globals_, per_class = [], []
    for weights, name in candidates:
        # NCNN/ONNX se evalúan en CPU (como en el Pi)
        device = "cpu" if name.endswith(("_ncnn", ".onnx")) else args.device
        print(f"\n=== {name} ({weights}) ===")
        r = evaluate(weights, name, args.split, args.imgsz, device)
        globals_.append(r["global"])
        per_class.extend(r["per_class"])
        print(r["global"])

    dfg = pd.DataFrame(globals_)
    dfc = pd.DataFrame(per_class)
    dfg.to_csv(OUT / f"metricas_globales_{args.split}.csv", index=False)
    dfc.to_csv(OUT / f"metricas_por_clase_{args.split}.csv", index=False)
    (OUT / f"metricas_{args.split}.json").write_text(
        json.dumps({"global": globals_, "per_class": per_class}, indent=2, ensure_ascii=False)
    )
    print("\n=== RESUMEN GLOBAL ===")
    print(dfg.to_string(index=False))
    print("\n=== POR CLASE ===")
    print(dfc.to_string(index=False))
    # Objetivo específico de la tesis: >= 90 %
    for g in globals_:
        ok = g["mAP50"] >= 0.90 and g["precision"] >= 0.90
        print(f"{g['modelo']}: objetivo >=90% {'CUMPLIDO' if ok else 'NO cumplido'} "
              f"(mAP50={g['mAP50']:.3f}, P={g['precision']:.3f})")


if __name__ == "__main__":
    main()
