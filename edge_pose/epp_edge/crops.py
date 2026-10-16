"""Inferencia por recorte (crop) alrededor de cada persona.

Motivación: con la cámara a 3-4 m el operario ocupa ~150 px de alto en un frame de 640 px; a la
resolución de entrada del modelo (320 px para pose, 640 para EPP) el brazo lejano y los guantes
quedan en muy pocos píxeles. Recortando un cuadrado expandido alrededor de la caja de la persona y
pasándolo al modelo, la persona llena la entrada: la pose gana precisión en muñecas/codos y el
detector de EPP ve los guantes ~2× más grandes, al mismo costo por inferencia.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from .detectors import Detection


def crop_region(box, frame_shape, expand: float = 1.35, min_size: int = 320) -> tuple[int, int, int, int]:
    """Cuadrado expandido alrededor de `box` (x1,y1,x2,y2), recortado a los límites del frame."""
    h, w = frame_shape[:2]
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    side = max((x2 - x1) * expand, (y2 - y1) * expand, float(min_size))
    side = min(side, float(max(w, h)))
    X1 = int(round(cx - side / 2)); Y1 = int(round(cy - side / 2))
    X2 = int(round(cx + side / 2)); Y2 = int(round(cy + side / 2))
    # desplazar dentro del frame conservando el tamaño cuando sea posible
    if X1 < 0: X2 -= X1; X1 = 0
    if Y1 < 0: Y2 -= Y1; Y1 = 0
    if X2 > w: X1 -= (X2 - w); X2 = w
    if Y2 > h: Y1 -= (Y2 - h); Y2 = h
    return max(0, X1), max(0, Y1), min(w, X2), min(h, Y2)


def shift_dets(dets: Iterable[Detection], ox: int, oy: int) -> list[Detection]:
    return [Detection(d.cls_id, d.label, d.conf, d.x1 + ox, d.y1 + oy, d.x2 + ox, d.y2 + oy) for d in dets]


def iou(a, b) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / float(area) if area else 0.0


def merge_dets(dets: list[Detection], thr: float = 0.6) -> list[Detection]:
    """NMS sencillo por clase para fusionar detecciones de recortes solapados."""
    out: list[Detection] = []
    for d in sorted(dets, key=lambda d: -d.conf):
        if all(not (o.label == d.label and iou(o.box, d.box) >= thr) for o in out):
            out.append(d)
    return out


def refine_person(p, crop_persons, ox: int, oy: int, min_iou: float = 0.2) -> bool:
    """Sustituye caja y puntos clave de `p` por los del recorte que mejor coincide. True si se refinó."""
    if not crop_persons:
        return False
    expected = (p.box[0] - ox, p.box[1] - oy, p.box[2] - ox, p.box[3] - oy)
    best = max(crop_persons, key=lambda q: iou(q.box, expected))
    if iou(best.box, expected) < min_iou:
        return False
    p.box = (best.box[0] + ox, best.box[1] + oy, best.box[2] + ox, best.box[3] + oy)
    kp = np.array(best.kp, dtype=float); kp[:, 0] += ox; kp[:, 1] += oy
    p.kp, p.kc = kp, np.array(best.kc, dtype=float)
    return True
