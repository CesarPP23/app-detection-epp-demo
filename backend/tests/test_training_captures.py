"""Captura de fotos para reentrenamiento: start/stop/list/download en routers/devices.py."""

import io
import zipfile

from tests.conftest import DEVICE, HDR


def err_code(resp):
    return resp.json()["error"]["code"]


def test_start_stop_list_exige_operator_y_mantiene_device_control(env):
    url = f"/api/devices/{DEVICE}/training-captures"

    env.login_as("viewer@x.com")
    r = env.client.post(url, headers=HDR)
    assert r.status_code == 403 and err_code(r) == "sin_permiso"

    env.login_as("op@x.com")
    r = env.client.post(url, headers=HDR)
    assert r.status_code == 200
    session_id = r.json()["session_id"]
    assert session_id.startswith(DEVICE)

    control = env.control.data["device_control"][DEVICE]
    assert control["capture_requested"] is True
    assert control["capture_session_id"] == session_id

    session_doc = env.events.data["training_sessions"][session_id]
    assert session_doc["status"] == "capturing"
    assert session_doc["frame_count"] == 0

    env.login_as("viewer@x.com")
    r = env.client.post(f"{url}/{session_id}/stop", headers=HDR)
    assert r.status_code == 403

    env.login_as("op@x.com")
    r = env.client.post(f"{url}/{session_id}/stop", headers=HDR)
    assert r.status_code == 204

    control = env.control.data["device_control"][DEVICE]
    assert control["capture_requested"] is False
    assert control["capture_session_id"] == session_id  # el borde lo usa para saber cuál cerrar

    r = env.client.get(url, headers=HDR)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1 and items[0]["session_id"] == session_id


def test_stop_con_session_id_vieja_no_toca_la_sesion_activa(env):
    env.login_as("op@x.com")
    url = f"/api/devices/{DEVICE}/training-captures"
    first = env.client.post(url, headers=HDR).json()["session_id"]
    second = env.client.post(url, headers=HDR).json()["session_id"]
    assert first != second

    r = env.client.post(f"{url}/{first}/stop", headers=HDR)
    assert r.status_code == 204

    control = env.control.data["device_control"][DEVICE]
    assert control["capture_requested"] is True
    assert control["capture_session_id"] == second


def test_download_feliz_404_y_sesion_demasiado_grande(env):
    env.login_as("admin@viz-ia.com")
    session_id = "sesion-1"
    prefix = f"raw/training_captures/device={DEVICE}/session={session_id}/"
    url = f"/api/devices/{DEVICE}/training-captures/{session_id}/download"

    r = env.client.get(url, headers=HDR)
    assert r.status_code == 404 and err_code(r) == "sesion_no_encontrada"

    env.archive_store[prefix] = [
        (f"{prefix}a.jpg", b"foto-a"),
        (f"{prefix}b.jpg", b"foto-b"),
    ]
    r = env.client.get(url, headers=HDR)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert f'filename="{session_id}.zip"' in r.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert sorted(zf.namelist()) == ["a.jpg", "b.jpg"]
        assert zf.read("a.jpg") == b"foto-a"

    max_objects = env.mods["training_captures"].MAX_OBJECTS
    big_session, big_prefix = "sesion-grande", f"raw/training_captures/device={DEVICE}/session=sesion-grande/"
    env.archive_store[big_prefix] = [(f"{big_prefix}{i}.jpg", b"x") for i in range(max_objects + 1)]
    r = env.client.get(f"/api/devices/{DEVICE}/training-captures/{big_session}/download", headers=HDR)
    assert r.status_code == 400 and err_code(r) == "sesion_demasiado_grande"


def test_iter_zip_baja_las_fotos_de_a_poco_y_arma_un_zip_valido(env):
    """Regresión: el ZIP entero en memoria rompía en Cloud Run con sesiones de ~2000 fotos
    (límite de 32 MiB por respuesta no streameada)."""
    tc = env.mods["training_captures"]
    loaded: list[int] = []

    def loader(i: int):
        def load() -> bytes:
            loaded.append(i)
            return f"foto-{i}".encode()

        return load

    archiver = tc.CaptureArchiver(lister=lambda prefix: [(f"{prefix}{i}.jpg", loader(i)) for i in range(50)])
    entries = archiver.list_session("p/")
    assert loaded == []  # listar no baja ninguna foto

    chunks = archiver.iter_zip(entries)
    first = next(chunks)
    assert first and len(loaded) <= tc.PREFETCH + 1  # empieza a responder sin bajar todo
    data = first + b"".join(chunks)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert len(zf.namelist()) == 50
        assert zf.read("49.jpg") == b"foto-49"


def test_download_exige_admin(env):
    env.login_as("op@x.com")
    r = env.client.get(f"/api/devices/{DEVICE}/training-captures/x/download", headers=HDR)
    assert r.status_code == 403 and err_code(r) == "sin_permiso"
