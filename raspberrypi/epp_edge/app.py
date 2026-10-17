"""Cableado, señales y ciclo de vida del servicio (2.12)."""

import logging
import os
import signal
import threading
import time

from . import __version__
from .aggregator import DetectionAggregator
from .capture import CaptureThread, build_source
from .captures import build_capture
from .config import Settings
from .control import DeviceControl, HealthReporter
from .inference import InferenceWorker, load_yolo
from .logging_setup import setup_logging
from .models import ClosedEvent, Frame, Inference
from .outbox import Outbox, Uploader
from .slots import LatestSlot
from .streamer import StreamWorker
from .systemd import sd_notify

MODEL_LOAD_GRACE_SECONDS = 180.0  # = TimeoutStartSec: cargar NCNN en una Pi puede tardar


class Pipeline:
    """Captura + inferencia + streaming + agregador. Se crea al pasar a `running` y se destruye
    al pausar o apagar; al detenerse cierra las sesiones abiertas y las manda al outbox (D1)."""

    def __init__(
        self,
        s: Settings,
        control: DeviceControl,
        health: HealthReporter,
        outbox: Outbox,
        log: logging.Logger,
        source_factory=build_source,
        model_factory=load_yolo,
        stream_connect=None,
    ) -> None:  # las tres fábricas son inyectables para probar el pipeline sin hardware ni ultralytics
        self.s, self.control, self.health, self.outbox, self.log = s, control, health, outbox, log
        self.stop_event = threading.Event()
        self.frames: LatestSlot[Frame] = LatestSlot()
        self.results: LatestSlot[Inference] = LatestSlot()
        self.agg = DetectionAggregator(
            device_id=s.device_id,
            min_seconds_to_confirm=s.min_seconds_to_confirm,
            min_frames_to_confirm=s.min_frames_to_confirm,
            session_gap_seconds=s.session_gap_seconds,
            camera_source=s.camera_source,
            is_simulated=s.is_simulated,
            edge_version=__version__,
            logger=log,
        )
        self._agg_lock = threading.Lock()  # on_result (hilo de inferencia) vs tick/stop
        self.capture = CaptureThread(s, self.frames, self.stop_event, log, source_factory)
        self.inference = InferenceWorker(
            s, self.frames, self.results, self._on_result, self.stop_event, log, model_factory
        )
        self.streamer = StreamWorker(
            s, control, health, self.frames, self.results, self.stop_event, log, connect=stream_connect
        )
        self._started = time.monotonic()
        self._camera_down_since: float | None = None
        self._last_health = 0.0

    def start(self) -> "Pipeline":
        self.log.info("Pipeline iniciando (cámara=%s)", self.s.camera_source)
        self.health.update(reported_state="running", camera_source=self.s.camera_source)
        for t in (self.capture, self.inference, self.streamer):
            t.start()
        return self

    # -- resultados --

    def _on_result(self, inf: Inference, frame: Frame) -> None:
        with self._agg_lock:
            self.agg.camera_source = self.capture.source_name or self.s.camera_source
            self.agg.model_version = self.inference.model_version
            closed, active = self.agg.update(inf, frame)
        if active:
            self.control.bump_auto_stream(30)  # no hace nada si AUTO_STREAM_ON_DETECTION=false
        for ev in closed:
            self._emit(ev)
        self._report_health()

    def _emit(self, ev: ClosedEvent) -> None:
        """Captura + outbox. Un fallo aquí no puede perder silenciosamente el evento."""
        try:
            cf = build_capture(ev, self.s)
            self.outbox.put(cf.payload, cf.pending_dir)
        except Exception:  # noqa: BLE001
            self.log.exception("No se pudo encolar el evento %s", ev.payload.get("event_id"))

    def _report_health(self) -> None:
        now = time.monotonic()
        if now - self._last_health < 1.0:
            return
        self._last_health = now
        self.health.update(
            camera_ok=self.capture.ok,
            camera_source=self.capture.source_name or self.s.camera_source,
            inference_fps=round(self.inference.fps_measured, 2),
            inference_ms_p50=round(self.inference.latency.p50, 1),
            inference_ms_p95=round(self.inference.latency.p95, 1),
            model_version=self.inference.model_version,
        )

    # -- supervisión --

    def tick(self) -> None:
        """Una vez por segundo: refleja la cámara en la salud y cierra sesiones si se perdió."""
        self.health.update(camera_ok=self.capture.ok)
        if self.capture.ok:
            self._camera_down_since = None
            return
        now = time.monotonic()
        self._camera_down_since = self._camera_down_since or now
        if now - self._camera_down_since >= self.s.session_gap_seconds:
            with self._agg_lock:
                closed = self.agg.flush_all("camera_lost")
            for ev in closed:
                self._emit(ev)

    def healthy(self, max_silence: float = 30.0) -> bool:
        """False si la inferencia se colgó (el watchdog de systemd reinicia el servicio)."""
        if not self.inference.is_alive():
            return time.monotonic() - self._started < MODEL_LOAD_GRACE_SECONDS
        if not self.inference.ready.is_set():
            return time.monotonic() - self._started < MODEL_LOAD_GRACE_SECONDS
        return time.monotonic() - self.inference.last_progress <= max_silence

    def stop(self, reason: str) -> None:
        self.log.info("Pipeline deteniéndose (%s)", reason)
        self.stop_event.set()
        for t in (self.inference, self.capture, self.streamer):
            if t.is_alive():
                t.join(timeout=10)
        with self._agg_lock:
            closed = self.agg.flush_all(reason)
        for ev in closed:
            self._emit(ev)
        self.health.update(reported_state="stopped", streaming=False)


def main() -> None:
    s = Settings.from_env()
    log = setup_logging(s)
    log.info("EPP Edge %s iniciando (device=%s)", __version__, s.device_id)
    s.warn_if_credentials_too_open()
    if s.google_application_credentials.exists():
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(s.google_application_credentials))
    os.environ.setdefault("YOLO_CONFIG_DIR", str(s.data_dir / "ultralytics"))

    from google.cloud import firestore, storage  # import tardío

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    control_db = firestore.Client(project=s.gcp_project_id, database=s.control_database_id)
    events_db = firestore.Client(project=s.gcp_project_id, database=s.detections_database_id)
    bucket = storage.Client(project=s.gcp_project_id).bucket(s.lake_bucket)

    outbox = Outbox(s.data_dir / "outbox.db", s, log=logging.getLogger("outbox"))
    uploader = Uploader(outbox, events_db, bucket, s, stop, log)
    uploader.start()
    health = HealthReporter(control_db, s, outbox, log, edge_version=__version__)
    health.start()
    control = DeviceControl(control_db, s, log)
    control.start()

    pipeline: Pipeline | None = None
    sd_notify("READY=1")
    try:
        while not stop.is_set():
            if control.should_run() and pipeline is None:
                pipeline = Pipeline(s, control, health, outbox, log).start()
            elif not control.should_run() and pipeline is not None:
                pipeline.stop(reason="stopped")  # flush_all -> outbox
                pipeline = None
            if pipeline is not None:
                pipeline.tick()
            if pipeline is None or pipeline.healthy(max_silence=30):
                sd_notify("WATCHDOG=1")  # solo si la inferencia avanza
            stop.wait(1.0)
    finally:
        sd_notify("STOPPING=1")
        if pipeline is not None:
            pipeline.stop(reason="shutdown")
        uploader.drain(timeout=10)
        control.close()
        health.final("offline")
        log.info("EPP Edge detenido")
