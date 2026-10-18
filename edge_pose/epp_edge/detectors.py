"""Envoltorios de inferencia (Ultralytics YOLO sobre NCNN / ONNX / PyTorch).

  - PoseDetector   : YOLOv8n-pose COCO (17 puntos clave) + ByteTrack, imgsz 320. Actúa de compuerta
                     (¿hay alguien?) y aporta los puntos clave para validar la colocación de los EPP.
                     Devuelve placement.Person (caja, id de seguimiento, puntos clave y confianzas).
  - EPPDetector    : modelo propio (Cofia, Guante, Mascarilla), imgsz 640. Devuelve Detection en
                     coordenadas de píxel del frame original.
  - PersonDetector : detector de personas COCO (clase 0); se conserva solo para comparación en benchmark.
  - draw()         : anotación del frame (cajas, esqueleto, veredicto por persona, ROI, difuminado).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

log = logging.getLogger("epp.detectors")


@dataclass
class Detection:
    cls_id: int
    label: str
    conf: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.x2, self.y2


class _YoloWrapper:
    def __init__(self, model_path: str | Path, imgsz: int, conf: float, iou: float = 0.5,
                 classes: Optional[Iterable[int]] = None, task: str = "detect"):
        from ultralytics import YOLO  # import tardío: acelera el arranque y facilita los tests

        self.model_path = str(model_path)
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou
        self.classes = list(classes) if classes is not None else None
        t0 = time.time()
        self.model = YOLO(self.model_path, task=task)
        self.names = self.model.names
        self.last_ms = 0.0
        self.calls = 0
        self.total_ms = 0.0
        log.info("Modelo cargado %s (imgsz=%d) en %.1fs", self.model_path, imgsz, time.time() - t0)

    def __call__(self, frame: np.ndarray) -> list[Detection]:
        t0 = time.perf_counter()
        results = self.model.predict(frame, imgsz=self.imgsz, conf=self.conf, iou=self.iou,
                                     classes=self.classes, verbose=False, device="cpu")
        self.last_ms = (time.perf_counter() - t0) * 1000
        self.calls += 1
        self.total_ms += self.last_ms
        out: list[Detection] = []
        r = results[0]
        if r.boxes is None or len(r.boxes) == 0:
            return out
        xyxy = r.boxes.xyxy.cpu().numpy().astype(int)
        confs = r.boxes.conf.cpu().numpy()
        clss = r.boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), c, k in zip(xyxy, confs, clss):
            out.append(Detection(int(k), str(self.names.get(int(k), k)), float(c), int(x1), int(y1), int(x2), int(y2)))
        return out

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0.0

    def warmup(self, shape=(480, 640, 3)) -> None:
        self(np.zeros(shape, dtype=np.uint8))
        self.calls, self.total_ms = 0, 0.0


class PersonDetector(_YoloWrapper):
    def __init__(self, model_path, imgsz=320, conf=0.45):
        super().__init__(model_path, imgsz, conf, iou=0.5, classes=[0])

    def has_person(self, frame: np.ndarray) -> tuple[bool, list[Detection]]:
        dets = self(frame)
        return len(dets) > 0, dets


class PoseDetector:
    """YOLOv8n-pose + ByteTrack: detecta personas, sus 17 puntos clave y un id estable por persona.

    Sustituye al detector de personas: sirve de compuerta (¿hay alguien?) y, en ACTIVE, aporta los
    puntos clave con los que se valida la colocación de cada EPP (ver placement.py).
    """

    def __init__(self, model_path, imgsz=320, conf=0.4, tracker="bytetrack.yaml"):
        from ultralytics import YOLO

        self.model_path = str(model_path)
        self.imgsz, self.conf, self.tracker = imgsz, conf, tracker
        t0 = time.time()
        self.model = YOLO(self.model_path, task="pose")
        self.last_ms = 0.0
        self.calls = 0
        self.total_ms = 0.0
        self._track_ok = True
        log.info("Modelo de pose cargado %s (imgsz=%d) en %.1fs", self.model_path, imgsz, time.time() - t0)

    def __call__(self, frame: np.ndarray, track: bool = True):
        """Devuelve lista de placement.Person (sin evaluar)."""
        from .placement import Person

        t0 = time.perf_counter()
        if track and self._track_ok:
            try:
                r = self.model.track(frame, imgsz=self.imgsz, conf=self.conf, persist=True, tracker=self.tracker,
                                     verbose=False, device="cpu")[0]
            except Exception as e:  # sin `lap` u otro fallo del tracker: seguir sin ids
                log.warning("Tracking no disponible (%s); se usan ids por orden", e.__class__.__name__)
                self._track_ok = False
                r = self.model.predict(frame, imgsz=self.imgsz, conf=self.conf, verbose=False, device="cpu")[0]
        else:
            r = self.model.predict(frame, imgsz=self.imgsz, conf=self.conf, verbose=False, device="cpu")[0]
        self.last_ms = (time.perf_counter() - t0) * 1000
        self.calls += 1
        self.total_ms += self.last_ms
        persons = []
        if r.boxes is None or len(r.boxes) == 0 or r.keypoints is None:
            return persons
        xyxy = r.boxes.xyxy.cpu().numpy().astype(int)
        ids = r.boxes.id.cpu().numpy().astype(int) if r.boxes.id is not None else np.arange(1, len(xyxy) + 1)
        kps = r.keypoints.xy.cpu().numpy()
        kcs = r.keypoints.conf.cpu().numpy() if r.keypoints.conf is not None else np.ones(kps.shape[:2])
        for (x1, y1, x2, y2), tid, kp, kc in zip(xyxy, ids, kps, kcs):
            persons.append(Person(int(tid), (int(x1), int(y1), int(x2), int(y2)), kp, kc))
        return persons

    def has_person(self, frame: np.ndarray):
        persons = self(frame, track=True)
        return len(persons) > 0, persons

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.calls if self.calls else 0.0

    def warmup(self, shape=(480, 640, 3)) -> None:
        self(np.zeros(shape, dtype=np.uint8), track=False)
        self.calls, self.total_ms = 0, 0.0


class EPPDetector(_YoloWrapper):
    def __init__(self, model_path, imgsz=640, conf=0.40, iou=0.5):
        super().__init__(model_path, imgsz, conf, iou=iou)


def blur_heads(img: np.ndarray, dets: list[Detection]) -> np.ndarray:
    """Difumina, sobre `img` (in situ), la región de la cabeza estimada a partir de las cajas de cofia y mascarilla."""
    import cv2
    for d in dets:
        if d.label in ("Cofia", "Mascarilla"):
            y1, y2 = max(0, d.y1 - 10), min(img.shape[0], d.y2 + 40)
            x1, x2 = max(0, d.x1 - 20), min(img.shape[1], d.x2 + 20)
            roi_ = img[y1:y2, x1:x2]
            if roi_.size:
                img[y1:y2, x1:x2] = cv2.GaussianBlur(roi_, (51, 51), 0)
    return img


def draw(frame: np.ndarray, dets: list[Detection], persons=None, status: str = "", fps: float = 0.0,
         blur_faces: bool = False, roi=None) -> np.ndarray:
    """Dibuja cajas de EPP, esqueleto simplificado y veredicto por persona sobre una copia del frame."""
    import cv2

    colors = {"Cofia": (255, 160, 0), "Guante": (0, 200, 0), "Mascarilla": (0, 120, 255)}
    st_color = {"ok": (0, 200, 0), "mal": (0, 0, 255), "ausente": (0, 0, 255), "na": (160, 160, 160)}
    img = frame.copy()
    if roi is not None:
        cv2.polylines(img, [np.array(roi, dtype=np.int32)], True, (255, 255, 0), 1)
    if blur_faces:
        blur_heads(img, dets)
    for d in dets:
        c = colors.get(d.label, (255, 255, 255))
        cv2.rectangle(img, (d.x1, d.y1), (d.x2, d.y2), c, 2)
        txt = f"{d.label} {d.conf:.2f}"
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.rectangle(img, (d.x1, d.y1 - th - 6), (d.x1 + tw + 4, d.y1), c, -1)
        cv2.putText(img, txt, (d.x1 + 2, d.y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1, cv2.LINE_AA)
    links = [(5, 6), (5, 7), (7, 9), (6, 8), (8, 10), (0, 1), (0, 2), (1, 3), (2, 4)]
    for p in persons or []:
        x1, y1, x2, y2 = p.box
        cv2.rectangle(img, (x1, y1), (x2, y2), (220, 220, 220), 1)
        kp = getattr(p, "kp", None)
        if kp is not None:
            for a_, b_ in links:
                if p.visible(a_) and p.visible(b_):
                    cv2.line(img, tuple(map(int, p.pt(a_))), tuple(map(int, p.pt(b_))), (255, 255, 255), 1, cv2.LINE_AA)
            for i in range(11):
                if p.visible(i):
                    cv2.circle(img, tuple(map(int, p.pt(i))), 2, (255, 255, 255), -1)
        # veredicto por EPP: bloque de 4 líneas encima de la caja (o debajo si no hay sitio)
        lines = [(f"P{p.track_id}", (255, 255, 255))]
        for k in ("Cofia", "Mascarilla", "Guante"):
            stv = p.status.get(k, "na") if hasattr(p, "status") else "na"
            mark = {"ok": "OK", "mal": "MAL", "ausente": "NO", "na": "--"}[stv]
            why = p.reasons.get(k, "") if hasattr(p, "reasons") and stv in ("mal", "ausente") else ""
            lines.append((f"{k[:4]} {mark} {why}"[:30], st_color[stv]))
        block_h = 13 * len(lines) + 4
        top = y1 - block_h if y1 - block_h > 24 else min(y2 + 2, img.shape[0] - block_h)
        cv2.rectangle(img, (x1, top), (x1 + 150, top + block_h), (0, 0, 0), -1)
        for i, (txt, col) in enumerate(lines):
            cv2.putText(img, txt, (x1 + 3, top + 11 + 13 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.38, col, 1, cv2.LINE_AA)
    if status:
        bar = f"{status}  {fps:.1f} fps  {time.strftime('%Y-%m-%d %H:%M:%S')}"
        cv2.rectangle(img, (0, 0), (img.shape[1], 22), (0, 0, 0), -1)
        cv2.putText(img, bar, (6, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return img
