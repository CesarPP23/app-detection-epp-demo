import asyncio
import importlib

import pytest

from helpers_live import DEVICE, SECRET_A, SECRET_B


@pytest.fixture
def lease_mod(monkeypatch):
    for key, value in {
        "DEVICE_TOKEN": SECRET_A,
        "SESSION_SECRET": SECRET_B,
        "ALLOWED_DEVICES": DEVICE,
        "AUTH_MODE": "dev",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("K_SERVICE", raising=False)
    from epp_api import lease

    return importlib.reload(lease)


class Recorder:
    def __init__(self, fail_set=False):
        self.sets, self.renews, self.fail_set = [], [], fail_set

    async def set(self, device_id, value, email):
        self.sets.append((device_id, value, email))
        if self.fail_set:
            raise RuntimeError("Firestore caído")

    async def renew(self, device_id):
        self.renews.append(device_id)


def make(lease_mod, rec, interval=0.02):
    return lease_mod.StreamingLease(rec.set, rec.renew, interval)


def test_cero_a_uno_y_uno_a_cero_escriben_stream_requested(lease_mod):
    async def scenario():
        rec = Recorder()
        lease = make(lease_mod, rec)
        t1 = await lease.acquire(DEVICE, "a@x.com")
        t2 = await lease.acquire(DEVICE, "b@x.com")
        assert rec.sets == [(DEVICE, True, "a@x.com")]  # solo la transición 0→1
        assert lease.viewers == 2
        await lease.release(t1)
        assert rec.sets == [(DEVICE, True, "a@x.com")]  # queda uno: sigue encendido
        await lease.release(t2)
        assert rec.sets[-1] == (DEVICE, False, "b@x.com")
        assert lease.viewers == 0
        return rec

    rec = asyncio.run(scenario())
    assert [s[1] for s in rec.sets] == [True, False]


def test_release_es_idempotente(lease_mod):
    async def scenario():
        rec = Recorder()
        lease = make(lease_mod, rec)
        keeper = await lease.acquire(DEVICE, "a@x.com")
        other = await lease.acquire(DEVICE, "b@x.com")
        await lease.release(other)
        await lease.release(other)  # segunda llamada: no resta otro espectador
        assert lease.viewers_of(DEVICE) == 1
        await lease.release(keeper)
        await lease.release(keeper)
        assert lease.viewers == 0
        assert [s[1] for s in rec.sets] == [True, False]  # un solo apagado

    asyncio.run(scenario())


def test_renueva_periodicamente_y_deja_de_renovar_al_liberar(lease_mod):
    async def scenario():
        rec = Recorder()
        lease = make(lease_mod, rec, interval=0.02)
        ticket = await lease.acquire(DEVICE, "a@x.com")
        await asyncio.sleep(0.15)
        renewed = len(rec.renews)
        assert renewed >= 3
        await lease.release(ticket)
        await asyncio.sleep(0.1)
        assert len(rec.renews) == renewed  # la tarea quedó cancelada
        assert lease._tasks == {}

    asyncio.run(scenario())


def test_un_fallo_de_firestore_no_rompe_el_contador(lease_mod):
    async def scenario():
        rec = Recorder(fail_set=True)
        lease = make(lease_mod, rec)
        ticket = await lease.acquire(DEVICE, "a@x.com")
        assert lease.viewers == 1
        await lease.release(ticket)
        assert lease.viewers == 0

    asyncio.run(scenario())


def test_dispositivos_independientes(lease_mod):
    async def scenario():
        rec = Recorder()
        lease = make(lease_mod, rec)
        a = await lease.acquire("pi-a", "a@x.com")
        b = await lease.acquire("pi-b", "a@x.com")
        await lease.release(a)
        assert lease.viewers_of("pi-b") == 1
        assert ("pi-a", False, "a@x.com") in rec.sets
        await lease.release(b)

    asyncio.run(scenario())
