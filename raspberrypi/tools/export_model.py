"""Exporta los pesos entrenados (`torch/best.pt`, fuera de git) a un formato de inferencia
optimizado para el borde (Fase 7 del plan de remediacion).

Uso:
    python -m tools.export_model                                   # ncnn, imgsz=416 (default de produccion)
    python -m tools.export_model --format onnx --imgsz 640
    python -m tools.export_model --weights torch/best.pt --format ncnn --imgsz 320

El valor por defecto de --imgsz (416) coincide con `MODEL_IMGSZ` en `epp_edge/config.py`.
El resultado de `model.export(...)` es una ruta:
  - ncnn     -> directorio `<stem>_ncnn_model/` (model.ncnn.param / model.ncnn.bin)
  - onnx     -> archivo `<stem>.onnx`
  - openvino -> directorio `<stem>_openvino_model/`

Requiere `ultralytics` (y por tanto `torch`) instalado. En Windows, Smart App Control /
Windows Defender Application Control puede bloquear las DLL de `torch` (no firmadas) con
el error "WinError 4551"; en ese caso este script falla al importar ultralytics y hay que
ejecutarlo en Linux (la Raspberry Pi real, o cualquier otra maquina Linux/WSL). Ver
docs/MODEL.md para el detalle de esta limitacion observada durante el desarrollo.
"""

import argparse
import sys
from pathlib import Path

FORMATS = ("ncnn", "onnx", "openvino")


def export(weights: Path, fmt: str, imgsz: int) -> Path:
    """Carga `weights` con Ultralytics y exporta a `fmt` con tamaño de entrada `imgsz`.

    Import tardío de `ultralytics`: no todos los entornos (p. ej. CI, o esta laptop con
    Smart App Control activo) lo tienen disponible, y los demás tools de este paquete
    siguen la misma convención de importar sus dependencias pesadas dentro de la función.
    """
    from ultralytics import YOLO

    model = YOLO(str(weights))
    result = model.export(format=fmt, imgsz=imgsz)
    return Path(result)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--weights", type=Path, default=Path("torch/best.pt"), help="checkpoint .pt de entrada")
    ap.add_argument("--format", choices=FORMATS, default="ncnn")
    ap.add_argument("--imgsz", type=int, default=416, help="debe ser múltiplo de 32 (default: 416)")
    args = ap.parse_args(argv)

    if args.imgsz <= 0 or args.imgsz % 32:
        print(f"--imgsz debe ser un múltiplo positivo de 32 (recibido {args.imgsz})", file=sys.stderr)
        return 2
    if not args.weights.exists():
        print(f"No existe {args.weights}", file=sys.stderr)
        return 2

    try:
        out = export(args.weights, args.format, args.imgsz)
    except ImportError as e:
        print(
            f"Falta una dependencia para exportar a {args.format}: {e}\n"
            "Instala raspberrypi/requirements.txt (ultralytics arrastra torch/torchvision).",
            file=sys.stderr,
        )
        return 1
    except Exception as e:  # noqa: BLE001 - ultralytics/pnnx/onnx lanzan excepciones heterogéneas
        print(
            f"Falló la exportación de {args.weights} a {args.format} (imgsz={args.imgsz}): {e}",
            file=sys.stderr,
        )
        return 1

    print(f"Exportado: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
