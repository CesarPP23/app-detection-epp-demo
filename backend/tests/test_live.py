import asyncio
import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from helpers_live import DEVICE, SECRET_A, SECRET_B, make_frame, wait_until

UPLINK = {"authorization": f"Bearer {SECRET_A}"}
USER = SimpleNamespace(email="viewer@viz-ia.com", role="viewer")


class Recorder:
    def __init__(self):
        self.sets, self.renews, self.audit = [], [], []

    async def set(self, device_id, value, email):
        self.sets.append((device_id, value, email))

    async def renew(self, device_id):
        self.renews.append(device_id)


def build(monkeypatch, user=USER, **env):
    base = {
        "DEVICE_TOKEN": SECRET_A,
        "SESSION_SECRET": SECRET_B,
        "ALLOWED_DEVICES": DEVICE,
        "AUTH_MODE": "dev",
        "STATIC_DIR": "/nonexistent",
    }
    base.update(env)
    monkeypatch.delenv("K_SERVICE", raising=False)
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    from epp_api import lease as lease_mod
    from epp_api import live, relay, settings

    importlib.reload(settings)
    importlib.reload(relay)
    importlib.reload(lease_mod)
    importlib.reload(live)
    rec = Recorder()
    monkeypatch.setattr(live, "lease", lease_mod.StreamingLease(rec.set, rec.renew, 0.02))

    async def fake_auth(ws):
        if user is None:
            await ws.close(code=1008)
        return user

    monkeypatch.setattr(live, "_authenticate", fake_auth)
    monkeypatch.setattr(live, "_audit", lambda email, action, target: rec.audit.append((email, action, target)))
    app = FastAPI()
    app.include_router(relay.router)
    app.include_router(live.router)
    return app, live, relay, rec


def test_espectador_enciende_y_apaga_el_lease(monkeypatch):
    app, live, _, rec = build(monkeypatch)
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/devices/{DEVICE}/live") as ws:
            assert ws.receive_json() == {"type": "status", "state": "waiting"}
            assert rec.sets == [(DEVICE, True, USER.email)]
        assert wait_until(lambda: rec.sets[-1] == (DEVICE, False, USER.email))
        assert wait_until(lambda: [a[1] for a in rec.audit] == ["live_start", "live_end"])
    assert live.lease.viewers == 0


def test_sin_sesion_se_rechaza_sin_tocar_el_lease(monkeypatch):
    app, _, _, rec = build(monkeypatch, user=None)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(f"/api/devices/{DEVICE}/live"):
                pass
    assert exc.value.code == 1008
    assert rec.sets == []


def test_dispositivo_desconocido_cierra_1008(monkeypatch):
    app, _, _, rec = build(monkeypatch)
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/devices/otro/live"):
                pass
    assert exc.value.code == 1008
    assert rec.sets == []


def test_limite_de_espectadores_cierra_1013(monkeypatch):
    app, live, _, rec = build(monkeypatch, MAX_LIVE_VIEWERS="1")
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/devices/{DEVICE}/live") as first:
            first.receive_json()
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect(f"/api/devices/{DEVICE}/live"):
                    pass
            assert exc.value.code == 1013
            assert live.lease.viewers == 1
        assert wait_until(lambda: live.lease.viewers == 0)


def test_reenvia_frames_sin_modificarlos_y_avisa_cuando_la_pi_cae(monkeypatch):
    app, _, _, _ = build(monkeypatch, LIVE_MAX_FPS="100")
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/devices/{DEVICE}/live") as viewer:
            assert viewer.receive_json()["state"] == "waiting"
            with client.websocket_connect(f"/ws/video/{DEVICE}", headers=UPLINK) as pi:
                frame = make_frame(3)
                pi.send_bytes(frame)
                assert viewer.receive_bytes() == frame  # tal cual, con su cabecera de 8 bytes
            # la Pi se desconectó
            assert viewer.receive_json() == {"type": "status", "state": "ended"}


def test_frames_por_encima_del_tope_se_saltan(monkeypatch):
    _, live, relay, _ = build(monkeypatch, LIVE_MAX_FPS="2")  # un frame cada 0.5 s

    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, data):
            pass

        async def send_bytes(self, data):
            self.sent.append(data)

    async def scenario():
        ws, ch = FakeWS(), relay.channel(DEVICE)
        task = asyncio.create_task(live._pump(ws, DEVICE))
        await asyncio.sleep(0.02)
        for i in range(5):  # 5 frames en 0.2 s: solo pasa el primero
            await ch.publish(make_frame(i))
            await asyncio.sleep(0.04)
        assert ws.sent == [make_frame(0)]
        await asyncio.sleep(0.5)
        await ch.publish(make_frame(9))  # ya pasó el intervalo
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return ws.sent

    assert asyncio.run(scenario()) == [make_frame(0), make_frame(9)]


def test_cliente_lento_salta_frames_pero_recibe_el_mas_reciente(monkeypatch):
    _, live, relay, _ = build(monkeypatch, LIVE_MAX_FPS="1000")

    class SlowWS:
        def __init__(self):
            self.sent = []

        async def send_json(self, data):
            pass

        async def send_bytes(self, data):
            await asyncio.sleep(0.1)  # cliente lento
            self.sent.append(data)

    async def scenario():
        ws, ch = SlowWS(), relay.channel(DEVICE)
        task = asyncio.create_task(live._pump(ws, DEVICE))
        await asyncio.sleep(0.02)
        for i in range(20):
            await ch.publish(make_frame(i))
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.4)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return ws.sent

    sent = asyncio.run(scenario())
    assert len(sent) < 20  # se saltaron frames
    assert sent[-1] == make_frame(19)  # y el último que se entregó es el más reciente


def test_desconexion_del_cliente_libera_el_lease_sin_esperar_frames(monkeypatch):
    app, live, _, rec = build(monkeypatch)
    with TestClient(app) as client:
        with client.websocket_connect(f"/api/devices/{DEVICE}/live") as ws:
            ws.receive_json()
        # el cliente cerró sin que hubiera ningún frame que enviar
        assert wait_until(lambda: rec.sets[-1][1] is False)
        assert wait_until(lambda: live.lease.viewers == 0)
