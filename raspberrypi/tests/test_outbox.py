import json
import sqlite3
import uuid

from _fakes import FakeClock, make_payload, make_pending_dir, make_settings
from epp_edge.outbox import Outbox, lake_prefix, local_day


def open_box(tmp_path, clock=None, **over):
    return Outbox(tmp_path / "outbox.db", make_settings(tmp_path, **over), clock=clock or FakeClock())


def test_put_es_idempotente(tmp_path):
    box = open_box(tmp_path)
    p = make_payload()
    assert box.put(p, None) is True
    assert box.put(p, None) is False
    assert box.stats()["pending"] == 1


def test_persiste_tras_reabrir(tmp_path):
    p = make_payload()
    box = open_box(tmp_path)
    box.put(p, None)
    box.close()
    box2 = open_box(tmp_path)
    rows = box2.due(10)
    assert [r.event_id for r in rows] == [p["event_id"]]
    assert json.loads(rows[0].payload)["nombre_clase"] == "latex_gloves"


def test_backoff_exponencial_y_tope(tmp_path):
    clock = FakeClock()
    box = open_box(tmp_path, clock=clock)
    p = make_payload()
    box.put(p, None)
    box.nack(p["event_id"], "boom")  # attempts=1 -> 2 s
    assert box.due(10) == []
    clock.advance(2.1)
    assert len(box.due(10)) == 1
    for _ in range(12):
        box.nack(p["event_id"], "boom")
    clock.advance(299)
    assert box.due(10) == []  # tope de 300 s
    clock.advance(2)
    assert len(box.due(10)) == 1


def test_stage_se_retoma_tras_reinicio(tmp_path):
    p = make_payload()
    box = open_box(tmp_path)
    box.put(p, None)
    box.advance(p["event_id"], "sidecar")
    box.close()
    assert open_box(tmp_path).due(10)[0].stage == "sidecar"


def test_ack_borra_fila_y_directorio(tmp_path):
    p = make_payload()
    d = make_pending_dir(tmp_path, p["event_id"])
    box = open_box(tmp_path)
    box.put(p, d)
    box.ack(p["event_id"])
    assert box.stats()["pending"] == 0
    assert not d.exists()


def test_advance_libera_las_imagenes_locales(tmp_path):
    p = make_payload()
    d = make_pending_dir(tmp_path, p["event_id"])
    box = open_box(tmp_path)
    box.put(p, d)
    box.advance(p["event_id"], "sidecar")
    assert not d.exists()
    assert box.due(10)[0].pending_dir is None


def test_migra_offline_detections(tmp_path):
    db = tmp_path / "outbox.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE offline_detections (id INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    legacy = {
        "nombre_clase": "hairnet",
        "clase_id": 0,
        "confianza_promedio": 0.7,
        "confianza_max": 0.8,
        "frame_count": 9,
        "start_ts": "2026-10-09T10:00:00+00:00",
        "end_ts": "2026-10-09T10:00:05+00:00",
    }
    con.execute("INSERT INTO offline_detections (payload) VALUES (?)", (json.dumps(legacy),))
    con.commit()
    con.close()

    box = open_box(tmp_path)
    rows = box.due(10)
    assert len(rows) == 1 and rows[0].stage == "firestore"
    p = json.loads(rows[0].payload)
    assert uuid.UUID(p["event_id"]).version == 7
    assert p["capture"]["status"] == "missing_legacy"
    assert p["duration_seconds"] == 5.0
    # la tabla vieja ya no existe
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE name='offline_detections'").fetchone() is None


def test_tope_de_bytes_borra_capturas_viejas_no_eventos(tmp_path):
    clock = FakeClock()
    box = open_box(tmp_path, clock=clock, outbox_max_bytes=2500)
    ids = []
    for _ in range(3):
        p = make_payload()
        ids.append(p["event_id"])
        box.put(p, make_pending_dir(tmp_path, p["event_id"], size=1000))
        clock.advance(1)
    # 3 capturas de ~1 KB > 2.5 KB: se descarta la MÁS VIEJA
    assert not (tmp_path / "pending" / ids[0]).exists()
    assert (tmp_path / "pending" / ids[2]).exists()
    assert box.stats()["pending"] == 3  # los eventos se conservan
    first = box.get_payload(ids[0])
    assert first["capture"]["status"] == "dropped_disk_full"
    assert first["capture"]["uri"] is None
    assert [r for r in box.due(10) if r.event_id == ids[0]][0].stage == "sidecar"


def test_limpia_capturas_huerfanas_al_abrir(tmp_path):
    box = open_box(tmp_path)
    kept = make_payload()
    d = make_pending_dir(tmp_path, kept["event_id"])
    box.put(kept, d)
    box.close()
    orphan = make_pending_dir(tmp_path, "huerfano")  # build_capture terminó pero no hubo put
    (d / "x.tmp").write_text("a medias")
    open_box(tmp_path)
    assert not orphan.exists()
    assert d.exists() and not (d / "x.tmp").exists()


def test_prefijo_del_lake_usa_fecha_local_de_lima():
    # 02:30Z del 11/oct = 21:30 del 10/oct en Lima (UTC-5)
    p = make_payload(end_ts="2026-10-11T02:30:00.000Z")
    assert local_day(p) == "2026-10-10"
    assert lake_prefix(p) == "device=raspberry-pi-01/dt=2026-10-10"
