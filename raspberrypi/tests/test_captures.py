import hashlib
import json

import cv2
import numpy as np
import pytest
from _fakes import make_closed_event, make_settings
from epp_edge import captures
from epp_edge.captures import build_capture


def test_captura_ok_escribe_tres_archivos_sin_tmp(tmp_path):
    s = make_settings(tmp_path)
    ev = make_closed_event()
    cf = build_capture(ev, s)

    assert cf.status == "ok" and cf.pending_dir == tmp_path / "pending" / ev.payload["event_id"]
    names = sorted(f.name for f in cf.pending_dir.iterdir())
    assert names == ["event.json", "original.jpg", "thumb.jpg"]  # ningún .tmp

    cap = cf.payload["capture"]
    original = (cf.pending_dir / "original.jpg").read_bytes()
    thumb = (cf.pending_dir / "thumb.jpg").read_bytes()
    assert cap["sha256"] == hashlib.sha256(original).hexdigest() and cap["bytes"] == len(original)
    assert cap["thumb_sha256"] == hashlib.sha256(thumb).hexdigest()
    assert (cap["width"], cap["height"]) == (640, 360) and cap["frame_seq"] == 81234
    assert cap["confidence_at_capture"] == 0.9312
    eid = ev.payload["event_id"]
    assert cap["uri"] == f"gs://test-lake/raw/images/device=raspberry-pi-01/dt=2026-10-10/{eid}.jpg"
    assert cap["thumb_uri"].startswith("gs://test-lake/raw/thumbs/")
    assert cap["captured_at"] == "2026-10-10T14:03:15.870Z"

    t = cv2.imdecode(np.frombuffer(thumb, np.uint8), cv2.IMREAD_COLOR)
    assert t.shape[1] == s.thumb_width
    # el original NO lleva cajas: es el dato para reentrenar
    o = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_COLOR)
    assert o.shape[:2] == (360, 640)
    assert len(cf.payload["detections_at_capture"]) == 2
    # event.json es el mismo contrato, en una sola línea
    side = (cf.pending_dir / "event.json").read_text()
    assert "\n" not in side and json.loads(side)["capture"]["status"] == "ok"


def test_no_muta_el_payload_original(tmp_path):
    ev = make_closed_event()
    build_capture(ev, make_settings(tmp_path))
    assert "capture" not in ev.payload


def test_sin_frame_el_evento_se_registra_como_missing(tmp_path):
    ev = make_closed_event(with_frame=False)
    cf = build_capture(ev, make_settings(tmp_path))
    assert cf.status == "missing" and cf.pending_dir is None
    assert cf.payload["capture"]["status"] == "missing" and cf.payload["capture"]["uri"] is None
    assert cf.payload["event_id"] == ev.payload["event_id"]  # el evento NO se descarta
    assert not (tmp_path / "pending").exists()


def test_fallo_de_codificacion_no_descarta_el_evento(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("encoder roto")

    monkeypatch.setattr(captures, "_encode", boom)
    cf = build_capture(make_closed_event(), make_settings(tmp_path))
    assert cf.status == "missing" and "encoder roto" in cf.payload["capture"]["reason"]
    assert cf.pending_dir is None


@pytest.mark.parametrize("fail_on", ["original.jpg", "thumb.jpg", "event.json"])
def test_escritura_a_medias_no_deja_directorio_pendiente(tmp_path, monkeypatch, fail_on):
    real = captures._atomic_write

    def flaky(path, data):
        if path.name == fail_on:
            raise OSError("disco lleno")
        real(path, data)

    monkeypatch.setattr(captures, "_atomic_write", flaky)
    ev = make_closed_event()
    cf = build_capture(ev, make_settings(tmp_path))
    assert cf.status == "missing" and cf.payload["event_id"] == ev.payload["event_id"]
    assert not (tmp_path / "pending" / ev.payload["event_id"]).exists()
