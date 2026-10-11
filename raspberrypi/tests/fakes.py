"""Dobles para simular el pipeline completo en la PC: modelo, conexión de streaming y salud."""
import threading
from types import SimpleNamespace

from _fakes import FakeDB, FakeDocRef


class _Tensor(list):
    def tolist(self):
        return list(self)


class FakeModel:
    """Modelo determinista: mientras `present` tenga un nombre de clase, detecta esa clase con
    confianza fija en cada frame; con None no detecta nada. El test lo cambia en caliente."""

    names = {0: "hairnet", 1: "lab_coat", 2: "face_mask", 3: "latex_gloves"}

    def __init__(self, confidence=0.9):
        self.present: str | None = None
        self.confidence = confidence
        self.calls = 0

    def predict(self, image, **_):
        self.calls += 1
        cls = [k for k, v in self.names.items() if v == self.present]
        h, w = image.shape[:2]
        box = [w * 0.2, h * 0.2, w * 0.6, h * 0.7]
        boxes = SimpleNamespace(
            cls=_Tensor([float(c) for c in cls]),
            conf=_Tensor([self.confidence for _ in cls]),
            xyxy=_Tensor([box for _ in cls]),
        )
        return [SimpleNamespace(boxes=boxes, names=self.names)]


class FakeWS:
    def __init__(self, spy):
        self.spy = spy

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, data):
        self.spy.sent.append(len(data))


class ConnectSpy:
    """Reemplaza websockets.sync.client.connect y cuenta las conexiones abiertas a Cloud Run."""

    def __init__(self):
        self.connections = 0
        self.sent: list[int] = []
        self._lock = threading.Lock()

    def __call__(self, url, **kw):
        with self._lock:
            self.connections += 1
        return FakeWS(self)


class RecordingDocRef(FakeDocRef):
    def set(self, data, merge=False):
        self.db.writes.append((self.path, dict(data)))


class RecordingDB(FakeDB):
    """Firestore falso para `device_health` (HealthReporter.write usa document().set)."""

    def __init__(self):
        super().__init__()
        self.writes = []

    def collection(self, name):
        db = self

        class _C:
            def document(self, doc_id):
                return RecordingDocRef(db, f"{name}/{doc_id}")

        return _C()
