import importlib
import sys
import types
from datetime import UTC, datetime, timedelta

import pytest
from starlette.testclient import TestClient

from tests.fake_gcp import FakeDB

BASE = "https://epp.test"
DEVICE = "raspberry-pi-01"
HDR = {"X-Requested-With": "epp-web"}

# Orden de recarga: cada módulo se importa después de aquello de lo que depende.
_MODULES = [
    "settings", "security_headers", "relay", "errors", "schemas", "repo", "audit", "lake", "auth",
    "summary", "lease", "live", "routers.auth", "routers.devices", "routers.events", "routers.admin", "main",
]  # fmt: skip


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def make_event(
    i: int, *, minutes_ago: float, clase="hairnet", conf=0.9, simulated=False, status="ok", now=None
):
    now = now or datetime.now(UTC)
    end = now - timedelta(minutes=minutes_ago)
    eid = f"0192f6c4-{i:04d}-7c3b-9f2d-4b8e1a2c3d4e"
    return eid, {
        "schema_version": 2,
        "event_id": eid,
        "device_id": DEVICE,
        "clase_id": 0,
        "nombre_clase": clase,
        "confianza_promedio": conf - 0.05,
        "confianza_max": conf,
        "frame_count": 10,
        "start_ts": end - timedelta(seconds=5),
        "end_ts": end,
        "duration_seconds": 5.0,
        "closed_reason": "gap",
        "is_simulated": simulated,
        "capture": {
            "status": status,
            "uri": f"gs://epp-detection-v1-lake/raw/images/device={DEVICE}/dt=2026-10-10/{eid}.jpg",
            "thumb_uri": f"gs://epp-detection-v1-lake/raw/thumbs/device={DEVICE}/dt=2026-10-10/{eid}.jpg",
            "captured_at": end,
            "width": 1280,
            "height": 720,
        },
        "detections_at_capture": [
            {"clase_id": 0, "nombre_clase": clase, "confianza": conf, "bbox_xyxy_norm": [0.1, 0.1, 0.4, 0.5]}
        ],
        "edge": {"edge_version": "2.0.0", "model_version": "abc123", "camera_source": "file"},
    }


class Env:
    pass


@pytest.fixture
def make_env(monkeypatch):
    def build(auth_mode: str = "google") -> Env:
        monkeypatch.delenv("K_SERVICE", raising=False)
        for key, value in {
            "DEVICE_TOKEN": "a" * 40,
            "SESSION_SECRET": "b" * 40,
            "ALLOWED_DEVICES": DEVICE,
            "AUTH_MODE": auth_mode,
            "GOOGLE_OAUTH_CLIENT_ID": "client-id.apps.googleusercontent.com",
            "PUBLIC_BASE_URL": BASE,
            "STATIC_DIR": "/nonexistent",
        }.items():
            monkeypatch.setenv(key, value)
        mods: dict[str, types.ModuleType] = {}
        for name in _MODULES:
            full = f"epp_api.{name}"
            try:
                mods[name] = (
                    importlib.reload(sys.modules[full])
                    if full in sys.modules
                    else importlib.import_module(full)
                )
            except ModuleNotFoundError:
                continue
        env = Env()
        env.mods = mods
        env.control = FakeDB()
        env.events = FakeDB()
        env.clock = Clock()
        env.repo = mods["repo"].Repo(env.control, env.events, ttl=10, clock=env.clock)
        env.audit_log = []
        env.audit = mods["audit"].Audit(
            lambda doc: (env.audit_log.append(doc), env.repo.write_audit_sync(doc))
        )
        env.signed = []
        env.lake = mods["lake"].Lake(
            signer=lambda uri: env.signed.append(uri) or f"https://signed.test/{len(env.signed)}"
        )
        app = mods["main"].app
        app.dependency_overrides[mods["repo"].get_repo] = lambda: env.repo
        app.dependency_overrides[mods["audit"].get_audit] = lambda: env.audit
        app.dependency_overrides[mods["lake"].get_lake] = lambda: env.lake
        mods["audit"]._audit = env.audit  # para audit.write() a nivel de módulo (live.py)
        env.app = app
        env.client = TestClient(app, base_url=BASE)
        for email, role in (
            ("admin@viz-ia.com", "admin"),
            ("op@x.com", "operator"),
            ("viewer@x.com", "viewer"),
        ):
            env.control.collection("dashboard_users").document(email).set(
                {"role": role, "active": True, "created_by": "bootstrap", "created_at": datetime.now(UTC)}
            )

        def login_as(email: str):
            record = env.control.data["dashboard_users"][email]
            user = mods["auth"].SessionUser(email=email, role=record["role"], name="Prueba")
            env.client.cookies.set("epp_session", mods["auth"].issue_cookie_value(user))

        def seed_events(rows: list[tuple[str, dict]]):
            for eid, data in rows:
                env.events.collection("detecciones_confirmadas").document(eid).set(data)

        def audit_actions() -> list[str]:
            import time

            time.sleep(0.15)  # las escrituras de auditoría corren en un hilo
            return [d["action"] for d in env.audit_log]

        env.login_as, env.seed_events, env.audit_actions = login_as, seed_events, audit_actions
        return env

    yield build
    for mod in list(sys.modules):
        if mod.startswith("epp_api"):
            sys.modules.pop(mod, None)


@pytest.fixture
def env(make_env):
    return make_env()
