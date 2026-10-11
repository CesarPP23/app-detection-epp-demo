"""Dibujo de cajas sobre frames. Todas las funciones trabajan sobre una copia."""
import cv2
import numpy as np

from .models import Detection, Frame, Inference

# Colores de clase (tokens 3.8, tema claro) en BGR. Mapeo fijo por nombre.
_CLASS_COLORS_BGR = {
    "hairnet": (0xD6, 0x78, 0x2A),
    "lab_coat": (0x34, 0x68, 0xEB),
    "face_mask": (0x7A, 0xAF, 0x1B),
    "latex_gloves": (0x00, 0xA1, 0xED),
}
_FALLBACK_BGR = (0x81, 0x87, 0x89)


def class_color(name: str) -> tuple[int, int, int]:
    return _CLASS_COLORS_BGR.get(name, _FALLBACK_BGR)


def draw_detections(
    image: np.ndarray,
    detections: list[Detection],
    emphasis: str | None = None,
    thickness: int | None = None,
) -> np.ndarray:
    """Devuelve una copia con las cajas (coordenadas normalizadas) y su etiqueta.
    Si `emphasis` es una clase, solo esa lleva su color; las demás van en gris y más finas."""
    out = image.copy()
    h, w = out.shape[:2]
    base = thickness or max(1, round(min(h, w) / 360))
    scale = max(0.4, min(h, w) / 900)
    for d in detections:
        x1, y1, x2, y2 = d.bbox_xyxy_norm
        p1, p2 = (int(x1 * w), int(y1 * h)), (int(x2 * w), int(y2 * h))
        main = emphasis is None or d.nombre_clase == emphasis
        color = class_color(d.nombre_clase) if main else _FALLBACK_BGR
        t = base * 2 if (main and emphasis is not None) else base
        cv2.rectangle(out, p1, p2, color, t)
        label = f"{d.nombre_clase} {d.confianza:.0%}"
        (tw, th), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
        y_top = max(0, p1[1] - th - bl - 2)
        cv2.rectangle(out, (p1[0], y_top), (p1[0] + tw + 4, y_top + th + bl + 2), color, -1)
        cv2.putText(
            out, label, (p1[0] + 2, y_top + th + 1), cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 1,
            cv2.LINE_AA,
        )
    return out


def annotate(frame: Frame, inference: Inference | None) -> np.ndarray:
    """Frame del stream: cajas de la última inferencia y hora de captura con milisegundos
    (sirve para medir la latencia extremo a extremo apuntando la cámara a la pantalla)."""
    out = draw_detections(frame.image, inference.detections if inference else [])
    ts = frame.ts_wall.strftime("%H:%M:%S.") + f"{frame.ts_wall.microsecond // 1000:03d}"
    cv2.putText(out, ts, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(out, ts, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return out


def resize_to_width(image: np.ndarray, width: int) -> np.ndarray:
    h, w = image.shape[:2]
    if w <= width:
        return image
    return cv2.resize(image, (width, max(1, round(h * width / w))), interpolation=cv2.INTER_AREA)
