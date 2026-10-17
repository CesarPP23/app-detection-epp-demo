"""Constantes y utilidades compartidas por test_live.py y test_lease.py."""

import struct

SECRET_A = "a" * 40
SECRET_B = "b" * 40
DEVICE = "raspberry-pi-01"


def make_frame(n: int = 0, size: int = 64) -> bytes:
    return struct.pack(">Q", 1_700_000_000_000 + n) + b"\xff\xd8" + bytes([n % 256]) * size


def wait_until(condition, timeout: float = 2.0) -> bool:
    """Espera (síncrona) a que se cumpla una condición: el handler del servidor termina de forma
    asíncrona después de que el TestClient cierra el socket."""
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(0.01)
    return condition()
