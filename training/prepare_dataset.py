"""
Prepara el dataset de entrenamiento a partir de la exportación de Roboflow
"Deteccion de EPPs v2 - v3" (3 clases: Gorra, Guante, Mascarilla).

Qué hace:
  1. Copia el dataset a training/data/epp_v3/ (no toca la carpeta original).
  2. Mueve las imágenes augmentadas (aug_*) que cayeron en valid/test hacia train.
     Motivo: las aug_* son variantes sintéticas de fotos originales; si una variante
     queda en valid/test y su original en train, las métricas se inflan (fuga de datos).
     Con esto valid y test quedan solo con fotos originales (DSC_* / IMG_*).
  3. Genera data.yaml con rutas absolutas y un resumen (dataset_summary.json)
     que luego se usa en la tesis.

Uso:
    python prepare_dataset.py                                  # dataset v3 original
    python prepare_dataset.py --src "ruta/export_roboflow" --name epp_v4   # nuevo dataset (p. ej. guantes azules)
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
SRC = ROOT.parent.parent / "05 Datasets" / "Deteccion de EPPs YOLOv8 (Roboflow)"
DST = ROOT / "data" / "epp_v3"
CLASSES = ["Gorra", "Guante", "Mascarilla"]
SPLITS = ["train", "valid", "test"]


def resolve_source(src: Path) -> Path:
    """Acepta la carpeta de la exportación de Roboflow o su .zip (se descomprime junto al zip)."""
    if src.suffix.lower() == ".zip":
        out = src.with_suffix("")
        if not (out / "data.yaml").exists():
            with zipfile.ZipFile(src) as z:
                z.extractall(out)
        src = out
    if not (src / "data.yaml").exists():
        raise SystemExit(f"No se encontró data.yaml en {src}")
    return src


def check_source(src: Path) -> dict:
    """Verifica clases, correspondencia imagen/etiqueta, archivos corruptos y rangos. Devuelve el resumen."""
    from PIL import Image
    cfg = yaml.safe_load((src / "data.yaml").read_text(encoding="utf-8"))
    names = list(cfg.get("names", []))
    if names != CLASSES:
        raise SystemExit(f"Las clases del dataset {names} no coinciden con {CLASSES}. "
                         "Al re-etiquetar (p. ej. guantes azules) mantén los mismos nombres y orden de clases.")
    problems, counts = [], {}
    for split in SPLITS:
        imgs = {p.stem: p for p in (src / split / "images").glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")}
        lbls = {p.stem: p for p in (src / split / "labels").glob("*.txt")}
        if not imgs:
            raise SystemExit(f"El split '{split}' no tiene imágenes; la exportación debe ser train/valid/test")
        for s_ in imgs.keys() - lbls.keys():
            problems.append(f"{split}: imagen sin etiqueta {s_}")
        for s_ in lbls.keys() - imgs.keys():
            problems.append(f"{split}: etiqueta sin imagen {s_}")
        for stem, lbl in lbls.items():
            for i, line in enumerate(lbl.read_text().splitlines()):
                parts = line.split()
                if not parts:
                    continue
                if len(parts) != 5 or not (0 <= int(parts[0]) < len(CLASSES)) or any(not (0 <= float(v) <= 1) for v in parts[1:]):
                    problems.append(f"{split}: etiqueta inválida {stem} línea {i + 1}")
        for p in imgs.values():
            try:
                Image.open(p).verify()
            except Exception:
                problems.append(f"{split}: imagen corrupta {p.name}")
        counts[split] = len(imgs)
    if problems:
        print(chr(10).join(problems[:30]))
        raise SystemExit(f"{len(problems)} problemas en el dataset; corrígelos en Roboflow y vuelve a exportar.")
    return counts


def copy_split(split: str) -> None:
    for sub in ("images", "labels"):
        (DST / split / sub).mkdir(parents=True, exist_ok=True)
        for f in (SRC / split / sub).iterdir():
            shutil.copy2(f, DST / split / sub / f.name)


def move_augmented_to_train() -> dict[str, int]:
    moved = {}
    for split in ("valid", "test"):
        n = 0
        for img in (DST / split / "images").glob("aug_*"):
            lbl = DST / split / "labels" / (img.stem + ".txt")
            shutil.move(img, DST / "train" / "images" / img.name)
            if lbl.exists():
                shutil.move(lbl, DST / "train" / "labels" / lbl.name)
            n += 1
        moved[split] = n
    return moved


def summarize() -> dict:
    summary: dict = {"classes": CLASSES, "splits": {}}
    for split in SPLITS:
        imgs = list((DST / split / "images").iterdir())
        boxes = Counter()
        background = 0
        for lbl in (DST / split / "labels").glob("*.txt"):
            lines = [l for l in lbl.read_text().splitlines() if l.strip()]
            if not lines:
                background += 1
            for l in lines:
                boxes[CLASSES[int(l.split()[0])]] += 1
        summary["splits"][split] = {
            "images": len(imgs),
            "augmented": sum(1 for i in imgs if i.name.startswith("aug_")),
            "background": background,
            "boxes": dict(boxes),
        }
    return summary


def main() -> None:
    global SRC, DST
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(SRC), help="carpeta o .zip exportado de Roboflow (formato YOLOv8)")
    ap.add_argument("--name", default="epp_v3", help="nombre del dataset preparado en training/data/")
    args = ap.parse_args()
    SRC = resolve_source(Path(args.src))
    DST = ROOT / "data" / args.name
    print("Verificando", SRC, "->", check_source(SRC))
    if DST.exists():
        shutil.rmtree(DST)
    for s in SPLITS:
        copy_split(s)
    moved = move_augmented_to_train()
    yaml_text = (
        f"path: {DST.as_posix()}\n"
        "train: train/images\n"
        "val: valid/images\n"
        "test: test/images\n"
        f"nc: {len(CLASSES)}\n"
        f"names: {CLASSES}\n"
    )
    (DST / "data.yaml").write_text(yaml_text, encoding="utf-8")
    summary = summarize()
    summary["moved_augmented_to_train"] = moved
    summary["name"] = args.name
    summary["source"] = str(SRC)
    (ROOT / "data" / f"dataset_summary_{args.name}.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (ROOT / "data" / "dataset_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\ndata.yaml -> {DST / 'data.yaml'}")


if __name__ == "__main__":
    main()
