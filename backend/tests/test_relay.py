import asyncio
import importlib
import struct

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

SECRET_A = "a" * 40
SECRET_B = "b" * 40
DEVICE = "raspberry-pi-01"


def make_frame(n: int = 0, size: int = 64) -> bytes:
    return struct.pack(">Q", 1_700_000_000_000 + n) + b"\xff\xd8" + bytes([n % 256]) * size


def load_app(monkeypatch, **overrides):
    env = {
        "DEVICE_TOKEN": SECRET_A,
        "SESSION_SECRET": SECRET_B,
        "ALLOWED_DEVICES": DEVICE,
        "AUTH_MODE": "dev",
        "STATIC_DIR": "/nonexistent",
    }
    env.update(overrides)
    monkeypatch.delenv("K_SERVICE", raising=False)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    from epp_api import main, relay, security_headers, settings

    importlib.reload(settings)
    importlib.reload(relay)
    importlib.reload(security_headers)
    importlib.reload(main)
    return main.app, relay


@pytest.mark.parametrize("missing", ["DEVICE_TOKEN", "SESSION_SECRET"])
def test_sin_secretos_no_arranca(monkeypatch, missing):
    with pytest.raises(RuntimeError):
        load_app(monkeypatch, **{missing: None})


def test_secreto_corto_no_arranca(monkeypatch):
    with pytest.raises(RuntimeError):
        load_app(monkeypatch, DEVICE_TOKEN="corto")  # noqa: S106 - valor de prueba, no un secreto real


def test_modo_dev_prohibido_en_cloud_run(monkeypatch):
    with pytest.raises(RuntimeError):
        load_app(monkeypatch, K_SERVICE="orchestrator-service")


@pytest.mark.parametrize(
    "headers,device",
    [
        ({}, DEVICE),
        ({"authorization": "Bearer " + "x" * 40}, DEVICE),
        ({"authorization": "Bearer " + SECRET_A}, "otro-dispositivo"),
    ],
)
def test_uplink_rechazado_cierra_1008(monkeypatch, headers, device):
    app, _ = load_app(monkeypatch)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/ws/video/{device}", headers=headers):
                pass
        assert exc.value.code == 1008


def test_uplink_valido_acepta(monkeypatch):
    app, relay = load_app(monkeypatch)
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ws/video/{DEVICE}", headers={"authorization": f"Bearer {SECRET_A}"}
        ) as ws:
            ws.send_bytes(make_frame(1))


def test_tres_frames_distintos_se_entregan_sin_duplicados(monkeypatch):
    _, relay = load_app(monkeypatch)

    async def scenario():
        ch = relay.DeviceChannel()
        received = []

        async def consumer():
            seq = 0
            while len(received) < 3:
                seq, frame = await ch.next_frame(seq, timeout=1)
                received.append(frame)

        task = asyncio.create_task(consumer())
        for i in range(3):
            await asyncio.sleep(0.01)
            await ch.publish(make_frame(i))
        await asyncio.wait_for(task, 2)
        # sin publicar nada nuevo no hay un cuarto frame
        with pytest.raises(asyncio.TimeoutError):
            await ch.next_frame(ch.seq, timeout=0.05)
        return received

    received = asyncio.run(scenario())
    assert received == [make_frame(i) for i in range(3)]


def test_cerrar_publicador_entrega_none(monkeypatch):
    _, relay = load_app(monkeypatch)

    async def scenario():
        ch = relay.DeviceChannel()
        ch.publisher_id = 1
        await ch.publish(make_frame(1))
        seq = ch.seq
        waiter = asyncio.create_task(ch.next_frame(seq, timeout=1))
        await asyncio.sleep(0.01)
        await ch.close(1)
        return await waiter, ch.live

    (seq, frame), live = asyncio.run(scenario())
    assert frame is None
    assert live is False


def test_frames_invalidos_se_ignoran(monkeypatch):
    _, relay = load_app(monkeypatch, MAX_FRAME_BYTES="200")
    assert relay.valid_frame(make_frame(1, size=50))
    assert not relay.valid_frame(b"\x00" * 5)  # sin cabecera completa
    assert not relay.valid_frame(struct.pack(">Q", 1) + b"\x00\x00" + b"\x01" * 50)  # sin FF D8
    assert not relay.valid_frame(make_frame(1, size=500))  # excede MAX_FRAME_BYTES


def test_frame_invalido_no_se_publica_por_websocket(monkeypatch):
    app, relay = load_app(monkeypatch)
    with TestClient(app) as client:
        with client.websocket_connect(
            f"/ws/video/{DEVICE}", headers={"authorization": f"Bearer {SECRET_A}"}
        ) as ws:
            ws.send_bytes(b"basura")
            ws.send_bytes(make_frame(7))
        # el publicador cerró: canal sin señal, pero la secuencia avanzó por el frame válido y el cierre
        assert relay.channel(DEVICE).seq == 2


def test_cerrar_publicador_viejo_no_apaga_al_nuevo(monkeypatch):
    _, relay = load_app(monkeypatch)

    async def scenario():
        ch = relay.DeviceChannel()
        ch.publisher_id = 1
        old_id = ch.publisher_id
        ch.publisher_id = 2  # reconexión de la Pi: el publicador nuevo gana
        await ch.publish(make_frame(2))
        await ch.close(old_id)
        assert ch.live  # el cierre del viejo no apaga el canal
        await ch.close(2)
        assert not ch.live

    asyncio.run(scenario())


def test_healthz_y_csp(monkeypatch):
    app, _ = load_app(monkeypatch, PUBLIC_BASE_URL="https://ejemplo.run.app")
    with TestClient(app) as client:
        r = client.get("/healthz")
        assert r.status_code == 200 and r.json()["ok"] is True
        assert "wss://ejemplo.run.app" in r.headers["content-security-policy"]
        assert client.get("/api/algo").status_code == 404
