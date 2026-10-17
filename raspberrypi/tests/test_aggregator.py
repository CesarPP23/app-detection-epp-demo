from datetime import UTC, datetime, timedelta

from epp_edge.aggregator import DetectionAggregator
from epp_edge.models import Detection, Frame, Inference

T0 = datetime(2026, 10, 10, 14, 0, 0, tzinfo=UTC)
IMG = object()  # el agregador no toca la imagen


def det(name="latex_gloves", conf=0.8, cid=3):
    return Detection(cid, name, conf, (0.1, 0.1, 0.5, 0.5))


def tick(agg, t, dets):
    f = Frame(IMG, t, T0 + timedelta(seconds=t), int(t * 100))
    inf = Inference(f.seq, t, f.ts_wall, dets, 10.0)
    return agg.update(inf, f), f


def make(**kw):
    kw.setdefault("min_seconds_to_confirm", 1.0)
    kw.setdefault("min_frames_to_confirm", 3)
    kw.setdefault("session_gap_seconds", 4.0)
    return DetectionAggregator("raspberry-pi-01", model_version="abc123", camera_source="file:x", **kw)


def test_parpadeo_descartado():
    agg = make()
    tick(agg, 0.0, [det()])
    (closed, active), _ = tick(agg, 10.0, [])
    assert closed == [] and agg.noise_discarded_count == 1 and not active


def test_sesion_sostenida_end_ts_es_ultima_deteccion():
    agg = make()
    for i in range(6):
        tick(agg, i * 0.5, [det()])  # 0..2.5 s
    (closed, _), _ = tick(agg, 10.0, [])
    assert len(closed) == 1
    p = closed[0].payload
    assert p["end_ts"] == "2026-10-10T14:00:02.500Z" and p["start_ts"] == "2026-10-10T14:00:00.000Z"
    assert p["duration_seconds"] == 2.5 and p["closed_reason"] == "gap"
    assert p["frame_count"] == 6 and p["schema_version"] == 2 and p["is_confirmed"] is True
    assert p["edge"]["model_version"] == "abc123" and p["is_simulated"] is False
    assert "capture" not in p


def test_oclusion_menor_al_gap_mantiene_event_id():
    agg = make()
    for t in (0.0, 0.5, 1.0, 1.5):
        tick(agg, t, [det()])
    tick(agg, 3.5, [])  # 2 s sin verse (< gap 4)
    for t in (4.0, 4.5):
        tick(agg, t, [det()])
    (closed, _), _ = tick(agg, 20.0, [])
    assert len(closed) == 1 and closed[0].payload["frame_count"] == 6


def test_oclusion_mayor_al_gap_separa_eventos():
    agg = make()
    for t in (0.0, 0.5, 1.0, 1.5):
        tick(agg, t, [det()])
    (closed, _), _ = tick(agg, 7.0, [])
    assert len(closed) == 1
    for t in (7.5, 8.0, 8.5, 9.0):
        tick(agg, t, [det()])
    (closed2, _), _ = tick(agg, 20.0, [])
    assert closed2[0].payload["event_id"] != closed[0].payload["event_id"]


def test_flush_all_cierra_confirmadas_y_descarta_ruido():
    agg = make()
    for t in (0.0, 0.5, 1.0, 1.5):
        tick(agg, t, [det("latex_gloves")])
    tick(agg, 1.5, [det("hairnet", cid=0)])  # una sola inferencia: ruido
    out = agg.flush_all("shutdown")
    assert [e.payload["nombre_clase"] for e in out] == ["latex_gloves"]
    assert out[0].payload["closed_reason"] == "shutdown"
    assert agg.flush_all("shutdown") == []


def test_dos_cajas_de_la_misma_clase_suman_un_frame():
    agg = make()
    tick(agg, 0.0, [det(conf=0.6), det(conf=0.9)])
    sess = agg._sessions["latex_gloves"]
    assert sess.frame_count == 1 and sess.max_confidence == 0.9


def test_min_frames_to_confirm():
    agg = make(min_frames_to_confirm=5)
    # dura 2 s pero solo 3 inferencias: no confirma
    for t in (0.0, 1.0, 2.0):
        tick(agg, t, [det()])
    (closed, active), _ = tick(agg, 20.0, [])
    assert closed == [] and agg.noise_discarded_count == 1 and not active


def test_best_frame_es_el_de_mayor_confianza():
    agg = make()
    frames = {}
    for t, c in ((0.0, 0.6), (0.5, 0.95), (1.0, 0.7), (1.5, 0.7)):
        _, f = tick(agg, t, [det(conf=c), det("hairnet", 0.77, 0)])
        frames[c] = f
    (closed, _), _ = tick(agg, 20.0, [])
    ev = closed[0]
    assert ev.best_frame is frames[0.95]
    assert {d.nombre_clase for d in ev.best_detections} == {"latex_gloves", "hairnet"}
    assert len(ev.payload["detections_at_capture"]) == 2
    assert ev.payload["confianza_max"] == 0.95
