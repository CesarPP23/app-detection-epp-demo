"""Criterios de comportamiento de la Fase 2 con el pipeline REAL (captura -> inferencia ->
agregador -> captura -> outbox -> uploader) y dobles en memoria para GCP.

No usa GCP, ultralytics ni picamera2. Los hilos y el FileSource son los de producción; el modelo
es FakeModel (detecciones deterministas) y el bucket/Firestore son los de _fakes.
"""
import json
import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from _fakes import FakeBucket, FakeClock, FakeDB, snap
from fakes import ConnectSpy, FakeModel, RecordingDB

from epp_edge import capture as capture_mod
from epp_edge.app import Pipeline
from epp_edge.config import ConfigError, Settings
from epp_edge.control import DeviceControl, HealthReporter
from epp_edge.outbox import Outbox, Uploader

log = logging.getLogger("sim")
REAL_VIDEO = Path(__file__).resolve().parents[1] / "media" / "prueba1.mp4"
DEVICE = "raspberry-pi-01"


# --- utilidades ----------------------------------------------------------------------------------


def wait_until(cond, timeout=15.0, step=0.02):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(step)
    return False


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> str:
    """prueba1.mp4 si existe; si no, un mp4 sintético pequeño."""
    if REAL_VIDEO.exists():
        return str(REAL_VIDEO)
    path = tmp_path_factory.mktemp("video") / "sim.mp4"
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30, (320, 240))
    rng = np.random.default_rng(1)
    for _ in range(60):
        w.write(rng.integers(0, 255, (240, 320, 3), dtype=np.uint8))
    w.release()
    assert path.stat().st_size > 0
    return str(path)


def make_env(tmp_path, source, **over):
    env = {
        "DEVICE_ID": DEVICE,
        "ORCHESTRATOR_HOST": "orchestrator.example.invalid",
        "DEVICE_TOKEN": "x" * 40,
        "CAMERA_SOURCE": source,
        "DATA_DIR": str(tmp_path / "data"),
        "MODEL_PATH": str(tmp_path / "no-model"),
        "INFERENCE_FPS": "50",
        "MIN_SECONDS_TO_CONFIRM": "0.1",
        "MIN_FRAMES_TO_CONFIRM": "2",
        "SESSION_GAP_SECONDS": "0.3",
        "THUMB_WIDTH": "160",
    }
    env.update(over)
    return env


class Rig:
    """Pipeline real + uploader manual + dobles de GCP."""

    def __init__(self, tmp_path, source, **env_over):
        self.s = Settings.from_env(make_env(tmp_path, source, **env_over))
        self.clock = FakeClock(1_000_000.0)
        self.outbox = Outbox(self.s.data_dir / "outbox.db", self.s, clock=self.clock)
        self.bucket, self.events_db = FakeBucket(), FakeDB()
        self.uploader = Uploader(self.outbox, self.events_db, self.bucket, self.s, threading.Event(), log)
        self.control = DeviceControl(FakeDB(), self.s, log)
        self.health_db = RecordingDB()
        self.health = HealthReporter(self.health_db, self.s, self.outbox, log, edge_version="2.0.0")
        self.model, self.spy = FakeModel(), ConnectSpy()
        self.pipeline = Pipeline(
            self.s,
            self.control,
            self.health,
            self.outbox,
            log,
            model_factory=lambda _p: self.model,
            stream_connect=self.spy,
        )

    def start(self):
        self.pipeline.start()
        return self

    def stop(self, reason="shutdown"):
        self.pipeline.stop(reason)

    def pending(self):
        return self.outbox.stats()["pending"]

    def produce_event(self, n_expected, clase="latex_gloves", present_s=0.6):
        """Presenta la clase un rato y la quita: la sesión se cierra por `gap`."""
        self.model.present = clase
        time.sleep(present_s)
        self.model.present = None
        assert wait_until(lambda: self.pending() >= n_expected), f"no se generó el evento #{n_expected}"

    def upload_all(self):
        for _ in range(5):
            rows = self.outbox.due(50)
            if not rows:
                break
            for r in rows:
                self.uploader._safe(r)

    def docs(self):
        pre = "detecciones_confirmadas/"
        return {k[len(pre):]: v for k, v in self.events_db.store.items() if k.startswith(pre)}


# --- (1) un evento = 3 objetos en el lake + documento en Firestore -------------------------------


def test_cada_evento_deja_imagen_miniatura_sidecar_y_documento(tmp_path, video):
    rig = Rig(tmp_path, f"file:{video}", ALLOW_TEST_VIDEO="true").start()
    try:
        rig.produce_event(1, "latex_gloves")
        rig.produce_event(2, "hairnet")
    finally:
        rig.stop("stopped")
    assert rig.pending() == 2
    rig.upload_all()

    docs = rig.docs()
    assert len(docs) == 2 and rig.pending() == 0
    for eid, doc in docs.items():
        sidecar = [n for n in rig.bucket.names("raw/events/") if n.endswith(f"/{eid}.json")]
        images = [n for n in rig.bucket.names("raw/images/") if n.endswith(f"/{eid}.jpg")]
        thumbs = [n for n in rig.bucket.names("raw/thumbs/") if n.endswith(f"/{eid}.jpg")]
        assert len(sidecar) == len(images) == len(thumbs) == 1, eid
        assert f"device={DEVICE}/dt=" in images[0]
        # el documento enlaza exactamente esos objetos
        assert doc["is_simulated"] is True and doc["event_id"] == eid
        assert doc["capture"]["status"] == "ok"
        assert doc["capture"]["uri"] == f"gs://{rig.s.lake_bucket}/{images[0]}"
        assert doc["capture"]["thumb_uri"] == f"gs://{rig.s.lake_bucket}/{thumbs[0]}"
        # el sidecar es NDJSON de una línea con el mismo contrato
        raw = rig.bucket.objects[sidecar[0]]["data"]
        assert "\n" not in raw and json.loads(raw)["event_id"] == eid
        assert doc["capture"]["width"] > 0 and doc["detections_at_capture"]
    assert len(rig.bucket.objects) == 6
    assert {d["nombre_clase"] for d in docs.values()} == {"latex_gloves", "hairnet"}


# --- (2) sin lease: cero conexiones del streamer -------------------------------------------------


def test_sin_lease_no_hay_conexiones_de_video(tmp_path, video):
    rig = Rig(tmp_path, f"file:{video}", ALLOW_TEST_VIDEO="true").start()
    try:
        rig.model.present = "latex_gloves"  # hay actividad, pero nadie mira
        time.sleep(1.5)
        assert rig.model.calls > 5 and rig.pipeline.capture.ok
        assert rig.spy.connections == 0, "una detección no debe abrir el stream"
        assert not rig.control.should_stream()
    finally:
        rig.stop("stopped")
    assert rig.spy.connections == 0


def test_con_lease_vigente_si_transmite(tmp_path, video):
    """Control positivo: el espía funciona y el lease sí abre el stream."""
    rig = Rig(tmp_path, f"file:{video}", ALLOW_TEST_VIDEO="true").start()
    try:
        now = snap({}).read_time
        rig.control._apply(
            snap(
                {
                    "desired_state": "running",
                    "stream_requested": True,
                    "lease_renewed_at": now,
                    "lease_seconds": 90,
                },
                read_time=now,
            )
        )
        assert wait_until(lambda: rig.spy.connections >= 1 and rig.spy.sent, timeout=10)
        rig.control._apply(snap({"desired_state": "running", "stream_requested": False}))
        assert wait_until(lambda: not rig.health.state["streaming"], timeout=10)
    finally:
        rig.stop("stopped")


# --- (3) parada con sesión abierta ---------------------------------------------------------------


def _confirmed_open(rig):
    return any(s.confirmed for s in list(rig.pipeline.agg._sessions.values()))


def test_parada_con_sesion_abierta_guarda_evento_shutdown(tmp_path, video):
    rig = Rig(tmp_path, f"file:{video}", ALLOW_TEST_VIDEO="true").start()
    rig.model.present = "face_mask"
    assert wait_until(lambda: _confirmed_open(rig))
    assert rig.pending() == 0  # sigue abierta
    rig.stop("shutdown")  # equivale a Ctrl+C / SIGTERM

    assert rig.pending() == 1
    p = rig.outbox.get_payload(rig.outbox.due(1)[0].event_id)
    assert p["closed_reason"] == "shutdown" and p["nombre_clase"] == "face_mask"
    assert p["capture"]["status"] == "ok"
    rig.upload_all()
    (doc,) = rig.docs().values()
    assert doc["closed_reason"] == "shutdown"
    assert len(rig.bucket.names("raw/images/")) == len(rig.bucket.names("raw/thumbs/")) == 1


# --- (4) red caída durante 3 eventos ------------------------------------------------------------


def test_sin_red_se_acumula_y_al_volver_sube_sin_duplicar(tmp_path, video):
    rig = Rig(tmp_path, f"file:{video}", ALLOW_TEST_VIDEO="true").start()
    rig.bucket.fail = lambda name: True  # sin red: GCS no responde
    rig.events_db.fail_commit = True  # ni Firestore
    try:
        for i, clase in enumerate(("latex_gloves", "hairnet", "lab_coat"), start=1):
            rig.produce_event(i, clase)
            time.sleep(0.2)
    finally:
        rig.stop("stopped")
    assert rig.pending() == 3

    rig.upload_all()  # intento sin red: todo falla, nada se pierde ni se escribe
    assert rig.pending() == 3 and rig.events_db.store == {} and rig.bucket.objects == {}
    rig.health.write()
    assert rig.health_db.writes[-1][1]["outbox_pending"] == 3

    rig.bucket.fail = lambda name: False  # vuelve la red
    rig.events_db.fail_commit = False
    rig.clock.advance(600)  # vence el backoff
    rig.upload_all()
    assert rig.pending() == 0
    assert len(rig.docs()) == 3 and len(rig.bucket.objects) == 9
    assert len(rig.events_db.sets) == 3  # daily_stats contado una vez por evento

    # reprocesar el mismo evento (ack perdido) no duplica nada
    eid, doc = next(iter(rig.docs().items()))
    payload = {k: v for k, v in doc.items() if k != "created_at"}
    rig.outbox.put(json.loads(json.dumps(payload, default=str)), None)
    rig.upload_all()
    assert len(rig.docs()) == 3 and len(rig.events_db.sets) == 3 and rig.pending() == 0


# --- (5) sin cámara y sin ALLOW_TEST_VIDEO ------------------------------------------------------


def test_sin_camara_no_usa_video_de_prueba_ni_genera_eventos(tmp_path, monkeypatch):
    class _NoCam:
        def __init__(self, *a, **k):
            pass

        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setattr(capture_mod.cv2, "VideoCapture", _NoCam)  # sin webcam, ni siquiera en la PC
    rig = Rig(tmp_path, "auto").start()  # ALLOW_TEST_VIDEO=false por defecto
    try:
        rig.model.present = "latex_gloves"
        time.sleep(1.5)
        rig.pipeline.tick()
        assert rig.pipeline.capture.ok is False and rig.health.state["camera_ok"] is False
        assert rig.pending() == 0
    finally:
        rig.stop("stopped")
    assert rig.pending() == 0 and rig.pipeline.agg.frames_processed == 0


def test_file_sin_allow_test_video_es_error_de_configuracion(tmp_path):
    with pytest.raises(ConfigError, match="ALLOW_TEST_VIDEO"):
        Settings.from_env(make_env(tmp_path, "file:/x.mp4"))
