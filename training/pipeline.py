"""Pipeline completo para incorporar un NUEVO dataset (p. ej. guantes azules) y dejar el modelo listo
para producción, en un solo comando:

    python pipeline.py --src "D:/.../Deteccion-de-EPPs-v4.zip" --name epp_v4
    python pipeline.py --src "carpeta_export_roboflow" --name epp_v4 --models yolov8n yolo11n --epochs 100

Pasos (cada uno se puede saltar con --skip-<paso>):
  1. prepare : valida la exportación de Roboflow (clases Gorra/Guante/Mascarilla, integridad) y la
               re-particiona (imágenes aumentadas solo en train)             -> data/<name>/
  2. train   : entrena los modelos indicados por transferencia (pesos COCO)  -> runs/<modelo>_<name>/
  3. evaluate: métricas en TEST de los runs nuevos                           -> runs/eval_<name>/
  4. export  : el mejor modelo (mAP50-95 en test) a NCNN 640 + ONNX          -> ../edge_pose/models/epp_<modelo>_<name>_640_ncnn_model
  5. config  : apunta edge/epp_edge/config.yaml (epp.model) al modelo nuevo
  6. regress : corre el nodo sobre los videos de planta y compara los episodios con el modelo anterior
  7. resumen : imprime métricas y los comandos para desplegar al Pi (deploy_ssh.sh)

Requisitos: entorno training/.venv activo (o ejecutar con .venv/Scripts/python.exe).
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYS = ROOT.parent
EDGE = SYS / "edge_pose"
PY = sys.executable
VIDEOS = SYS.parent / "06 Material Fotografico - EPPs Envasado" / "Convertidos" / "Videos (MP4)"


def run(cmd: list[str], cwd: Path = ROOT, env: dict | None = None) -> None:
    print("\n$", " ".join(str(c) for c in cmd), flush=True)
    e = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    r = subprocess.run([str(c) for c in cmd], cwd=str(cwd), env=e)
    if r.returncode != 0:
        raise SystemExit(f"Fallo en: {' '.join(str(c) for c in cmd)}")


def set_config_model(model_dir_name: str) -> None:
    cfg = EDGE / "epp_edge" / "config.yaml"
    lines = cfg.read_text(encoding="utf-8").splitlines()
    in_epp = False
    for i, l in enumerate(lines):
        if l.startswith("epp:"):
            in_epp = True
        elif in_epp and l and not l.startswith(" "):
            in_epp = False
        if in_epp and l.strip().startswith("model:"):
            lines[i] = f'  model: "../models/{model_dir_name}"'
            break
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"config.yaml -> epp.model = ../models/{model_dir_name}")


def regression(name: str, seconds: int = 60) -> list[dict]:
    """Ejecuta el nodo con cada video de planta y devuelve los episodios registrados."""
    db = EDGE / "data" / f"regresion_{name}.sqlite"
    for p in EDGE.glob(f"data/regresion_{name}.sqlite*"):
        p.unlink()
    cfg_src = (EDGE / "epp_edge" / "config.yaml").read_text(encoding="utf-8")
    cfg_test = EDGE / "epp_edge" / f"config_regresion_{name}.yaml"
    cfg_test.write_text(cfg_src.replace('db_path: "../data/epp_edge.sqlite"', f'db_path: "../data/regresion_{name}.sqlite"')
                        .replace("absence_timeout_s: 5.0", "absence_timeout_s: 3.0"), encoding="utf-8")
    env = {"YOLO_CONFIG_DIR": str(EDGE / "data" / ".yolo")}
    for v in sorted(VIDEOS.glob("*.mp4")):
        run([PY, "-m", "epp_edge.main", "--config", cfg_test, "--source", v, "--no-cloud",
             "--max-seconds", str(seconds), "--port", "8099"], cwd=EDGE, env=env)
    cfg_test.unlink(missing_ok=True)
    con = sqlite3.connect(str(db)); con.row_factory = sqlite3.Row
    eps = [dict(r) for r in con.execute("SELECT * FROM episodios WHERE ts_fin IS NOT NULL ORDER BY ts_inicio")]
    con.close()
    return eps


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", required=True, help="carpeta o .zip exportado de Roboflow (YOLOv8, train/valid/test)")
    ap.add_argument("--name", required=True, help="nombre de la versión, p. ej. epp_v4")
    ap.add_argument("--models", nargs="+", default=["yolov8n", "yolo11n"])
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--regress-seconds", type=int, default=60)
    ap.add_argument("--force", action="store_true", help="actualizar config.yaml aunque no se alcance el objetivo del 90 %")
    for step in ("prepare", "train", "evaluate", "export", "config", "regress"):
        ap.add_argument(f"--skip-{step}", action="store_true")
    args = ap.parse_args()
    suffix = f"_{args.name}"
    data_yaml = ROOT / "data" / args.name / "data.yaml"

    if not args.skip_prepare:
        run([PY, "prepare_dataset.py", "--src", args.src, "--name", args.name])
    if not args.skip_train:
        run([PY, "train.py", "--models", *args.models, "--epochs", str(args.epochs), "--batch", str(args.batch),
             "--data", data_yaml, "--suffix", suffix])
    if not args.skip_evaluate:
        run([PY, "evaluate.py", "--data", data_yaml, "--runs", f"yolo*{suffix}", "--no-export", "--out", f"eval_{args.name}"])

    # mejor modelo según mAP50-95 en test
    metrics = json.loads((ROOT / "runs" / f"eval_{args.name}" / "metricas_test.json").read_text(encoding="utf-8"))
    best = max(metrics["global"], key=lambda g: g["mAP50-95"])
    best_run = best["modelo"]
    best_model = best_run.replace(suffix, "")
    print(f"\nMejor modelo en test: {best_run}  mAP50={best['mAP50']:.3f}  mAP50-95={best['mAP50-95']:.3f}  "
          f"P={best['precision']:.3f}  R={best['recall']:.3f}")
    ok_target = best["mAP50"] >= 0.90 and best["precision"] >= 0.90
    if not ok_target:
        print("AVISO: el modelo no alcanza el objetivo del 90 %; revisa el dataset antes de desplegar."
              + ("" if args.force else " (no se modifica config.yaml; usa --force para hacerlo)"))

    tag = f"{best_model}{suffix}"
    model_dir = f"epp_{tag}_640_ncnn_model"
    if not args.skip_export:
        run([PY, "export.py", "--epp-weights", ROOT / "runs" / best_run / "weights" / "best.pt", "--tag", tag, "--skip-person"])
    if not args.skip_config and (ok_target or args.force):
        set_config_model(model_dir)

    eps = []
    if not args.skip_regress:
        eps = regression(args.name, args.regress_seconds)
        print("\n=== Regresión con videos de planta ===")
        for e in eps:
            personas = json.loads(e.get("personas") or "[]")
            print(f"  {e['id']}: cumple={bool(e['cumple'])} faltantes={e.get('faltantes')} mal={e.get('mal_colocados')} "
                  f"gorra={e.get('ratio_gorra')} guante={e.get('ratio_guante')} mascarilla={e.get('ratio_mascarilla')} "
                  f"manos={[p.get('manos') for p in personas]}")

    summary = {"name": args.name, "src": args.src, "best_run": best_run, "metrics": best,
               "edge_model": model_dir, "regression": [{k: e.get(k) for k in ("id", "cumple", "faltantes", "mal_colocados",
                                                                              "ratio_gorra", "ratio_guante", "ratio_mascarilla")} for e in eps]}
    out = ROOT / "runs" / f"pipeline_{args.name}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResumen guardado en {out}")
    print("\nSiguiente paso — desplegar al Raspberry Pi:")
    print("  bash ../pi-boot/deploy_ssh.sh epp@epp-pi.local")
    print("y regenerar la tesis con:  python ../docs/build_tesis_v3.py")


if __name__ == "__main__":
    main()
