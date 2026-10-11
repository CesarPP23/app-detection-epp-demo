"""reconcile_lake.py (Fase 5.4) y bootstrap_admin.py (Fase 4.4) con fakes en memoria: sin GCP."""

import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path

from tools import reconcile_lake as rl

DAY = date(2026, 10, 10)
A, B, C = (
    "0192f6c4-8a1e-7c3b-9f2d-4b8e1a2c3d4e",
    "0192f6c4-9b2f-7d4c-8a3e-5c9f2b3d4e5f",
    "0192f6c5-0c30-7e5d-9b4f-6d0a3c4e5f60",
)


class FakeSources:
    def __init__(self, stats=None, docs=None, sidecars=(), images=(), bq=()):
        self.stats, self.docs = stats, docs or {}
        self.sidecars, self.images, self.bq = set(sidecars), set(images), set(bq)

    def daily_stats_events(self, device_id, day):
        return self.stats

    def firestore_events(self, device_id, day):
        return dict(self.docs)

    def lake_event_ids(self, device_id, day):
        return self.sidecars

    def lake_image_ids(self, device_id, day):
        return self.images

    def bigquery_event_ids(self, device_id, day):
        return self.bq


def coherent():
    return FakeSources(stats=2, docs={A: "ok", B: "ok"}, sidecars={A, B}, images={A, B}, bq={A, B})


def test_dia_coherente_no_tiene_diferencias():
    rep = rl.reconcile(coherent(), "raspberry-pi-01", DAY)
    assert rep.ok and not rep.missing and not rep.warnings
    assert rep.counts["2 documentos de Firestore"] == 2


def test_evento_sin_sidecar_se_reporta_con_su_id():
    src = coherent()
    src.sidecars.discard(B)
    rep = rl.reconcile(src, "raspberry-pi-01", DAY)
    assert not rep.ok
    assert rep.missing["en Firestore pero sin sidecar en el lake"] == [B]
    assert B in rep.render()


def test_imagen_faltante_solo_si_el_estado_es_ok():
    src = FakeSources(stats=2, docs={A: "ok", B: "missing"}, sidecars={A, B}, images=set())
    rep = rl.reconcile(src, "raspberry-pi-01", DAY, check_bigquery=False)
    assert rep.missing["capture.status=ok pero sin imagen en el lake"] == [A]
    assert "imagen en el lake sin capture.status=ok en Firestore" not in rep.missing


def test_imagen_huerfana_y_sidecar_huerfano():
    src = FakeSources(stats=1, docs={A: "ok"}, sidecars={A, C}, images={A, B})
    rep = rl.reconcile(src, "raspberry-pi-01", DAY, check_bigquery=False)
    assert rep.missing["sidecar en el lake pero sin documento en Firestore"] == [C]
    assert rep.missing["imagen en el lake sin capture.status=ok en Firestore"] == [B]


def test_contador_de_daily_stats_distinto_es_diferencia():
    src = FakeSources(stats=3, docs={A: "ok", B: "ok"}, sidecars={A, B}, images={A, B})
    assert not rl.reconcile(src, "raspberry-pi-01", DAY, check_bigquery=False).ok


def test_bigquery_atrasado_es_aviso_no_fallo():
    src = coherent()
    src.bq = {A}
    rep = rl.reconcile(src, "raspberry-pi-01", DAY)
    assert rep.ok
    assert rep.warnings["aún no están en BigQuery (se cargan con el MERGE diario)"] == [B]


def test_main_dry_run_usa_datos_de_prueba_y_sale_con_cero(capsys):
    assert rl.main(["--dry-run", "--date", "2026-10-10"]) == 0
    assert "RESULTADO: coherente" in capsys.readouterr().out


def test_main_devuelve_uno_si_hay_diferencias(capsys):
    src = FakeSources(stats=1, docs={A: "ok"}, sidecars=set(), images={A})
    assert rl.main(["--date", "2026-10-10", "--skip-bq"], sources=src) == 1
    assert A in capsys.readouterr().out


def test_limites_del_dia_local_de_lima():
    start, end = rl.local_day_bounds(DAY, "America/Lima")
    assert start.astimezone(timezone.utc) == datetime(2026, 10, 10, 5, 0, tzinfo=timezone.utc)
    assert (end - start).days == 1


# --- bootstrap_admin ---------------------------------------------------------------------------


def _load_bootstrap():
    path = Path(__file__).resolve().parents[2] / "infra" / "bootstrap_admin.py"
    spec = importlib.util.spec_from_file_location("bootstrap_admin", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeRef:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def get(self):
        class Snap:
            exists = self.path in self.store

        return Snap()

    def create(self, data):
        assert self.path not in self.store, "create() no debe sobrescribir"
        self.store[self.path] = data


class FakeDb:
    def __init__(self, store):
        self.store = store

    def document(self, path):
        return FakeRef(self.store, path)


def test_bootstrap_crea_y_es_idempotente():
    mod = _load_bootstrap()
    store: dict = {}
    first = mod.bootstrap(FakeDb(store), "TS")
    assert set(first.values()) == {"creado"}
    assert store["dashboard_users/admin@viz-ia.com"]["role"] == "admin"
    assert store["device_control/raspberry-pi-01"]["desired_state"] == "stopped"
    store["device_control/raspberry-pi-01"]["desired_state"] = "running"  # cambio posterior de un operador
    second = mod.bootstrap(FakeDb(store), "TS")
    assert set(second.values()) == {"existente"}
    assert store["device_control/raspberry-pi-01"]["desired_state"] == "running"
