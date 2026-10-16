"""Etapa 0 — detección de movimiento con sustracción de fondo (MOG2).

Es la única etapa que corre cuando la zona está vacía. Trabaja sobre una versión
reducida del frame (ancho 160 px) para que cueste ~1 ms en la Raspberry Pi 5.
"""
from __future__ import annotations

import cv2
import numpy as np


class MotionDetector:
    def __init__(self, min_area_ratio: float = 0.01, history: int = 300,
                 var_threshold: float = 40, small_width: int = 160, roi=None, frame_size=None):
        self.min_area_ratio = min_area_ratio
        self.small_width = small_width
        self._roi_mask = None
        self._roi_area = None
        if roi and frame_size:
            w, h = frame_size
            scale = small_width / float(w)
            mask = np.zeros((int(h * scale), small_width), np.uint8)
            pts = np.array([(int(x * scale), int(y * scale)) for x, y in roi], np.int32)
            cv2.fillPoly(mask, [pts], 255)
            self._roi_mask, self._roi_area = mask, max(int(cv2.countNonZero(mask)), 1)
        self._bg = cv2.createBackgroundSubtractorMOG2(history=history, varThreshold=var_threshold,
                                                      detectShadows=False)
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.last_ratio = 0.0

    def detect(self, frame: np.ndarray) -> bool:
        h, w = frame.shape[:2]
        scale = self.small_width / float(w)
        small = cv2.resize(frame, (self.small_width, int(h * scale)), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        mask = self._bg.apply(gray)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        if self._roi_mask is not None and self._roi_mask.shape == mask.shape:
            mask = cv2.bitwise_and(mask, self._roi_mask)
            self.last_ratio = int(cv2.countNonZero(mask)) / float(self._roi_area)
        else:
            self.last_ratio = int(cv2.countNonZero(mask)) / float(mask.size)
        return self.last_ratio >= self.min_area_ratio
