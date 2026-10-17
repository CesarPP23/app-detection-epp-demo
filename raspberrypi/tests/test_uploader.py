import json
import logging
import threading

from _fakes import FakeBucket, FakeClock, FakeDB, make_payload, make_pending_dir, make_settings
from epp_edge.outbox import Outbox, Uploader


def build(tmp_path):
    s = make_settings(tmp_path)
    clock = FakeClock()
    box = Outbox(tmp_path / "outbox.db", s, clock=clock)
    bucket, db = FakeBucket(), FakeDB()
    up = Uploader(box, db, bucket, s, threading.Event(), logging.getLogger("test"))
    return s, clock, box, bucket, db, up


def enqueue(tmp_path, box, **over):
    p = make_payload(**over)
    box.put(p, make_pending_dir(tmp_path, p["event_id"]))
    return p


def run_once(up, box):
    for row in box.due(10):
        up._safe(row)


def test_subida_completa_imagenes_sidecar_y_firestore(tmp_path):
    s, clock, box, bucket, db, up = build(tmp_path)
    p = enqueue(tmp_path, box)
    run_once(up, box)

    eid, pre = p["event_id"], "device=raspberry-pi-01/dt=2026-10-10"
    assert sorted(bucket.objects) == [
        f"raw/events/{pre}/{eid}.json",
        f"raw/images/{pre}/{eid}.jpg",
        f"raw/thumbs/{pre}/{eid}.jpg",
    ]
    meta = bucket.objects[f"raw/images/{pre}/{eid}.jpg"]["metadata"]
    assert meta["event_id"] == eid and meta["kind"] == "original" and meta["sha256"] == "aa"
    assert bucket.objects[f"raw/thumbs/{pre}/{eid}.jpg"]["metadata"]["sha256"] == "bb"
    sidecar = bucket.objects[f"raw/events/{pre}/{eid}.json"]["data"]
    assert "\n" not in sidecar and json.loads(sidecar)["event_id"] == eid  # NDJSON de 1 línea
    assert all(o["if_generation_match"] == 0 for o in bucket.objects.values())

    doc = db.store[f"detecciones_confirmadas/{eid}"]
    assert doc["is_simulated"] is True and doc["end_ts"].year == 2026 and "created_at" in doc
    path, data, merge = db.sets[0]
    assert path == "daily_stats/raspberry-pi-01_2026-10-10" and merge is True
    assert data["date"] == "2026-10-10" and "events" in data
    assert box.stats()["pending"] == 0  # ack
    assert not (tmp_path / "pending" / eid).exists()


def test_reintento_idempotente_no_duplica(tmp_path):
    """Los objetos y el documento ya existen (la Pi murió antes del ack): PreconditionFailed y
    Conflict se tratan como éxito y no se vuelve a contar en daily_stats."""
    s, clock, box, bucket, db, up = build(tmp_path)
    p = enqueue(tmp_path, box)
    run_once(up, box)
    assert len(db.sets) == 1
    # el mismo evento reaparece en el outbox (p. ej. ack perdido)
    box.put(p, make_pending_dir(tmp_path, p["event_id"]))
    clock.advance(1)
    run_once(up, box)
    assert box.stats()["pending"] == 0
    assert len(db.sets) == 1  # el batch falló entero con Conflict: stats NO se incrementó dos veces
    assert len([k for k in db.store if k.startswith("detecciones_confirmadas/")]) == 1


def test_si_falla_el_sidecar_firestore_no_se_escribe(tmp_path):
    s, clock, box, bucket, db, up = build(tmp_path)
    p = enqueue(tmp_path, box)
    bucket.fail = lambda name: name.startswith("raw/events/")
    run_once(up, box)

    assert db.commits == 0 and db.store == {}
    row = box.due(10)  # en backoff: aún no vence
    assert row == []
    clock.advance(5)
    row = box.due(10)[0]
    assert row.stage == "sidecar" and row.attempts == 1  # las imágenes ya avanzaron

    # recupera la red: continúa desde el sidecar SIN resubir las imágenes
    bucket.fail = lambda name: False
    image_uploads = len([c for c in bucket.calls if c[0] == "file"])
    run_once(up, box)
    assert len([c for c in bucket.calls if c[0] == "file"]) == image_uploads
    assert f"detecciones_confirmadas/{p['event_id']}" in db.store
    assert box.stats()["pending"] == 0


def test_si_fallan_las_imagenes_no_se_escribe_nada_mas(tmp_path):
    s, clock, box, bucket, db, up = build(tmp_path)
    enqueue(tmp_path, box)
    bucket.fail = lambda name: name.startswith("raw/images/")
    run_once(up, box)
    assert bucket.names("raw/events/") == [] and db.commits == 0
    assert box.due(10) == [] and box.stats()["pending"] == 1


def test_fallo_de_firestore_reintenta_sin_perder_el_evento(tmp_path):
    s, clock, box, bucket, db, up = build(tmp_path)
    p = enqueue(tmp_path, box)
    db.fail_commit = True
    run_once(up, box)
    assert box.stats()["pending"] == 1
    db.fail_commit = False
    clock.advance(5)
    run_once(up, box)
    assert f"detecciones_confirmadas/{p['event_id']}" in db.store
    assert box.stats()["pending"] == 0


def test_evento_sin_imagenes_locales_se_registra_igual(tmp_path):
    """Sin pending_dir (p. ej. dropped_disk_full o missing) no hay imágenes, pero sí sidecar y Firestore."""
    s, clock, box, bucket, db, up = build(tmp_path)
    p = make_payload(capture={"status": "missing", "uri": None, "thumb_uri": None})
    box.put(p, None)
    run_once(up, box)
    assert bucket.names("raw/images/") == [] and len(bucket.names("raw/events/")) == 1
    assert db.store[f"detecciones_confirmadas/{p['event_id']}"]["capture"]["status"] == "missing"


def test_archivos_locales_borrados_no_descartan_el_evento(tmp_path):
    s, clock, box, bucket, db, up = build(tmp_path)
    p = enqueue(tmp_path, box)
    (tmp_path / "pending" / p["event_id"] / "original.jpg").unlink()
    run_once(up, box)
    doc = db.store[f"detecciones_confirmadas/{p['event_id']}"]
    assert doc["capture"]["status"] == "missing" and doc["capture"]["uri"] is None
    assert bucket.names("raw/images/") == []


def test_drain_procesa_lo_pendiente(tmp_path):
    s, clock, box, bucket, db, up = build(tmp_path)
    enqueue(tmp_path, box)
    enqueue(tmp_path, box)
    up.drain(timeout=5)  # el hilo nunca arrancó: procesa en línea
    assert box.stats()["pending"] == 0 and len(db.store) == 2
