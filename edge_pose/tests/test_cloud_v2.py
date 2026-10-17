"""Pruebas de la integración con la nube 2.0.0 (sin Google Cloud: lago y Firestore emulados en carpetas)."""
import json
import sqlite3
import struct
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

from epp_edge import cloud_v2 as cv
from epp_edge.storage import Storage

EDGE = {"edge_version": cv.EDGE_VERSION, "model_version": "abc123def456", "camera_source": "usb"}


def _episode(storage: Storage, tmp: Path, ep_id="pi-01-1", cumple=1, ts="2026-10-10T19:00:00.000+00:00"):
    img = np.full((480, 640, 3), 120, np.uint8)
    raw, ann = tmp / f"{ep_id}_original.jpg", tmp / f"{ep_id}.jpg"
    cv2.imwrite(str(raw), img); cv2.imwrite(str(ann), img)
    storage.open_episode(ep_id, "pi-01", "Envasado", ts)
    end = (datetime.fromisoformat(ts) + timedelta(seconds=12)).isoformat()
    storage.close_episode(ep_id, {
        "ts_fin": end, "duracion_s": 12.0, "frames": 60, "personas_max": 1, "cumple": cumple,
        "ratio_gorra": 1.0, "ratio_guante": 0.8, "ratio_mascarilla": 0.9,
        "faltantes": [] if cumple else ["Guante"], "mal_colocados": [], "no_evaluables": [], "n_personas": 1,
        "personas": [{"id": 1, "cumple": bool(cumple)}], "snapshot": str(ann), "snapshot_raw": str(raw),
        "best_dets": [{"clase_id": 0, "nombre_clase": "Gorra", "confianza": 0.91, "bbox_xyxy_norm": [0.4, 0.1, 0.5, 0.2],
                       "img_w": 640, "img_h": 480}],
        "best_ts": end, "conf_mean": 0.85, "conf_max": 0.91, "closed_reason": "gap"})
    return ep_id


@pytest.fixture
def env(tmp_path):
    db = tmp_path / "edge.sqlite"
    st = Storage(db)
    ob = cv.OutboxV2(db)
    bucket = cv.LocalBucket(tmp_path / "gcs")
    fs = cv.LocalFirestore(tmp_path / "fs")
    up = cv.Uploader(ob, bucket, fs, edge=EDGE, is_simulated=False, tz="America/Lima",
                     bucket_name="lake", thumbs_dir=tmp_path / "thumbs")
    yield st, ob, up, tmp_path
    ob.close(); st.close()


def test_uuid7_version_y_orden():
    a, b = cv.uuid7(1_700_000_000_000), cv.uuid7(1_700_000_000_005)
    assert uuid.UUID(a).version == 7 and a < b
    assert len({cv.uuid7() for _ in range(2000)}) == 2000


def test_lake_path_usa_fecha_local_de_lima():
    # 02:00 UTC del 11 de octubre = 21:00 del 10 de octubre en Lima
    p = cv.lake_path("images", "pi-01", "2026-10-11T02:00:00Z", "E1")
    assert p == "raw/images/device=pi-01/dt=2026-10-10/E1.jpg"
    assert cv.lake_path("events", "pi-01", "2026-10-11T02:00:00Z", "E1").endswith("E1.json")


def test_contrato_compatible_con_la_api(env):
    st, ob, up, tmp = env
    ep_id = _episode(st, tmp, cumple=0)
    ev = cv.build_event(st.get_episode(ep_id), cv.uuid7(), edge=EDGE, is_simulated=False)
    for k in ("schema_version", "event_id", "device_id", "start_ts", "end_ts", "nombre_clase", "confianza_promedio",
              "confianza_max", "frame_count", "duration_seconds", "closed_reason", "is_simulated", "capture",
              "detections_at_capture", "edge"):
        assert k in ev
    assert ev["nombre_clase"] == "episodio_no_conforme" and ev["clase_id"] == 101
    assert ev["faltantes"] == ["Guante"] and ev["cumple"] is False
    box = ev["detections_at_capture"][0]
    assert set(box) == {"clase_id", "nombre_clase", "confianza", "bbox_xyxy_norm"}   # DetectionBox de la API
    assert ev["end_ts"].endswith("Z") and ev["capture"]["status"] == "ok" and ev["capture"]["width"] == 640


def test_publicacion_completa_e_idempotente(env):
    st, ob, up, tmp = env
    ep_id = _episode(st, tmp)
    assert up.tick() == 1
    row = sqlite3.connect(tmp / "edge.sqlite").execute("SELECT event_id, stage FROM outbox_v2").fetchone()
    eid, stage = row
    assert stage == "done" and st.pending_count() == 0
    day = "dt=2026-10-10"
    for kind, ext in (("images", "jpg"), ("thumbs", "jpg"), ("events", "json")):
        assert (tmp / "gcs" / f"raw/{kind}/device=pi-01/{day}/{eid}.{ext}").exists()
    doc = json.loads((tmp / "fs" / cv.EVENTS_COLLECTION / f"{eid}.json").read_text(encoding="utf-8"))
    assert doc["nombre_clase"] == "episodio_conforme" and doc["capture"]["uri"].startswith("gs://lake/raw/images/")
    stats = json.loads((tmp / "fs" / cv.STATS_COLLECTION / "pi-01_2026-10-10.json").read_text(encoding="utf-8"))
    assert stats["events"] == 1 and stats["by_class"]["episodio_conforme"] == 1
    # reintento forzado de todas las etapas: no duplica ni vuelve a contar
    sqlite3.connect(tmp / "edge.sqlite", isolation_level=None).execute("UPDATE outbox_v2 SET stage='images'")
    assert up.tick() == 1
    stats = json.loads((tmp / "fs" / cv.STATS_COLLECTION / "pi-01_2026-10-10.json").read_text(encoding="utf-8"))
    assert stats["events"] == 1


def test_fallo_de_red_reintenta_con_espera(env, monkeypatch):
    st, ob, up, tmp = env
    _episode(st, tmp)

    def falla(*a, **k):
        raise ConnectionError("sin red")
    monkeypatch.setattr(up, "_firestore", falla)
    assert up.tick() == 0
    row = sqlite3.connect(tmp / "edge.sqlite").execute("SELECT stage, attempts, next_ts FROM outbox_v2").fetchone()
    assert row[0] == "firestore" and row[1] == 1 and row[2] > 0      # imágenes y sidecar ya subidos
    assert st.pending_count() == 1                                   # el episodio sigue en la cola local


def test_lease_y_pausa():
    clock = [1000.0]
    fs = cv.LocalFirestore(Path("/nonexistent"))
    ctl = cv.DeviceControl(fs, "pi-01", clock=lambda: clock[0])
    now = datetime.now(timezone.utc)
    ctl.apply({"desired_state": "running", "stream_requested": True, "lease_renewed_at": now, "lease_seconds": 90}, now)
    assert ctl.should_run() and ctl.should_stream()
    clock[0] += 91
    assert not ctl.should_stream()                       # el lease vence solo, sin aviso del servidor
    ctl.apply({"desired_state": "stopped"}, now)
    assert not ctl.should_run() and not ctl.should_stream()
    ctl.apply({"desired_state": "basura"}, now)
    assert ctl.should_run()                              # valor inválido -> estado por defecto


def test_protocolo_del_frame_de_video():
    msg = cv.pack_frame(1_760_000_000_123, b"\xff\xd8jpeg")
    assert struct.unpack(">Q", msg[:8])[0] == 1_760_000_000_123 and msg[8:] == b"\xff\xd8jpeg"


def test_vaciado_al_apagar_ignora_la_parada(env):
    st, ob, up, tmp = env
    _episode(st, tmp)
    up.stop()
    assert up.tick() == 0                 # con la parada activa, el ciclo normal no procesa
    assert up.tick(drain=True) == 1       # el vaciado final sí publica lo pendiente
    assert st.pending_count() == 0
