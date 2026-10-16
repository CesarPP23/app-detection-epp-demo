"""Pruebas de las reglas geométricas de colocación (sin modelos ni cámara).

    cd edge && python -m pytest tests -q
"""
import numpy as np
import pytest

from epp_edge.detectors import Detection
from epp_edge.placement import (L_ELB, L_EYE, L_SHO, L_WRI, NOSE, R_ELB, R_EYE, R_SHO, R_WRI, Person, PersonStats,
                                evaluate_frame)


def make_person(track_id=1, nose=(300, 200), s=40.0, wrists=((240, 330), (360, 330)), hide=()):
    """Persona frontal sintética: ojos a 0.25·s sobre la nariz, hombros a 1.2·s bajo la nariz."""
    kp = np.zeros((17, 2)); kc = np.zeros(17)
    nx, ny = nose
    pts = {NOSE: (nx, ny), L_EYE: (nx + 0.19 * s, ny - 0.25 * s), R_EYE: (nx - 0.19 * s, ny - 0.25 * s),
           L_SHO: (nx + 1.1 * s, ny + 1.2 * s), R_SHO: (nx - 1.1 * s, ny + 1.2 * s),
           L_ELB: (nx + 1.4 * s, ny + 2.6 * s), R_ELB: (nx - 1.4 * s, ny + 2.6 * s),
           L_WRI: wrists[1], R_WRI: wrists[0]}
    for i, (x, y) in pts.items():
        kp[i] = (x, y); kc[i] = 0.0 if i in hide else 0.95
    box = (int(nx - 1.5 * s), int(ny - 1.2 * s), int(nx + 1.5 * s), int(ny + 6 * s))
    return Person(track_id, box, kp, kc)


def det(label, x1, y1, x2, y2, conf=0.9):
    return Detection(0, label, conf, int(x1), int(y1), int(x2), int(y2))


def gorra_ok(nose=(300, 200), s=40):
    nx, ny = nose; ey = ny - 0.25 * s
    return det("Gorra", nx - 0.7 * s, ey - 1.1 * s, nx + 0.7 * s, ey + 0.1 * s)


def masc_ok(nose=(300, 200), s=40):
    nx, ny = nose; d = 0.25 * s
    return det("Mascarilla", nx - 0.5 * s, ny - 0.4 * d, nx + 0.5 * s, ny + 1.6 * d)


def guante(w):
    return det("Guante", w[0] - 15, w[1] - 12, w[0] + 15, w[1] + 22)


def test_all_ok():
    p = make_person()
    evaluate_frame([p], [gorra_ok(), masc_ok(), guante((240, 330)), guante((360, 330))])
    assert p.status == {"Gorra": "ok", "Mascarilla": "ok", "Guante": "ok"}


def test_mask_under_nose():
    p = make_person()
    nx, ny = 300, 200
    low = det("Mascarilla", nx - 20, ny + 8, nx + 20, ny + 30)   # empieza por debajo de la nariz
    evaluate_frame([p], [gorra_ok(), low])
    assert p.status["Mascarilla"] == "mal" and p.reasons["Mascarilla"] == "bajo la nariz"


def test_mask_absent_and_gorra_back():
    p = make_person()
    back = det("Gorra", 270, 195, 330, 215)   # caja pequeña a la altura de los ojos: no cubre la coronilla
    evaluate_frame([p], [back])
    assert p.status["Mascarilla"] == "ausente"
    assert p.status["Gorra"] == "mal"


def test_glove_far_from_wrist_is_ignored():
    p = make_person()
    table_glove = det("Guante", 500, 420, 540, 460)   # guante sobre la mesa
    evaluate_frame([p], [gorra_ok(), masc_ok(), table_glove, guante((240, 330))])
    assert p.status["Guante"] == "mal" and "sin guante" in p.reasons["Guante"]


def test_not_evaluable_when_keypoints_hidden():
    p = make_person(hide=(L_WRI, R_WRI, NOSE, L_EYE, R_EYE))
    evaluate_frame([p], [])
    assert p.status == {"Gorra": "na", "Mascarilla": "na", "Guante": "na"}


def test_association_two_persons():
    a = make_person(1, nose=(200, 200), wrists=((150, 330), (250, 330)))
    b = make_person(2, nose=(500, 200), wrists=((450, 330), (550, 330)))
    dets = [gorra_ok((200, 200)), masc_ok((200, 200)), guante((150, 330)), guante((250, 330)), gorra_ok((500, 200))]
    evaluate_frame([a, b], dets)
    assert a.status == {"Gorra": "ok", "Mascarilla": "ok", "Guante": "ok"}
    assert b.status["Gorra"] == "ok" and b.status["Mascarilla"] == "ausente" and b.status["Guante"] == "ausente"


def test_person_stats_verdict():
    st = PersonStats(1)
    for i in range(10):
        p = make_person()
        dets = [gorra_ok(), guante((240, 330)), guante((360, 330))]
        if i < 4:
            dets.append(masc_ok())
        else:
            dets.append(det("Mascarilla", 280, 208, 320, 230))  # bajo la nariz
        evaluate_frame([p], dets); st.add(p)
    v = st.verdict(["Gorra", "Guante", "Mascarilla"], 0.6, 5)
    assert v["cumple"] == 0 and v["faltantes"] == [] and v["mal_colocados"] == ["Mascarilla: bajo la nariz"]
    assert st.ratio_ok("Mascarilla") == pytest.approx(0.4)
