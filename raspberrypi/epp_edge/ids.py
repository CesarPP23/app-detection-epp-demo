import os
import time
import uuid


def uuid7() -> str:
    """UUID versión 7 (RFC 9562): 48 bits de timestamp en ms + aleatorio.
    Único globalmente y ordenable por tiempo: sirve como ID en Firestore,
    nombre de objeto en GCS y clave en BigQuery."""
    if hasattr(uuid, "uuid7"):  # Python >= 3.14
        return str(uuid.uuid7())
    ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")  # 80 bits
    value = (
        ((ms & ((1 << 48) - 1)) << 80)
        | (0x7 << 76)
        | (((rand >> 68) & 0xFFF) << 64)
        | (0b10 << 62)
        | (rand & ((1 << 62) - 1))
    )
    return str(uuid.UUID(int=value))
