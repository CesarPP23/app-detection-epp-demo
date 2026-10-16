"""
Análisis de robustez de la evaluación del detector de EPP (capítulo 5 de la tesis).

Motivación: las 995 fotografías originales se tomaron en ráfagas (17 escenas en ~30 min). Con la partición aleatoria de
Roboflow, 133 de las 135 imágenes de prueba tienen una imagen de entrenamiento tomada a <= 2 s, por lo que la métrica de
prueba puede estar inflada por casi-duplicados. Este script cuantifica ese efecto y la variabilidad del resultado:

  splits     Construye dos particiones sin fuga temporal a partir de data/epp_v3 (marcas EXIF de los originales):
               - epp_scene : validación y prueba formadas por escenas completas no vistas en entrenamiento.
               - epp_camera: entrenamiento con la Nikon D3500; prueba con la escena del iPhone 14 Pro Max (otra cámara,
                             otro punto de vista).
             Las imágenes aumentadas (aug_*) solo se conservan si sus 5 fotografías más parecidas pertenecen a escenas de
             entrenamiento (data/groups_epp_v3.json).
  train      Cola de entrenamientos: semillas 1 y 2 de YOLOv8n y YOLO11n sobre la partición original; YOLOv8n con semillas
             0-2 sobre epp_scene y semilla 0 sobre epp_camera. Los runs van a runs/robustez/.
  eval       Evalúa cada run sobre la prueba de su partición (model.val) -> runs/robustez/metricas.json
  bootstrap  Intervalos de confianza al 95 % por remuestreo de imágenes (B = 1000) de mAP50, mAP50-95, AP50 por clase,
             precisión y recall -> runs/robustez/bootstrap.json
  all        Todo lo anterior en orden.

Uso:  python robustness.py all
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BASE = DATA / "epp_v3"
OUT = ROOT / "runs" / "robustez"
CLASSES = ["Gorra", "Guante", "Mascarilla"]

# Escenas (grupos con separación > 10 s entre fotos consecutivas; ver data/groups_epp_v3.json)
SCENE_SPLIT = {"test": {2, 6, 13, 14}, "valid": {5, 8, 11, 12}}
CAMERA_SPLIT = {"test": {16}, "valid": {15}}

EXPERIMENTS = [
    # (run, modelo, dataset, semilla)
    ("v8n_random_s1", "yolov8n", "epp_v3", 1),
    ("v8n_random_s2", "yolov8n", "epp_v3", 2),
    ("11n_random_s1", "yolo11n", "epp_v3", 1),
    ("11n_random_s2", "yolo11n", "epp_v3", 2),
    ("v8n_scene_s0", "yolov8n", "epp_scene", 0),
    ("v8n_scene_s1", "yolov8n", "epp_scene", 1),
    ("v8n_scene_s2", "yolov8n", "epp_scene", 2),
    ("v8n_camera_s0", "yolov8n", "epp_camera", 0),
]
# runs existentes (semilla 0 de la partición original)
EXISTING = {"v8n_random_s0": ROOT / "runs" / "yolov8n", "11n_random_s0": ROOT / "runs" / "yolo11n"}


def stem_of(name: str) -> str:
    return re.sub(r"_(jpg|JPG|jpeg)\.rf\..*", "", name)


# ----------------------------------------------------------------------------------------------------------- splits
def _link(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        return
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def build_split(name: str, spec: dict) -> dict:
    g = json.loads((DATA / "groups_epp_v3.json").read_text())
    groups, aug = g["groups"], g["aug"]
    # top-5 de cada aumentada: se recalcula la pertenencia con los votos guardados (grupo ganador + votos)
    held = spec["test"] | spec["valid"]
    dst = DATA / name
    if dst.exists():
        shutil.rmtree(dst)
    counts = Counter()
    boxes = {s: Counter() for s in ("train", "valid", "test")}
    dropped_aug = 0
    for split in ("train", "valid", "test"):
        for img in (BASE / split / "images").iterdir():
            lbl = BASE / split / "labels" / (img.stem + ".txt")
            if img.name.startswith("aug_"):
                a = aug[img.name]
                # se exige unanimidad razonable (>= 3 de 5 votos) y que el grupo ganador sea de entrenamiento
                if a["group"] in held or a["votes"] < 3:
                    dropped_aug += 1
                    continue
                target = "train"
            else:
                grp = groups[img.name]
                target = "test" if grp in spec["test"] else "valid" if grp in spec["valid"] else "train"
            _link(img, dst / target / "images" / img.name)
            _link(lbl, dst / target / "labels" / lbl.name)
            counts[target] += 1
            for line in lbl.read_text().splitlines():
                if line.strip():
                    boxes[target][CLASSES[int(line.split()[0])]] += 1
    (dst / "data.yaml").write_text(
        f"path: {dst.as_posix()}\ntrain: train/images\nval: valid/images\ntest: test/images\nnc: 3\nnames: {CLASSES}\n")
    info = {"name": name, "test_groups": sorted(spec["test"]), "valid_groups": sorted(spec["valid"]),
            "images": dict(counts), "boxes": {k: dict(v) for k, v in boxes.items()}, "dropped_aug": dropped_aug}
    print(json.dumps(info, indent=1))
    return info


def leakage_stats() -> dict:
    """Distancia temporal de cada imagen de prueba/validación a la imagen de entrenamiento más cercana (partición original)."""
    import datetime as dt
    ts = json.loads((DATA / "timestamps_epp_v3.json").read_text())
    t = {r["file"]: dt.datetime.fromisoformat(r["t"]) for r in ts}
    tr = np.array([t[r["file"]].timestamp() for r in ts if r["split"] == "train"])
    out = {}
    for s in ("valid", "test"):
        d = np.array([np.min(np.abs(tr - t[r["file"]].timestamp())) for r in ts if r["split"] == s])
        out[s] = {"n": int(len(d)), "le2s": int((d <= 2).sum()), "le5s": int((d <= 5).sum()), "median_s": float(np.median(d))}
    times = sorted(t.values())
    out["capture"] = {"first": times[0].isoformat(), "last": times[-1].isoformat(),
                      "days": sorted({x.date().isoformat() for x in times}),
                      "per_day": dict(Counter(x.date().isoformat() for x in times))}
    g = json.loads((DATA / "groups_epp_v3.json").read_text())["groups"]
    out["n_scenes"] = len(set(g.values()))
    return out


def cmd_splits() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    info = {"leakage": leakage_stats(), "epp_scene": build_split("epp_scene", SCENE_SPLIT),
            "epp_camera": build_split("epp_camera", CAMERA_SPLIT)}
    (OUT / "splits.json").write_text(json.dumps(info, indent=2))


# ------------------------------------------------------------------------------------------------------------ train
def cmd_train(only: list[str] | None = None) -> None:
    sys.path.insert(0, str(ROOT))
    from train import train_one
    OUT.mkdir(parents=True, exist_ok=True)
    for run, model, ds, seed in EXPERIMENTS:
        if only and run not in only:
            continue
        if (OUT / run / "weights" / "best.pt").exists() and (OUT / run / "train_info.json").exists():
            print(f"[skip] {run}")
            continue
        print(f"\n=== {run}: {model} {ds} seed={seed} ===", flush=True)
        train_one(model, 100, 16, 640, 20, DATA / ds / "data.yaml", run, seed=seed, project=OUT)


def runs_available() -> dict[str, tuple[Path, str]]:
    runs = {k: (v / "weights" / "best.pt", "epp_v3") for k, v in EXISTING.items()}
    for run, _m, ds, _s in EXPERIMENTS:
        w = OUT / run / "weights" / "best.pt"
        if w.exists():
            runs[run] = (w, ds)
    return runs


# ------------------------------------------------------------------------------------------------------------- eval
def cmd_eval() -> None:
    from ultralytics import YOLO
    res = {}
    for run, (w, ds) in runs_available().items():
        m = YOLO(str(w)).val(data=str(DATA / ds / "data.yaml"), split="test", imgsz=640, batch=16, conf=0.001, iou=0.6,
                             device=0, plots=False, verbose=False, project=str(OUT / "_val"), name=run, exist_ok=True)
        pc = {}
        for i, c in enumerate(CLASSES):
            p, r, ap50, ap = m.box.class_result(i)
            pc[c] = {"precision": float(p), "recall": float(r), "AP50": float(ap50), "AP50-95": float(ap)}
        res[run] = {"dataset": ds, "precision": float(m.box.mp), "recall": float(m.box.mr), "mAP50": float(m.box.map50),
                    "mAP50-95": float(m.box.map), "per_class": pc}
        print(run, {k: round(v, 4) for k, v in res[run].items() if isinstance(v, float)}, flush=True)
    (OUT / "metricas.json").write_text(json.dumps(res, indent=2))


# -------------------------------------------------------------------------------------------------------- bootstrap
IOUS = np.linspace(0.5, 0.95, 10)


def _iou(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU entre cajas xyxy a (N,4) y b (M,4)."""
    lt = np.maximum(a[:, None, :2], b[None, :, :2]); rb = np.minimum(a[:, None, 2:], b[None, :, 2:])
    inter = np.clip(rb - lt, 0, None).prod(2)
    aa = (a[:, 2:] - a[:, :2]).prod(1); bb = (b[:, 2:] - b[:, :2]).prod(1)
    return inter / (aa[:, None] + bb[None, :] - inter + 1e-9)


def _match(pred_cls, pred_box, gt_cls, gt_box) -> np.ndarray:
    """Coincidencias TP (N_pred x 10 umbrales) con la misma lógica que Ultralytics (asignación única por IoU)."""
    tp = np.zeros((len(pred_cls), len(IOUS)), bool)
    if len(pred_cls) == 0 or len(gt_cls) == 0:
        return tp
    iou = _iou(gt_box, pred_box) * (gt_cls[:, None] == pred_cls[None, :])
    for k, th in enumerate(IOUS):
        x = np.nonzero(iou >= th)
        if x[0].size:
            m = np.stack([x[0], x[1], iou[x]], 1)
            m = m[m[:, 2].argsort()[::-1]]
            m = m[np.unique(m[:, 1], return_index=True)[1]]
            m = m[np.unique(m[:, 0], return_index=True)[1]]
            tp[m[:, 1].astype(int), k] = True
    return tp


def _ap(recall: np.ndarray, precision: np.ndarray) -> float:
    mrec = np.concatenate(([0.0], recall, [1.0])); mpre = np.concatenate(([1.0], precision, [0.0]))
    mpre = np.flip(np.maximum.accumulate(np.flip(mpre)))
    x = np.linspace(0, 1, 101)
    return float(np.trapz(np.interp(x, mrec, mpre), x))


def _metrics(tp, conf, pcls, n_gt) -> dict:
    """mAP50, mAP50-95, AP50 por clase y P/R en la confianza de máximo F1 medio (como Ultralytics)."""
    order = np.argsort(-conf); tp, conf, pcls = tp[order], conf[order], pcls[order]
    ap = np.zeros((len(CLASSES), len(IOUS))); px = np.linspace(0, 1, 1000)
    pcurve = np.zeros((len(CLASSES), 1000)); rcurve = np.zeros((len(CLASSES), 1000))
    for c in range(len(CLASSES)):
        i = pcls == c
        n_l, n_p = n_gt[c], i.sum()
        if n_p == 0 or n_l == 0:
            continue
        fpc = (1 - tp[i]).cumsum(0); tpc = tp[i].cumsum(0)
        rec = tpc / (n_l + 1e-16); prec = tpc / (tpc + fpc)
        rcurve[c] = np.interp(-px, -conf[i], rec[:, 0], left=0)
        pcurve[c] = np.interp(-px, -conf[i], prec[:, 0], left=1)
        for k in range(len(IOUS)):
            ap[c, k] = _ap(rec[:, k], prec[:, k])
    f1 = 2 * pcurve * rcurve / (pcurve + rcurve + 1e-16)
    j = int(np.convolve(f1.mean(0), np.ones(int(1000 * 0.1)) / int(1000 * 0.1), mode="same").argmax())
    return {"mAP50": ap[:, 0].mean(), "mAP50-95": ap.mean(), "precision": pcurve[:, j].mean(), "recall": rcurve[:, j].mean(),
            **{f"AP50_{c}": ap[k, 0] for k, c in enumerate(CLASSES)}}


def predictions(weights: Path, ds: str) -> list[dict]:
    from ultralytics import YOLO
    model = YOLO(str(weights))
    imgs = sorted((DATA / ds / "test" / "images").iterdir())
    out = []
    for k in range(0, len(imgs), 16):
        for img, r in zip(imgs[k:k + 16], model.predict([str(p) for p in imgs[k:k + 16]], imgsz=640, conf=0.001, iou=0.6,
                                                         max_det=300, device=0, verbose=False)):
            h, w = r.orig_shape
            g = np.loadtxt(DATA / ds / "test" / "labels" / (img.stem + ".txt"), ndmin=2)
            gc = g[:, 0].astype(int) if g.size else np.zeros(0, int)
            gb = (np.stack([g[:, 1] - g[:, 3] / 2, g[:, 2] - g[:, 4] / 2, g[:, 1] + g[:, 3] / 2, g[:, 2] + g[:, 4] / 2], 1)
                  * [w, h, w, h]) if g.size else np.zeros((0, 4))
            pb = r.boxes.xyxy.cpu().numpy(); pc = r.boxes.cls.cpu().numpy().astype(int); cf = r.boxes.conf.cpu().numpy()
            out.append({"tp": _match(pc, pb, gc, gb), "conf": cf, "cls": pc, "n_gt": np.bincount(gc, minlength=3)})
    return out


def cmd_bootstrap(b: int = 1000, runs: list[str] | None = None) -> None:
    rng = np.random.default_rng(0)
    res = {}
    for run, (w, ds) in runs_available().items():
        if runs and run not in runs:
            continue
        per = predictions(w, ds)
        cat = lambda idx: _metrics(np.concatenate([per[i]["tp"] for i in idx]), np.concatenate([per[i]["conf"] for i in idx]),
                                   np.concatenate([per[i]["cls"] for i in idx]), sum(per[i]["n_gt"] for i in idx))
        full = cat(range(len(per)))
        samples = [cat(rng.integers(0, len(per), len(per))) for _ in range(b)]
        res[run] = {"dataset": ds, "n_images": len(per), "B": b,
                    **{k: {"value": float(v), "ci95": [float(np.percentile([s[k] for s in samples], 2.5)),
                                                       float(np.percentile([s[k] for s in samples], 97.5))]}
                       for k, v in full.items()}}
        print(run, {k: (round(v["value"], 4), [round(x, 4) for x in v["ci95"]]) for k, v in res[run].items()
                    if isinstance(v, dict)}, flush=True)
    prev = json.loads((OUT / "bootstrap.json").read_text()) if (OUT / "bootstrap.json").exists() else {}
    prev.update(res)
    (OUT / "bootstrap.json").write_text(json.dumps(prev, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["splits", "train", "eval", "bootstrap", "all"])
    ap.add_argument("--only", nargs="*", help="limitar a estos runs")
    ap.add_argument("-B", type=int, default=1000)
    a = ap.parse_args()
    t0 = time.time()
    if a.cmd in ("splits", "all"):
        cmd_splits()
    if a.cmd in ("train", "all"):
        cmd_train(a.only)
    if a.cmd in ("eval", "all"):
        cmd_eval()
    if a.cmd in ("bootstrap", "all"):
        cmd_bootstrap(a.B, a.only)
    print(f"listo en {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
