import time
import uuid

from epp_edge.ids import uuid7


def test_unicidad_y_version():
    ids = [uuid7() for _ in range(10_000)]
    assert len(set(ids)) == 10_000
    assert all(uuid.UUID(i).version == 7 for i in ids)


def test_orden_temporal():
    a = uuid7()
    time.sleep(0.002)
    b = uuid7()
    time.sleep(0.002)
    c = uuid7()
    assert a < b < c
