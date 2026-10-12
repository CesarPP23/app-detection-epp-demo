import logging
import threading
from dataclasses import replace

import cv2
import numpy as np
import pytest
from epp_edge import capture
from epp_edge.capture import CaptureError, CaptureThread, FileSource, build_source
from epp_edge.config import Settings
from epp_edge.slots import LatestSlot

BASE = {
    "DEVICE_ID": "raspberry-pi-01",
    "ORCHESTRATOR_HOST": "orchestrator.example.run.app",
    "DEVICE_TOKEN": "x" * 40,
}


def settings(**kw):
    return Settings.from_env({**BASE, **kw})


@pytest.fixture()
def video(tmp_path):
    path = str(tmp_path / "t.avi")
    w = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"MJPG"), 50, (64, 48))
    for i in range(10):
        w.write(np.full((48, 64, 3), i * 20, np.uint8))
    w.release()
    return path


def test_file_source_hace_loop(video):
    s = settings(CAMERA_SOURCE=f"file:{video}", ALLOW_TEST_VIDEO="true")
    src = build_source(s)
    frames = [src.read() for _ in range(25)]  # más que los 10 del archivo => vuelve al inicio
    src.close()
    assert all(f is not None and f.shape == (48, 64, 3) for f in frames)


def test_file_source_exige_allow_test_video(video):
    s = replace(settings(CAMERA_SOURCE=f"file:{video}", ALLOW_TEST_VIDEO="true"), allow_test_video=False)
    with pytest.raises(CaptureError, match="ALLOW_TEST_VIDEO"):
        FileSource(s, video)


def test_auto_sin_camara_no_usa_video_de_prueba(monkeypatch):
    class Falla:
        def __init__(self, *a, **k):
            pass

        def open(self):
            raise CaptureError("sin cámara")

    monkeypatch.setattr(capture, "PiCameraSource", Falla)
    monkeypatch.setattr(capture, "OpenCVSource", Falla)
    monkeypatch.setattr(capture, "FileSource", lambda *a, **k: pytest.fail("no debe usarse FileSource"))
    with pytest.raises(CaptureError):
        build_source(settings(CAMERA_SOURCE="auto", ALLOW_TEST_VIDEO="true"))


def test_capture_thread_publica_frames(video):
    s = settings(CAMERA_SOURCE=f"file:{video}", ALLOW_TEST_VIDEO="true")
    slot, stop = LatestSlot(), threading.Event()
    t = CaptureThread(s, slot, stop, logging.getLogger("test"))
    t.start()
    seq, frame = slot.wait_newer(0, timeout=5)
    assert frame is not None and frame.seq >= 1 and frame.ts_wall.tzinfo is not None
    assert t.ok and t.source_name.startswith("file:")
    stop.set()
    t.join(timeout=5)


def test_capture_thread_sin_camara_marca_ok_false_y_no_emite():
    calls = []

    def factory(_s):
        calls.append(1)
        raise CaptureError("sin cámara")

    slot, stop = LatestSlot(), threading.Event()
    t = CaptureThread(settings(), slot, stop, logging.getLogger("test"), source_factory=factory)
    t.start()
    seq, frame = slot.wait_newer(0, timeout=0.5)
    assert frame is None and t.ok is False and calls
    stop.set()
    t.join(timeout=5)
