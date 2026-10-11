"""Dobles de prueba para outbox, uploader, control y capturas (sin GCP, sin ultralytics)."""
import itertools
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
from google.api_core.exceptions import Conflict, PreconditionFailed

from epp_edge.models import ClosedEvent, Detection, Frame


def make_settings(tmp_path, **over):
    base = dict(
        device_id="raspberry-pi-01",
        data_dir=tmp_path,
        outbox_max_bytes=5_000_000_000,
        site_timezone="America/Lima",
        lake_bucket="test-lake",
        capture_jpeg_quality=90,
        thumb_width=320,
        thumb_jpeg_quality=75,
        control_safety_poll_seconds=300,
        default_desired_state="running",
        auto_stream_on_detection=False,
        heartbeat_seconds=300,
        healthcheck_url="",
        stream_fps=50.0,
        stream_width=320,
        stream_jpeg_quality=60,
        device_token="x" * 40,
        uplink_url="wss://example.invalid/ws/video/raspberry-pi-01",
    )
    base.update(over)
    return SimpleNamespace(**base)


def make_payload(event_id=None, end_ts="2026-10-10T14:03:20.388Z", clase="latex_gloves", **over):
    p = {
        "schema_version": 2,
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": "deteccion_confirmada",
        "device_id": "raspberry-pi-01",
        "clase_id": 3,
        "nombre_clase": clase,
        "confianza_promedio": 0.8,
        "confianza_max": 0.93,
        "frame_count": 30,
        "start_ts": "2026-10-10T14:03:11.204Z",
        "end_ts": end_ts,
        "duration_seconds": 9.18,
        "is_confirmed": True,
        "closed_reason": "gap",
        "is_simulated": True,
        "detections_at_capture": [],
        "edge": {"edge_version": "2.0.0", "model_version": "abc123", "camera_source": "file:x.mp4"},
        "capture": {
            "status": "ok",
            "uri": "gs://test-lake/raw/images/x.jpg",
            "thumb_uri": "gs://test-lake/raw/thumbs/x.jpg",
            "captured_at": "2026-10-10T14:03:15.870Z",
            "sha256": "aa",
            "thumb_sha256": "bb",
        },
    }
    p.update(over)
    return p


def make_pending_dir(tmp_path, event_id, size=1000):
    d = tmp_path / "pending" / event_id
    d.mkdir(parents=True)
    (d / "original.jpg").write_bytes(b"\xff\xd8" + b"o" * size)
    (d / "thumb.jpg").write_bytes(b"\xff\xd8" + b"t" * 10)
    (d / "event.json").write_text("{}")
    return d


def make_closed_event(with_frame=True, clase="latex_gloves", **over):
    p = make_payload(**over)
    p.pop("capture")
    p["nombre_clase"] = clase
    frame = None
    if with_frame:
        img = np.random.default_rng(0).integers(0, 255, (360, 640, 3), dtype=np.uint8)
        frame = Frame(img, 1.0, datetime(2026, 10, 10, 14, 3, 15, 870000, tzinfo=timezone.utc), 81234)
    dets = [
        Detection(3, clase, 0.9312, (0.41, 0.52, 0.55, 0.71)),
        Detection(0, "hairnet", 0.77, (0.38, 0.05, 0.52, 0.18)),
    ]
    return ClosedEvent(payload=p, best_frame=frame, best_detections=dets)


# --- GCS / Firestore falsos -----------------------------------------------------------------------


class FakeBlob:
    def __init__(self, bucket, name):
        self.bucket, self.name, self.metadata = bucket, name, None

    def _put(self, kind, **kw):
        self.bucket.calls.append((kind, self.name))
        if self.bucket.fail(self.name):
            raise RuntimeError(f"fallo simulado subiendo {self.name}")
        if kw.get("if_generation_match") == 0 and self.name in self.bucket.objects:
            raise PreconditionFailed("ya existe")
        self.bucket.objects[self.name] = {"metadata": self.metadata, "kind": kind, **kw}

    def upload_from_filename(self, path, **kw):
        with open(path, "rb"):  # FileNotFoundError si el archivo local desapareció
            pass
        self._put("file", path=path, **kw)

    def upload_from_string(self, data, **kw):
        self._put("string", data=data, **kw)


class FakeBucket:
    def __init__(self):
        self.objects, self.calls = {}, []
        self.fail = lambda name: False

    def blob(self, name):
        return FakeBlob(self, name)

    def names(self, prefix):
        return sorted(n for n in self.objects if n.startswith(prefix))


class FakeDocRef:
    def __init__(self, db, path):
        self.db, self.path = db, path


class FakeCollection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def document(self, doc_id):
        return FakeDocRef(self.db, f"{self.name}/{doc_id}")


class FakeBatch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def create(self, ref, data):
        self.ops.append(("create", ref.path, data))

    def set(self, ref, data, merge=False):
        self.ops.append(("set", ref.path, data, merge))

    def commit(self, timeout=None):
        self.db.commits += 1
        if self.db.fail_commit:
            raise RuntimeError("Firestore caído")
        for op in self.ops:
            if op[0] == "create" and op[1] in self.db.store:
                raise Conflict("ya existe")  # el batch es atómico: no se aplica nada
        for op in self.ops:
            if op[0] == "create":
                self.db.store[op[1]] = op[2]
            else:
                self.db.sets.append(op[1:])


class FakeDB:
    def __init__(self):
        self.store, self.sets, self.commits, self.fail_commit = {}, [], 0, False

    def collection(self, name):
        return FakeCollection(self, name)

    def batch(self):
        return FakeBatch(self)


class FakeClock:
    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


_ids = itertools.count()


def snap(data, read_time=None, exists=True):
    """DocumentSnapshot falso."""
    return SimpleNamespace(
        exists=exists, to_dict=lambda: dict(data) if data is not None else None,
        read_time=read_time or datetime(2026, 10, 10, 15, 0, 0, tzinfo=timezone.utc),
    )


def secs(base: datetime, delta: float) -> datetime:
    return base + timedelta(seconds=delta)
