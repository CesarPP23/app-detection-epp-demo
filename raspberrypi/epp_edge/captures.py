"""Una captura por evento (U4, D4): imagen original, miniatura anotada y sidecar JSON.

Todo se escribe en DATA_DIR/pending/{event_id}/ de forma atómica (.tmp + os.replace) y el
Uploader lo sube después. Un evento NUNCA se descarta porque su captura falle: se registra con
`capture.status` ("missing" aquí; "dropped_disk_full" lo marca el Outbox).
"""

import copy
import hashlib
import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2

from .annotate import draw_detections, resize_to_width
from .models import ClosedEvent
from .outbox import iso, lake_prefix

log = logging.getLogger("captures")


@dataclass
class CaptureFiles:
    event_id: str
    pending_dir: Path | None  # None si no hay imágenes que subir
    payload: dict[str, Any]  # contrato v2 con la sección `capture` completa
    status: str


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _encode(image, quality: int) -> bytes:
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("cv2.imencode falló")
    return buf.tobytes()


def _no_capture(p: dict, status: str, reason: str) -> dict:
    p["capture"] = {"status": status, "reason": reason, "uri": None, "thumb_uri": None}
    return p


def build_capture(ev: ClosedEvent, s) -> CaptureFiles:
    p = copy.deepcopy(ev.payload)
    event_id = p["event_id"]
    p["detections_at_capture"] = [d.to_dict() for d in ev.best_detections]
    frame = ev.best_frame
    if frame is None or getattr(frame, "image", None) is None:
        log.error("Evento %s sin frame de captura; se registra con capture.status=missing", event_id)
        return CaptureFiles(event_id, None, _no_capture(p, "missing", "no_best_frame"), "missing")

    pending_dir = Path(s.data_dir) / "pending" / event_id
    try:
        original = _encode(frame.image, s.capture_jpeg_quality)
        thumb_img = draw_detections(
            resize_to_width(frame.image, s.thumb_width), ev.best_detections, p["nombre_clase"]
        )
        thumb = _encode(thumb_img, s.thumb_jpeg_quality)

        h, w = frame.image.shape[:2]
        prefix = lake_prefix(p, s.site_timezone)
        base = f"gs://{s.lake_bucket}/raw"
        at_class = [d.confianza for d in ev.best_detections if d.nombre_clase == p["nombre_clase"]]
        p["capture"] = {
            "status": "ok",
            "uri": f"{base}/images/{prefix}/{event_id}.jpg",
            "thumb_uri": f"{base}/thumbs/{prefix}/{event_id}.jpg",
            "captured_at": iso(frame.ts_wall),
            "sha256": hashlib.sha256(original).hexdigest(),
            "width": w,
            "height": h,
            "bytes": len(original),
            "thumb_sha256": hashlib.sha256(thumb).hexdigest(),
            "thumb_bytes": len(thumb),
            "frame_seq": frame.seq,
            "confidence_at_capture": round(max(at_class), 4) if at_class else p.get("confianza_max"),
        }
        pending_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(pending_dir / "original.jpg", original)
        _atomic_write(pending_dir / "thumb.jpg", thumb)
        _atomic_write(
            pending_dir / "event.json",
            json.dumps(p, separators=(",", ":"), default=iso).encode(),
        )
    except Exception as e:  # noqa: BLE001 - el evento se conserva sin imagen
        log.exception("No se pudo construir la captura de %s", event_id)
        shutil.rmtree(pending_dir, ignore_errors=True)
        return CaptureFiles(
            event_id, None, _no_capture(p, "missing", f"{type(e).__name__}: {e}"[:200]), "missing"
        )
    return CaptureFiles(event_id, pending_dir, p, "ok")
