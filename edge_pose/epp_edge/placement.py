"""Validación de la colocación de los EPP mediante estimación de pose (YOLOv8n-pose, 17 puntos COCO).

Para cada persona detectada se asocian las cajas de EPP a sus puntos clave y se aplican reglas
geométricas relativas al tamaño de la cabeza `s`, de modo que el criterio no depende de la distancia
a la cámara:

  Cofia           : centrada sobre la cabeza; borde superior >= 0,5·s por encima de la línea de ojos
                    (cubre la coronilla) y borde inferior <= 0,4·s por debajo de los ojos.
  Mascarilla      : la nariz cae dentro de la caja y el borde inferior queda >= 0,8·d bajo la nariz
                    (d = distancia ojos→nariz) -> cubre nariz y boca.  Si la nariz queda por encima
                    de la caja -> "bajo la nariz"; si la caja empieza bajo la boca -> "en la barbilla".
  Guante          : cada muñeca visible debe tener una caja de guante a menos de
                    0,7·antebrazo + 0,5·diagonal de la caja. Guantes lejos de toda muñeca se ignoran
                    (p. ej. un guante sobre la mesa).

Estados por EPP y frame:  "ok" | "mal" | "ausente" | "na" (no evaluable: puntos clave no visibles).
Los frames "na" no cuentan ni a favor ni en contra: evita penalizar a un operario de espaldas.

Índices COCO: 0 nariz, 1 ojo izq, 2 ojo der, 3 oreja izq, 4 oreja der, 5 hombro izq, 6 hombro der,
7 codo izq, 8 codo der, 9 muñeca izq, 10 muñeca der, 11 cadera izq, 12 cadera der.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .detectors import Detection

NOSE, L_EYE, R_EYE, L_EAR, R_EAR, L_SHO, R_SHO, L_ELB, R_ELB, L_WRI, R_WRI = range(11)
KP_CONF = 0.5
WRIST_CONF = 0.6      # las muñecas ocluidas conservan confianza media; se exige más para juzgar el guante
EPP_LABELS = ("Cofia", "Guante", "Mascarilla")
HANDS = ("izquierda", "derecha")


@dataclass
class Person:
    track_id: int
    box: tuple[int, int, int, int]
    kp: np.ndarray          # (17, 2) píxeles
    kc: np.ndarray          # (17,) confianza
    head_size: float = 0.0
    face_visible: bool = False
    status: dict = field(default_factory=dict)      # {"Cofia": "ok", ...}
    reasons: dict = field(default_factory=dict)     # {"Mascarilla": "bajo la nariz"}
    epp_boxes: dict = field(default_factory=dict)   # {"Cofia": [Detection], ...}
    hands: dict = field(default_factory=dict)       # {"izquierda": "gloved"|"bare"} solo muñecas visibles

    @property
    def center(self) -> tuple[float, float]:
        return (self.box[0] + self.box[2]) / 2, (self.box[1] + self.box[3]) / 2

    def visible(self, i: int) -> bool:
        return float(self.kc[i]) >= KP_CONF

    def pt(self, i: int) -> tuple[float, float]:
        return float(self.kp[i][0]), float(self.kp[i][1])


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _box_center(d: Detection) -> tuple[float, float]:
    return (d.x1 + d.x2) / 2, (d.y1 + d.y2) / 2


def _box_diag(d: Detection) -> float:
    return math.hypot(d.x2 - d.x1, d.y2 - d.y1)


def head_size(p: Person) -> float:
    """Tamaño de cabeza en píxeles, robusto a la pose (perfil / frente)."""
    cands = []
    if p.visible(L_EAR) and p.visible(R_EAR):
        cands.append(_dist(p.pt(L_EAR), p.pt(R_EAR)))
    if p.visible(L_EYE) and p.visible(R_EYE):
        cands.append(2.6 * _dist(p.pt(L_EYE), p.pt(R_EYE)))
    if p.visible(L_SHO) and p.visible(R_SHO):
        cands.append(0.45 * _dist(p.pt(L_SHO), p.pt(R_SHO)))
    h = p.box[3] - p.box[1]
    cands.append(0.13 * h)
    return max(cands)


def eye_line(p: Person) -> Optional[tuple[float, float]]:
    """Punto medio de los ojos visibles (o de las orejas) como referencia de la línea de ojos."""
    pts = [p.pt(i) for i in (L_EYE, R_EYE) if p.visible(i)]
    if not pts:
        pts = [p.pt(i) for i in (L_EAR, R_EAR) if p.visible(i)]
    if not pts:
        return None
    return sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)


# --------------------------------------------------------------------------- asociación
def associate(persons: list[Person], dets: list[Detection], conf_high: float = 1.0) -> list[Detection]:
    """Asigna cada caja de EPP a la persona cuyo punto de referencia esté más cerca (o a ninguna).

    Umbral contextual: las detecciones con confianza < `conf_high` solo se aceptan si caen donde la
    anatomía las espera (radio 1,0·s del punto de referencia); las de confianza alta usan un radio
    amplio. Devuelve la lista de detecciones aceptadas (las débiles sin contexto se descartan).
    """
    for p in persons:
        p.epp_boxes = {k: [] for k in EPP_LABELS}
        p.head_size = head_size(p)
    kept: list[Detection] = []
    for d in dets:
        c = _box_center(d)
        strong = d.conf >= conf_high
        best, best_d = None, float("inf")
        for p in persons:
            if d.label in ("Cofia", "Mascarilla"):
                ref = eye_line(p) or (p.pt(NOSE) if p.visible(NOSE) else None)
                limit = (2.0 if strong else 1.0) * p.head_size
            else:  # Guante -> muñeca más cercana
                wr = [p.pt(i) for i in (L_WRI, R_WRI) if p.visible(i)]
                ref = min(wr, key=lambda w: _dist(w, c)) if wr else None
                limit = (3.0 if strong else 1.2) * p.head_size
            if ref is None:
                if not strong:
                    continue
                # sin puntos clave: usar el centro de la caja de la persona con límite amplio
                ref, limit = p.center, max(p.box[2] - p.box[0], p.box[3] - p.box[1])
            dd = _dist(ref, c)
            if dd < best_d and dd <= limit:
                best, best_d = p, dd
        if best is not None:
            best.epp_boxes[d.label].append(d)
            kept.append(d)
        elif strong:
            kept.append(d)
    return kept


# --------------------------------------------------------------------------- reglas
def eval_cofia(p: Person) -> tuple[str, str]:
    el = eye_line(p)
    if el is None:
        return "na", ""
    boxes = p.epp_boxes.get("Cofia", [])
    if not boxes:
        return "ausente", "sin cofia"
    s = p.head_size
    b = max(boxes, key=lambda d: d.conf)
    cx, _ = _box_center(b)
    if abs(cx - el[0]) > 0.9 * s:
        return "mal", "desplazada lateralmente"
    if b.y1 > el[1] - 0.45 * s:
        return "mal", "echada hacia atrás / no cubre la coronilla"
    if b.y2 > el[1] + 0.6 * s:
        return "mal", "cubre los ojos"
    return "ok", ""


def eval_mascarilla(p: Person) -> tuple[str, str]:
    if not p.visible(NOSE):
        return "na", ""
    el = eye_line(p)
    if el is None:
        return "na", ""
    nose = p.pt(NOSE)
    s = p.head_size
    d = max(nose[1] - el[1], 0.22 * s)          # distancia ojos -> nariz
    boxes = p.epp_boxes.get("Mascarilla", [])
    if not boxes:
        return "ausente", "sin mascarilla"
    b = max(boxes, key=lambda x: x.conf)
    margin = 0.15 * s
    nose_inside = (b.x1 - margin <= nose[0] <= b.x2 + margin) and (b.y1 - margin <= nose[1] <= b.y2 + margin)
    if not nose_inside:
        if nose[1] < b.y1 - margin:
            return "mal", "bajo la nariz"
        if nose[1] > b.y2 + margin:
            return "mal", "sobre la frente"
        return "mal", "desplazada"
    if b.y2 < nose[1] + 0.8 * d:
        return "mal", "no cubre la boca"
    if b.y1 > nose[1] + 0.3 * d:
        return "mal", "en la barbilla"
    return "ok", ""


def eval_guante(p: Person) -> tuple[str, str]:
    """Juzga cada muñeca visible por separado; el veredicto del episodio se agrega por mano (PersonStats).

    Cada muñeca toma el guante más cercano dentro de su radio (sin exclusividad). Dos muñecas pueden
    compartir un guante solo si están juntas (< 1,2·s: manos sobre el bidón o brazo ocluido colapsado
    por el modelo de pose); si están separadas y comparten caja, la más lejana se considera sin guante.
    """
    wrists = [(i, p.pt(i)) for i in (L_WRI, R_WRI) if float(p.kc[i]) >= WRIST_CONF]
    p.hands = {}
    s = p.head_size
    if not wrists:
        return "na", ""
    boxes = list(p.epp_boxes.get("Guante", []))
    match: dict[int, Optional[Detection]] = {}
    dist_to: dict[int, float] = {}
    for i, w in wrists:
        elb = L_ELB if i == L_WRI else R_ELB
        forearm = _dist(w, p.pt(elb)) if p.visible(elb) else 2.5 * s
        best, best_d = None, float("inf")
        for b in boxes:
            dd = _dist(w, _box_center(b))
            if dd <= 0.7 * forearm + 0.6 * _box_diag(b) and dd < best_d:
                best, best_d = b, dd
        match[i], dist_to[i] = best, best_d
    if len(wrists) == 2 and match[L_WRI] is not None and match[L_WRI] is match[R_WRI]:
        if _dist(wrists[0][1], wrists[1][1]) > 1.2 * s:
            far = max((L_WRI, R_WRI), key=lambda i: dist_to[i])
            match[far] = None
    bare = []
    for i, _ in wrists:
        hand = "izquierda" if i == L_WRI else "derecha"
        if match[i] is not None:
            p.hands[hand] = "gloved"
        else:
            p.hands[hand] = "bare"
            bare.append(hand)
    if not bare:
        return "ok", ""
    if len(bare) == len(wrists):
        return "ausente", "sin guantes"
    return "mal", f"mano {bare[0]} sin guante"


def evaluate_frame(persons: list[Person], dets: list[Detection], conf_high: float = 1.0) -> list[Detection]:
    """Asocia, valida y devuelve las detecciones aceptadas (ver associate)."""
    kept = associate(persons, dets, conf_high)
    for p in persons:
        p.face_visible = eye_line(p) is not None
        for label, fn in (("Cofia", eval_cofia), ("Mascarilla", eval_mascarilla), ("Guante", eval_guante)):
            st, why = fn(p)
            p.status[label] = st
            if why:
                p.reasons[label] = why
    return kept


# --------------------------------------------------------------------------- agregación por persona
class PersonStats:
    """Acumula, por persona (track id), los estados de cada EPP a lo largo del episodio."""

    def __init__(self, track_id: int):
        self.track_id = track_id
        self.frames = 0
        self.counts = {k: {"ok": 0, "mal": 0, "ausente": 0, "na": 0} for k in EPP_LABELS}
        self.reasons: dict[str, dict[str, int]] = {k: {} for k in EPP_LABELS}
        self.hands = {h: {"gloved": 0, "bare": 0} for h in HANDS}

    def add(self, p: Person) -> None:
        self.frames += 1
        for k in EPP_LABELS:
            st = p.status.get(k, "na")
            self.counts[k][st] += 1
            if st == "mal" and k in p.reasons:
                r = self.reasons[k]
                r[p.reasons[k]] = r.get(p.reasons[k], 0) + 1
        for h, st in (p.hands or {}).items():
            self.hands[h][st] += 1

    # ---- guante: agregación por mano (una mano ocluida a ratos no penaliza)
    def hand_eval(self, h: str) -> int:
        return self.hands[h]["gloved"] + self.hands[h]["bare"]

    def hand_ratio(self, h: str) -> Optional[float]:
        e = self.hand_eval(h)
        return self.hands[h]["gloved"] / e if e else None

    def evaluable(self, k: str) -> int:
        if k == "Guante":
            return max(self.hand_eval(h) for h in HANDS)
        c = self.counts[k]
        return c["ok"] + c["mal"] + c["ausente"]

    def ratio_ok(self, k: str) -> Optional[float]:
        if k == "Guante":
            rs = [self.hand_ratio(h) for h in HANDS if self.hand_ratio(h) is not None]
            return min(rs) if rs else None
        e = self.evaluable(k)
        return self.counts[k]["ok"] / e if e else None

    def ratio_mal(self, k: str) -> Optional[float]:
        e = self.evaluable(k)
        return self.counts[k]["mal"] / e if e else None

    def main_reason(self, k: str) -> Optional[str]:
        r = self.reasons[k]
        return max(r, key=r.get) if r else None

    def verdict(self, required: list[str], compliance_ratio: float, min_eval: int) -> dict:
        faltantes, mal, no_eval = [], [], []
        for k in required:
            if k == "Guante":
                hands = [h for h in HANDS if self.hand_eval(h) >= min_eval]
                if not hands:
                    no_eval.append(k)
                    continue
                bad = [h for h in hands if (self.hand_ratio(h) or 0.0) < compliance_ratio]
                if not bad:
                    continue
                if len(bad) == len(hands):
                    faltantes.append(k)          # todas las manos visibles sin guante
                else:
                    mal.append(f"Guante: mano {bad[0]} sin guante")
                continue
            e = self.evaluable(k)
            if e < min_eval:
                no_eval.append(k)
                continue
            if (self.ratio_ok(k) or 0.0) < compliance_ratio:
                rm = self.ratio_mal(k) or 0.0
                if rm >= 0.3 and self.main_reason(k):
                    mal.append(f"{k}: {self.main_reason(k)}")
                else:
                    faltantes.append(k)
        return {"cumple": int(not faltantes and not mal), "faltantes": faltantes, "mal_colocados": mal, "no_evaluables": no_eval}

    def to_dict(self, required, compliance_ratio, min_eval) -> dict:
        v = self.verdict(required, compliance_ratio, min_eval)
        return {
            "persona": self.track_id, "frames": self.frames,
            **{f"ratio_{k.lower()}": (round(self.ratio_ok(k), 3) if self.ratio_ok(k) is not None else None) for k in EPP_LABELS},
            **{f"eval_{k.lower()}": self.evaluable(k) for k in EPP_LABELS},
            **{f"mal_{k.lower()}": (round(self.ratio_mal(k), 3) if self.ratio_mal(k) is not None else None) for k in EPP_LABELS},
            "manos": {h: {"frames": self.hand_eval(h), "ratio": (round(self.hand_ratio(h), 3) if self.hand_ratio(h) is not None else None)} for h in HANDS},
            "motivos": {k: self.main_reason(k) for k in EPP_LABELS if self.main_reason(k)},
            **v,
        }
