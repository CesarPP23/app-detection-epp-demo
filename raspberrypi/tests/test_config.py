import pytest
from epp_edge.config import ConfigError, Settings

BASE = {
    "DEVICE_ID": "raspberry-pi-01",
    "ORCHESTRATOR_HOST": "orchestrator.example.run.app",
    "DEVICE_TOKEN": "x" * 40,
}


def env(**kw):
    e = dict(BASE)
    e.update(kw)
    return {k: v for k, v in e.items() if v is not None}


def test_defaults_validos():
    s = Settings.from_env(env())
    assert s.model_imgsz == 416 and s.inference_fps == 5 and s.camera_source == "auto"
    assert s.uplink_url == "wss://orchestrator.example.run.app/ws/video/raspberry-pi-01"
    assert s.control_database_id == "(default)" and s.detections_database_id == "dbraspberry"
    assert s.auto_stream_on_detection is False and s.is_simulated is False


@pytest.mark.parametrize("missing", ["DEVICE_ID", "ORCHESTRATOR_HOST", "DEVICE_TOKEN"])
def test_obligatorias(missing):
    with pytest.raises(ConfigError, match=missing):
        Settings.from_env(env(**{missing: None}))


def test_token_corto_rechazado():
    with pytest.raises(ConfigError, match="32"):
        Settings.from_env(env(DEVICE_TOKEN="corto"))  # noqa: S106 - valor de prueba, no un secreto real


def test_host_sin_esquema():
    with pytest.raises(ConfigError, match="ORCHESTRATOR_HOST"):
        Settings.from_env(env(ORCHESTRATOR_HOST="https://x.run.app"))


def test_device_id_invalido():
    with pytest.raises(ConfigError, match="DEVICE_ID"):
        Settings.from_env(env(DEVICE_ID="Pi 01"))


def test_file_exige_allow_test_video():
    with pytest.raises(ConfigError, match="ALLOW_TEST_VIDEO"):
        Settings.from_env(env(CAMERA_SOURCE="file:media/prueba1.mp4"))
    s = Settings.from_env(env(CAMERA_SOURCE="file:media/prueba1.mp4", ALLOW_TEST_VIDEO="true"))
    assert s.is_simulated


def test_imgsz_multiplo_de_32_y_rangos():
    with pytest.raises(ConfigError, match="MODEL_IMGSZ"):
        Settings.from_env(env(MODEL_IMGSZ="400"))
    with pytest.raises(ConfigError, match="CONFIDENCE_THRESHOLD"):
        Settings.from_env(env(CONFIDENCE_THRESHOLD="1.5"))
    with pytest.raises(ConfigError, match="INFERENCE_FPS"):
        Settings.from_env(env(INFERENCE_FPS="abc"))


def test_camera_source_invalida_y_estado_por_defecto():
    with pytest.raises(ConfigError, match="CAMERA_SOURCE"):
        Settings.from_env(env(CAMERA_SOURCE="webcam"))
    with pytest.raises(ConfigError, match="DEFAULT_DESIRED_STATE"):
        Settings.from_env(env(DEFAULT_DESIRED_STATE="paused"))


def test_variables_eliminadas_ya_no_existen():
    s = Settings.from_env(env())
    for old in ("frame_skip", "uplink_token", "cloud_run_url", "force_test_video", "state_poll_interval"):
        assert not hasattr(s, old)
