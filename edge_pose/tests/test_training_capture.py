"""Pruebas de la captura de fotos para reentrenamiento (sin Google Cloud: emulado en carpetas)."""
import json
import time

import numpy as np
import pytest

from epp_edge import cloud_v2 as cv
from epp_edge.training_capture import CaptureWorker, TRAINING_SESSIONS_COLLECTION


class FakeCamera:
    """Devuelve siempre un frame "nuevo" (no depende de last_id/timeout, a diferencia de la Camera real)."""

    def __init__(self):
        self._id = 0
        self.frame = np.full((48, 64, 3), 100, np.uint8)

    def wait_new(self, last_id, timeout=1.0):
        self._id += 1
        return self.frame, self._id, time.time()


@pytest.fixture
def env(tmp_path):
    bucket = cv.LocalBucket(tmp_path / "gcs")
    fs = cv.LocalFirestore(tmp_path / "fs")
    control = cv.DeviceControl(cv.LocalFirestore(tmp_path / "fs_control"), "pi-01")
    cam = FakeCamera()
    return control, bucket, fs, cam, tmp_path


def _photos(tmp_path, session_id="sess-1"):
    d = tmp_path / "gcs" / "raw" / "training_captures" / "device=pi-01" / f"session={session_id}"
    return list(d.glob("*.jpg")) if d.exists() else []


def test_no_sube_nada_sin_captura_solicitada(env):
    control, bucket, fs, cam, tmp_path = env
    worker = CaptureWorker(cam, control, bucket, fs, "pi-01", fps=10.0)
    for _ in range(3):
        worker.tick()
    assert not (tmp_path / "gcs").exists()


def test_sube_fotos_y_cierra_la_sesion_al_detener(env):
    control, bucket, fs, cam, tmp_path = env
    worker = CaptureWorker(cam, control, bucket, fs, "pi-01", fps=10.0)
    control.apply({"capture_requested": True, "capture_session_id": "sess-1"})
    for _ in range(5):
        worker.tick()
    assert len(_photos(tmp_path)) == 5
    assert worker.photo_count == 5

    control.apply({"capture_requested": False})
    worker.tick()  # debe notar el cambio y cerrar la sesión (sin subir una foto más)
    assert len(_photos(tmp_path)) == 5

    doc = json.loads((tmp_path / "fs" / TRAINING_SESSIONS_COLLECTION / "sess-1.json").read_text(encoding="utf-8"))
    assert doc["status"] == "completed"
    assert doc["closed_reason"] == "stopped_by_operator"
    assert doc["frame_count"] == 5
    assert "ended_at" in doc


def test_cierra_sola_por_max_duration(env):
    control, bucket, fs, cam, tmp_path = env
    clock = [1_000.0]
    worker = CaptureWorker(cam, control, bucket, fs, "pi-01", fps=10.0, max_duration_s=60.0, clock=lambda: clock[0])
    control.apply({"capture_requested": True, "capture_session_id": "sess-1"})
    worker.tick()  # abre la sesión y sube una foto
    assert len(_photos(tmp_path)) == 1

    clock[0] += 61
    worker.tick()  # supera max_duration_s: cierra sin subir otra foto, aunque capture_requested siga en true
    assert len(_photos(tmp_path)) == 1
    assert control.should_capture()  # el flag remoto sigue en true: no lo toca el worker, solo deja de obedecerlo

    doc = json.loads((tmp_path / "fs" / TRAINING_SESSIONS_COLLECTION / "sess-1.json").read_text(encoding="utf-8"))
    assert doc["status"] == "completed" and doc["closed_reason"] == "max_duration"

    # sin esto, el worker reabriría "sess-1" en el próximo ciclo porque el flag remoto no cambió
    worker.tick()
    worker.tick()
    assert len(_photos(tmp_path)) == 1, "la salvaguarda debe detener la captura, no cerrarla y reabrirla cada ciclo"
    doc = json.loads((tmp_path / "fs" / TRAINING_SESSIONS_COLLECTION / "sess-1.json").read_text(encoding="utf-8"))
    assert doc["status"] == "completed"

    # una sesión nueva de verdad (otro session_id) sí debe poder capturar
    control.apply({"capture_requested": True, "capture_session_id": "sess-2"})
    worker.tick()
    assert len(_photos(tmp_path, "sess-2")) == 1


def test_cambio_de_sesion_cierra_la_anterior_y_abre_otra(env):
    control, bucket, fs, cam, tmp_path = env
    worker = CaptureWorker(cam, control, bucket, fs, "pi-01", fps=10.0)
    control.apply({"capture_requested": True, "capture_session_id": "sess-1"})
    worker.tick()
    control.apply({"capture_requested": True, "capture_session_id": "sess-2"})
    worker.tick()
    doc1 = json.loads((tmp_path / "fs" / TRAINING_SESSIONS_COLLECTION / "sess-1.json").read_text(encoding="utf-8"))
    assert doc1["status"] == "completed"
    assert len(_photos(tmp_path, "sess-1")) == 1
    assert len(_photos(tmp_path, "sess-2")) == 1


def test_una_subida_fallida_se_reintenta_sin_perder_la_foto(env, monkeypatch):
    control, bucket, fs, cam, tmp_path = env
    worker = CaptureWorker(cam, control, bucket, fs, "pi-01", fps=10.0)
    control.apply({"capture_requested": True, "capture_session_id": "sess-1"})

    real_upload = worker._upload
    calls = {"n": 0}

    def flaky(path, data):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("sin red")
        real_upload(path, data)

    monkeypatch.setattr(worker, "_upload", flaky)
    worker.tick()
    assert calls["n"] == 2
    assert worker.photo_count == 1
    assert len(_photos(tmp_path)) == 1


def test_fallo_en_el_ciclo_no_tira_el_proceso(env, monkeypatch):
    control, bucket, fs, cam, tmp_path = env
    worker = CaptureWorker(cam, control, bucket, fs, "pi-01", fps=10.0)
    control.apply({"capture_requested": True, "capture_session_id": "sess-1"})

    def boom(*_a, **_k):
        raise RuntimeError("cámara caída")

    monkeypatch.setattr(cam, "wait_new", boom)
    worker.tick()  # no debe propagar la excepción
    assert worker.photo_count == 0
