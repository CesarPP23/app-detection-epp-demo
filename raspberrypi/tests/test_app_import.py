import sys


def test_los_modulos_del_servicio_se_importan_sin_ultralytics_ni_picamera2():
    import epp_edge.app  # noqa: F401
    import epp_edge.captures  # noqa: F401
    import epp_edge.control  # noqa: F401
    import epp_edge.outbox  # noqa: F401
    import epp_edge.streamer  # noqa: F401

    assert "ultralytics" not in sys.modules and "picamera2" not in sys.modules
