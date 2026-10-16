"""
Exporta los modelos al formato que corre en la Raspberry Pi 5 (CPU ARM).

  - EPP  : runs/<run>/weights/best.pt   -> export/epp_<tag>_640_ncnn_model/ (imgsz 640, FP16)
                                          -> export/epp_<tag>_640.onnx  (para benchmark comparativo)
  - Pose (compuerta + puntos clave): yolov8n-pose.pt COCO -> export/pose_yolov8n_320_ncnn_model/ (imgsz 320)

Luego copia los modelos NCNN a ../edge_pose/models/ para el despliegue.

NCNN es el formato recomendado por Ultralytics para Raspberry Pi: inferencia CPU
optimizada para ARM (NEON), sin dependencias de PyTorch en el dispositivo.

Uso:
    python export.py                                   # runs/yolov8n -> epp_yolov8n_640_ncnn_model
    python export.py --epp-weights runs/yolo11n/weights/best.pt --tag yolo11n --skip-person
    python export.py --imgsz 480     # si el benchmark en el Pi exige menos resolución (ajustar epp.imgsz en config.yaml)
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent
EXPORT = ROOT / "export"
EDGE_MODELS = ROOT.parent / "edge_pose" / "models"


def export_ncnn(weights: Path | str, out_name: str, imgsz: int, half: bool = True) -> Path:
    model = YOLO(str(weights))
    path = Path(model.export(format="ncnn", imgsz=imgsz, half=half))
    dst = EXPORT / out_name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.move(str(path), dst)
    return dst


def export_onnx(weights: Path | str, out_name: str, imgsz: int) -> Path:
    model = YOLO(str(weights))
    path = Path(model.export(format="onnx", imgsz=imgsz, simplify=True, opset=12, dynamic=False))
    dst = EXPORT / out_name
    shutil.move(str(path), dst)
    return dst


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epp-weights", default=str(ROOT / "runs" / "yolov8n" / "weights" / "best.pt"))
    ap.add_argument("--imgsz", type=int, default=640, help="resolución del modelo EPP")
    ap.add_argument("--person-imgsz", type=int, default=320)
    ap.add_argument("--tag", default="yolov8n", help="nombre del modelo en edge_pose/models (p. ej. yolov8n_v4)")
    ap.add_argument("--skip-person", action="store_true", help="no reexportar el modelo de pose")
    args = ap.parse_args()

    EXPORT.mkdir(exist_ok=True)
    EDGE_MODELS.mkdir(parents=True, exist_ok=True)

    epp_ncnn = export_ncnn(args.epp_weights, f"epp_{args.tag}_{args.imgsz}_ncnn_model", args.imgsz)
    epp_onnx = export_onnx(args.epp_weights, f"epp_{args.tag}_{args.imgsz}.onnx", args.imgsz)
    aux = []
    if not args.skip_person:
        aux.append(export_ncnn("yolov8n-pose.pt", f"pose_yolov8n_{args.person_imgsz}_ncnn_model", args.person_imgsz))

    # copia al paquete edge
    for src in [epp_ncnn] + aux:
        dst = EDGE_MODELS / src.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    shutil.copy2(args.epp_weights, EDGE_MODELS / f"epp_{args.tag}.pt")  # para benchmark PyTorch en el Pi
    shutil.copy2(epp_onnx, EDGE_MODELS / epp_onnx.name)

    print("Exportado:")
    for p in [epp_ncnn, epp_onnx] + aux:
        size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
        print(f"  {p.name:45s} {size/1e6:6.2f} MB")
    print(f"Copiado a {EDGE_MODELS}")


if __name__ == "__main__":
    main()
