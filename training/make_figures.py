"""
Genera las figuras y tablas del capítulo de Resultados a partir de runs/.

Salidas en runs/figures/:
  - curvas_entrenamiento_<modelo>.png   (box/cls/dfl loss + mAP50 / mAP50-95 por época)
  - comparativa_mAP.png                 (mAP50 y mAP50-95 en test por modelo)
  - metricas_por_clase.png              (P/R/AP50 por clase, modelo nano)
  - tabla_metricas_globales.md / tabla_metricas_por_clase.md (para pegar en la tesis)
  - copia de matrices de confusión normalizadas desde runs/eval/<modelo>/

Uso:
    python make_figures.py
"""
from __future__ import annotations

import shutil
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
EVAL = RUNS / "eval"
FIG = RUNS / "figures"
FIG.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({"font.size": 10, "figure.dpi": 150})


def training_curves(run: Path) -> None:
    csv = run / "results.csv"
    if not csv.exists():
        return
    df = pd.read_csv(csv)
    df.columns = [c.strip() for c in df.columns]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    for col, lbl in [("train/box_loss", "box"), ("train/cls_loss", "cls"), ("train/dfl_loss", "dfl")]:
        ax[0].plot(df["epoch"], df[col], label=f"train {lbl}")
    for col, lbl in [("val/box_loss", "box"), ("val/cls_loss", "cls"), ("val/dfl_loss", "dfl")]:
        ax[0].plot(df["epoch"], df[col], "--", label=f"val {lbl}")
    ax[0].set_title(f"Pérdidas — {run.name}"); ax[0].set_xlabel("época"); ax[0].legend(fontsize=8)
    ax[1].plot(df["epoch"], df["metrics/mAP50(B)"], label="mAP50")
    ax[1].plot(df["epoch"], df["metrics/mAP50-95(B)"], label="mAP50-95")
    ax[1].plot(df["epoch"], df["metrics/precision(B)"], ":", label="precisión")
    ax[1].plot(df["epoch"], df["metrics/recall(B)"], ":", label="recall")
    ax[1].axhline(0.90, color="gray", lw=0.8, ls="--"); ax[1].text(0, 0.905, "objetivo 90 %", fontsize=7, color="gray")
    ax[1].set_ylim(0, 1); ax[1].set_title(f"Métricas de validación — {run.name}"); ax[1].set_xlabel("época"); ax[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(FIG / f"curvas_entrenamiento_{run.name}.png")
    plt.close(fig)


def comparison(split: str = "test") -> None:
    g = EVAL / f"metricas_globales_{split}.csv"
    c = EVAL / f"metricas_por_clase_{split}.csv"
    if not g.exists():
        return
    dfg = pd.read_csv(g)
    dfc = pd.read_csv(c)

    fig, ax = plt.subplots(figsize=(7, 4))
    x = range(len(dfg))
    ax.bar([i - 0.2 for i in x], dfg["mAP50"], width=0.4, label="mAP50")
    ax.bar([i + 0.2 for i in x], dfg["mAP50-95"], width=0.4, label="mAP50-95")
    ax.set_xticks(list(x)); ax.set_xticklabels(dfg["modelo"], rotation=15)
    ax.axhline(0.90, color="gray", lw=0.8, ls="--")
    ax.set_ylim(0, 1); ax.set_ylabel("valor"); ax.set_title(f"Comparativa de modelos ({split})"); ax.legend()
    for i, v in enumerate(dfg["mAP50"]):
        ax.text(i - 0.2, v + 0.01, f"{v:.3f}", ha="center", fontsize=8)
    fig.tight_layout(); fig.savefig(FIG / "comparativa_mAP.png"); plt.close(fig)

    nano = dfc[dfc["modelo"] == "yolov8n"]
    if len(nano):
        fig, ax = plt.subplots(figsize=(7, 4))
        x = range(len(nano))
        for k, (col, off) in enumerate([("precision", -0.25), ("recall", 0), ("AP50", 0.25)]):
            ax.bar([i + off for i in x], nano[col], width=0.25, label=col)
        ax.set_xticks(list(x)); ax.set_xticklabels(nano["clase"])
        ax.set_ylim(0, 1); ax.set_title("YOLOv8n — métricas por clase (test)"); ax.legend()
        fig.tight_layout(); fig.savefig(FIG / "metricas_por_clase.png"); plt.close(fig)

    (FIG / "tabla_metricas_globales.md").write_text(dfg.to_markdown(index=False), encoding="utf-8")
    (FIG / "tabla_metricas_por_clase.md").write_text(dfc.to_markdown(index=False), encoding="utf-8")

    for run in EVAL.iterdir():
        for name in ("confusion_matrix_normalized.png", "PR_curve.png", "F1_curve.png"):
            src = run / name
            if src.exists():
                shutil.copy2(src, FIG / f"{run.name}_{name}")


def main() -> None:
    for run in sorted(RUNS.glob("yolo*")):
        training_curves(run)
    comparison("test")
    print("Figuras en", FIG)
    for p in sorted(FIG.iterdir()):
        print("  ", p.name)


if __name__ == "__main__":
    main()
