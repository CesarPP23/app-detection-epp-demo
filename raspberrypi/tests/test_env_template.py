from pathlib import Path

from epp_edge.config import Settings

TEMPLATE = Path(__file__).resolve().parents[1] / "deploy" / "edge.env.template"


def parse(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def test_la_plantilla_es_una_configuracion_valida():
    s = Settings.from_env(parse(TEMPLATE))
    assert s.device_id == "raspberry-pi-01" and s.auto_stream_on_detection is False
    assert not s.allow_test_video


def test_la_plantilla_no_contiene_secretos_reales():
    env = parse(TEMPLATE)
    assert env["DEVICE_TOKEN"].startswith("REEMPLAZAR")
    assert "000000000000" in env["ORCHESTRATOR_HOST"]
