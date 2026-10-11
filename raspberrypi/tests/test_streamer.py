import logging
import struct
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np

from epp_edge.models import Frame
from epp_edge.slots import LatestSlot
from epp_edge.streamer import StreamWorker, pack_frame

from _fakes import make_settings

LOG = logging.getLogger("test")


class FakeWS:
    def __init__(self):
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def send(self, data):
        self.sent.append(data)


def worker(tmp_path, should_stream, connect):
    frames, results = LatestSlot(), LatestSlot()
    ts = datetime(2026, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    frames.put(Frame(np.zeros((360, 640, 3), np.uint8), time.monotonic(), ts, 1))
    control = SimpleNamespace(should_stream=should_stream)
    health = SimpleNamespace(set_streaming=lambda on: None)
    stop = threading.Event()
    w = StreamWorker(make_settings(tmp_path), control, health, frames, results, stop, LOG, connect=connect)
    return w, stop


def test_pack_frame_sigue_el_protocolo_1_2():
    msg = pack_frame(1_760_000_000_123, b"\xff\xd8abc")
    assert struct.unpack(">Q", msg[:8])[0] == 1_760_000_000_123 and msg[8:10] == b"\xff\xd8"


def test_sin_lease_no_hay_ninguna_conexion(tmp_path):
    calls = []
    w, stop = worker(tmp_path, lambda: False, lambda *a, **k: calls.append(1))
    w.start()
    time.sleep(0.6)
    stop.set()
    w.join(2)
    assert calls == []


def test_con_lease_envia_frames_con_cabecera_y_jpeg(tmp_path):
    ws, state = FakeWS(), {"on": True}
    w, stop = worker(tmp_path, lambda: state["on"], lambda *a, **k: ws)
    w.start()
    deadline = time.monotonic() + 3
    while not ws.sent and time.monotonic() < deadline:
        time.sleep(0.05)
    state["on"] = False  # el último espectador se fue
    stop.set()
    w.join(3)
    assert ws.sent
    msg = ws.sent[0]
    assert struct.unpack(">Q", msg[:8])[0] == int(datetime(2026, 10, 10, 14, tzinfo=timezone.utc).timestamp() * 1000)
    assert msg[8:10] == b"\xff\xd8"
