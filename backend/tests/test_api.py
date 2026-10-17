from datetime import UTC, datetime, timedelta

import pytest
from starlette.websockets import WebSocketDisconnect
from tests.conftest import BASE, DEVICE, HDR, make_event

GOOD_INFO = {"email": "Viewer@X.com", "name": "Vi Ewer", "picture": "https://img.test/a.png"}


def err_code(resp):
    return resp.json()["error"]["code"]


# --- autenticación ----------------------------------------------------------------------------


def test_auth_config_es_publica_y_devuelve_el_client_id(env):
    r = env.client.get("/api/auth/config")
    assert r.status_code == 200
    assert r.json() == {"client_id": "client-id.apps.googleusercontent.com"}


def test_login_feliz_emite_cookie_y_me(env, monkeypatch):
    monkeypatch.setattr(env.mods["auth"], "verify_google_token", lambda c: GOOD_INFO)
    r = env.client.post("/api/auth/google", json={"credential": "x" * 20}, headers=HDR)
    assert r.status_code == 200
    assert r.json() == {
        "email": "viewer@x.com",
        "name": "Vi Ewer",
        "picture": GOOD_INFO["picture"],
        "role": "viewer",
    }
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "secure" in set_cookie and "samesite=strict" in set_cookie
    me = env.client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["role"] == "viewer"
    assert "login" in env.audit_actions()


def test_login_cuenta_no_autorizada_da_403_y_audita(env, monkeypatch):
    monkeypatch.setattr(env.mods["auth"], "verify_google_token", lambda c: {"email": "intruso@x.com"})
    r = env.client.post("/api/auth/google", json={"credential": "x" * 20}, headers=HDR)
    assert r.status_code == 403 and err_code(r) == "acceso_denegado"
    assert "intruso@x.com" in r.json()["error"]["message"]
    assert "epp_session" not in r.headers.get("set-cookie", "")
    assert env.audit_actions() == ["login_denied"]


def test_login_con_usuario_inactivo_se_deniega(env, monkeypatch):
    env.control.data["dashboard_users"]["viewer@x.com"]["active"] = False
    monkeypatch.setattr(env.mods["auth"], "verify_google_token", lambda c: GOOD_INFO)
    r = env.client.post("/api/auth/google", json={"credential": "x" * 20}, headers=HDR)
    assert r.status_code == 403


def test_credencial_invalida_da_401(env, monkeypatch):
    def boom(_):
        raise ValueError("mal token")

    monkeypatch.setattr(env.mods["auth"], "verify_google_token", boom)
    r = env.client.post("/api/auth/google", json={"credential": "x" * 20}, headers=HDR)
    assert r.status_code == 401 and err_code(r) == "credencial_invalida"


def test_sin_cookie_devuelve_401_con_formato_uniforme(env):
    r = env.client.get("/api/devices")
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "no_autenticado", "message": "Inicia sesión para continuar."}}


def test_cookie_manipulada_o_expirada_da_401(env):
    env.client.cookies.set("epp_session", "basura.firma.falsa")
    assert env.client.get("/api/devices").status_code == 401


def test_login_sin_cabecera_csrf_da_403(env):
    r = env.client.post("/api/auth/google", json={"credential": "x" * 20})
    assert r.status_code == 403 and err_code(r) == "csrf"


def test_limite_de_intentos_de_login(env, monkeypatch):
    monkeypatch.setattr(env.mods["auth"], "verify_google_token", lambda c: {"email": "nadie@x.com"})
    codes = [
        env.client.post("/api/auth/google", json={"credential": "x" * 20}, headers=HDR).status_code
        for _ in range(11)
    ]
    assert codes[:10] == [403] * 10 and codes[10] == 429


def test_usuario_dado_de_baja_pierde_acceso_tras_60s(env):
    env.login_as("viewer@x.com")
    assert env.client.get("/api/devices").status_code == 200
    env.control.data["dashboard_users"]["viewer@x.com"]["active"] = False
    assert env.client.get("/api/devices").status_code == 200  # aún en la caché de 60 s
    env.clock.advance(61)
    assert env.client.get("/api/devices").status_code == 401


def test_rol_se_revalida_contra_la_base(env):
    env.login_as("op@x.com")  # la cookie dice operator
    env.control.data["dashboard_users"]["op@x.com"]["role"] = "viewer"
    env.clock.advance(61)
    r = env.client.put(f"/api/devices/{DEVICE}/desired-state", json={"state": "stopped"}, headers=HDR)
    assert r.status_code == 403


def test_logout_borra_la_cookie(env):
    env.login_as("viewer@x.com")
    r = env.client.post("/api/auth/logout", headers=HDR)
    assert r.status_code == 204
    assert "epp_session" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()


def test_modo_dev_no_requiere_cookie(make_env):
    env = make_env("dev")
    r = env.client.get("/api/auth/me")
    assert r.status_code == 200 and r.json()["email"] == "dev@localhost" and r.json()["role"] == "admin"


# --- roles y CSRF -----------------------------------------------------------------------------


def test_desired_state_exige_csrf_y_rol(env):
    url = f"/api/devices/{DEVICE}/desired-state"
    env.login_as("op@x.com")
    r = env.client.put(url, json={"state": "stopped"})
    assert r.status_code == 403 and err_code(r) == "csrf"
    env.login_as("viewer@x.com")
    r = env.client.put(url, json={"state": "stopped"}, headers=HDR)
    assert r.status_code == 403 and err_code(r) == "sin_permiso"
    env.login_as("op@x.com")
    r = env.client.put(url, json={"state": "stopped"}, headers=HDR)
    assert r.status_code == 200
    doc = env.control.data["device_control"][DEVICE]
    assert doc["desired_state"] == "stopped" and doc["updated_by"] == "op@x.com"
    assert "desired_state" in env.audit_actions()


def test_admin_solo_para_admin(env):
    env.login_as("op@x.com")
    assert env.client.get("/api/admin/users").status_code == 403
    env.login_as("admin@viz-ia.com")
    assert env.client.get("/api/admin/users").status_code == 200


def test_ruta_api_desconocida_y_validacion_usan_formato_uniforme(env):
    env.login_as("viewer@x.com")
    r = env.client.get("/api/no-existe")
    assert r.status_code == 404 and err_code(r) == "no_encontrado"
    r = env.client.get(f"/api/devices/{DEVICE}/events?limit=0")
    assert r.status_code == 422 and err_code(r) == "solicitud_invalida"
    assert env.client.get("/api/devices/otro/summary").status_code == 404


# --- dispositivos -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("control", "health", "esperado"),
    [
        ({}, {}, "offline"),
        ({}, {"last_seen": datetime.now(UTC) - timedelta(minutes=30)}, "offline"),
        ({}, {"last_seen": datetime.now(UTC), "camera_ok": False}, "camera_error"),
        ({"desired_state": "stopped"}, {"last_seen": datetime.now(UTC)}, "paused"),
        ({}, {"last_seen": datetime.now(UTC), "reported_state": "stopped"}, "paused"),
        ({}, {"last_seen": datetime.now(UTC), "streaming": True}, "live"),
        ({}, {"last_seen": datetime.now(UTC), "camera_ok": True}, "detecting"),
    ],
)
def test_estado_del_dispositivo(env, control, health, esperado):
    state = env.mods["routers.devices"].device_state(control, health, datetime.now(UTC))
    assert state == esperado


def test_lista_de_dispositivos_incluye_salud(env):
    env.control.collection("device_health").document(DEVICE).set(
        {"last_seen": datetime.now(UTC), "camera_ok": True, "inference_fps": 4.8, "model_version": "abc"}
    )
    env.login_as("viewer@x.com")
    [d] = env.client.get("/api/devices").json()
    assert d["id"] == DEVICE and d["state"] == "detecting"
    assert d["health"]["inference_fps"] == 4.8 and d["health"]["model_version"] == "abc"


# --- eventos: caché incremental, paginación y filtros -----------------------------------------


def test_cache_incremental_una_lectura_sin_cambios(env):
    now = datetime.now(UTC)
    env.seed_events([make_event(i, minutes_ago=i + 1, now=now) for i in range(5)])
    env.login_as("viewer@x.com")
    url = f"/api/devices/{DEVICE}/events"
    assert len(env.client.get(url).json()["items"]) == 5
    inicial = env.events.reads
    assert inicial == 5
    # Varios usuarios dentro del TTL: cero lecturas nuevas.
    for _ in range(3):
        env.client.get(url)
    assert env.events.reads == inicial
    # Pasado el TTL y sin eventos nuevos: exactamente 1 lectura.
    env.clock.advance(11)
    env.client.get(url)
    assert env.events.reads == inicial + 1
    # Con un evento nuevo: solo se lee ese documento.
    env.seed_events([make_event(99, minutes_ago=0, now=now + timedelta(seconds=30))])
    env.clock.advance(11)
    items = env.client.get(url).json()["items"]
    assert env.events.reads == inicial + 2
    assert len(items) == 6 and items[0]["event_id"].startswith("0192f6c4-0099")


def test_paginacion_por_cursor_recorre_todo_sin_repetir(env):
    now = datetime.now(UTC)
    env.seed_events([make_event(i, minutes_ago=i + 1, now=now) for i in range(30)])
    env.login_as("viewer@x.com")
    url = f"/api/devices/{DEVICE}/events?limit=10"
    vistos, cursor = [], None
    for _ in range(5):
        page = env.client.get(url + (f"&cursor={cursor}" if cursor else "")).json()
        vistos += [e["event_id"] for e in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(vistos) == 30 and len(set(vistos)) == 30
    assert vistos == sorted(vistos)  # end_ts descendente = id ascendente en este conjunto
    assert env.client.get(url + "&cursor=%%%").status_code == 400


def test_paginacion_con_cursor_respeta_los_filtros(env):
    now = datetime.now(UTC)
    rows = [
        make_event(i, minutes_ago=i + 1, clase="hairnet" if i % 2 else "lab_coat", now=now) for i in range(20)
    ]
    env.seed_events(rows)
    env.login_as("viewer@x.com")
    url = f"/api/devices/{DEVICE}/events?limit=4&clase=lab_coat"
    p1 = env.client.get(url).json()
    p2 = env.client.get(url + f"&cursor={p1['next_cursor']}").json()
    clases = {e["nombre_clase"] for e in p1["items"] + p2["items"]}
    assert clases == {"lab_coat"} and len(p1["items"]) == len(p2["items"]) == 4
    assert not set(e["event_id"] for e in p1["items"]) & set(e["event_id"] for e in p2["items"])


def test_filtros_confianza_y_simulados(env):
    now = datetime.now(UTC)
    env.seed_events(
        [
            make_event(1, minutes_ago=1, conf=0.95, now=now),
            make_event(2, minutes_ago=2, conf=0.55, now=now),
            make_event(3, minutes_ago=3, conf=0.95, simulated=True, now=now),
        ]
    )
    env.login_as("viewer@x.com")
    base = f"/api/devices/{DEVICE}/events"
    assert len(env.client.get(base).json()["items"]) == 2  # simulados ocultos por defecto
    assert len(env.client.get(base + "?include_simulated=true").json()["items"]) == 3
    assert len(env.client.get(base + "?min_conf=0.9").json()["items"]) == 1


def test_captura_expirada_por_retencion(env):
    now = datetime.now(UTC)
    eid, data = make_event(1, minutes_ago=61 * 24 * 60, now=now)
    nuevo_id, nuevo = make_event(2, minutes_ago=5, now=now)
    env.seed_events([(eid, data), (nuevo_id, nuevo)])
    env.login_as("viewer@x.com")
    items = {e["event_id"]: e for e in env.client.get(f"/api/devices/{DEVICE}/events").json()["items"]}
    assert items[eid]["capture_status"] == "expired" and items[eid]["thumb_url"] is None
    assert items[nuevo_id]["capture_status"] == "ok"
    r = env.client.get(f"/api/events/{eid}/capture?kind=thumb", follow_redirects=False)
    assert r.status_code == 410 and err_code(r) == "captura_expirada"
    detail = env.client.get(f"/api/events/{eid}").json()
    assert detail["capture"]["status"] == "expired" and detail["original_url"] is None


def test_detalle_de_evento_y_lake_uri_solo_admin(env):
    eid, data = make_event(1, minutes_ago=3)
    env.seed_events([(eid, data)])
    env.login_as("viewer@x.com")
    d = env.client.get(f"/api/events/{eid}").json()
    assert d["lake_uri"] is None and d["detections_at_capture"][0]["bbox_xyxy_norm"] == [0.1, 0.1, 0.4, 0.5]
    assert (
        d["original_url"] == f"/api/events/{eid}/capture?kind=original"
        and d["edge"]["model_version"] == "abc123"
    )
    env.login_as("admin@viz-ia.com")
    assert env.client.get(f"/api/events/{eid}").json()["lake_uri"].startswith("gs://")
    assert env.client.get("/api/events/no-existe").status_code == 404


def test_capture_redirige_a_url_firmada_y_audita_solo_el_original(env):
    eid, data = make_event(1, minutes_ago=3)
    env.seed_events([(eid, data)])
    env.login_as("viewer@x.com")
    r = env.client.get(f"/api/events/{eid}/capture?kind=thumb", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://signed.test/1"
    assert env.audit_actions() == []
    r = env.client.get(f"/api/events/{eid}/capture?kind=original", follow_redirects=False)
    assert r.status_code == 302 and env.audit_actions() == ["capture_original"]
    assert "thumbs" in env.signed[0] and "images" in env.signed[1]
    # La URL firmada de un mismo objeto se reutiliza durante 9 minutos.
    env.client.get(f"/api/events/{eid}/capture?kind=thumb", follow_redirects=False)
    assert len(env.signed) == 2


def test_no_se_firma_un_uri_fuera_del_bucket(env):
    eid, data = make_event(1, minutes_ago=3)
    data["capture"]["thumb_uri"] = "gs://otro-bucket/x.jpg"
    env.seed_events([(eid, data)])
    env.login_as("viewer@x.com")
    r = env.client.get(f"/api/events/{eid}/capture?kind=thumb", follow_redirects=False)
    assert r.status_code == 404 and env.signed == []


# --- resumen ----------------------------------------------------------------------------------


def _stats(env, today, n, per_day=10):
    for i in range(n):
        d = (today - timedelta(days=i)).isoformat()
        env.events.collection("daily_stats").document(f"{DEVICE}_{d}").set(
            {
                "device_id": DEVICE,
                "date": d,
                "events": per_day,
                "seconds": 60.0,
                "by_class": {"hairnet": 6, "lab_coat": 4},
            }
        )


def test_resumen_7d_usa_daily_stats_con_periodo_anterior(env):
    from zoneinfo import ZoneInfo

    today = datetime.now(UTC).astimezone(ZoneInfo("America/Lima")).date()
    _stats(env, today, 14)
    env.seed_events([make_event(i, minutes_ago=i + 1) for i in range(4)])
    env.login_as("viewer@x.com")
    s = env.client.get(f"/api/devices/{DEVICE}/summary?range=7d").json()
    assert s["kpis"]["events"] == {"value": 70.0, "previous": 70.0}
    assert s["kpis"]["epp_seconds"]["value"] == 420.0
    assert s["kpis"]["top_class"]["nombre_clase"] == "hairnet" and s["kpis"]["top_class"]["count"] == 42
    assert len(s["series"]) == 7 and all(b["total"] == 10 for b in s["series"])
    assert s["by_class"][0] == {"nombre_clase": "hairnet", "count": 42}
    assert s["kpis"]["avg_confidence"]["value"] == pytest.approx(0.85)
    assert s["kpis"]["capture_coverage"]["value"] == 100.0
    assert s["partial"] is False and s["sample"]["events"] == 4


def test_resumen_sin_periodo_anterior_devuelve_null_no_cero(env):
    from zoneinfo import ZoneInfo

    today = datetime.now(UTC).astimezone(ZoneInfo("America/Lima")).date()
    _stats(env, today, 3)
    env.login_as("viewer@x.com")
    s = env.client.get(f"/api/devices/{DEVICE}/summary?range=7d").json()
    assert s["kpis"]["events"]["value"] == 30 and s["kpis"]["events"]["previous"] is None
    assert s["kpis"]["avg_confidence"]["value"] is None


def test_resumen_24h_sale_de_la_ventana_de_eventos(env):
    now = datetime.now(UTC)
    rows = [
        make_event(i, minutes_ago=30 * i + 1, clase="hairnet" if i % 2 else "lab_coat", now=now)
        for i in range(10)
    ]
    rows.append(make_event(50, minutes_ago=60 * 30, now=now))  # hace 30 h: periodo anterior
    env.seed_events(rows)
    env.login_as("viewer@x.com")
    s = env.client.get(f"/api/devices/{DEVICE}/summary?range=24h").json()
    assert s["kpis"]["events"]["value"] == 10 and s["kpis"]["events"]["previous"] == 1
    assert len(s["series"]) == 24 and sum(b["total"] for b in s["series"]) == 10
    assert s["partial"] is False and s["timezone"] == "America/Lima"


# --- administración ---------------------------------------------------------------------------


def test_crud_de_usuarios_y_auditoria(env):
    env.login_as("admin@viz-ia.com")
    r = env.client.post("/api/admin/users", json={"email": "Nuevo@X.com", "role": "operator"}, headers=HDR)
    assert r.status_code == 201 and r.json()["email"] == "nuevo@x.com" and r.json()["active"] is True
    assert env.client.post("/api/admin/users", json={"email": "nuevo@x.com"}, headers=HDR).status_code == 409
    assert env.client.post("/api/admin/users", json={"email": "no-es-correo"}, headers=HDR).status_code == 422
    r = env.client.patch("/api/admin/users/nuevo@x.com", json={"role": "viewer"}, headers=HDR)
    assert r.status_code == 200 and r.json()["role"] == "viewer"
    assert env.client.delete("/api/admin/users/nuevo@x.com", headers=HDR).status_code == 204
    assert env.client.delete("/api/admin/users/nuevo@x.com", headers=HDR).status_code == 404
    assert env.audit_actions() == ["user_create", "user_update", "user_delete"]
    page = env.client.get("/api/admin/audit?limit=2").json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    rest = env.client.get(f"/api/admin/audit?limit=2&cursor={page['next_cursor']}").json()
    assert len(rest["items"]) == 1
    only = env.client.get("/api/admin/audit?action=user_delete").json()
    assert [i["action"] for i in only["items"]] == ["user_delete"]


def test_no_se_puede_modificar_ni_borrar_a_uno_mismo(env):
    env.login_as("admin@viz-ia.com")
    r = env.client.patch("/api/admin/users/admin@viz-ia.com", json={"active": False}, headers=HDR)
    assert r.status_code == 409 and err_code(r) == "uno_mismo"
    r = env.client.delete("/api/admin/users/admin@viz-ia.com", headers=HDR)
    assert r.status_code == 409 and err_code(r) == "uno_mismo"


def test_no_se_puede_dejar_el_sistema_sin_admin(make_env):
    env = make_env("dev")  # el usuario de desarrollo no está en la base: el único admin real es el de la base
    r = env.client.patch("/api/admin/users/admin@viz-ia.com", json={"role": "viewer"}, headers=HDR)
    assert r.status_code == 409 and err_code(r) == "ultimo_admin"
    r = env.client.patch("/api/admin/users/admin@viz-ia.com", json={"active": False}, headers=HDR)
    assert r.status_code == 409 and err_code(r) == "ultimo_admin"
    assert env.client.delete("/api/admin/users/admin@viz-ia.com", headers=HDR).status_code == 409
    # Con un segundo admin, ya se puede degradar al primero.
    env.client.post("/api/admin/users", json={"email": "otro@x.com", "role": "admin"}, headers=HDR)
    r = env.client.patch("/api/admin/users/admin@viz-ia.com", json={"role": "viewer"}, headers=HDR)
    assert r.status_code == 200


# --- SPA estática ------------------------------------------------------------------------------


def test_spa_sirve_archivos_sueltos_de_la_raiz_del_build(make_env, tmp_path):
    """`frontend/public/*` (p. ej. theme-init.js) se copia a la raíz del build, no a assets/."""
    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (tmp_path / "theme-init.js").write_text("// tema", encoding="utf-8")
    env = make_env(static_dir=str(tmp_path))
    r = env.client.get("/theme-init.js")
    assert r.status_code == 200 and r.text == "// tema"
    assert "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"


def test_spa_ruta_desconocida_sigue_devolviendo_index(make_env, tmp_path):
    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    env = make_env(static_dir=str(tmp_path))
    r = env.client.get("/eventos/algo-que-no-existe")
    assert r.status_code == 200 and r.text == "<html>spa</html>" and r.headers["cache-control"] == "no-store"


def test_spa_no_sirve_archivos_fuera_del_directorio_estatico(make_env, tmp_path):
    (tmp_path / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (tmp_path.parent / "secreto.txt").write_text("no deberia verse", encoding="utf-8")
    env = make_env(static_dir=str(tmp_path))
    r = env.client.get(f"/%2e%2e/{tmp_path.name}/../secreto.txt")
    assert r.status_code in (200, 404) and "no deberia verse" not in r.text


# --- WebSocket del navegador ------------------------------------------------------------------


def _ws_app(env):
    from fastapi import FastAPI, WebSocket

    app = FastAPI()
    app.dependency_overrides[env.mods["repo"].get_repo] = lambda: env.repo

    @app.websocket("/ws-test")
    async def ws_test(ws: WebSocket):
        user = await env.mods["auth"].authenticate_ws(ws)
        if user is None:
            return
        await ws.accept()
        await ws.send_json({"email": user.email})
        await ws.close()

    return app


def _cookie(env, email="viewer@x.com"):
    record = env.control.data["dashboard_users"][email]
    user = env.mods["auth"].SessionUser(email=email, role=record["role"])
    return {"cookie": f"epp_session={env.mods['auth'].issue_cookie_value(user)}"}


def test_ws_acepta_cookie_y_origin_correctos(env):
    from starlette.testclient import TestClient

    client = TestClient(_ws_app(env), base_url=BASE)
    with client.websocket_connect("/ws-test", headers={**_cookie(env), "origin": BASE}) as ws:
        assert ws.receive_json() == {"email": "viewer@x.com"}


@pytest.mark.parametrize("headers", ["origen_ajeno", "sin_cookie", "sin_origin"])
def test_ws_rechaza_origin_ajeno_sin_cookie_o_sin_origin(env, headers):
    from starlette.testclient import TestClient

    client = TestClient(_ws_app(env), base_url=BASE)
    h = {"origen_ajeno": {**_cookie(env), "origin": "https://evil.test"}, "sin_cookie": {"origin": BASE}}.get(
        headers, _cookie(env)
    )
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws-test", headers=h):
            pass


# --- OpenAPI ----------------------------------------------------------------------------------


def test_openapi_expone_el_contrato(env):
    spec = env.client.get("/api/openapi.json").json()
    paths = set(spec["paths"])
    assert {
        "/api/auth/google", "/api/auth/logout", "/api/auth/me", "/api/devices",
        "/api/devices/{device_id}/summary", "/api/devices/{device_id}/events",
        "/api/devices/{device_id}/desired-state", "/api/events/{event_id}",
        "/api/events/{event_id}/capture", "/api/admin/users", "/api/admin/audit",
    } <= paths  # fmt: skip
