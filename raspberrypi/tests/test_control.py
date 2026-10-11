import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from epp_edge.control import DeviceControl, HealthReporter

from _fakes import FakeClock, make_settings, snap

LOG = logging.getLogger("test")
T0 = datetime(2026, 10, 10, 15, 0, 0, tzinfo=timezone.utc)


class FakeControlDB:
    def __init__(self, doc):
        self.doc = doc

    def collection(self, name):
        self.last_collection = name
        return SimpleNamespace(document=lambda _id: self.doc)


class FakeControlDoc:
    def __init__(self):
        self.snap, self.fail_get, self.subscriptions, self.unsubscribed = None, False, 0, 0

    def on_snapshot(self, cb):
        self.subscriptions += 1
        self.cb = cb
        return SimpleNamespace(unsubscribe=self._unsub)

    def _unsub(self):
        self.unsubscribed += 1

    def get(self):
        if self.fail_get:
            raise RuntimeError("Firestore no disponible")
        return self.snap


def make(tmp_path, **over):
    doc, clock = FakeControlDoc(), FakeClock()
    ctl = DeviceControl(FakeControlDB(doc), make_settings(tmp_path, **over), LOG, clock=clock)
    return ctl, doc, clock


def lease_doc(age, lease=90, requested=True, desired="running"):
    return {"desired_state": desired, "stream_requested": requested, "lease_seconds": lease,
            "lease_renewed_at": T0 - timedelta(seconds=age)}


def test_lease_vigente_descuenta_la_edad_del_servidor(tmp_path):
    ctl, _, clock = make(tmp_path)
    ctl._apply(snap(lease_doc(age=10), read_time=T0))  # quedan 80 s
    assert ctl.should_stream()
    clock.advance(79)
    assert ctl.should_stream()
    clock.advance(2)
    assert not ctl.should_stream()


def test_lease_vencido_no_transmite(tmp_path):
    ctl, _, _ = make(tmp_path)
    ctl._apply(snap(lease_doc(age=100), read_time=T0))
    assert not ctl.should_stream() and not ctl.lease_active()


def test_snapshot_sin_cambio_de_lease_no_lo_extiende(tmp_path):
    ctl, _, clock = make(tmp_path)
    ctl._apply(snap(lease_doc(age=0), read_time=T0))  # vence en +90
    clock.advance(50)
    # otro campo cambió: el mismo lease_renewed_at llega con un read_time posterior
    ctl._apply(snap(lease_doc(age=50), read_time=T0 + timedelta(seconds=50)))
    clock.advance(41)
    assert not ctl.should_stream()  # 91 s desde el lease original


def test_renovar_el_lease_lo_extiende(tmp_path):
    ctl, _, clock = make(tmp_path)
    ctl._apply(snap(lease_doc(age=0), read_time=T0))
    clock.advance(60)
    ctl._apply(snap({**lease_doc(age=0), "lease_renewed_at": T0 + timedelta(seconds=60)},
                    read_time=T0 + timedelta(seconds=60)))
    clock.advance(60)
    assert ctl.should_stream()


def test_sin_stream_requested_corta_el_stream(tmp_path):
    ctl, _, _ = make(tmp_path)
    ctl._apply(snap(lease_doc(age=0), read_time=T0))
    assert ctl.should_stream()
    ctl._apply(snap(lease_doc(age=0, requested=False), read_time=T0))
    assert not ctl.should_stream()


def test_pausado_no_transmite_aunque_haya_lease(tmp_path):
    ctl, _, _ = make(tmp_path)
    ctl._apply(snap(lease_doc(age=0, desired="stopped"), read_time=T0))
    assert not ctl.should_run() and not ctl.should_stream()


def test_documento_inexistente_usa_el_valor_por_defecto(tmp_path):
    ctl, doc, _ = make(tmp_path, default_desired_state="stopped")
    ctl._on_snapshot([], [], T0)  # el listener entrega lista vacía
    assert ctl.desired_state == "stopped" and ctl._ever_synced


def test_error_de_firestore_conserva_el_ultimo_estado(tmp_path):
    ctl, doc, clock = make(tmp_path)  # defecto: running
    ctl._apply(snap(lease_doc(age=0, desired="stopped"), read_time=T0))
    doc.fail_get = True
    clock.advance(400)
    ctl.safety_tick()  # get() falla
    assert ctl.desired_state == "stopped"  # NO vuelve a 'running' (D6)


def test_error_sin_lectura_previa_usa_el_defecto(tmp_path):
    ctl, doc, _ = make(tmp_path)
    doc.fail_get = True
    ctl.safety_tick()
    assert ctl.should_run() and not ctl._ever_synced


def test_resuscribe_si_pierde_sincronia(tmp_path):
    ctl, doc, clock = make(tmp_path, control_safety_poll_seconds=300)
    ctl._subscribe()
    ctl._apply(snap(lease_doc(age=0), read_time=T0))
    doc.fail_get = True
    clock.advance(601)
    ctl.safety_tick()
    assert doc.subscriptions == 2 and doc.unsubscribed == 1


def test_respaldo_aplica_cambios_que_el_listener_no_vio(tmp_path):
    ctl, doc, _ = make(tmp_path)
    doc.snap = snap({"desired_state": "stopped"})
    ctl.safety_tick()
    assert not ctl.should_run()


def test_auto_stream_solo_si_esta_habilitado(tmp_path):
    ctl, _, clock = make(tmp_path, auto_stream_on_detection=False)
    ctl._apply(snap({"desired_state": "running"}))
    ctl.bump_auto_stream(30)
    assert not ctl.should_stream()
    ctl2, _, clock2 = make(tmp_path, auto_stream_on_detection=True)
    ctl2._apply(snap({"desired_state": "running"}))
    ctl2.bump_auto_stream(30)
    assert ctl2.should_stream()
    clock2.advance(31)
    assert not ctl2.should_stream()


# --- HealthReporter --------------------------------------------------------------------------------


class FakeHealthDoc:
    def __init__(self):
        self.writes = []

    def set(self, data, merge=False):
        self.writes.append((data, merge))


def test_health_escribe_estado_y_outbox(tmp_path):
    doc = FakeHealthDoc()
    db = SimpleNamespace(collection=lambda name: SimpleNamespace(document=lambda _id: doc))
    outbox = SimpleNamespace(stats=lambda: {"pending": 3, "bytes": 1234})
    h = HealthReporter(db, make_settings(tmp_path), outbox, LOG, edge_version="2.0.0")
    h.update(reported_state="running", camera_ok=True, inference_fps=4.8)
    h.write()
    data, merge = doc.writes[0]
    assert merge is True
    assert data["reported_state"] == "running" and data["outbox_pending"] == 3 and data["outbox_bytes"] == 1234
    assert data["edge_version"] == "2.0.0" and "last_seen" in data and "cpu_temp_c" in data


def test_health_final_deja_offline(tmp_path):
    doc = FakeHealthDoc()
    db = SimpleNamespace(collection=lambda name: SimpleNamespace(document=lambda _id: doc))
    h = HealthReporter(db, make_settings(tmp_path), SimpleNamespace(stats=lambda: {"pending": 0, "bytes": 0}), LOG)
    h.set_streaming(True)
    h.final("offline")
    data, _ = doc.writes[-1]
    assert data["reported_state"] == "offline" and data["streaming"] is False
